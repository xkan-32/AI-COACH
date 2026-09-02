import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from app.domain.models import ApprovalStatus, WorkoutProposal
from app.state import StravaTokenStore
from app.strava import StoredStravaToken, StravaClient


@dataclass(frozen=True)
class ProposalDecisionTask:
    proposal_id: str
    line_user_id: str
    decision: Literal["approve", "reject"]


class ProposalStateStore(Protocol):
    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None: ...
    async def claim(self, task: ProposalDecisionTask) -> WorkoutProposal | None: ...
    async def complete(self, proposal_id: str) -> None: ...
    async def release(self, proposal_id: str) -> None: ...


class ProposalAnalyticsStore(Protocol):
    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None: ...
    async def update_status(self, proposal_id: str, status: ApprovalStatus) -> None: ...


class ProposalOwnerMismatch(ValueError):
    pass


class ProposalExpired(ValueError):
    pass


class InMemoryProposalStateStore:
    def __init__(self) -> None:
        self.items: dict[str, tuple[WorkoutProposal, str, str]] = {}

    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None:
        self.items[proposal.id] = (proposal, line_user_id, "pending")

    async def claim(self, task: ProposalDecisionTask) -> WorkoutProposal | None:
        item = self.items.get(task.proposal_id)
        if item is None:
            return None
        proposal, owner, state = item
        if owner != task.line_user_id:
            raise ProposalOwnerMismatch("Proposal does not belong to this LINE user")
        if proposal.expires_at <= datetime.now(UTC):
            raise ProposalExpired("Proposal approval has expired")
        if state != "pending":
            return None
        next_state = "applying" if task.decision == "approve" else "rejected"
        if task.decision == "reject":
            proposal.status = ApprovalStatus.REJECTED
        self.items[proposal.id] = (proposal, owner, next_state)
        return proposal

    async def complete(self, proposal_id: str) -> None:
        proposal, owner, _ = self.items[proposal_id]
        proposal.status = ApprovalStatus.APPROVED
        self.items[proposal_id] = (proposal, owner, "approved")

    async def release(self, proposal_id: str) -> None:
        proposal, owner, state = self.items[proposal_id]
        if state == "applying":
            self.items[proposal_id] = (proposal, owner, "pending")


class FirestoreProposalStateStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None:
        values = proposal.model_dump(mode="json")
        values.update(line_user_id=line_user_id, workflow_state="pending")
        await (
            self._client.collection("proposal_states")
            .document(proposal.id)
            .create(values)
        )

    async def claim(self, task: ProposalDecisionTask) -> WorkoutProposal | None:
        from google.cloud import firestore

        document = self._client.collection("proposal_states").document(task.proposal_id)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def claim_once(txn):
            snapshot = await document.get(transaction=txn)
            if not snapshot.exists:
                return None
            values = snapshot.to_dict()
            if values["line_user_id"] != task.line_user_id:
                raise ProposalOwnerMismatch(
                    "Proposal does not belong to this LINE user"
                )
            proposal = WorkoutProposal.model_validate(values)
            if proposal.expires_at <= datetime.now(UTC):
                raise ProposalExpired("Proposal approval has expired")
            if values["workflow_state"] != "pending":
                return None
            next_state = "applying" if task.decision == "approve" else "rejected"
            update = {"workflow_state": next_state}
            if task.decision == "reject":
                update["status"] = ApprovalStatus.REJECTED.value
            txn.update(document, update)
            return proposal

        return await claim_once(transaction)

    async def complete(self, proposal_id: str) -> None:
        await (
            self._client.collection("proposal_states")
            .document(proposal_id)
            .update(
                {"workflow_state": "approved", "status": ApprovalStatus.APPROVED.value}
            )
        )

    async def release(self, proposal_id: str) -> None:
        document = self._client.collection("proposal_states").document(proposal_id)
        snapshot = await document.get()
        if snapshot.exists and snapshot.get("workflow_state") == "applying":
            await document.update({"workflow_state": "pending"})


class CompositeProposalStore:
    def __init__(
        self, state: ProposalStateStore, analytics: ProposalAnalyticsStore
    ) -> None:
        self._state = state
        self._analytics = analytics

    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None:
        await self._state.save(proposal, line_user_id)
        await self._analytics.save(proposal, line_user_id)


logger = logging.getLogger(__name__)


class ApprovalService:
    def __init__(
        self,
        states: ProposalStateStore,
        analytics: ProposalAnalyticsStore,
        tokens: StravaTokenStore,
        strava: StravaClient,
    ) -> None:
        self._states = states
        self._analytics = analytics
        self._tokens = tokens
        self._strava = strava

    async def decide(self, task: ProposalDecisionTask) -> str:
        logger.info(
            "proposal_decision_started proposal_id=%s decision=%s",
            task.proposal_id,
            task.decision,
        )
        proposal = await self._states.claim(task)
        if proposal is None:
            return "duplicate"
        if task.decision == "reject":
            await self._analytics.update_status(proposal.id, ApprovalStatus.REJECTED)
            return "rejected"
        try:
            token = await self._tokens.get(proposal.athlete_id)
            if token is None:
                await self._states.release(proposal.id)
                logger.info(
                    "proposal_decision_skipped proposal_id=%s reason=missing_strava_token",
                    proposal.id,
                )
                return "missing_strava_link"
            token = await self._fresh_token(token)
            activity = await self._strava.get_activity(
                proposal.source_activity_id, token.access_token
            )
            marker = f"[AI-COACH:{proposal.id}]"
            if marker not in activity.description:
                block = (
                    f"{marker}\n翌日の提案: {proposal.title} "
                    f"({proposal.duration_minutes}分 / {proposal.intensity})"
                )
                description = "\n\n".join(filter(None, [activity.description, block]))
                await self._strava.update_description(
                    proposal.source_activity_id, token.access_token, description
                )
            await self._states.complete(proposal.id)
            await self._analytics.update_status(proposal.id, ApprovalStatus.APPROVED)
            logger.info(
                "proposal_decision_completed proposal_id=%s decision=approve activity_id=%s",
                proposal.id,
                proposal.source_activity_id,
            )
            return "approved"
        except Exception:
            await self._states.release(proposal.id)
            raise

    async def _fresh_token(self, token: StoredStravaToken) -> StoredStravaToken:
        import time

        if token.expires_at > int(time.time()) + 300:
            return token
        refreshed = await self._strava.refresh(token.refresh_token)
        token = StoredStravaToken(
            athlete_id=token.athlete_id,
            line_user_id=token.line_user_id,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            expires_at=refreshed.expires_at,
        )
        await self._tokens.save(token)
        return token
