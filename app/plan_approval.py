from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.planning import (
    ActivePlanPointerStore,
    PlanningHistoryStore,
    PlanningService,
    TrainingPlanLifecycleEvent,
    TrainingPlanStatus,
    TrainingPlanVersion,
    create_plan_lifecycle_event,
)

PlanDecision = Literal["approve", "reject", "repropose"]


class PlanApprovalError(ValueError):
    pass


class PlanApprovalStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REPROPOSAL_REQUESTED = "reproposal_requested"
    EXPIRED = "expired"


class PlanApprovalState(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str
    version: int
    week_start: date
    user_id: str
    line_user_id: str
    status: PlanApprovalStatus = PlanApprovalStatus.DRAFT
    expires_at: datetime
    presented_at: datetime | None = None
    decided_at: datetime | None = None
    decision: PlanDecision | None = None


class PlanApprovalStateStore(Protocol):
    async def register_draft(self, state: PlanApprovalState) -> None: ...
    async def get(self, plan_id: str) -> PlanApprovalState | None: ...
    async def get_current(
        self, user_id: str | None, line_user_id: str
    ) -> PlanApprovalState | None: ...
    async def get_latest_for_line(
        self, line_user_id: str
    ) -> PlanApprovalState | None: ...
    async def present(
        self, plan_id: str, version: int, line_user_id: str, now: datetime
    ) -> bool: ...
    async def claim(
        self,
        plan_id: str,
        version: int,
        user_id: str,
        line_user_id: str,
        decision: PlanDecision,
        now: datetime,
    ) -> bool: ...


class InMemoryPlanApprovalStateStore:
    def __init__(self) -> None:
        self.items: dict[str, PlanApprovalState] = {}
        self.pending_by_line: dict[str, tuple[str, date, int]] = {}
        self.pending_by_user: dict[str, tuple[str, date, int]] = {}

    async def register_draft(self, state: PlanApprovalState) -> None:
        existing = self.items.get(state.plan_id)
        if existing is not None:
            if _state_identity(existing) != _state_identity(state):
                raise PlanApprovalError("Plan approval state is immutable")
            return
        current = self.pending_by_line.get(state.line_user_id)
        if current is not None and current[1:] > (state.week_start, state.version):
            raise PlanApprovalError("A newer plan version is already pending")
        self.items[state.plan_id] = state
        pointer = (state.plan_id, state.week_start, state.version)
        self.pending_by_line[state.line_user_id] = pointer
        self.pending_by_user[state.user_id] = pointer

    async def get(self, plan_id: str) -> PlanApprovalState | None:
        return self.items.get(plan_id)

    async def get_current(
        self, user_id: str | None, line_user_id: str
    ) -> PlanApprovalState | None:
        line_pointer = self.pending_by_line.get(line_user_id)
        if line_pointer is None:
            return None
        state = self.items.get(line_pointer[0])
        user_pointer = self.pending_by_user.get(state.user_id) if state else None
        if (
            state is None
            or (user_id is not None and state.user_id != user_id)
            or line_pointer != user_pointer
            or state.line_user_id != line_user_id
            or state.status
            not in {PlanApprovalStatus.DRAFT, PlanApprovalStatus.PENDING}
        ):
            return None
        return state

    async def get_latest_for_line(self, line_user_id: str) -> PlanApprovalState | None:
        pointer = self.pending_by_line.get(line_user_id)
        return self.items.get(pointer[0]) if pointer is not None else None

    async def present(
        self, plan_id: str, version: int, line_user_id: str, now: datetime
    ) -> bool:
        state = self._target(plan_id, version, line_user_id)
        if state.expires_at <= now:
            self.items[plan_id] = state.model_copy(
                update={"status": PlanApprovalStatus.EXPIRED}
            )
            raise PlanApprovalError("Plan approval has expired")
        if state.status == PlanApprovalStatus.PENDING:
            return False
        if state.status != PlanApprovalStatus.DRAFT:
            raise PlanApprovalError("Plan is no longer available")
        self.items[plan_id] = state.model_copy(
            update={"status": PlanApprovalStatus.PENDING, "presented_at": now}
        )
        return True

    async def claim(
        self,
        plan_id: str,
        version: int,
        user_id: str,
        line_user_id: str,
        decision: PlanDecision,
        now: datetime,
    ) -> bool:
        state = self._target(plan_id, version, line_user_id)
        if state.user_id != user_id:
            raise PlanApprovalError("Plan owner mismatch")
        target = _decision_status(decision)
        if state.status == target and state.decision == decision:
            return False
        pointer = self.pending_by_line.get(line_user_id)
        if pointer != (plan_id, state.week_start, version):
            raise PlanApprovalError("A newer plan version is available")
        if state.expires_at <= now:
            self.items[plan_id] = state.model_copy(
                update={"status": PlanApprovalStatus.EXPIRED}
            )
            raise PlanApprovalError("Plan approval has expired")
        if state.status != PlanApprovalStatus.PENDING:
            raise PlanApprovalError("Plan decision has already been recorded")
        self.items[plan_id] = state.model_copy(
            update={
                "status": target,
                "decision": decision,
                "decided_at": now,
            }
        )
        return True

    def _target(
        self, plan_id: str, version: int, line_user_id: str
    ) -> PlanApprovalState:
        state = self.items.get(plan_id)
        if state is None:
            raise PlanApprovalError("Plan approval state not found")
        if state.version != version or state.line_user_id != line_user_id:
            raise PlanApprovalError("Plan approval target mismatch")
        return state


class FirestorePlanApprovalStateStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _state(self, plan_id: str):
        return self._client.collection("plan_approval_states").document(plan_id)

    def _line_pointer(self, line_user_id: str):
        return self._client.collection("pending_training_plans_by_line").document(
            _owner_key(line_user_id)
        )

    def _user_pointer(self, user_id: str):
        return self._client.collection("pending_training_plans_by_user").document(
            _owner_key(user_id)
        )

    async def register_draft(self, state: PlanApprovalState) -> None:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        document = self._state(state.plan_id)
        line_pointer = self._line_pointer(state.line_user_id)
        user_pointer = self._user_pointer(state.user_id)
        transaction = self._client.transaction()

        @async_transactional
        async def register_once(txn: object) -> None:
            existing = await document.get(transaction=txn)
            if existing.exists:
                current_state = PlanApprovalState.model_validate(existing.to_dict())
                if _state_identity(current_state) != _state_identity(state):
                    raise PlanApprovalError("Plan approval state is immutable")
                return
            current = await line_pointer.get(transaction=txn)
            if current.exists:
                current_values = current.to_dict()
                current_order = (
                    date.fromisoformat(str(current_values["week_start"])),
                    int(current_values["version"]),
                )
                if current_order > (state.week_start, state.version):
                    raise PlanApprovalError("A newer plan version is already pending")
            values = _firestore_approval_state_payload(state)
            pointer = {
                "plan_id": state.plan_id,
                "version": state.version,
                "week_start": state.week_start.isoformat(),
                "user_id": state.user_id,
                "line_user_id": state.line_user_id,
                "expires_at": state.expires_at,
            }
            txn.create(document, values)
            txn.set(line_pointer, pointer)
            txn.set(user_pointer, pointer)

        await register_once(transaction)

    async def get(self, plan_id: str) -> PlanApprovalState | None:
        snapshot = await self._state(plan_id).get()
        return (
            PlanApprovalState.model_validate(snapshot.to_dict())
            if snapshot.exists
            else None
        )

    async def get_current(
        self, user_id: str | None, line_user_id: str
    ) -> PlanApprovalState | None:
        line = await self._line_pointer(line_user_id).get()
        if not line.exists:
            return None
        line_values = line.to_dict()
        pointed_user = str(line_values.get("user_id", ""))
        if user_id is not None and pointed_user != user_id:
            return None
        user = await self._user_pointer(pointed_user).get()
        if not user.exists:
            return None
        user_values = user.to_dict()
        if (
            line_values.get("plan_id") != user_values.get("plan_id")
            or line_values.get("week_start") != user_values.get("week_start")
            or line_values.get("version") != user_values.get("version")
            or line_values.get("user_id") != pointed_user
            or line_values.get("line_user_id") != line_user_id
        ):
            return None
        snapshot = await self._state(str(line_values["plan_id"])).get()
        if not snapshot.exists:
            return None
        state = PlanApprovalState.model_validate(snapshot.to_dict())
        if state.status not in {PlanApprovalStatus.DRAFT, PlanApprovalStatus.PENDING}:
            return None
        return state

    async def get_latest_for_line(self, line_user_id: str) -> PlanApprovalState | None:
        pointer = await self._line_pointer(line_user_id).get()
        if not pointer.exists:
            return None
        values = pointer.to_dict()
        if values.get("line_user_id") != line_user_id:
            return None
        snapshot = await self._state(str(values.get("plan_id", ""))).get()
        if not snapshot.exists:
            return None
        state = PlanApprovalState.model_validate(snapshot.to_dict())
        return state if state.line_user_id == line_user_id else None

    async def present(
        self, plan_id: str, version: int, line_user_id: str, now: datetime
    ) -> bool:
        return await self._transition(
            plan_id, version, "", line_user_id, None, now, presenting=True
        )

    async def claim(
        self,
        plan_id: str,
        version: int,
        user_id: str,
        line_user_id: str,
        decision: PlanDecision,
        now: datetime,
    ) -> bool:
        return await self._transition(
            plan_id, version, user_id, line_user_id, decision, now, presenting=False
        )

    async def _transition(
        self,
        plan_id: str,
        version: int,
        user_id: str,
        line_user_id: str,
        decision: PlanDecision | None,
        now: datetime,
        *,
        presenting: bool,
    ) -> bool:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        document = self._state(plan_id)
        pointer = self._line_pointer(line_user_id)
        transaction = self._client.transaction()

        @async_transactional
        async def transition_once(txn: object) -> bool | str:
            snapshot = await document.get(transaction=txn)
            current_pointer = await pointer.get(transaction=txn)
            if not snapshot.exists:
                raise PlanApprovalError("Plan approval state not found")
            state = PlanApprovalState.model_validate(snapshot.to_dict())
            if state.version != version or state.line_user_id != line_user_id:
                raise PlanApprovalError("Plan approval target mismatch")
            if user_id and state.user_id != user_id:
                raise PlanApprovalError("Plan owner mismatch")
            if not presenting:
                assert decision is not None
                target = _decision_status(decision)
                if state.status == target and state.decision == decision:
                    return False
            if state.expires_at <= now:
                txn.update(document, {"status": PlanApprovalStatus.EXPIRED.value})
                return "expired"
            if presenting:
                if state.status == PlanApprovalStatus.PENDING:
                    return False
                if state.status != PlanApprovalStatus.DRAFT:
                    raise PlanApprovalError("Plan is no longer available")
                txn.update(
                    document,
                    {"status": PlanApprovalStatus.PENDING.value, "presented_at": now},
                )
                return True
            if (
                not current_pointer.exists
                or current_pointer.to_dict().get("plan_id") != plan_id
                or current_pointer.to_dict().get("week_start")
                != state.week_start.isoformat()
                or int(current_pointer.to_dict().get("version", 0)) != version
            ):
                raise PlanApprovalError("A newer plan version is available")
            assert decision is not None
            target = _decision_status(decision)
            if state.status != PlanApprovalStatus.PENDING:
                raise PlanApprovalError("Plan decision has already been recorded")
            txn.update(
                document,
                {
                    "status": target.value,
                    "decision": decision,
                    "decided_at": now,
                },
            )
            return True

        result = await transition_once(transaction)
        if result == "expired":
            raise PlanApprovalError("Plan approval has expired")
        return bool(result)


class PlanActionSigner:
    def __init__(self, key: str, clock=lambda: datetime.now(UTC)) -> None:
        self._key = key.encode()
        self._clock = clock

    def create(
        self,
        plan_id: str,
        version: int,
        line_user_id: str,
        decision: PlanDecision,
        expires_at: datetime,
    ) -> str:
        expiry = int(expires_at.timestamp())
        signature = self._signature(plan_id, version, line_user_id, decision, expiry)
        return f"{expiry}.{signature}"

    def verify(
        self,
        token: str,
        *,
        plan_id: str,
        version: int,
        line_user_id: str,
        decision: PlanDecision,
    ) -> None:
        try:
            raw_expiry, supplied = token.split(".", 1)
            expiry = int(raw_expiry)
            if expiry <= int(self._clock().timestamp()):
                raise PlanApprovalError("Plan action token expired")
            expected = self._signature(plan_id, version, line_user_id, decision, expiry)
            if not hmac.compare_digest(expected, supplied):
                raise PlanApprovalError("Invalid plan action token target")
        except (ValueError, TypeError) as exc:
            if isinstance(exc, PlanApprovalError):
                raise
            raise PlanApprovalError("Invalid plan action token") from exc

    def _signature(
        self,
        plan_id: str,
        version: int,
        line_user_id: str,
        decision: PlanDecision,
        expiry: int,
    ) -> str:
        payload = f"{plan_id}:{version}:{line_user_id}:{decision}:{expiry}"
        return hmac.new(
            self._key,
            f"weekly-plan:action:{payload}".encode(),
            hashlib.sha256,
        ).hexdigest()


class PlanApprovalService:
    def __init__(
        self,
        states: PlanApprovalStateStore,
        history: PlanningHistoryStore,
        pointers: ActivePlanPointerStore,
        signer: PlanActionSigner,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._states = states
        self._history = history
        self._pointers = pointers
        self._signer = signer
        self._clock = clock

    async def register_draft(
        self, plan: TrainingPlanVersion, lifetime: timedelta = timedelta(hours=24)
    ) -> None:
        if plan.status != TrainingPlanStatus.DRAFT or not plan.line_user_id:
            raise PlanApprovalError("Only an owned draft plan can be registered")
        await self._states.register_draft(
            PlanApprovalState(
                plan_id=plan.id,
                version=plan.version,
                week_start=plan.week_start,
                user_id=plan.user_id,
                line_user_id=plan.line_user_id,
                expires_at=self._clock() + lifetime,
            )
        )

    async def current_for_line(self, line_user_id: str) -> TrainingPlanVersion | None:
        state = await self._states.get_current(None, line_user_id)
        if state is None:
            return None
        plan = await self._history.get_plan(state.plan_id)
        if (
            plan is None
            or plan.user_id != state.user_id
            or plan.line_user_id != line_user_id
            or plan.version != state.version
        ):
            raise PlanApprovalError("Pending plan target mismatch")
        return plan

    async def latest_state_for_line(
        self, line_user_id: str
    ) -> PlanApprovalState | None:
        return await self._states.get_latest_for_line(line_user_id)

    async def present(self, plan: TrainingPlanVersion) -> PlanApprovalState:
        if not plan.line_user_id:
            raise PlanApprovalError("Plan has no LINE owner")
        await self._states.present(
            plan.id, plan.version, plan.line_user_id, self._clock()
        )
        current = await self._states.get(plan.id)
        if current is None or current.presented_at is None:
            raise PlanApprovalError("Plan presentation state is unavailable")
        event = create_plan_lifecycle_event(
            plan,
            TrainingPlanStatus.DRAFT,
            TrainingPlanStatus.PENDING_APPROVAL,
            "opened_from_line_progress",
            f"present:{plan.id}:{plan.version}",
            occurred_at=current.presented_at,
        )
        await self._history.save_lifecycle_event(event)
        state = await self._states.get_current(plan.user_id, plan.line_user_id)
        if state is None:
            raise PlanApprovalError("Plan is no longer available")
        return state

    async def decide(
        self,
        *,
        plan: TrainingPlanVersion,
        line_user_id: str,
        decision: PlanDecision,
        action_token: str,
    ) -> tuple[str, TrainingPlanLifecycleEvent | None]:
        self._signer.verify(
            action_token,
            plan_id=plan.id,
            version=plan.version,
            line_user_id=line_user_id,
            decision=decision,
        )
        try:
            changed = await self._states.claim(
                plan.id,
                plan.version,
                plan.user_id,
                line_user_id,
                decision,
                self._clock(),
            )
        except PlanApprovalError:
            state = await self._states.get(plan.id)
            if state is not None and state.status == PlanApprovalStatus.EXPIRED:
                await self._history.save_lifecycle_event(
                    create_plan_lifecycle_event(
                        plan,
                        TrainingPlanStatus.PENDING_APPROVAL,
                        TrainingPlanStatus.EXPIRED,
                        "approval_expired",
                        f"expire:{plan.id}:{plan.version}",
                        occurred_at=state.expires_at,
                    )
                )
            raise
        if not changed:
            duplicate = True
        else:
            duplicate = False
        state = await self._states.get(plan.id)
        if state is None or state.decided_at is None:
            raise PlanApprovalError("Plan decision state is unavailable")
        target = {
            "approve": TrainingPlanStatus.ACTIVE,
            "reject": TrainingPlanStatus.REJECTED,
            "repropose": TrainingPlanStatus.REPROPOSAL_REQUESTED,
        }[decision]
        event = create_plan_lifecycle_event(
            plan,
            TrainingPlanStatus.PENDING_APPROVAL,
            target,
            f"user_{decision}",
            f"decision:{plan.id}:{plan.version}:{decision}",
            occurred_at=state.decided_at,
        )
        workouts = await self._history.list_workouts(plan.id)
        if decision == "approve":
            await PlanningService(
                self._history, self._pointers
            ).activate_approved_version(plan, workouts, event)
        else:
            await self._history.save_lifecycle_event(event)
        return ("duplicate" if duplicate else target.value), event


def _decision_status(decision: PlanDecision) -> PlanApprovalStatus:
    return {
        "approve": PlanApprovalStatus.APPROVED,
        "reject": PlanApprovalStatus.REJECTED,
        "repropose": PlanApprovalStatus.REPROPOSAL_REQUESTED,
    }[decision]


def _state_identity(state: PlanApprovalState) -> tuple[object, ...]:
    return (
        state.plan_id,
        state.version,
        state.week_start,
        state.user_id,
        state.line_user_id,
    )


def _firestore_approval_state_payload(state: PlanApprovalState) -> dict[str, object]:
    """Serialize date fields because Firestore does not accept datetime.date."""
    return state.model_dump(mode="json")


def _owner_key(owner: str) -> str:
    return hashlib.sha256(owner.encode()).hexdigest()
