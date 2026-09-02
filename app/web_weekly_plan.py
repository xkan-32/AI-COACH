from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from app.plan_approval import PlanActionSigner, PlanApprovalState
from app.planning import PlannedWorkout, TrainingPlanVersion


class InvalidWeeklyPlanToken(ValueError):
    pass


@dataclass(frozen=True)
class WeeklyPlanLink:
    nonce: str
    line_user_id: str
    plan_id: str
    version: int
    expires_at: datetime
    used_at: datetime | None = None


class WeeklyPlanLinkStore(Protocol):
    async def create(self, link: WeeklyPlanLink) -> None: ...
    async def consume(self, nonce: str, now: datetime) -> WeeklyPlanLink | None: ...


class InMemoryWeeklyPlanLinkStore:
    def __init__(self) -> None:
        self.items: dict[str, WeeklyPlanLink] = {}

    async def create(self, link: WeeklyPlanLink) -> None:
        self.items[link.nonce] = link

    async def consume(self, nonce: str, now: datetime) -> WeeklyPlanLink | None:
        link = self.items.get(nonce)
        if link is None or link.used_at is not None or link.expires_at <= now:
            return None
        consumed = WeeklyPlanLink(
            nonce=link.nonce,
            line_user_id=link.line_user_id,
            plan_id=link.plan_id,
            version=link.version,
            expires_at=link.expires_at,
            used_at=now,
        )
        self.items[nonce] = consumed
        return consumed


class FirestoreWeeklyPlanLinkStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def create(self, link: WeeklyPlanLink) -> None:
        await (
            self._client.collection("weekly_plan_links")
            .document(link.nonce)
            .create(
                {
                    "line_user_id": link.line_user_id,
                    "plan_id": link.plan_id,
                    "version": link.version,
                    "expires_at": link.expires_at,
                    "used_at": None,
                }
            )
        )

    async def consume(self, nonce: str, now: datetime) -> WeeklyPlanLink | None:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        document = self._client.collection("weekly_plan_links").document(nonce)
        transaction = self._client.transaction()

        @async_transactional
        async def consume_once(txn: object) -> WeeklyPlanLink | None:
            snapshot = await document.get(transaction=txn)
            if not snapshot.exists:
                return None
            values = snapshot.to_dict()
            expires_at = values.get("expires_at")
            if (
                values.get("used_at") is not None
                or expires_at is None
                or expires_at <= now
            ):
                return None
            txn.update(document, {"used_at": now})
            return WeeklyPlanLink(
                nonce=nonce,
                line_user_id=str(values["line_user_id"]),
                plan_id=str(values["plan_id"]),
                version=int(values["version"]),
                expires_at=expires_at,
                used_at=now,
            )

        return await consume_once(transaction)


class WeeklyPlanWebSigner:
    def __init__(self, key: str, clock=lambda: datetime.now(UTC)) -> None:
        self._key = key.encode()
        self._clock = clock

    def create_link(
        self, line_user_id: str, plan_id: str, version: int
    ) -> tuple[str, WeeklyPlanLink]:
        expires_at = self._clock() + timedelta(minutes=10)
        nonce = secrets.token_urlsafe(24)
        token = self._sign("link", {"n": nonce, "exp": int(expires_at.timestamp())})
        return token, WeeklyPlanLink(nonce, line_user_id, plan_id, version, expires_at)

    def verify_link(self, token: str) -> str:
        payload = self._verify("link", token)
        nonce = payload.get("n")
        if not isinstance(nonce, str) or not nonce:
            raise InvalidWeeklyPlanToken("Invalid weekly plan link")
        return nonce

    def create_session(self, link: WeeklyPlanLink) -> str:
        expires_at = min(
            self._clock() + timedelta(minutes=30),
            link.expires_at + timedelta(minutes=30),
        )
        return self._sign(
            "session",
            {
                "u": link.line_user_id,
                "p": link.plan_id,
                "v": link.version,
                "exp": int(expires_at.timestamp()),
            },
        )

    def verify_session(self, token: str) -> tuple[str, str, int]:
        payload = self._verify("session", token)
        owner, plan_id, version = (
            payload.get("u"),
            payload.get("p"),
            payload.get("v"),
        )
        if (
            not isinstance(owner, str)
            or not owner
            or not isinstance(plan_id, str)
            or not plan_id
            or not isinstance(version, int)
        ):
            raise InvalidWeeklyPlanToken("Invalid weekly plan session")
        return owner, plan_id, version

    def _sign(self, purpose: str, payload: dict[str, object]) -> str:
        encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(
            self._key,
            f"weekly-plan:web:{purpose}:{encoded}".encode(),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_encode(signature)}"

    def _verify(self, purpose: str, token: str) -> dict[str, object]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = hmac.new(
                self._key,
                f"weekly-plan:web:{purpose}:{encoded}".encode(),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _decode(supplied)):
                raise InvalidWeeklyPlanToken("Invalid weekly plan token")
            payload = json.loads(_decode(encoded))
            if int(payload["exp"]) <= int(self._clock().timestamp()):
                raise InvalidWeeklyPlanToken("Weekly plan token expired")
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            if isinstance(exc, InvalidWeeklyPlanToken):
                raise
            raise InvalidWeeklyPlanToken("Invalid weekly plan token") from exc


def build_weekly_plan_dto(
    *,
    plan: TrainingPlanVersion,
    workouts: list[PlannedWorkout],
    approval: PlanApprovalState,
    action_signer: PlanActionSigner,
    previous_plan: TrainingPlanVersion | None = None,
    previous_workouts: list[PlannedWorkout] | None = None,
) -> dict[str, object]:
    current_by_date = _by_date(workouts)
    previous_by_date = _by_date(previous_workouts or [])
    days = []
    for offset in range(7):
        day = plan.week_start + timedelta(days=offset)
        current = current_by_date.get(day, [])
        previous = previous_by_date.get(day, [])
        days.append(
            {
                "date": day.isoformat(),
                "workouts": [_workout_dto(item) for item in current],
                "changes": _daily_changes(current, previous),
            }
        )
    actions = {
        decision: action_signer.create(
            plan.id,
            plan.version,
            approval.line_user_id,
            decision,  # type: ignore[arg-type]
            approval.expires_at,
        )
        for decision in ("approve", "reject", "repropose")
    }
    return {
        "plan": {
            "id": plan.id,
            "week_start": plan.week_start.isoformat(),
            "version": plan.version,
            "status": approval.status.value,
            "rationale": plan.plan_rationale,
            "safety_constraints": list(plan.safety_flags),
            "previous_version": previous_plan.version if previous_plan else None,
            "version_changes": _version_changes(
                plan, workouts, previous_plan, previous_workouts or []
            ),
            "days": days,
        },
        "approval": {
            "expires_at": approval.expires_at.isoformat(),
            "actions": actions,
        },
    }


def _workout_dto(workout: PlannedWorkout) -> dict[str, object]:
    return {
        "id": workout.id,
        "type": workout.workout_type,
        "start_time": workout.scheduled_start_local_time.isoformat()
        if workout.scheduled_start_local_time
        else None,
        "duration_minutes": workout.target_duration_minutes,
        "distance_meters": workout.target_distance_meters,
        "intensity": workout.target_intensity,
        "outdoors": workout.outdoors,
        "environment_ids": list(workout.environment_ids),
        "rationale": workout.rationale,
        "safety_constraints": list(workout.safety_constraints),
    }


def _by_date(workouts: list[PlannedWorkout]) -> dict[date, list[PlannedWorkout]]:
    result: dict[date, list[PlannedWorkout]] = {}
    for workout in workouts:
        result.setdefault(workout.scheduled_date, []).append(workout)
    return result


def _daily_changes(
    current: list[PlannedWorkout], previous: list[PlannedWorkout]
) -> list[str]:
    if not previous:
        return ["初回計画"] if current else []
    current_values = [_comparable(item) for item in current]
    previous_values = [_comparable(item) for item in previous]
    return [] if current_values == previous_values else ["メニュー内容を変更"]


def _version_changes(
    plan: TrainingPlanVersion,
    workouts: list[PlannedWorkout],
    previous_plan: TrainingPlanVersion | None,
    previous_workouts: list[PlannedWorkout],
) -> list[str]:
    if previous_plan is None:
        return ["初回計画"]
    changes = []
    if plan.plan_rationale != previous_plan.plan_rationale:
        changes.append("週間理由を変更")
    if [_comparable(item) for item in workouts] != [
        _comparable(item) for item in previous_workouts
    ]:
        changes.append("日次メニューを変更")
    if plan.safety_flags != previous_plan.safety_flags:
        changes.append("安全制約を変更")
    return changes or ["表示内容に変更なし"]


def _comparable(workout: PlannedWorkout) -> tuple[object, ...]:
    return (
        workout.scheduled_date,
        workout.sequence,
        workout.workout_type,
        workout.scheduled_start_local_time,
        workout.target_duration_minutes,
        workout.target_distance_meters,
        workout.target_intensity,
        workout.outdoors,
        tuple(workout.environment_ids),
        workout.rationale,
        tuple(workout.safety_constraints),
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
