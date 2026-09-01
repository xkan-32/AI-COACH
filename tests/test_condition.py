from datetime import UTC, datetime

import pytest

from app.condition import (
    ActivityContext,
    ConditionDraft,
    ConditionWorkflow,
    FirestoreConditionDraftStore,
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


class FakeSnapshot:
    exists = True

    def to_dict(self) -> dict[str, object]:
        return {
            "activity_id": "activity-1",
            "athlete_id": "athlete-1",
            "line_user_id": "line-1",
            "level": "pain",
            "step": "severity",
            "body_part": "右ふくらはぎ",
            "severity": None,
        }


class FakeDocument:
    async def get(self) -> FakeSnapshot:
        return FakeSnapshot()


class FakeCollection:
    def document(self, _document_id: str) -> FakeDocument:
        return FakeDocument()


class FakeFirestoreClient:
    def collection(self, _name: str) -> FakeCollection:
        return FakeCollection()


async def test_firestore_draft_restores_condition_level_enum() -> None:
    store = FirestoreConditionDraftStore(FakeFirestoreClient())
    draft = await store.get("line-1")

    assert isinstance(draft, ConditionDraft)
    assert draft.level.value == "pain"


async def test_expired_condition_draft_rejects_late_reply() -> None:
    workflow, drafts, _, _ = await workflow_fixture()
    await workflow.handle_postback(
        "line-1", "action=condition&activity_id=activity-1&level=discomfort"
    )
    draft = await drafts.get("line-1")
    await drafts.save(
        ConditionDraft(
            activity_id=draft.activity_id,
            athlete_id=draft.athlete_id,
            line_user_id=draft.line_user_id,
            level=draft.level,
            expires_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
    )

    with pytest.raises(InvalidConditionAction, match="有効期限"):
        await workflow.handle_text("line-1", "右膝")

    assert await drafts.get("line-1") is None


async def test_condition_acknowledgement_precedes_proposal_message() -> None:
    contexts = InMemoryActivityContextStore()
    drafts = InMemoryConditionDraftStore()
    reports = InMemoryConditionReportStore()
    messenger = InMemoryConditionPromptSender()
    await contexts.save(ActivityContext("activity-1", "athlete-1", "line-1"))

    async def send_proposal(_report) -> None:
        await messenger.send_text("line-1", "AI提案")

    workflow = ConditionWorkflow(
        contexts,
        drafts,
        reports,
        messenger,
        on_completed=send_proposal,
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )

    await workflow.handle_postback(
        "line-1", "action=condition&activity_id=activity-1&level=good"
    )

    assert [text for _, text in messenger.texts] == [
        "体調を記録しました。ありがとうございます。",
        "AI提案",
    ]
