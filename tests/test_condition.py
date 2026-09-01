from datetime import UTC, datetime

import pytest

from app.condition import (
    ActivityContext,
    ConditionWorkflow,
    InMemoryActivityContextStore,
    InMemoryConditionDraftStore,
    InMemoryConditionReportStore,
    InvalidConditionAction,
)
from app.line import InMemoryConditionPromptSender


async def workflow_fixture():
    contexts = InMemoryActivityContextStore()
    drafts = InMemoryConditionDraftStore()
    reports = InMemoryConditionReportStore()
    messenger = InMemoryConditionPromptSender()
    await contexts.save(ActivityContext("activity-1", "athlete-1", "line-1"))
    workflow = ConditionWorkflow(
        contexts,
        drafts,
        reports,
        messenger,
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )
    return workflow, drafts, reports, messenger


async def test_good_condition_is_saved_immediately() -> None:
    workflow, _, reports, messenger = await workflow_fixture()
    result = await workflow.handle_postback(
        "line-1", "action=condition&activity_id=activity-1&level=good"
    )
    assert result == "completed"
    assert reports.items[0].level.value == "good"
    assert messenger.texts[-1][0] == "line-1"


async def test_pain_follow_up_completes_report() -> None:
    workflow, drafts, reports, _ = await workflow_fixture()
    assert (
        await workflow.handle_postback(
            "line-1", "action=condition&activity_id=activity-1&level=pain"
        )
        == "follow_up"
    )
    assert await workflow.handle_text("line-1", "右ふくらはぎ") == "follow_up"
    assert await workflow.handle_text("line-1", "4") == "follow_up"
    assert await workflow.handle_text("line-1", "はい") == "completed"
    report = reports.items[0]
    assert report.body_part == "右ふくらはぎ"
    assert report.severity == 4
    assert report.worsened_during_activity is True
    assert await drafts.get("line-1") is None


async def test_condition_action_rejects_different_line_user() -> None:
    workflow, _, _, _ = await workflow_fixture()
    with pytest.raises(InvalidConditionAction, match="does not belong"):
        await workflow.handle_postback(
            "attacker", "action=condition&activity_id=activity-1&level=good"
        )


async def test_severity_must_be_between_one_and_ten() -> None:
    workflow, _, _, _ = await workflow_fixture()
    await workflow.handle_postback(
        "line-1", "action=condition&activity_id=activity-1&level=discomfort"
    )
    await workflow.handle_text("line-1", "左膝")
    with pytest.raises(InvalidConditionAction, match="1〜10"):
        await workflow.handle_text("line-1", "11")
