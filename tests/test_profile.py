from app.domain.models import GoalPriority
from app.line import InMemoryConditionPromptSender
from app.profile import (
    InMemoryGoalStore,
    InMemoryTrainingResourceStore,
    ProfileCommandError,
    ProfileCommandService,
)


async def service_fixture():
    goals = InMemoryGoalStore()
    resources = InMemoryTrainingResourceStore()
    messenger = InMemoryConditionPromptSender()
    return (
        ProfileCommandService(goals, resources, messenger),
        goals,
        resources,
        messenger,
    )


async def test_registers_and_lists_primary_goal() -> None:
    service, goals, _, messenger = await service_fixture()

    handled = await service.handle("line-1", "目標登録 主目標 marathon 完走 2027-03-14")
    await service.handle("line-1", "目標確認")

    assert handled is True
    stored = await goals.list("line-1")
    assert stored[0].priority == GoalPriority.PRIMARY
    assert "2027-03-14" in messenger.texts[-1][1]


async def test_new_primary_goal_demotes_previous_primary() -> None:
    service, goals, _, _ = await service_fixture()
    await service.handle("line-1", "目標登録 primary 10km 60分 2026-12-01")
    await service.handle("line-1", "目標登録 primary half 完走 2027-02-01")

    stored = await goals.list("line-1")

    assert len(stored) == 2
    assert [goal.priority for goal in stored].count(GoalPriority.PRIMARY) == 1
    assert (
        next(goal for goal in stored if goal.priority == GoalPriority.PRIMARY).goal_type
        == "half"
    )


async def test_registers_and_lists_training_resources() -> None:
    service, _, resources, messenger = await service_fixture()

    await service.handle("line-1", "運動環境登録 屋外ランニング、ルームバイク,ダンベル")
    await service.handle("line-1", "運動環境確認")

    assert await resources.list("line-1") == [
        "屋外ランニング",
        "ルームバイク",
        "ダンベル",
    ]
    assert "ルームバイク" in messenger.texts[-1][1]


async def test_rejects_invalid_goal_date() -> None:
    service, _, _, _ = await service_fixture()

    try:
        await service.handle("line-1", "目標登録 primary marathon 完走 invalid")
    except ProfileCommandError as exc:
        assert "日付" in str(exc)
    else:
        raise AssertionError("ProfileCommandError was not raised")
