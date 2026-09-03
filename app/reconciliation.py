from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.models import Activity
from app.planning import (
    ActivePlanPointerStore,
    PlannedWorkout,
    PlanningHistoryStore,
    ReconciliationStatus,
    TrainingSettingsStateStore,
    UserTrainingProfile,
    WorkoutExecutionStatus,
    WorkoutReconciliation,
    create_reconciliation,
    create_workout_execution_state,
)

MATCHER_VERSION = "workout-matcher-v2"
HIGH_CONFIDENCE_THRESHOLD = 0.70
MINIMUM_CANDIDATE_THRESHOLD = 0.45
AMBIGUITY_MARGIN = 0.10
MISSING_ACTIVITY_GRACE = timedelta(hours=2)


class ReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class MatchCandidate:
    workout: PlannedWorkout
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ReconciliationResult:
    reconciliation: WorkoutReconciliation
    candidates: tuple[PlannedWorkout, ...] = ()


class WorkoutReconciliationService:
    def __init__(
        self,
        history: PlanningHistoryStore,
        active_plans: ActivePlanPointerStore,
        settings: TrainingSettingsStateStore,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._history = history
        self._active_plans = active_plans
        self._settings = settings
        self._clock = clock

    async def reconcile(
        self, activity: Activity, operation_id: str | None = None
    ) -> ReconciliationResult:
        user_id = activity.user_id
        if not user_id:
            raise ReconciliationError("Activity requires an app user owner")
        operation = operation_id or f"activity:{activity.id}"
        existing = await self._history.list_activity_reconciliations(activity.id)
        same_operation = next(
            (item for item in reversed(existing) if item.operation_id == operation),
            None,
        )
        if same_operation is not None:
            await self._ensure_execution_for_existing(same_operation)
            return ReconciliationResult(
                same_operation,
                await self._candidate_workouts(same_operation),
            )
        confirmed = next((item for item in reversed(existing) if item.confirmed), None)
        if confirmed is not None:
            await self._ensure_execution_for_existing(confirmed)
            return ReconciliationResult(
                confirmed,
                await self._candidate_workouts(confirmed),
            )

        profile = await self._profile(user_id)
        local_started = activity.started_at.astimezone(ZoneInfo(profile.timezone))
        week_start = profile.local_week_start(activity.started_at)
        plan_id = await self._active_plans.get(user_id, week_start)
        workouts = (
            await self._history.list_workouts(plan_id) if plan_id is not None else []
        )
        if activity.planned_workout_id:
            workout = next(
                (
                    item
                    for item in workouts
                    if item.id == activity.planned_workout_id
                    and item.user_id == user_id
                ),
                None,
            )
            if workout is None:
                raise ReconciliationError(
                    "Explicit planned workout does not belong to the active plan"
                )
            result = await self._create_for_workout(
                activity,
                workout,
                status=_completion_status(activity, matched=True),
                confidence=1.0,
                evidence=["explicit_planned_workout_link"],
                candidate_ids=[workout.id],
                confirmed=True,
                operation_id=operation,
            )
            return ReconciliationResult(result, (workout,))

        day_workouts = [
            item
            for item in workouts
            if item.scheduled_date == local_started.date()
            and _workout_family(item.workout_type) != "rest"
        ]
        candidates = sorted(
            (_score(activity, item, local_started) for item in day_workouts),
            key=lambda item: (-item.confidence, item.workout.sequence),
        )
        candidate_ids = [item.workout.id for item in candidates]
        if not candidates:
            result = create_reconciliation(
                None,
                activity.source_type.value,
                MATCHER_VERSION,
                activity.id,
                user_id=user_id,
                athlete_id=activity.athlete_id,
                plan_version_id=plan_id,
                operation_id=operation,
                status=ReconciliationStatus.UNPLANNED,
                match_confidence=0.0,
                matching_evidence=["no_planned_workout_on_local_date"],
                confirmed=True,
                created_at=self._clock(),
            )
            await self._history.save_reconciliation(result)
            return ReconciliationResult(result)

        top = candidates[0]
        duplicate = await self._is_duplicate_candidate(top.workout, activity.id)
        ambiguous = (
            len(candidates) > 1
            and candidates[1].confidence >= MINIMUM_CANDIDATE_THRESHOLD
            and top.confidence - candidates[1].confidence <= AMBIGUITY_MARGIN
        )
        if duplicate:
            status = ReconciliationStatus.DUPLICATE_CANDIDATE
            is_confirmed = False
            evidence = [*top.evidence, "workout_already_has_activity"]
        elif ambiguous or (
            MINIMUM_CANDIDATE_THRESHOLD <= top.confidence < HIGH_CONFIDENCE_THRESHOLD
        ):
            status = ReconciliationStatus.AMBIGUOUS
            is_confirmed = False
            evidence = list(top.evidence)
        elif top.confidence < MINIMUM_CANDIDATE_THRESHOLD:
            status = ReconciliationStatus.UNMATCHED
            is_confirmed = False
            evidence = list(top.evidence)
        else:
            status = _completion_status(activity, matched=True)
            is_confirmed = True
            evidence = list(top.evidence)
        result = await self._create_for_workout(
            activity,
            top.workout,
            status=status,
            confidence=top.confidence,
            evidence=evidence,
            candidate_ids=candidate_ids,
            confirmed=is_confirmed,
            operation_id=operation,
        )
        return ReconciliationResult(result, tuple(item.workout for item in candidates))

    async def correct(
        self,
        *,
        user_id: str,
        activity: Activity,
        expected_reconciliation_id: str,
        planned_workout_id: str | None,
        reason: str = "user_selection",
    ) -> ReconciliationResult:
        if activity.user_id != user_id:
            raise ReconciliationError("Activity does not belong to this user")
        expected = await self._history.get_reconciliation(expected_reconciliation_id)
        if (
            expected is None
            or expected.user_id != user_id
            or expected.activity_id != activity.id
        ):
            raise ReconciliationError("Reconciliation target mismatch")
        history = await self._history.list_activity_reconciliations(activity.id)
        latest = history[-1] if history else None
        operation = f"manual:{expected.id}:{planned_workout_id or 'unplanned'}"
        if latest is not None and latest.id != expected.id:
            if (
                latest.supersedes_reconciliation_id == expected.id
                and latest.manual_correction
                and latest.operation_id == operation
            ):
                await self._ensure_execution_for_existing(latest)
                return ReconciliationResult(
                    latest, await self._candidate_workouts(latest)
                )
            raise ReconciliationError("Reconciliation selection is stale")
        if planned_workout_id is None:
            corrected = create_reconciliation(
                None,
                activity.source_type.value,
                MATCHER_VERSION,
                activity.id,
                user_id=user_id,
                athlete_id=activity.athlete_id,
                plan_version_id=expected.plan_version_id,
                operation_id=operation,
                status=ReconciliationStatus.UNPLANNED,
                candidate_planned_workout_ids=expected.candidate_planned_workout_ids,
                match_confidence=1.0,
                matching_evidence=["user_confirmed_unplanned"],
                confirmed=True,
                manual_correction=True,
                correction_reason=reason,
                supersedes_reconciliation_id=expected.id,
                created_at=max(
                    self._clock(), expected.created_at + timedelta(microseconds=1)
                ),
            )
            await self._history.save_reconciliation(corrected)
            return ReconciliationResult(corrected)

        if expected.plan_version_id is None:
            raise ReconciliationError("No active plan is available for correction")
        workout = next(
            (
                item
                for item in await self._history.list_workouts(expected.plan_version_id)
                if item.id == planned_workout_id and item.user_id == user_id
            ),
            None,
        )
        if workout is None:
            raise ReconciliationError("Planned workout does not belong to this user")
        corrected = await self._create_for_workout(
            activity,
            workout,
            status=_completion_status(activity, matched=True),
            confidence=1.0,
            evidence=["user_confirmed_planned_workout"],
            candidate_ids=expected.candidate_planned_workout_ids,
            confirmed=True,
            operation_id=operation,
            manual_correction=True,
            correction_reason=reason,
            supersedes_reconciliation_id=expected.id,
            created_at=max(
                self._clock(), expected.created_at + timedelta(microseconds=1)
            ),
        )
        return ReconciliationResult(corrected, (workout,))

    async def missing_candidates(
        self,
        user_id: str,
        local_date: date,
        *,
        provider_sync_confirmed: bool,
        now: datetime | None = None,
    ) -> list[WorkoutReconciliation]:
        if not provider_sync_confirmed:
            return []
        reference = (now or self._clock()).astimezone(UTC)
        profile = await self._profile(user_id)
        local_reference = reference.astimezone(ZoneInfo(profile.timezone))
        week_start = local_date - timedelta(days=local_date.weekday())
        plan_id = await self._active_plans.get(user_id, week_start)
        if plan_id is None:
            return []
        plan_reconciliations = await self._history.list_plan_reconciliations(plan_id)
        results: list[WorkoutReconciliation] = []
        for workout in await self._history.list_workouts(plan_id):
            if (
                workout.scheduled_date != local_date
                or _workout_family(workout.workout_type) == "rest"
                or not _past_grace_period(workout, local_reference)
            ):
                continue
            existing = await self._history.list_workout_reconciliations(workout.id)
            has_activity_candidate = any(
                item.activity_id is not None
                and (
                    item.planned_workout_id == workout.id
                    or workout.id in item.candidate_planned_workout_ids
                )
                for item in plan_reconciliations
            )
            if has_activity_candidate or any(item.confirmed for item in existing):
                continue
            operation = f"missing:{workout.id}:{local_date.isoformat()}"
            retry = next(
                (item for item in existing if item.operation_id == operation), None
            )
            if retry is not None:
                results.append(retry)
                continue
            candidate = create_reconciliation(
                workout,
                "none",
                MATCHER_VERSION,
                operation_id=operation,
                status=ReconciliationStatus.NOT_PERFORMED,
                candidate_planned_workout_ids=[workout.id],
                match_confidence=None,
                matching_evidence=["grace_period_elapsed", "provider_sync_confirmed"],
                confirmed=False,
                created_at=reference,
            )
            await self._history.save_reconciliation(candidate)
            results.append(candidate)
        return results

    async def resolve_missing(
        self,
        *,
        user_id: str,
        expected_reconciliation_id: str,
        decision: str,
    ) -> WorkoutReconciliation:
        if decision not in {"not_performed", "sync_pending", "schedule_changed"}:
            raise ReconciliationError("Unknown missing workout decision")
        expected = await self._history.get_reconciliation(expected_reconciliation_id)
        if (
            expected is None
            or expected.user_id != user_id
            or expected.status != ReconciliationStatus.NOT_PERFORMED
            or expected.planned_workout_id is None
            or expected.activity_id is not None
        ):
            raise ReconciliationError("Missing workout target mismatch")
        existing = await self._history.list_workout_reconciliations(
            expected.planned_workout_id
        )
        operation = f"missing-decision:{expected.id}:{decision}"
        latest = existing[-1] if existing else None
        if latest is not None and latest.id != expected.id:
            if (
                latest.supersedes_reconciliation_id == expected.id
                and latest.operation_id == operation
            ):
                await self._ensure_execution_for_existing(latest)
                return latest
            raise ReconciliationError("Missing workout selection is stale")
        if expected.plan_version_id is None:
            raise ReconciliationError("Missing workout has no plan")
        workout = next(
            (
                item
                for item in await self._history.list_workouts(expected.plan_version_id)
                if item.id == expected.planned_workout_id and item.user_id == user_id
            ),
            None,
        )
        if workout is None:
            raise ReconciliationError("Missing workout does not belong to this user")
        status = (
            ReconciliationStatus.UNMATCHED
            if decision == "sync_pending"
            else ReconciliationStatus.NOT_PERFORMED
        )
        resolved = create_reconciliation(
            workout,
            "none",
            MATCHER_VERSION,
            operation_id=operation,
            status=status,
            candidate_planned_workout_ids=[workout.id],
            matching_evidence=[f"user_confirmed:{decision}"],
            confirmed=decision != "sync_pending",
            manual_correction=True,
            correction_reason=decision,
            supersedes_reconciliation_id=expected.id,
            created_at=max(
                self._clock(), expected.created_at + timedelta(microseconds=1)
            ),
        )
        await self._history.save_reconciliation(resolved)
        if decision != "sync_pending":
            await self._ensure_execution(resolved, workout)
        return resolved

    async def _create_for_workout(
        self,
        activity: Activity,
        workout: PlannedWorkout,
        *,
        status: ReconciliationStatus,
        confidence: float,
        evidence: Sequence[str],
        candidate_ids: Sequence[str],
        confirmed: bool,
        operation_id: str,
        **values,
    ) -> WorkoutReconciliation:
        created_at = values.pop("created_at", self._clock())
        duration_delta = (
            activity.duration_seconds / 60 - workout.target_duration_minutes
            if workout.target_duration_minutes is not None
            else None
        )
        distance_delta = (
            activity.distance_meters - workout.target_distance_meters
            if workout.target_distance_meters is not None
            else None
        )
        objective = _objective_factors(
            activity, workout, duration_delta, distance_delta
        )
        reconciliation = create_reconciliation(
            workout,
            activity.source_type.value,
            MATCHER_VERSION,
            activity.id,
            operation_id=operation_id,
            status=status,
            candidate_planned_workout_ids=list(candidate_ids),
            match_confidence=round(confidence, 4),
            matching_evidence=list(evidence),
            duration_delta_minutes=duration_delta,
            distance_delta_meters=distance_delta,
            intensity_delta=_intensity_delta(
                activity.perceived_intensity, workout.target_intensity
            ),
            objective_factors=objective,
            confirmed=confirmed,
            created_at=created_at,
            **values,
        )
        await self._history.save_reconciliation(reconciliation)
        if confirmed and status in {
            ReconciliationStatus.MATCHED,
            ReconciliationStatus.PARTIAL,
            ReconciliationStatus.NOT_PERFORMED,
        }:
            await self._ensure_execution(reconciliation, workout)
        return reconciliation

    async def _ensure_execution_for_existing(
        self, reconciliation: WorkoutReconciliation
    ) -> None:
        if (
            not reconciliation.confirmed
            or reconciliation.planned_workout_id is None
            or reconciliation.plan_version_id is None
            or reconciliation.status
            not in {
                ReconciliationStatus.MATCHED,
                ReconciliationStatus.PARTIAL,
                ReconciliationStatus.NOT_PERFORMED,
            }
        ):
            return
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
        if workout is not None:
            await self._ensure_execution(reconciliation, workout)

    async def _ensure_execution(
        self,
        reconciliation: WorkoutReconciliation,
        workout: PlannedWorkout,
    ) -> None:
        history = await self._history.list_workout_reconciliations(workout.id)
        confirmed = [
            item
            for item in history
            if item.confirmed
            and item.status
            in {
                ReconciliationStatus.MATCHED,
                ReconciliationStatus.PARTIAL,
                ReconciliationStatus.NOT_PERFORMED,
            }
        ]
        revision = next(
            (
                index
                for index, item in enumerate(confirmed, start=1)
                if item.id == reconciliation.id
            ),
            len(confirmed),
        )
        execution_status = {
            ReconciliationStatus.MATCHED: WorkoutExecutionStatus.COMPLETED,
            ReconciliationStatus.PARTIAL: WorkoutExecutionStatus.PARTIAL,
            ReconciliationStatus.NOT_PERFORMED: WorkoutExecutionStatus.NOT_PERFORMED,
        }[reconciliation.status]
        if "reported_completion:replaced" in reconciliation.objective_factors:
            execution_status = WorkoutExecutionStatus.REPLACED
        elif "reported_completion:skipped" in reconciliation.objective_factors:
            execution_status = WorkoutExecutionStatus.SKIPPED
        if reconciliation.correction_reason == "schedule_changed":
            execution_status = WorkoutExecutionStatus.REPLACED
        execution = create_workout_execution_state(
            workout,
            revision=max(revision, 1),
            status=execution_status,
            operation_id=reconciliation.operation_id,
            source_reconciliation_ids=[reconciliation.id],
            recorded_at=reconciliation.created_at,
        )
        await self._history.save_execution_state(execution)

    async def _is_duplicate_candidate(
        self, workout: PlannedWorkout, activity_id: str
    ) -> bool:
        if workout.split_allowed:
            return False
        existing = await self._history.list_workout_reconciliations(workout.id)
        return any(
            item.activity_id != activity_id
            and item.confirmed
            and item.status
            in {ReconciliationStatus.MATCHED, ReconciliationStatus.PARTIAL}
            for item in existing
        )

    async def _candidate_workouts(
        self, reconciliation: WorkoutReconciliation
    ) -> tuple[PlannedWorkout, ...]:
        if reconciliation.plan_version_id is None:
            return ()
        candidate_ids = set(reconciliation.candidate_planned_workout_ids)
        return tuple(
            item
            for item in await self._history.list_workouts(
                reconciliation.plan_version_id
            )
            if item.id in candidate_ids
        )

    async def _profile(self, user_id: str) -> UserTrainingProfile:
        return await self._settings.get_profile(user_id) or UserTrainingProfile(
            user_id=user_id, operation_id="reconciliation-default"
        )


def _score(
    activity: Activity, workout: PlannedWorkout, local_started: datetime
) -> MatchCandidate:
    score = 0.0
    evidence = ["same_local_date"]
    if _activity_family(activity.activity_type) == _workout_family(
        workout.workout_type
    ):
        score += 0.45
        evidence.append("activity_type_match")
    else:
        evidence.append("activity_type_mismatch")

    if workout.target_duration_minutes is None:
        score += 0.10
        evidence.append("duration_not_prescribed")
    else:
        ratio = abs(
            activity.duration_seconds / 60 - workout.target_duration_minutes
        ) / max(workout.target_duration_minutes, 1)
        if ratio <= 0.20:
            score += 0.25
            evidence.append("duration_within_20_percent")
        elif ratio <= 0.50:
            score += 0.12
            evidence.append("duration_within_50_percent")
        else:
            evidence.append("duration_outside_50_percent")

    if workout.target_distance_meters is None:
        score += 0.05
    elif workout.target_distance_meters > 0:
        ratio = (
            abs(activity.distance_meters - workout.target_distance_meters)
            / workout.target_distance_meters
        )
        if ratio <= 0.20:
            score += 0.15
            evidence.append("distance_within_20_percent")
        elif ratio <= 0.50:
            score += 0.07
            evidence.append("distance_within_50_percent")

    start_is_outside_confirmation_window = False
    if workout.scheduled_start_local_time is None:
        score += 0.10
        evidence.append("scheduled_time_unspecified")
    else:
        scheduled = datetime.combine(
            workout.scheduled_date,
            workout.scheduled_start_local_time,
            local_started.tzinfo,
        )
        difference = abs((local_started - scheduled).total_seconds()) / 60
        if difference <= 90:
            score += 0.15
            evidence.append("start_within_90_minutes")
        elif difference <= 180:
            score += 0.07
            evidence.append("start_within_180_minutes")
        else:
            evidence.append("start_outside_180_minutes")
            start_is_outside_confirmation_window = True

    # A matching sport and duration are useful evidence, but they cannot safely
    # distinguish a morning slot from a distant evening slot. Preserve the
    # candidate for manual selection instead of silently consuming that workout.
    if start_is_outside_confirmation_window:
        score = min(score, HIGH_CONFIDENCE_THRESHOLD - 0.01)
        evidence.append("scheduled_time_requires_confirmation")
    return MatchCandidate(workout, min(score, 1.0), tuple(evidence))


def _completion_status(activity: Activity, *, matched: bool) -> ReconciliationStatus:
    if activity.completion_status == "skipped":
        return ReconciliationStatus.NOT_PERFORMED
    if activity.completion_status in {"partial", "replaced"}:
        return ReconciliationStatus.PARTIAL
    return ReconciliationStatus.MATCHED if matched else ReconciliationStatus.UNMATCHED


def _objective_factors(
    activity: Activity,
    workout: PlannedWorkout,
    duration_delta: float | None,
    distance_delta: float | None,
) -> list[str]:
    factors = [f"source:{activity.source_type.value}"]
    if duration_delta is not None:
        if duration_delta < -1:
            factors.append("duration_below_plan")
        elif duration_delta > 1:
            factors.append("duration_above_plan")
        else:
            factors.append("duration_on_plan")
    if distance_delta is not None:
        tolerance = max((workout.target_distance_meters or 0) * 0.05, 100)
        if distance_delta < -tolerance:
            factors.append("distance_below_plan")
        elif distance_delta > tolerance:
            factors.append("distance_above_plan")
        else:
            factors.append("distance_on_plan")
    if activity.completion_status:
        factors.append(f"reported_completion:{activity.completion_status}")
    return factors


def _intensity_delta(actual: str | None, planned: str) -> str | None:
    if actual is None:
        return None
    levels = {"easy": 1, "moderate": 2, "hard": 3}
    if actual not in levels or planned not in levels:
        return "different" if actual != planned else "same"
    delta = levels[actual] - levels[planned]
    return "same" if delta == 0 else ("higher" if delta > 0 else "lower")


def _activity_family(value: str) -> str:
    normalized = value.lower().replace("_", "")
    if "run" in normalized or normalized in {"walk", "hike"}:
        return "run"
    if "ride" in normalized or "cycl" in normalized or "bike" in normalized:
        return "ride"
    if normalized in {"weighttraining", "workout", "crossfit"} or any(
        token in normalized for token in ("strength", "weight", "gym")
    ):
        return "strength"
    if any(token in normalized for token in ("yoga", "mobility", "stretch")):
        return "mobility"
    return normalized


def _workout_family(value: str) -> str:
    normalized = value.lower().replace("_", "")
    if normalized in {"rest", "recoveryday"}:
        return "rest"
    return _activity_family(value)


def _past_grace_period(workout: PlannedWorkout, local_reference: datetime) -> bool:
    start = workout.scheduled_start_local_time or time(23, 59)
    scheduled = datetime.combine(workout.scheduled_date, start, local_reference.tzinfo)
    end = scheduled + timedelta(minutes=workout.target_duration_minutes or 0)
    return local_reference >= end + MISSING_ACTIVITY_GRACE
