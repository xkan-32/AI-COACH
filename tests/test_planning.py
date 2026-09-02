from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.domain.models import Goal, GoalPriority
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    PlanningService,
    PlanVersionConflict,
    create_plan_version,
    create_planned_workout,
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
