from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain.models import Activity, ActivitySource
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    ReconciliationStatus,
    UserTrainingProfile,
    WorkoutExecutionStatus,
    create_plan_version,
    create_planned_workout,
)
from app.reconciliation import (
    ReconciliationError,
    WorkoutReconciliationService,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


async def setup_plan(*workout_values):
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    profile = UserTrainingProfile(
        user_id="line-1",
        timezone="Asia/Tokyo",
        operation_id="profile-1",
        updated_at=NOW,
    )
    await settings.save_profile(profile, None)
    plan = create_plan_version(
        "line-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [],
        "test",
        athlete_id="athlete-1",
        created_at=NOW,
    )
    workouts = []
    for sequence, values in enumerate(workout_values):
        values = dict(values)
        workouts.append(
            create_planned_workout(
                plan,
                values.pop("scheduled_date", date(2026, 9, 8)),
                sequence,
                values.pop("workout_type", "easy_run"),
                values.pop("target_intensity", "easy"),
                created_at=NOW,
                **values,
            )
        )
    await history.save_plan(plan)
    await history.save_workouts(workouts)
    await pointers.set("line-1", date(2026, 9, 7), plan.id, None)
    return history, pointers, settings, workouts


def activity(**values) -> Activity:
    defaults = {
        "id": "activity-1",
        "athlete_id": "athlete-1",
        "user_id": "line-1",
        "source_type": ActivitySource.STRAVA,
        "source_activity_id": "activity-1",
        "activity_type": "Run",
        "started_at": datetime(2026, 9, 8, 6, 30, tzinfo=UTC),
        "duration_seconds": 30 * 60,
        "distance_meters": 5000,
    }
    defaults.update(values)
    return Activity(**defaults)


async def test_explicit_manual_link_has_priority_and_records_execution() -> None:
    history, pointers, settings, workouts = await setup_plan(
        {
            "workout_type": "strength",
            "target_duration_minutes": 40,
            "scheduled_start_local_time": time(19),
        },
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "scheduled_start_local_time": time(15, 30),
        },
    )
    actual = activity(
        activity_type="WeightTraining",
        source_type=ActivitySource.LINE_MANUAL,
        planned_workout_id=workouts[0].id,
        completion_status="partial",
        perceived_intensity="moderate",
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )

    result = await service.reconcile(actual)

    assert result.reconciliation.planned_workout_id == workouts[0].id
    assert result.reconciliation.status == ReconciliationStatus.PARTIAL
    assert result.reconciliation.confirmed is True
    assert result.reconciliation.match_confidence == 1
    assert result.reconciliation.matching_evidence == ["explicit_planned_workout_link"]
    execution = next(iter(history.execution_states.values()))
    assert execution.status == WorkoutExecutionStatus.PARTIAL
    assert execution.source_reconciliation_ids == [result.reconciliation.id]


@pytest.mark.parametrize(
    ("completion_status", "expected_execution"),
    [
        ("replaced", WorkoutExecutionStatus.REPLACED),
        ("skipped", WorkoutExecutionStatus.SKIPPED),
    ],
)
async def test_manual_completion_status_is_kept_outside_planned_workout(
    completion_status: str, expected_execution: WorkoutExecutionStatus
) -> None:
    history, pointers, settings, workouts = await setup_plan(
        {"workout_type": "strength", "target_duration_minutes": 30}
    )
    actual = activity(
        activity_type="WeightTraining",
        source_type=ActivitySource.LINE_MANUAL,
        planned_workout_id=workouts[0].id,
        completion_status=completion_status,
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )

    await service.reconcile(actual)

    execution = next(iter(history.execution_states.values()))
    assert execution.status == expected_execution
    assert workouts[0].status.value == "planned"


async def test_high_confidence_match_uses_type_time_duration_and_distance() -> None:
    history, pointers, settings, workouts = await setup_plan(
        {
            "workout_type": "easy_run",
            "target_duration_minutes": 30,
            "target_distance_meters": 5000,
            "scheduled_start_local_time": time(15, 30),
        },
        {
            "workout_type": "strength",
            "target_duration_minutes": 45,
            "scheduled_start_local_time": time(19),
        },
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )

    result = await service.reconcile(activity())

    assert result.reconciliation.status == ReconciliationStatus.MATCHED
    assert result.reconciliation.planned_workout_id == workouts[0].id
    assert result.reconciliation.match_confidence == 1
    assert result.reconciliation.confirmed is True
    assert "activity_type_match" in result.reconciliation.matching_evidence
    assert result.reconciliation.duration_delta_minutes == 0
    assert result.reconciliation.distance_delta_meters == 0


async def test_same_type_activity_outside_scheduled_time_requires_confirmation() -> (
    None
):
    history, pointers, settings, workouts = await setup_plan(
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "target_distance_meters": 5000,
            "scheduled_start_local_time": time(7),
            "availability_slot_id": "monday-morning",
        }
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )

    result = await service.reconcile(activity())

    assert result.reconciliation.status == ReconciliationStatus.AMBIGUOUS
    assert result.reconciliation.confirmed is False
    assert result.reconciliation.planned_workout_id == workouts[0].id
    assert result.reconciliation.match_confidence == 0.69
    assert "start_outside_180_minutes" in result.reconciliation.matching_evidence
    assert (
        "scheduled_time_requires_confirmation"
        in result.reconciliation.matching_evidence
    )


async def test_close_candidates_remain_ambiguous_until_user_selects() -> None:
    history, pointers, settings, workouts = await setup_plan(
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "scheduled_start_local_time": time(15),
        },
        {
            "workout_type": "run",
            "target_duration_minutes": 35,
            "scheduled_start_local_time": time(16),
        },
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    initial = await service.reconcile(activity())

    assert initial.reconciliation.status == ReconciliationStatus.AMBIGUOUS
    assert initial.reconciliation.confirmed is False
    assert initial.reconciliation.candidate_planned_workout_ids == [
        workouts[0].id,
        workouts[1].id,
    ]
    assert history.execution_states == {}

    corrected = await service.correct(
        user_id="line-1",
        activity=activity(),
        expected_reconciliation_id=initial.reconciliation.id,
        planned_workout_id=workouts[1].id,
    )
    retry = await service.correct(
        user_id="line-1",
        activity=activity(),
        expected_reconciliation_id=initial.reconciliation.id,
        planned_workout_id=workouts[1].id,
    )

    assert corrected.reconciliation.id == retry.reconciliation.id
    assert corrected.reconciliation.manual_correction is True
    assert corrected.reconciliation.confirmed is True
    assert corrected.reconciliation.supersedes_reconciliation_id == (
        initial.reconciliation.id
    )
    assert len(await history.list_activity_reconciliations("activity-1")) == 2


async def test_stale_or_foreign_manual_correction_is_rejected() -> None:
    history, pointers, settings, workouts = await setup_plan(
        {"workout_type": "run", "target_duration_minutes": 30}
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    initial = await service.reconcile(activity())
    corrected = await service.correct(
        user_id="line-1",
        activity=activity(),
        expected_reconciliation_id=initial.reconciliation.id,
        planned_workout_id=None,
    )
    assert corrected.reconciliation.status == ReconciliationStatus.UNPLANNED

    with pytest.raises(ReconciliationError, match="stale"):
        await service.correct(
            user_id="line-1",
            activity=activity(),
            expected_reconciliation_id=initial.reconciliation.id,
            planned_workout_id=workouts[0].id,
        )
    with pytest.raises(ReconciliationError, match="does not belong"):
        await service.correct(
            user_id="other-user",
            activity=activity(),
            expected_reconciliation_id=corrected.reconciliation.id,
            planned_workout_id=None,
        )


async def test_second_activity_is_duplicate_candidate_unless_split_is_allowed() -> None:
    history, pointers, settings, _ = await setup_plan(
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "split_allowed": False,
        }
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    await service.reconcile(activity())
    second = await service.reconcile(
        activity(
            id="activity-2",
            source_activity_id="activity-2",
            source_type=ActivitySource.LINE_MANUAL,
        )
    )
    assert second.reconciliation.status == ReconciliationStatus.DUPLICATE_CANDIDATE
    assert second.reconciliation.confirmed is False

    split_history, split_pointers, split_settings, _ = await setup_plan(
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "split_allowed": True,
        }
    )
    split_service = WorkoutReconciliationService(
        split_history, split_pointers, split_settings, clock=lambda: NOW
    )
    await split_service.reconcile(activity())
    split_second = await split_service.reconcile(
        activity(id="activity-2", source_activity_id="activity-2")
    )
    assert split_second.reconciliation.status == ReconciliationStatus.MATCHED
    assert split_second.reconciliation.confirmed is True


async def test_no_active_plan_is_recorded_as_unplanned_without_sensitive_text() -> None:
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    actual = activity(description="private description", details="private health text")

    result = await service.reconcile(actual)

    assert result.reconciliation.status == ReconciliationStatus.UNPLANNED
    assert result.reconciliation.planned_workout_id is None
    assert result.reconciliation.plan_version_id is None
    assert "private" not in str(result.reconciliation.model_dump())


async def test_missing_candidate_waits_for_grace_and_confirmed_provider_sync() -> None:
    history, pointers, settings, workouts = await setup_plan(
        {
            "workout_type": "run",
            "scheduled_start_local_time": time(7),
            "target_duration_minutes": 30,
        }
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    before_grace = datetime(2026, 9, 7, 23, 0, tzinfo=UTC)

    assert (
        await service.missing_candidates(
            "line-1",
            date(2026, 9, 8),
            provider_sync_confirmed=False,
            now=NOW,
        )
        == []
    )
    assert (
        await service.missing_candidates(
            "line-1",
            date(2026, 9, 8),
            provider_sync_confirmed=True,
            now=before_grace,
        )
        == []
    )

    candidates = await service.missing_candidates(
        "line-1",
        date(2026, 9, 8),
        provider_sync_confirmed=True,
        now=NOW,
    )
    retry = await service.missing_candidates(
        "line-1",
        date(2026, 9, 8),
        provider_sync_confirmed=True,
        now=NOW + timedelta(minutes=5),
    )

    assert len(candidates) == 1
    assert candidates[0].planned_workout_id == workouts[0].id
    assert candidates[0].status == ReconciliationStatus.NOT_PERFORMED
    assert candidates[0].confirmed is False
    assert retry[0].id == candidates[0].id
    assert history.execution_states == {}

    resolved = await service.resolve_missing(
        user_id="line-1",
        expected_reconciliation_id=candidates[0].id,
        decision="not_performed",
    )
    resolved_retry = await service.resolve_missing(
        user_id="line-1",
        expected_reconciliation_id=candidates[0].id,
        decision="not_performed",
    )
    assert resolved.id == resolved_retry.id
    assert resolved.confirmed is True
    assert resolved.supersedes_reconciliation_id == candidates[0].id
    execution = next(iter(history.execution_states.values()))
    assert execution.status == WorkoutExecutionStatus.NOT_PERFORMED


async def test_missing_sync_pending_does_not_mark_workout_not_performed() -> None:
    history, pointers, settings, _ = await setup_plan(
        {
            "workout_type": "run",
            "scheduled_start_local_time": time(7),
            "target_duration_minutes": 30,
        }
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    candidate = (
        await service.missing_candidates(
            "line-1",
            date(2026, 9, 8),
            provider_sync_confirmed=True,
            now=NOW,
        )
    )[0]

    resolved = await service.resolve_missing(
        user_id="line-1",
        expected_reconciliation_id=candidate.id,
        decision="sync_pending",
    )

    assert resolved.status == ReconciliationStatus.UNMATCHED
    assert resolved.confirmed is False
    assert resolved.correction_reason == "sync_pending"
    assert history.execution_states == {}


async def test_missing_scan_does_not_conflict_with_ambiguous_activity_candidates() -> (
    None
):
    history, pointers, settings, _ = await setup_plan(
        {
            "workout_type": "run",
            "target_duration_minutes": 30,
            "scheduled_start_local_time": time(7),
        },
        {
            "workout_type": "run",
            "target_duration_minutes": 35,
            "scheduled_start_local_time": time(8),
        },
    )
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )
    ambiguous = await service.reconcile(
        activity(started_at=datetime(2026, 9, 7, 22, 30, tzinfo=UTC))
    )
    assert ambiguous.reconciliation.status == ReconciliationStatus.AMBIGUOUS

    missing = await service.missing_candidates(
        "line-1",
        date(2026, 9, 8),
        provider_sync_confirmed=True,
        now=NOW,
    )

    assert missing == []


async def test_retry_recovers_when_execution_save_failed_after_reconciliation() -> None:
    class FlakyHistory(InMemoryPlanningHistoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        async def save_execution_state(self, state) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary execution failure")
            await super().save_execution_state(state)

    base_history, pointers, settings, workouts = await setup_plan(
        {"workout_type": "run", "target_duration_minutes": 30}
    )
    history = FlakyHistory()
    await history.save_plan(next(iter(base_history.plans.values())))
    await history.save_workouts(workouts)
    service = WorkoutReconciliationService(
        history, pointers, settings, clock=lambda: NOW
    )

    with pytest.raises(RuntimeError, match="temporary execution"):
        await service.reconcile(activity())
    result = await service.reconcile(activity())

    assert result.reconciliation.confirmed is True
    assert len(history.reconciliations) == 1
    assert len(history.execution_states) == 1
