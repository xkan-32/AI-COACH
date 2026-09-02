from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.domain.models import Activity, ConditionLevel, ConditionReport
from app.planning import (
    AchievementStatus,
    ActivePlanPointerStore,
    NextWorkoutReadinessAssessment,
    PlannedWorkout,
    PlanningHistoryStore,
    ReadinessStatus,
    ReconciliationStatus,
    SafetyGateStatus,
    TrainingSettingsStateStore,
    UserTrainingProfile,
    WorkoutReview,
    create_readiness_assessment,
    create_safety_gate_result,
    create_workout_review,
    readiness_status_for_gate,
)

REVIEW_RULE_VERSION = "workout-review-v1"
READINESS_RULE_VERSION = "next-workout-readiness-v1"
READINESS_PROMPT_VERSION = "readiness-prompt-v1"


class ReadinessOutput(BaseModel):
    status: ReadinessStatus
    reason_codes: list[str] = Field(default_factory=list, max_length=10)
    display_reason: str = Field(min_length=1, max_length=500)


class ReadinessGenerator(Protocol):
    model_name: str | None

    async def generate(self, input_snapshot: dict) -> ReadinessOutput: ...


class LocalReadinessGenerator:
    model_name = None

    async def generate(self, input_snapshot: dict) -> ReadinessOutput:
        return ReadinessOutput(
            status=ReadinessStatus.AS_PLANNED,
            reason_codes=["reviewed_as_planned"],
            display_reason="直近の実績と体調から、次の予定は計画どおり実施できます。",
        )


class VertexReadinessGenerator:
    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model
        self.model_name = model

    async def generate(self, input_snapshot: dict) -> ReadinessOutput:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=json.dumps(input_snapshot, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You conservatively assess readiness for an already planned workout. "
                    "Never diagnose, never alter the plan, and obey safety_gate."
                ),
                response_mime_type="application/json",
                response_schema=ReadinessOutput,
                temperature=0.1,
            ),
        )
        if response.parsed is not None:
            return ReadinessOutput.model_validate(response.parsed)
        return ReadinessOutput.model_validate_json(response.text)


class ActiveReadinessPointerStore(Protocol):
    async def get(
        self, user_id: str, local_date: date, planned_workout_id: str
    ) -> str | None: ...

    async def set(
        self,
        user_id: str,
        local_date: date,
        planned_workout_id: str,
        assessment_id: str,
        expected_previous_id: str | None,
    ) -> None: ...


class ReadinessPointerConflict(ValueError):
    pass


class InMemoryActiveReadinessPointerStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, date, str], str] = {}

    async def get(
        self, user_id: str, local_date: date, planned_workout_id: str
    ) -> str | None:
        return self.items.get((user_id, local_date, planned_workout_id))

    async def set(
        self,
        user_id: str,
        local_date: date,
        planned_workout_id: str,
        assessment_id: str,
        expected_previous_id: str | None,
    ) -> None:
        key = (user_id, local_date, planned_workout_id)
        if self.items.get(key) != expected_previous_id:
            raise ReadinessPointerConflict("Active readiness assessment changed")
        self.items[key] = assessment_id


class FirestoreActiveReadinessPointerStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _document(self, user_id: str, local_date: date, workout_id: str):
        return self._client.collection("active_readiness_assessments").document(
            f"{user_id}:{local_date.isoformat()}:{workout_id}"
        )

    async def get(
        self, user_id: str, local_date: date, planned_workout_id: str
    ) -> str | None:
        snapshot = await self._document(user_id, local_date, planned_workout_id).get()
        return str(snapshot.to_dict()["assessment_id"]) if snapshot.exists else None

    async def set(
        self,
        user_id: str,
        local_date: date,
        planned_workout_id: str,
        assessment_id: str,
        expected_previous_id: str | None,
    ) -> None:
        from google.cloud import firestore

        document = self._document(user_id, local_date, planned_workout_id)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def update(txn):
            snapshot = await document.get(transaction=txn)
            current = (
                snapshot.to_dict().get("assessment_id") if snapshot.exists else None
            )
            if current != expected_previous_id:
                raise ReadinessPointerConflict("Active readiness assessment changed")
            txn.set(
                document,
                {
                    "user_id": user_id,
                    "local_date": local_date.isoformat(),
                    "planned_workout_id": planned_workout_id,
                    "assessment_id": assessment_id,
                    "updated_at": datetime.now(UTC),
                },
            )

        await update(transaction)


@dataclass(frozen=True)
class WorkoutFeedbackResult:
    review: WorkoutReview | None
    next_workout: PlannedWorkout | None
    assessment: NextWorkoutReadinessAssessment | None


class WorkoutFeedbackService:
    def __init__(
        self,
        history: PlanningHistoryStore,
        active_plans: ActivePlanPointerStore,
        settings: TrainingSettingsStateStore,
        active_readiness: ActiveReadinessPointerStore,
        generator: ReadinessGenerator,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._history = history
        self._active_plans = active_plans
        self._settings = settings
        self._active_readiness = active_readiness
        self._generator = generator
        self._clock = clock

    async def evaluate(
        self,
        activity: Activity,
        condition: ConditionReport | None,
        operation_id: str | None = None,
    ) -> WorkoutFeedbackResult:
        if not activity.user_id:
            raise ValueError("Activity requires an app user owner")
        operation = operation_id or f"activity:{activity.id}"
        profile = await self._profile(activity.user_id)
        local_started = activity.started_at.astimezone(ZoneInfo(profile.timezone))
        reconciliations = await self._history.list_activity_reconciliations(activity.id)
        reconciliation = next(
            (item for item in reversed(reconciliations) if item.confirmed), None
        )
        review = await self._review(activity, reconciliation, condition, operation)
        plan_id = reconciliation.plan_version_id if reconciliation else None
        if plan_id is None:
            plan_id = await self._active_plans.get(
                activity.user_id, profile.local_week_start(activity.started_at)
            )
        if plan_id is None:
            return WorkoutFeedbackResult(review, None, None)
        workouts = await self._history.list_workouts(plan_id)
        next_workout = _next_workout(workouts, reconciliation, local_started.date())
        if next_workout is None:
            next_week = profile.local_week_start(activity.started_at) + timedelta(
                days=7
            )
            next_plan_id = await self._active_plans.get(activity.user_id, next_week)
            if next_plan_id is not None:
                next_workout = _next_workout(
                    await self._history.list_workouts(next_plan_id),
                    None,
                    local_started.date(),
                )
        if next_workout is None:
            return WorkoutFeedbackResult(review, None, None)
        assessment = await self._assess(
            activity.user_id,
            local_started.date(),
            next_workout,
            condition,
            operation,
            profile.timezone,
        )
        return WorkoutFeedbackResult(review, next_workout, assessment)

    async def _review(
        self, activity, reconciliation, condition, operation
    ) -> WorkoutReview | None:
        if (
            reconciliation is None
            or reconciliation.planned_workout_id is None
            or reconciliation.plan_version_id is None
        ):
            return None
        existing = await self._history.list_reconciliation_reviews(reconciliation.id)
        same_operation = next(
            (item for item in reversed(existing) if item.operation_id == operation),
            None,
        )
        if same_operation is not None:
            return same_operation
        workout = next(
            (
                item
                for item in await self._history.list_workouts(
                    reconciliation.plan_version_id
                )
                if item.id == reconciliation.planned_workout_id
            ),
            None,
        )
        if workout is None:
            return None
        achievement = {
            ReconciliationStatus.MATCHED: AchievementStatus.ACHIEVED,
            ReconciliationStatus.PARTIAL: AchievementStatus.PARTIAL,
            ReconciliationStatus.NOT_PERFORMED: AchievementStatus.NOT_ACHIEVED,
        }.get(reconciliation.status, AchievementStatus.UNASSESSED)
        condition_factors = _condition_factors(condition)
        snapshot = {
            "activity_id": activity.id,
            "reconciliation_id": reconciliation.id,
            "planned_workout_id": workout.id,
            "reconciliation_status": reconciliation.status.value,
            "duration_delta_minutes": reconciliation.duration_delta_minutes,
            "distance_delta_meters": reconciliation.distance_delta_meters,
            "intensity_delta": reconciliation.intensity_delta,
            "condition_factors": condition_factors,
        }
        review = create_workout_review(
            workout,
            REVIEW_RULE_VERSION,
            reconciliation.id,
            operation_id=operation,
            activity_id=activity.id,
            achievement_status=achievement,
            objective_factors=reconciliation.objective_factors,
            condition_factors=condition_factors,
            dialogue_factors=(
                ["manual_reconciliation_correction"]
                if reconciliation.manual_correction
                else []
            ),
            feedback_codes=_feedback_codes(achievement, condition),
            input_snapshot=snapshot,
            supersedes_review_id=existing[-1].id if existing else None,
            created_at=self._clock(),
        )
        await self._history.save_review(review)
        return review

    async def _assess(
        self, user_id, local_date, workout, condition, operation, timezone
    ) -> NextWorkoutReadinessAssessment:
        same_day_reviews = [
            item
            for item in await self._history.list_plan_reviews(workout.plan_version_id)
            if item.created_at.astimezone(ZoneInfo(timezone)).date() == local_date
        ]
        latest_by_activity = {}
        for item in same_day_reviews:
            latest_by_activity[item.activity_id or item.id] = item
        reviews = list(latest_by_activity.values())
        previous = await self._history.list_readiness_assessments(user_id, workout.id)
        same_operation = next(
            (item for item in reversed(previous) if item.operation_id == operation),
            None,
        )
        if same_operation is not None:
            await self._repair_pointer(same_operation)
            return same_operation
        gate_status, gate_reasons = _safety_gate(condition, previous)
        snapshot = {
            "user_id": user_id,
            "local_date": local_date.isoformat(),
            "planned_workout": {
                "id": workout.id,
                "scheduled_date": workout.scheduled_date.isoformat(),
                "workout_type": workout.workout_type,
                "target_duration_minutes": workout.target_duration_minutes,
                "target_intensity": workout.target_intensity,
                "safety_constraints": workout.safety_constraints,
            },
            "review_ids": sorted(item.id for item in reviews),
            "review_codes": sorted(
                {code for item in reviews for code in item.feedback_codes}
            ),
            "condition_factors": _condition_factors(condition),
            "safety_gate": {"status": gate_status.value, "reason_codes": gate_reasons},
        }
        gate = create_safety_gate_result(
            user_id,
            operation,
            gate_status,
            gate_reasons,
            READINESS_RULE_VERSION,
            snapshot,
            workout.id,
        )
        await self._history.save_safety_gate(gate)
        generated = await self._generate(gate_status, gate_reasons, snapshot)
        status = readiness_status_for_gate(gate, generated.status)
        reasons = list(dict.fromkeys([*gate_reasons, *generated.reason_codes]))
        if status == ReadinessStatus.BLOCKED and not reasons:
            reasons.append("ai_safety_block")
        revision = len(previous) + 1
        assessment = create_readiness_assessment(
            user_id,
            local_date,
            workout,
            revision,
            status,
            gate.id,
            READINESS_RULE_VERSION,
            operation,
            snapshot,
            reason_codes=reasons,
            display_reason=generated.display_reason,
            referenced_review_ids=sorted(item.id for item in reviews),
            supersedes_assessment_id=previous[-1].id if previous else None,
            ai_model=self._generator.model_name,
            prompt_version=READINESS_PROMPT_VERSION,
            created_at=self._clock(),
        )
        await self._history.save_readiness(assessment)
        expected = (
            previous[-1].id
            if previous and previous[-1].local_date == local_date
            else None
        )
        await self._active_readiness.set(
            user_id, local_date, workout.id, assessment.id, expected
        )
        return assessment

    async def _generate(self, gate_status, gate_reasons, snapshot) -> ReadinessOutput:
        if gate_status == SafetyGateStatus.BLOCKED:
            return ReadinessOutput(
                status=ReadinessStatus.BLOCKED,
                reason_codes=gate_reasons,
                display_reason="安全を優先し、次の運動は実施せず追加確認が必要です。",
            )
        if "condition_missing" in gate_reasons:
            return ReadinessOutput(
                status=ReadinessStatus.NEEDS_INFORMATION,
                reason_codes=gate_reasons,
                display_reason="次の予定を判断するため、現在の体調を教えてください。",
            )
        try:
            return await self._generator.generate(snapshot)
        except Exception:  # noqa: BLE001 - provider failures require safe fallback
            return ReadinessOutput(
                status=(
                    ReadinessStatus.WITH_ADJUSTMENT
                    if gate_status == SafetyGateStatus.ADJUSTMENT_REQUIRED
                    else ReadinessStatus.NEEDS_INFORMATION
                ),
                reason_codes=["readiness_provider_unavailable"],
                display_reason="安全側の暫定判断です。体調を優先してください。",
            )

    async def _repair_pointer(self, assessment) -> None:
        current = await self._active_readiness.get(
            assessment.user_id, assessment.local_date, assessment.planned_workout_id
        )
        if current == assessment.id:
            return
        if current is not None:
            return
        await self._active_readiness.set(
            assessment.user_id,
            assessment.local_date,
            assessment.planned_workout_id,
            assessment.id,
            None,
        )

    async def _profile(self, user_id: str) -> UserTrainingProfile:
        return await self._settings.get_profile(user_id) or UserTrainingProfile(
            user_id=user_id, operation_id="readiness-default-profile"
        )


def _next_workout(workouts, reconciliation, local_date) -> PlannedWorkout | None:
    current_sequence = None
    if reconciliation and reconciliation.planned_workout_id:
        current = next(
            (item for item in workouts if item.id == reconciliation.planned_workout_id),
            None,
        )
        current_sequence = current.sequence if current else None
    candidates = [
        item
        for item in workouts
        if item.scheduled_date > local_date
        or (
            item.scheduled_date == local_date
            and current_sequence is not None
            and item.sequence > current_sequence
        )
    ]
    return min(
        candidates, key=lambda item: (item.scheduled_date, item.sequence), default=None
    )


def _condition_factors(condition: ConditionReport | None) -> list[str]:
    if condition is None:
        return ["condition:missing"]
    factors = [f"condition:{condition.level.value}"]
    if condition.severity is not None:
        factors.append(
            "severity:high" if condition.severity >= 7 else "severity:moderate"
        )
    if condition.worsened_during_activity is True:
        factors.append("condition:worsened_during_activity")
    return factors


def _feedback_codes(achievement, condition) -> list[str]:
    return [f"achievement:{achievement.value}", *_condition_factors(condition)]


def _safety_gate(condition, previous) -> tuple[SafetyGateStatus, list[str]]:
    if any(item.status == ReadinessStatus.BLOCKED for item in previous):
        return SafetyGateStatus.BLOCKED, ["previous_safety_block"]
    if condition is None:
        return SafetyGateStatus.ALLOWED, ["condition_missing"]
    if condition.level == ConditionLevel.PAIN:
        return SafetyGateStatus.BLOCKED, ["condition_pain"]
    if condition.worsened_during_activity is True:
        return SafetyGateStatus.BLOCKED, ["condition_worsened"]
    if condition.level in {ConditionLevel.DISCOMFORT, ConditionLevel.FATIGUED}:
        return SafetyGateStatus.ADJUSTMENT_REQUIRED, [
            f"condition_{condition.level.value}"
        ]
    return SafetyGateStatus.ALLOWED, []
