from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import CoachingContext, GoalPriority, TrainingEnvironmentCategory
from app.line import InMemoryConditionPromptSender
from app.profile import (
    InMemoryGoalStore,
    InMemoryProfileDraftStore,
    InMemoryTrainingResourceStore,
    ProfileCommandError,
    ProfileCommandService,
    ProfileDraft,
    ProfileWorkflow,
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

    stored = await resources.list("line-1")
    assert [item.display_name for item in stored] == [
        "屋外ランニング",
        "インドアバイク",
        "ダンベル",
    ]
    assert "インドアバイク" in messenger.texts[-1][1]


async def test_unknown_environment_is_preserved_without_guessing() -> None:
    service, _, resources, _ = await service_fixture()
    await service.handle("line-1", "運動環境登録 河川敷の階段")
    item = (await resources.list("line-1"))[0]
    assert item.category == TrainingEnvironmentCategory.OTHER
    assert item.detail == "河川敷の階段"


async def test_environment_alias_duplicates_are_collapsed() -> None:
    service, _, resources, _ = await service_fixture()
    await service.handle(
        "line-1", "運動環境登録 ルームバイク、エアロバイク、インドアバイク"
    )
    assert [item.display_name for item in await resources.list("line-1")] == [
        "インドアバイク"
    ]


async def test_environment_command_rejects_empty_and_upper_limit() -> None:
    service, _, _, _ = await service_fixture()
    assert await service.handle("line-1", "運動環境登録 ") is False
    with pytest.raises(ProfileCommandError, match="1〜20"):
        await service.handle(
            "line-1", "運動環境登録 " + "、".join(f"項目{i}" for i in range(21))
        )


async def test_conversation_add_change_deactivate_and_cancel() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    workflow = ProfileWorkflow(goals, resources, drafts, messenger)
    for text in ["運動環境追加", "エアロバイク"]:
        assert await workflow.handle_text("line-1", text)
    item = (await resources.list("line-1"))[0]
    assert item.display_name == "インドアバイク"
    await workflow.handle_text("line-1", "運動環境変更")
    await workflow.handle_text("line-1", item.id[:8])
    await workflow.handle_text("line-1", "ローラー台")
    assert (await resources.list("line-1"))[0].display_name == "ローラー台"
    await workflow.handle_text("line-1", "運動環境無効化")
    await workflow.handle_text("line-1", item.id[:8])
    assert await resources.list("line-1") == []
    await workflow.handle_text("line-1", "目標追加")
    await workflow.handle_text("line-1", "cancel")
    assert await drafts.get("line-1") is None


async def test_rich_menu_uses_tappable_profile_actions() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    workflow = ProfileWorkflow(goals, resources, drafts, messenger)

    assert await workflow.handle_postback(
        "line-1", "action=menu&version=1&target=settings"
    )
    assert [label for label, _ in messenger.quick_replies[-1][2]] == [
        "目標",
        "運動環境",
        "キャンセル",
    ]

    await workflow.handle_postback(
        "line-1", "action=profile&section=environments&operation=menu"
    )
    add_action = messenger.quick_replies[-1][2][0][1]
    await workflow.handle_postback("line-1", add_action)
    place_action = messenger.quick_replies[-1][2][0][1]
    await workflow.handle_postback("line-1", place_action)
    indoor_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "インドアバイク"
    )
    await workflow.handle_postback("line-1", indoor_action)
    back_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "種類選択へ戻る"
    )
    await workflow.handle_postback("line-1", back_action)
    equipment_action = next(
        action for label, action in messenger.quick_replies[-1][2] if label == "器具"
    )
    await workflow.handle_postback("line-1", equipment_action)
    dumbbell_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "ダンベル"
    )
    await workflow.handle_postback("line-1", dumbbell_action)
    complete_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "選択完了"
    )
    await workflow.handle_postback("line-1", complete_action)

    assert [item.display_name for item in await resources.list("line-1")] == [
        "インドアバイク",
        "ダンベル",
    ]
    assert await drafts.get("line-1") is None


async def test_goal_priority_can_be_selected_without_typing_command() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    workflow = ProfileWorkflow(goals, resources, drafts, messenger)
    await workflow.handle_postback(
        "line-1", "action=profile&section=goals&operation=add"
    )
    primary_action = messenger.quick_replies[-1][2][0][1]
    await workflow.handle_postback("line-1", primary_action)
    assert (await drafts.get("line-1")).step == "type"
    assert [label for label, _ in messenger.quick_replies[-1][2]][:5] == [
        "大会",
        "タイム・距離",
        "運動習慣",
        "体力づくり",
        "その他",
    ]
    habit_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "運動習慣"
    )
    await workflow.handle_postback("line-1", habit_action)
    assert (await drafts.get("line-1")).step == "target"
    assert "週3回運動する" in messenger.texts[-1][1]


async def test_environment_multi_select_can_toggle_before_completion() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    workflow = ProfileWorkflow(goals, resources, drafts, messenger)
    await workflow.handle_postback(
        "line-1", "action=profile&section=environments&operation=add"
    )
    await workflow.handle_postback(
        "line-1",
        "action=profile&section=environments&operation=group&value=equipment",
    )
    dumbbell_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "ダンベル"
    )
    await workflow.handle_postback("line-1", dumbbell_action)
    selected_action = next(
        action
        for label, action in messenger.quick_replies[-1][2]
        if label == "✓ ダンベル"
    )
    await workflow.handle_postback("line-1", selected_action)
    with pytest.raises(ProfileCommandError, match="1件以上"):
        await workflow.handle_postback(
            "line-1", "action=profile&section=environments&operation=complete"
        )
    assert await resources.list("line-1") == []


async def test_expired_draft_is_deleted() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    now = datetime(2026, 9, 2, tzinfo=UTC)
    await drafts.save(
        ProfileDraft(
            "line-1",
            "op",
            "environment_add",
            "name",
            expires_at=now - timedelta(seconds=1),
        )
    )
    workflow = ProfileWorkflow(goals, resources, drafts, messenger, clock=lambda: now)
    with pytest.raises(ProfileCommandError, match="有効期限"):
        await workflow.handle_text("line-1", "ダンベル")
    assert await drafts.get("line-1") is None


async def test_retry_uses_stable_operation_id_without_duplicate() -> None:
    goals, resources, drafts = (
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
    )
    messenger = InMemoryConditionPromptSender()
    workflow = ProfileWorkflow(goals, resources, drafts, messenger)
    await workflow.handle_text("line-1", "運動環境追加")
    draft = await drafts.get("line-1")
    await workflow.handle_text("line-1", "ダンベル")
    await resources.save("line-1", (await resources.list("line-1"))[0])
    assert len(await resources.list("line-1")) == 1
    assert (await resources.list("line-1"))[0].id == draft.operation_id


async def test_only_latest_active_profile_is_passed_to_coaching_context() -> None:
    service, goals, resources, _ = await service_fixture()
    await service.handle("line-1", "目標登録 primary race 完走 なし")
    old_goal = (await goals.list("line-1"))[0]
    await goals.deactivate("line-1", old_goal.id)
    await service.handle("line-1", "目標登録 primary fitness 継続 なし")
    await service.handle("line-1", "運動環境登録 自宅トレーニング、ダンベル")
    old_resource = (await resources.list("line-1"))[1]
    await resources.deactivate("line-1", old_resource.id)

    context = CoachingContext(
        goals=await goals.list("line-1"),
        training_resources=await resources.list("line-1"),
    )

    assert [goal.target for goal in context.goals] == ["継続"]
    assert [item.display_name for item in context.training_resources] == [
        "自宅トレーニング"
    ]


async def test_rejects_invalid_goal_date() -> None:
    service, _, _, _ = await service_fixture()

    try:
        await service.handle("line-1", "目標登録 primary marathon 完走 invalid")
    except ProfileCommandError as exc:
        assert "日付" in str(exc)
    else:
        raise AssertionError("ProfileCommandError was not raised")
