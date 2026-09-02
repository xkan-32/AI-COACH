from datetime import UTC, date, datetime, timedelta

import pytest

from app.line import InMemoryConditionPromptSender
from app.planning import InMemoryTrainingSettingsStore, create_user_training_profile
from app.weight import (
    FirestoreWeightDraftStore,
    InMemoryWeightDraftStore,
    InMemoryWeightLogStore,
    InMemoryWeightTargetStore,
    InvalidWeightAction,
    WeightDraft,
    WeightLog,
    WeightWorkflow,
    current_logs_by_day,
    looks_like_kilograms,
    parse_kilograms,
    weight_log_id,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


async def make_workflow(**overrides):
    drafts = overrides.pop("drafts", InMemoryWeightDraftStore())
    logs = overrides.pop("logs", InMemoryWeightLogStore())
    targets = overrides.pop("targets", InMemoryWeightTargetStore())
    messenger = InMemoryConditionPromptSender()
    workflow = WeightWorkflow(
        drafts,
        logs,
        targets,
        messenger,
        clock=overrides.pop("clock", lambda: NOW),
        **overrides,
    )
    return workflow, drafts, logs, targets, messenger


def choice(messenger: InMemoryConditionPromptSender, label: str) -> str:
    return dict(messenger.quick_replies[-1][2])[label]


def test_parse_kilograms_accepts_units_and_fullwidth() -> None:
    assert parse_kilograms("70.2") == 70.2
    assert parse_kilograms("70.2kg") == 70.2
    assert parse_kilograms("７０．２") == 70.2
    assert parse_kilograms("70.24") == 70.2


def test_looks_like_kilograms_accepts_numeric_text_only() -> None:
    assert looks_like_kilograms("70.2")
    assert looks_like_kilograms("70.2kg")
    assert not looks_like_kilograms("こんにちは")
    assert not looks_like_kilograms("2026-09-02")


def test_parse_kilograms_rejects_out_of_range() -> None:
    with pytest.raises(InvalidWeightAction, match="25.0〜250.0"):
        parse_kilograms("20")
    with pytest.raises(InvalidWeightAction, match="25.0〜250.0"):
        parse_kilograms("abc")


async def test_weight_command_records_today_and_hides_kg_from_logs(caplog) -> None:
    workflow, drafts, logs, _, messenger = await make_workflow()
    with caplog.at_level("INFO", logger="app.weight"):
        assert await workflow.handle_text("line-1", "体重")
        assert "今日の体重をkgで送ってください" in messenger.quick_replies[-1][1]
        await workflow.handle_text("line-1", "70.2")
    saved = next(iter(logs.logs.values()))
    assert saved.measured_on == date(2026, 9, 2)
    assert saved.kilograms == 70.2
    assert saved.user_id == "line-1"
    assert await drafts.get("line-1") is None
    assert messenger.texts[-1][1].startswith("2026-09-02 70.2kgを記録しました。")
    assert "70.2" not in caplog.text
    assert "weight_log_saved" in caplog.text
    assert "measured_on=2026-09-02" in caplog.text
    assert "correction=False" in caplog.text


async def test_inline_command_records_immediately() -> None:
    workflow, _, logs, _, messenger = await make_workflow()
    assert await workflow.handle_text("line-1", "体重 68.5")
    saved = next(iter(logs.logs.values()))
    assert saved.kilograms == 68.5
    assert saved.measured_on == date(2026, 9, 2)
    assert messenger.texts[-1][1].startswith("2026-09-02 68.5kgを記録しました。")
    assert messenger.quick_replies == []


async def test_bare_number_records_today_without_start_command() -> None:
    workflow, drafts, logs, _, messenger = await make_workflow()
    assert await workflow.handle_text("line-1", "70.2")
    saved = next(iter(logs.logs.values()))
    assert saved.kilograms == 70.2
    assert saved.measured_on == date(2026, 9, 2)
    assert await drafts.get("line-1") is None
    assert messenger.texts[-1][1].startswith("2026-09-02 70.2kgを記録しました。")


async def test_out_of_range_bare_number_is_rejected() -> None:
    workflow, _, logs, _, _ = await make_workflow()
    with pytest.raises(InvalidWeightAction, match="25.0〜250.0"):
        await workflow.handle_text("line-1", "20")
    assert logs.logs == {}


async def test_unrelated_text_is_ignored() -> None:
    workflow, _, logs, _, messenger = await make_workflow()
    assert await workflow.handle_text("line-1", "こんにちは") is False
    assert logs.logs == {}
    assert messenger.texts == []


async def test_same_day_correction_keeps_history_and_uses_latest() -> None:
    workflow, _, logs, _, messenger = await make_workflow()
    await workflow.handle_text("line-1", "70.2")
    first = next(iter(logs.logs.values()))
    await workflow.handle_text("line-1", "70.4")
    current = current_logs_by_day(list(logs.logs.values()))[date(2026, 9, 2)]
    assert current.kilograms == 70.4
    assert current.supersedes_log_id == first.id
    assert first.id in logs.logs
    assert messenger.texts[-1][1].startswith("2026-09-02 70.4kgを訂正しました。")


async def test_same_operation_id_does_not_duplicate_a_log() -> None:
    workflow, _, logs, _, messenger = await make_workflow()
    await workflow.handle_text("line-1", "70.2", operation_id="evt-1")
    await workflow.handle_text("line-1", "70.2", operation_id="evt-1")
    assert len(logs.logs) == 1
    assert messenger.texts[-1][1].startswith("2026-09-02 70.2kgを記録しました。")


async def test_averages_and_goal_delta_use_current_daily_values() -> None:
    logs = InMemoryWeightLogStore()
    targets = InMemoryWeightTargetStore()
    await targets.save("line-1", 68.0)
    for offset, kg in ((0, 70.0), (1, 71.0), (2, 72.0), (10, 80.0)):
        day = date(2026, 9, 2) - timedelta(days=offset)
        log = WeightLog(
            id=f"log-{offset}",
            user_id="line-1",
            measured_on=day,
            kilograms=kg,
            recorded_at=NOW - timedelta(days=offset),
            operation_id=f"op-{offset}",
        )
        await logs.save(log)
    superseded = WeightLog(
        id="old-today",
        user_id="line-1",
        measured_on=date(2026, 9, 2),
        kilograms=99.0,
        recorded_at=NOW - timedelta(hours=1),
        operation_id="old",
    )
    await logs.save(superseded)
    workflow, _, _, _, messenger = await make_workflow(logs=logs, targets=targets)
    await workflow.handle_text("line-1", "70.0")
    summary = messenger.texts[-1][1]
    assert "7日平均: 71.0kg（3日分）" in summary
    assert "30日平均: 73.3kg（4日分）" in summary
    assert "目標 68.0kg まで 2.0kg減量が目安です。" in summary


async def test_target_command_does_not_create_a_log(caplog) -> None:
    workflow, _, logs, targets, messenger = await make_workflow()
    with caplog.at_level("INFO", logger="app.weight"):
        assert await workflow.handle_text("line-1", "目標体重 68")
    assert logs.logs == {}
    assert await targets.get("line-1") == 68.0
    assert messenger.texts[-1][1] == "目標体重を68.0kgに設定しました。"
    assert "68" not in caplog.text
    assert "weight_target_saved user_id=line-1" in caplog.text


async def test_save_is_idempotent_for_the_same_operation() -> None:
    workflow, drafts, logs, _, _ = await make_workflow()
    draft = WeightDraft(
        line_user_id="line-1",
        user_id="line-1",
        operation_id="op-1",
        step="confirm",
        measured_on=date(2026, 9, 2),
        kilograms=70.2,
        expires_at=NOW + timedelta(hours=1),
    )
    await drafts.save(draft)
    save_data = "action=weight&op=save&oid=op-1"
    await workflow.handle_postback("line-1", save_data)
    saved = next(iter(logs.logs.values()))
    assert await drafts.get("line-1") is None
    assert await workflow.handle_postback("line-1", save_data)
    assert list(logs.logs.values()) == [saved]


async def test_expired_draft_rejects_late_reply() -> None:
    workflow, drafts, _, _, messenger = await make_workflow()
    await workflow.handle_text("line-1", "体重")
    draft = await drafts.get("line-1")
    draft.expires_at = NOW - timedelta(seconds=1)
    await drafts.save(draft)
    with pytest.raises(InvalidWeightAction, match="有効期限"):
        await workflow.handle_postback("line-1", choice(messenger, "昨日"))


async def test_start_postback_opens_today_entry() -> None:
    workflow, drafts, _, _, messenger = await make_workflow()
    assert await workflow.handle_postback("line-1", "action=weight&op=start")
    draft = await drafts.get("line-1")
    assert draft is not None
    assert draft.step == "kg"
    assert draft.measured_on == date(2026, 9, 2)
    assert "今日の体重をkgで送ってください" in messenger.quick_replies[-1][1]


async def test_yesterday_choice_then_number_records_that_day() -> None:
    workflow, _, logs, _, messenger = await make_workflow()
    await workflow.handle_text("line-1", "体重")
    await workflow.handle_postback("line-1", choice(messenger, "昨日"))
    await workflow.handle_text("line-1", "69.8")
    saved = next(iter(logs.logs.values()))
    assert saved.measured_on == date(2026, 9, 1)
    assert saved.kilograms == 69.8


async def test_target_can_be_cleared() -> None:
    targets = InMemoryWeightTargetStore()
    await targets.save("line-1", 68.0)
    await targets.save("line-1", None)
    assert await targets.get("line-1") is None


async def test_future_date_is_rejected() -> None:
    workflow, _, _, _, messenger = await make_workflow()
    await workflow.handle_text("line-1", "体重")
    await workflow.handle_postback("line-1", choice(messenger, "日付を入力"))
    with pytest.raises(InvalidWeightAction, match="未来"):
        await workflow.handle_text("line-1", "2026-09-03")


async def test_timezone_changes_local_today() -> None:
    settings = InMemoryTrainingSettingsStore()
    settings.profiles["line-1"] = create_user_training_profile(
        "line-1", "America/Los_Angeles", 1, "profile-1"
    )
    local_now = datetime(2026, 9, 2, 4, tzinfo=UTC)
    workflow, _, logs, _, _ = await make_workflow(
        settings=settings, clock=lambda: local_now
    )
    await workflow.handle_text("line-1", "体重 70.0")
    saved = next(iter(logs.logs.values()))
    assert saved.measured_on == date(2026, 9, 1)


async def test_weight_log_id_is_stable() -> None:
    assert weight_log_id("line-1", "op-1") == weight_log_id("line-1", "op-1")


class FakeSnapshot:
    exists = True

    def to_dict(self) -> dict[str, object]:
        return {
            "line_user_id": "line-1",
            "user_id": "line-1",
            "operation_id": "op-1",
            "step": "kg",
            "timezone": "Asia/Tokyo",
            "measured_on": date(2026, 9, 2),
            "expires_at": NOW + timedelta(hours=1),
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


async def test_firestore_draft_round_trip_restores_fields() -> None:
    store = FirestoreWeightDraftStore(FakeFirestoreClient())
    draft = await store.get("line-1")
    assert draft is not None
    assert isinstance(draft, WeightDraft)
    assert draft.measured_on == date(2026, 9, 2)
    assert draft.step == "kg"
