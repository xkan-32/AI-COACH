import asyncio
import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field


class PublicationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PublicationDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    athlete_id: str
    line_user_id: str
    provider: str
    source_plan_version_id: str
    source_review_ids: list[str] = Field(default_factory=list)
    revision: int = Field(ge=1)
    title: str
    body: str
    status: PublicationStatus = PublicationStatus.PENDING
    supersedes_draft_id: str | None = None
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=24)
    )
    ai_model: str | None = None
    prompt_version: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    draft_id: str
    draft_revision: int = Field(ge=1)
    athlete_id: str
    line_user_id: str
    provider: str
    decision: Literal["approve", "reject"]
    approval_event_id: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicationHistoryStore(Protocol):
    async def save_draft(self, draft: PublicationDraft) -> None: ...
    async def save_decision(self, decision: PublicationDecision) -> None: ...


class PublicationApprovalStateStore(Protocol):
    async def register(self, draft: PublicationDraft) -> None: ...
    async def claim(
        self,
        draft_id: str,
        line_user_id: str,
        provider: str,
        revision: int,
        decision: Literal["approve", "reject"],
        now: datetime,
    ) -> bool: ...


class PublicationApprovalError(ValueError):
    pass


class PublicationActionSigner:
    def __init__(self, secret: str, clock=time.time) -> None:
        self._secret = secret.encode("utf-8")
        self._clock = clock

    def create(
        self,
        draft: PublicationDraft,
        decision: Literal["approve", "reject"],
    ) -> str:
        expiry = int(draft.expires_at.timestamp())
        signature = self._signature(
            draft.id,
            draft.line_user_id,
            draft.provider,
            draft.revision,
            decision,
            expiry,
        )
        return urlencode(
            {
                "action": "publication",
                "draft_id": draft.id,
                "provider": draft.provider,
                "revision": draft.revision,
                "decision": decision,
                "expires_at": expiry,
                "signature": signature,
            }
        )

    def verify(
        self,
        draft_id: str,
        line_user_id: str,
        provider: str,
        revision: int,
        decision: Literal["approve", "reject"],
        expires_at: int,
        signature: str,
    ) -> None:
        if expires_at < int(self._clock()):
            raise PublicationApprovalError("Publication approval has expired")
        expected = self._signature(
            draft_id,
            line_user_id,
            provider,
            revision,
            decision,
            expires_at,
        )
        if not hmac.compare_digest(expected, signature):
            raise PublicationApprovalError("Invalid publication approval signature")

    def _signature(
        self,
        draft_id: str,
        line_user_id: str,
        provider: str,
        revision: int,
        decision: str,
        expires_at: int,
    ) -> str:
        payload = (
            f"{draft_id}:{line_user_id}:{provider}:{revision}:{decision}:{expires_at}"
        )
        return hmac.new(
            self._secret, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()


class InMemoryPublicationHistoryStore:
    def __init__(self) -> None:
        self.drafts: dict[str, PublicationDraft] = {}
        self.decisions: dict[str, PublicationDecision] = {}

    async def save_draft(self, draft: PublicationDraft) -> None:
        _save_immutable(self.drafts, draft.id, draft)

    async def save_decision(self, decision: PublicationDecision) -> None:
        _save_immutable(self.decisions, decision.id, decision)


class InMemoryPublicationApprovalStateStore:
    def __init__(self) -> None:
        self.items: dict[str, tuple[PublicationDraft, PublicationStatus]] = {}

    async def register(self, draft: PublicationDraft) -> None:
        if draft.id in self.items:
            existing, _ = self.items[draft.id]
            if existing != draft:
                raise PublicationApprovalError("Draft revision is immutable")
            return
        self.items[draft.id] = (draft, PublicationStatus.PENDING)

    async def claim(
        self,
        draft_id: str,
        line_user_id: str,
        provider: str,
        revision: int,
        decision: Literal["approve", "reject"],
        now: datetime,
    ) -> bool:
        item = self.items.get(draft_id)
        if item is None:
            raise PublicationApprovalError("Publication draft not found")
        draft, status = item
        if (
            draft.line_user_id != line_user_id
            or draft.provider != provider
            or draft.revision != revision
        ):
            raise PublicationApprovalError("Publication approval target mismatch")
        if draft.expires_at <= now:
            self.items[draft_id] = (draft, PublicationStatus.EXPIRED)
            raise PublicationApprovalError("Publication approval has expired")
        if status != PublicationStatus.PENDING:
            return False
        next_status = (
            PublicationStatus.APPROVED
            if decision == "approve"
            else PublicationStatus.REJECTED
        )
        self.items[draft_id] = (draft, next_status)
        return True


class FirestorePublicationApprovalStateStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def register(self, draft: PublicationDraft) -> None:
        values = draft.model_dump(mode="json")
        values["workflow_state"] = PublicationStatus.PENDING.value
        document = self._client.collection("publication_approval_states").document(
            draft.id
        )
        snapshot = await document.get()
        if snapshot.exists:
            existing = snapshot.to_dict()
            if any(
                existing.get(name) != values[name]
                for name in ("line_user_id", "provider", "revision")
            ):
                raise PublicationApprovalError("Draft revision is immutable")
            return
        await document.create(values)

    async def claim(
        self,
        draft_id: str,
        line_user_id: str,
        provider: str,
        revision: int,
        decision: Literal["approve", "reject"],
        now: datetime,
    ) -> bool:
        from google.cloud import firestore

        document = self._client.collection("publication_approval_states").document(
            draft_id
        )
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def claim_once(txn):
            snapshot = await document.get(transaction=txn)
            if not snapshot.exists:
                raise PublicationApprovalError("Publication draft not found")
            values = snapshot.to_dict()
            if (
                values["line_user_id"] != line_user_id
                or values["provider"] != provider
                or int(values["revision"]) != revision
            ):
                raise PublicationApprovalError("Publication approval target mismatch")
            expires_at = values["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if expires_at <= now:
                txn.update(
                    document,
                    {"workflow_state": PublicationStatus.EXPIRED.value},
                )
                raise PublicationApprovalError("Publication approval has expired")
            if values["workflow_state"] != PublicationStatus.PENDING.value:
                return False
            status = (
                PublicationStatus.APPROVED
                if decision == "approve"
                else PublicationStatus.REJECTED
            )
            txn.update(document, {"workflow_state": status.value})
            return True

        return await claim_once(transaction)


class BigQueryPublicationHistoryStore:
    def __init__(self, client: object, table_prefix: str) -> None:
        self._client = client
        self._prefix = table_prefix

    async def save_draft(self, draft: PublicationDraft) -> None:
        row = draft.model_dump(mode="json")
        await self._insert("publication_drafts", row, draft.id)

    async def save_decision(self, decision: PublicationDecision) -> None:
        await self._insert(
            "publication_decisions",
            decision.model_dump(mode="json"),
            decision.id,
        )

    async def _insert(self, table: str, row: dict, row_id: str) -> None:
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            f"{self._prefix}.{table}",
            [row],
            row_ids=[row_id],
        )
        if errors:
            raise RuntimeError(f"BigQuery insert failed for {table}")


class PublicationApprovalService:
    def __init__(
        self,
        states: PublicationApprovalStateStore,
        history: PublicationHistoryStore,
        signer: PublicationActionSigner,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._states = states
        self._history = history
        self._signer = signer
        self._clock = clock

    async def register(self, draft: PublicationDraft) -> None:
        await self._history.save_draft(draft)
        await self._states.register(draft)

    async def decide(
        self,
        *,
        draft: PublicationDraft,
        line_user_id: str,
        decision: Literal["approve", "reject"],
        expires_at: int,
        signature: str,
        approval_event_id: str,
    ) -> PublicationDecision | None:
        self._signer.verify(
            draft.id,
            line_user_id,
            draft.provider,
            draft.revision,
            decision,
            expires_at,
            signature,
        )
        claimed = await self._states.claim(
            draft.id,
            line_user_id,
            draft.provider,
            draft.revision,
            decision,
            self._clock(),
        )
        if not claimed:
            return None
        record = PublicationDecision(
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ai-coach:publication-decision:{draft.id}:"
                    f"{draft.revision}:{decision}",
                )
            ),
            draft_id=draft.id,
            draft_revision=draft.revision,
            athlete_id=draft.athlete_id,
            line_user_id=line_user_id,
            provider=draft.provider,
            decision=decision,
            approval_event_id=approval_event_id,
            decided_at=self._clock(),
        )
        await self._history.save_decision(record)
        return record


def create_publication_draft(
    *,
    athlete_id: str,
    line_user_id: str,
    provider: str,
    source_plan_version_id: str,
    revision: int,
    title: str,
    body: str,
    **values,
) -> PublicationDraft:
    draft_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:publication:{athlete_id}:{provider}:"
            f"{source_plan_version_id}:{revision}",
        )
    )
    return PublicationDraft(
        id=draft_id,
        athlete_id=athlete_id,
        line_user_id=line_user_id,
        provider=provider,
        source_plan_version_id=source_plan_version_id,
        revision=revision,
        title=title,
        body=body,
        **values,
    )


def _save_immutable(store: dict, item_id: str, item: BaseModel) -> None:
    existing = store.get(item_id)
    if existing is not None and existing != item:
        raise PublicationApprovalError("Immutable publication record conflict")
    store[item_id] = item
