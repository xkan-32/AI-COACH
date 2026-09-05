"""Deterministic activity evaluation and safe Strava publication helpers."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import Activity
from app.planning import PlannedWorkout, WorkoutReconciliation

EVALUATION_VERSION = "activity-evaluation-v1"
MANAGED_BLOCK_START = "<!-- AI-COACH:START -->"
MANAGED_BLOCK_END = "<!-- AI-COACH:END -->"


class PublicationState(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActivityEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    activity_id: str
    planned_workout_id: str | None = None
    reconciliation_id: str
    user_id: str
    athlete_id: str
    evaluation_version: str = EVALUATION_VERSION
    combined_activity: bool = False
    plan_comparison: list[str] = Field(default_factory=list)
    actual_summary: dict[str, float | int | str | None] = Field(default_factory=dict)
    load_summary: dict[str, float | int | str | None] = Field(default_factory=dict)
    next_advice: str = Field(max_length=300)
    safety_corrections: list[str] = Field(default_factory=list)
    publication_state: PublicationState = PublicationState.NOT_REQUESTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvaluationPublicationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    evaluation_id: str
    activity_id: str
    user_id: str
    state: PublicationState
    attempt_count: int = Field(ge=0)
    error_code: str | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvaluationStore(Protocol):
    async def save(self, evaluation: ActivityEvaluation) -> None: ...
    async def get(self, evaluation_id: str) -> ActivityEvaluation | None: ...


class EvaluationPublicationStore(Protocol):
    async def claim(self, evaluation: ActivityEvaluation) -> bool: ...
    async def complete(self, evaluation: ActivityEvaluation) -> None: ...
    async def fail(self, evaluation: ActivityEvaluation, error_code: str) -> None: ...


class InMemoryEvaluationStore:
    def __init__(self) -> None:
        self.items: dict[str, ActivityEvaluation] = {}

    async def save(self, evaluation: ActivityEvaluation) -> None:
        existing = self.items.get(evaluation.id)
        if existing is not None and existing != evaluation:
            raise ValueError("Activity evaluation is immutable")
        self.items[evaluation.id] = evaluation

    async def get(self, evaluation_id: str) -> ActivityEvaluation | None:
        return self.items.get(evaluation_id)


class InMemoryEvaluationPublicationStore:
    def __init__(self) -> None:
        self.items: dict[str, EvaluationPublicationRecord] = {}

    async def claim(self, evaluation: ActivityEvaluation) -> bool:
        existing = self.items.get(evaluation.id)
        if existing and existing.state in {
            PublicationState.SUCCEEDED,
            PublicationState.PENDING,
        }:
            return False
        self.items[evaluation.id] = _publication_record(
            evaluation,
            PublicationState.PENDING,
            (existing.attempt_count if existing else 0) + 1,
        )
        return True

    async def complete(self, evaluation: ActivityEvaluation) -> None:
        current = self.items[evaluation.id]
        self.items[evaluation.id] = _publication_record(
            evaluation, PublicationState.SUCCEEDED, current.attempt_count
        )

    async def fail(self, evaluation: ActivityEvaluation, error_code: str) -> None:
        current = self.items[evaluation.id]
        self.items[evaluation.id] = _publication_record(
            evaluation, PublicationState.FAILED, current.attempt_count, error_code
        )


class FirestoreEvaluationPublicationStore:
    """Firestore is the authoritative idempotency and failure-monitoring state."""

    def __init__(self, client: object) -> None:
        self._client = client

    def _document(self, evaluation_id: str):
        return self._client.collection("activity_evaluation_publications").document(
            evaluation_id
        )

    async def claim(self, evaluation: ActivityEvaluation) -> bool:
        from google.cloud import firestore

        document = self._document(evaluation.id)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def claim_once(txn):
            snapshot = await document.get(transaction=txn)
            if snapshot.exists and snapshot.to_dict().get("state") in {
                PublicationState.SUCCEEDED.value,
                PublicationState.PENDING.value,
            }:
                return False
            attempts = (
                int(snapshot.to_dict().get("attempt_count", 0))
                if snapshot.exists
                else 0
            )
            txn.set(
                document,
                _publication_record(
                    evaluation, PublicationState.PENDING, attempts + 1
                ).model_dump(mode="json"),
            )
            return True

        return await claim_once(transaction)

    async def complete(self, evaluation: ActivityEvaluation) -> None:
        snapshot = await self._document(evaluation.id).get()
        attempts = int(snapshot.to_dict().get("attempt_count", 1))
        await self._document(evaluation.id).set(
            _publication_record(
                evaluation, PublicationState.SUCCEEDED, attempts
            ).model_dump(mode="json")
        )

    async def fail(self, evaluation: ActivityEvaluation, error_code: str) -> None:
        snapshot = await self._document(evaluation.id).get()
        attempts = int(snapshot.to_dict().get("attempt_count", 1))
        await self._document(evaluation.id).set(
            _publication_record(
                evaluation, PublicationState.FAILED, attempts, error_code
            ).model_dump(mode="json")
        )


class BigQueryEvaluationStore:
    def __init__(self, client: object, table: str) -> None:
        self._client, self._table = client, table

    async def save(self, evaluation: ActivityEvaluation) -> None:
        row = evaluation.model_dump(mode="json")
        for field in ("plan_comparison", "safety_corrections"):
            row[field] = row[field]
        errors = await asyncio.to_thread(
            self._client.insert_rows_json, self._table, [row], row_ids=[evaluation.id]
        )
        if errors:
            raise RuntimeError("BigQuery insert failed for activity_evaluations")

    async def get(self, evaluation_id: str) -> ActivityEvaluation | None:
        return None  # Firestore publication claim and deterministic IDs handle worker retries.


def create_evaluation(
    activity: Activity,
    workout: PlannedWorkout | None,
    reconciliation: WorkoutReconciliation,
) -> ActivityEvaluation:
    if not reconciliation.confirmed:
        raise ValueError("Only confirmed activities can be evaluated")
    if workout is None:
        if reconciliation.planned_workout_id is not None:
            raise ValueError("A planned activity requires its planned workout")
        if reconciliation.status.value != "unplanned":
            raise ValueError("Only confirmed unplanned activities can omit a workout")
    elif reconciliation.planned_workout_id != workout.id:
        raise ValueError("Only confirmed planned activities can be evaluated")
    combined = "combined_activity" in reconciliation.matching_evidence
    comparison = (
        [] if combined or workout is None else _plan_comparison(activity, workout)
    )
    load = _load_summary(activity)
    advice, corrections = _safe_next_advice(activity, load)
    identifier = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "ai-coach:evaluation:"
            f"{activity.id}:{workout.id if workout else 'unplanned'}:{EVALUATION_VERSION}",
        )
    )
    return ActivityEvaluation(
        id=identifier,
        activity_id=activity.id,
        planned_workout_id=workout.id if workout else None,
        reconciliation_id=reconciliation.id,
        user_id=workout.user_id if workout else reconciliation.user_id,
        athlete_id=activity.athlete_id,
        combined_activity=combined,
        plan_comparison=comparison if workout else [],
        actual_summary={
            "duration_minutes": round(activity.duration_seconds / 60, 1),
            "distance_meters": activity.distance_meters,
            "average_heartrate_bpm": activity.average_heartrate_bpm,
            "max_heartrate_bpm": activity.max_heartrate_bpm,
            "elevation_gain_meters": activity.total_elevation_gain_meters,
        },
        load_summary=load,
        next_advice=advice,
        safety_corrections=corrections,
    )


def render_managed_block(evaluation: ActivityEvaluation) -> str:
    facts = evaluation.actual_summary
    parts = [
        "AI-COACH 評価",
        f"実績: {facts['duration_minutes']}分 / {facts['distance_meters']:.0f}m",
    ]
    if facts.get("average_heartrate_bpm") is not None:
        parts.append(f"平均心拍: {facts['average_heartrate_bpm']:.0f} bpm")
    if evaluation.combined_activity:
        parts.append("複数予定をまとめて実施（予定別の数値配分はしていません）")
    elif evaluation.planned_workout_id is None:
        parts.append("計画外として記録（予定達成の判定はしていません）")
    elif evaluation.plan_comparison:
        parts.append("計画対比: " + "、".join(evaluation.plan_comparison))
    parts.append("次回: " + evaluation.next_advice)
    return f"{MANAGED_BLOCK_START}\n" + "\n".join(parts) + f"\n{MANAGED_BLOCK_END}"


def merge_managed_block(description: str, block: str) -> str:
    start, end = (
        description.find(MANAGED_BLOCK_START),
        description.find(MANAGED_BLOCK_END),
    )
    if start >= 0 and end >= start:
        return (
            description[:start].rstrip()
            + ("\n\n" if description[:start].strip() else "")
            + block
            + description[end + len(MANAGED_BLOCK_END) :]
        )
    return description.rstrip() + ("\n\n" if description.strip() else "") + block


def _plan_comparison(activity: Activity, workout: PlannedWorkout) -> list[str]:
    result: list[str] = []
    if workout.target_duration_minutes:
        delta = round(
            activity.duration_seconds / 60 - workout.target_duration_minutes, 1
        )
        result.append(f"時間差 {delta:+.1f}分")
    if workout.target_distance_meters:
        delta = round(activity.distance_meters - workout.target_distance_meters)
        result.append(f"距離差 {delta:+.0f}m")
    return result or ["計画値なし"]


def _load_summary(activity: Activity) -> dict[str, float | int | str | None]:
    load = (activity.suffer_score or 0) + activity.duration_seconds / 60
    band = (
        "high"
        if load >= 100 or (activity.average_heartrate_bpm or 0) >= 165
        else "moderate"
        if load >= 45
        else "low"
    )
    return {
        "load_score": round(load, 1),
        "load_band": band,
        "average_heartrate_bpm": activity.average_heartrate_bpm,
    }


def _safe_next_advice(
    activity: Activity, load: dict[str, float | int | str | None]
) -> tuple[str, list[str]]:
    if load["load_band"] == "high":
        return "次回は回復を優先し、高強度は避けてください。", [
            "post_ai_high_load_recovery"
        ]
    if activity.average_heartrate_bpm is None:
        return "心拍データがないため、次回は主観的なきつさも確認してください。", [
            "heartrate_missing"
        ]
    return "次回も体調を確認し、無理のない範囲で計画を続けてください。", []


def _publication_record(
    evaluation: ActivityEvaluation,
    state: PublicationState,
    attempts: int,
    error_code: str | None = None,
) -> EvaluationPublicationRecord:
    return EvaluationPublicationRecord(
        id=hashlib.sha256(
            f"evaluation-publication:{evaluation.id}".encode()
        ).hexdigest(),
        evaluation_id=evaluation.id,
        activity_id=evaluation.activity_id,
        user_id=evaluation.user_id,
        state=state,
        attempt_count=attempts,
        error_code=error_code,
        completed_at=datetime.now(UTC)
        if state in {PublicationState.SUCCEEDED, PublicationState.FAILED}
        else None,
    )
