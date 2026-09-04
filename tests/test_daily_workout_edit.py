from datetime import UTC, date, datetime, time

import pytest

from app.daily_workout_edit import (
    DailyWorkoutEdit,
    DailyWorkoutEditError,
    DailyWorkoutEditKind,
    DailyWorkoutEditService,
)
from app.planning import (
    AvailabilitySlot,
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    TrainingPlanStatus,
    UserTrainingProfile,
    create_plan_version,
    create_planned_workout,
    create_weekly_availability,
)

NOW = datetime(2026, 9, 7, 3, tzinfo=UTC)  # Monday noon in Tokyo


async def _setup():
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    await settings.save_profile(
        UserTrainingProfile(
            user_id="line-1", timezone="Asia/Tokyo", operation_id="profile"
        ),
        None,
    )
    await settings.save_availability(
        create_weekly_availability(
            "line-1",
            "Asia/Tokyo",
            1,
            "availability",
            slots=[
                AvailabilitySlot(
                    id="tuesday-morning",
                    weekday=1,
                    start_local_time=time(6),
                    end_local_time=time(8),
                    max_workout_minutes=90,
                    environment_ids=["outdoor_running"],
                ),
                AvailabilitySlot(
                    id="wednesday-morning",
                    weekday=2,
                    start_local_time=time(6),
                    end_local_time=time(8),
                    max_workout_minutes=90,
                    environment_ids=["outdoor_running"],
                ),
            ],
        ),
        None,
    )
    base = create_plan_version(
        "line-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [],
        "initial",
        status=TrainingPlanStatus.ACTIVE,
        created_at=NOW,
    )
    workout = create_planned_workout(
        base,
        date(2026, 9, 8),
        0,
        "run",
        "moderate",
        scheduled_start_local_time=time(6, 30),
        target_duration_minutes=60,
        target_distance_meters=8_000,
        outdoors=True,
        environment_ids=["outdoor_running"],
        created_at=NOW,
    )
    await history.save_plan(base)
    await history.save_workouts([workout])
    await pointers.set("line-1", base.week_start, base.id, None)
    return (
        DailyWorkoutEditService(history, pointers, settings, clock=lambda: NOW),
        history,
        pointers,
        base,
        workout,
    )


async def test_direct_edit_creates_active_immutable_revision() -> None:
    service, history, pointers, base, workout = await _setup()

    plan, duplicate = await service.apply(
        user_id="line-1",
        line_user_id="line-1",
        base_plan_id=base.id,
        edit=DailyWorkoutEdit(
            planned_workout_id=workout.id,
            kind=DailyWorkoutEditKind.EDIT,
            duration_minutes=30,
            distance_meters=4_000,
            intensity="easy",
            operation_id="edit-1",
        ),
    )

    assert duplicate is False
    assert plan.status == TrainingPlanStatus.ACTIVE
    assert await pointers.get("line-1", base.week_start) == plan.id
    revised = (await history.list_workouts(plan.id))[0]
    assert revised.target_duration_minutes == 30
    assert revised.target_distance_meters == 4_000
    assert revised.supersedes_planned_workout_id == workout.id
    assert revised.workout_lineage_id == workout.workout_lineage_id


async def test_cancel_records_execution_state_and_retry_is_idempotent() -> None:
    service, history, _, base, workout = await _setup()
    edit = DailyWorkoutEdit(
        planned_workout_id=workout.id,
        kind=DailyWorkoutEditKind.CANCEL,
        operation_id="cancel-1",
    )

    plan, duplicate = await service.apply(
        user_id="line-1", line_user_id="line-1", base_plan_id=base.id, edit=edit
    )
    retry, retried_duplicate = await service.apply(
        user_id="line-1", line_user_id="line-1", base_plan_id=base.id, edit=edit
    )

    assert duplicate is False
    assert retried_duplicate is True
    assert retry.id == plan.id
    assert (await history.list_workouts(plan.id))[0].workout_type == "rest"
    assert next(iter(history.execution_states.values())).status.value == "cancelled"


async def test_direct_edit_rejects_load_increase_and_started_workout() -> None:
    service, _, _, base, workout = await _setup()
    with pytest.raises(DailyWorkoutEditError, match="負荷を上げ"):
        await service.apply(
            user_id="line-1",
            line_user_id="line-1",
            base_plan_id=base.id,
            edit=DailyWorkoutEdit(
                planned_workout_id=workout.id,
                kind=DailyWorkoutEditKind.EDIT,
                duration_minutes=61,
                operation_id="increase-1",
            ),
        )

    started = workout.model_copy(
        update={"scheduled_date": NOW.date(), "scheduled_start_local_time": time(8)}
    )
    service, history, _, base, _ = await _setup()
    history.workouts[started.id] = started
    with pytest.raises(DailyWorkoutEditError, match="開始済み"):
        await service.apply(
            user_id="line-1",
            line_user_id="line-1",
            base_plan_id=base.id,
            edit=DailyWorkoutEdit(
                planned_workout_id=started.id,
                kind=DailyWorkoutEditKind.REST,
                operation_id="started-1",
            ),
        )
