from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from app.domain.models import Goal, GoalPriority
from app.planning import (
    AvailabilitySlot,
    DatedAvailabilityOverride,
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    PlanningService,
    PlanVersionConflict,
    PreferenceConfirmationStatus,
    PreferenceSource,
    ReadinessStatus,
    SafetyGateStatus,
    TrainingPlanStatus,
    TrainingSettingsService,
    UserTrainingProfile,
    WeeklyAvailabilityVersion,
    WorkoutExecutionStatus,
    WorkoutPreference,
    create_plan_lifecycle_event,
    create_plan_version,
    create_planned_workout,
    create_safety_gate_result,
    create_workout_execution_state,
    readiness_status_for_gate,
)


def goal(target: str = "10kmを60分以内") -> Goal:
    return Goal(
        id="goal-1",
        goal_type="タイム・距離",
        target=target,
        priority=GoalPriority.PRIMARY,
    )


async def test_plan_versions_are_immutable_and_supersede_active_version() -> None:
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    service = PlanningService(history, pointers)
    first = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "initial",
        created_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    first_workout = create_planned_workout(
        first,
        date(2026, 9, 7),
        0,
        "easy_run",
        "easy",
        target_duration_minutes=30,
    )

    await service.activate_version(first, [first_workout])
    second = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        2,
        [goal("10kmを58分以内")],
        "condition_feedback",
        supersedes_plan_version_id=first.id,
    )
    await service.activate_version(second, [])

    assert await pointers.get("athlete-1", date(2026, 9, 7)) == second.id
    assert (await history.get_plan(first.id)).goal_snapshot[
        0
    ].target == "10kmを60分以内"
    with pytest.raises(ValidationError):
        first.version = 3


async def test_plan_rejects_stale_supersede_and_skipped_version() -> None:
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    service = PlanningService(history, pointers)
    first = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "initial",
    )
    await service.activate_version(first, [])
    invalid = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        3,
        [goal()],
        "invalid",
        supersedes_plan_version_id=first.id,
    )

    with pytest.raises(PlanVersionConflict, match="increment"):
        await service.activate_version(invalid, [])


def test_plan_and_daily_workout_ids_are_deterministic() -> None:
    first = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "initial",
    )
    second = create_plan_version(
        "athlete-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "retry",
    )

    assert first.id == second.id
    assert (
        create_planned_workout(first, date(2026, 9, 8), 0, "run", "easy").id
        == create_planned_workout(first, date(2026, 9, 8), 0, "run", "easy").id
    )


def test_user_timezone_week_boundary_and_dst_use_local_monday() -> None:
    tokyo = UserTrainingProfile(
        user_id="user-1", timezone="Asia/Tokyo", operation_id="profile-1"
    )
    new_york = UserTrainingProfile(
        user_id="user-2", timezone="America/New_York", operation_id="profile-2"
    )

    assert tokyo.local_week_start(datetime(2026, 9, 6, 15, 0, tzinfo=UTC)) == date(
        2026, 9, 7
    )
    assert new_york.local_week_start(datetime(2026, 3, 8, 7, 30, tzinfo=UTC)) == date(
        2026, 3, 2
    )
    with pytest.raises(ValidationError, match="IANA"):
        UserTrainingProfile(user_id="user-3", timezone="JST", operation_id="profile-3")


def test_availability_rejects_cross_midnight_and_applies_dated_override() -> None:
    with pytest.raises(ValidationError, match="crossing midnight"):
        AvailabilitySlot(
            id="overnight",
            weekday=0,
            start_local_time=time(23),
            end_local_time=time(1),
        )
    morning = AvailabilitySlot(
        id="monday-am",
        weekday=0,
        start_local_time=time(6),
        end_local_time=time(7),
        max_workout_minutes=45,
        buffer_after_minutes=15,
        environment_ids=["outdoor-run"],
    )
    replacement = AvailabilitySlot(
        id="monday-pm",
        weekday=0,
        start_local_time=time(20),
        end_local_time=time(21),
        outdoors_allowed=False,
        split_allowed=True,
    )
    availability = WeeklyAvailabilityVersion(
        id="availability-1",
        user_id="user-1",
        timezone="Asia/Tokyo",
        version=1,
        slots=[morning],
        overrides=[
            DatedAvailabilityOverride(
                id="override-1", local_date=date(2026, 9, 7), slots=[replacement]
            )
        ],
        operation_id="availability-op-1",
    )

    assert availability.slots_for(date(2026, 9, 7)) == [replacement]
    assert availability.slots_for(date(2026, 9, 14)) == [morning]


def test_fixed_rest_day_cannot_contain_availability() -> None:
    rest = AvailabilitySlot(id="rest", weekday=6, fixed_rest_day=True)
    workout = AvailabilitySlot(
        id="workout",
        weekday=6,
        start_local_time=time(8),
        end_local_time=time(9),
    )
    with pytest.raises(ValidationError, match="fixed rest"):
        WeeklyAvailabilityVersion(
            id="availability-1",
            user_id="user-1",
            timezone="Asia/Tokyo",
            version=1,
            slots=[rest, workout],
            operation_id="availability-op-1",
        )


async def test_availability_version_conflict_and_retry_are_idempotent() -> None:
    store = InMemoryTrainingSettingsStore()
    service = TrainingSettingsService(store, store)
    created_at = datetime(2026, 9, 2, tzinfo=UTC)
    first = WeeklyAvailabilityVersion(
        id="availability-1",
        user_id="user-1",
        timezone="Asia/Tokyo",
        version=1,
        operation_id="operation-1",
        created_at=created_at,
    )
    await service.save_availability(first, None)
    await service.save_availability(first, None)

    stale = WeeklyAvailabilityVersion(
        id="availability-2",
        user_id="user-1",
        timezone="Asia/Tokyo",
        version=2,
        supersedes_version_id=first.id,
        operation_id="operation-2",
        created_at=created_at + timedelta(minutes=1),
    )
    with pytest.raises(PlanVersionConflict, match="version changed"):
        await service.save_availability(stale, None)
    await service.save_availability(stale, 1)
    assert (await store.get_availability("user-1")).id == stale.id


async def test_explicit_preference_overrides_inferred_and_expiry_is_honored() -> None:
    store = InMemoryTrainingSettingsStore()
    service = TrainingSettingsService(store, store)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    inferred = WorkoutPreference(
        id="preference-inferred",
        user_id="user-1",
        preference_type="long_workout_day",
        value={"weekday": 5},
        source=PreferenceSource.INFERRED,
        confidence=0.8,
        evidence_event_ids=["event-1", "event-2"],
        confirmation_status=PreferenceConfirmationStatus.PENDING,
        operation_id="preference-op-1",
        expires_at=now + timedelta(days=30),
        created_at=now,
    )
    explicit = WorkoutPreference(
        id="preference-explicit",
        user_id="user-1",
        preference_type="long_workout_day",
        value={"weekday": 6},
        source=PreferenceSource.EXPLICIT,
        operation_id="preference-op-2",
        created_at=now,
    )
    expired = WorkoutPreference(
        id="preference-expired",
        user_id="user-1",
        preference_type="workout_time",
        value={"period": "morning"},
        source=PreferenceSource.INFERRED,
        confidence=0.7,
        evidence_event_ids=["event-3"],
        confirmation_status=PreferenceConfirmationStatus.PENDING,
        operation_id="preference-op-3",
        expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(days=30),
    )
    for item in (inferred, explicit, expired):
        await service.save_preference(item)

    effective = await service.effective_preferences("user-1", now)
    assert effective == [explicit]


def test_plan_lifecycle_rejects_unapproved_activation_and_invalid_transition() -> None:
    plan = create_plan_version(
        "user-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "generation",
        status=TrainingPlanStatus.GENERATING,
        athlete_id=None,
    )
    draft = create_plan_lifecycle_event(
        plan,
        TrainingPlanStatus.GENERATING,
        TrainingPlanStatus.DRAFT,
        "validated",
        "transition-1",
    )
    assert draft.to_status == TrainingPlanStatus.DRAFT
    with pytest.raises(PlanVersionConflict, match="Invalid"):
        create_plan_lifecycle_event(
            plan,
            TrainingPlanStatus.DRAFT,
            TrainingPlanStatus.ACTIVE,
            "approval_missing",
            "transition-2",
        )


async def test_draft_plan_cannot_move_active_pointer() -> None:
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    plan = create_plan_version(
        "user-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [goal()],
        "generation",
        status=TrainingPlanStatus.DRAFT,
        athlete_id=None,
    )
    with pytest.raises(PlanVersionConflict, match="approved"):
        await PlanningService(history, pointers).activate_version(plan, [])
    assert await pointers.get("user-1", date(2026, 9, 7)) is None


def test_execution_state_is_separate_and_safety_block_is_sticky() -> None:
    plan = create_plan_version(
        "user-1", "line-1", date(2026, 9, 7), 1, [goal()], "initial"
    )
    workout = create_planned_workout(plan, date(2026, 9, 7), 0, "run", "moderate")
    execution = create_workout_execution_state(
        workout, 1, WorkoutExecutionStatus.SKIPPED, "execution-op-1"
    )
    gate = create_safety_gate_result(
        "user-1",
        "gate-op-1",
        SafetyGateStatus.BLOCKED,
        ["pain_worsened"],
        "safety-v1",
        {"condition_codes": ["pain_worsened"]},
        planned_workout_id=workout.id,
    )

    assert workout.status.value == "planned"
    assert execution.status == WorkoutExecutionStatus.SKIPPED
    assert (
        readiness_status_for_gate(gate, ReadinessStatus.AS_PLANNED)
        == ReadinessStatus.BLOCKED
    )
