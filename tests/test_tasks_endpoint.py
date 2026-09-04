import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.line_menu import CONDITION_HUB_PROMPT, MENU_MESSAGES
from app.main import app, runtime
from app.strava import StravaApiError

client = TestClient(app)


@pytest.mark.parametrize("target", sorted(MENU_MESSAGES))
def test_line_event_worker_routes_every_rich_menu_action(target: str) -> None:
    runtime.messenger.texts.clear()
    runtime.messenger.quick_replies.clear()
    runtime.messenger.settings_links.clear()
    runtime.messenger.weekly_plan_links.clear()
    runtime.manual_activity_drafts.items.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": f"menu-{target}",
            "event": {
                "type": "postback",
                "source": {"userId": "U-menu"},
                "postback": {"data": f"action=menu&version=1&target={target}"},
            },
        },
    )
    assert response.status_code == 200
    if target == "goals":
        assert "目標" in runtime.messenger.texts[0][1]
        assert runtime.messenger.quick_replies == []
    elif target == "settings":
        assert len(runtime.messenger.settings_links) == 1
        assert (
            "/settings/profile/start?token=" in runtime.messenger.settings_links[0][1]
        )
    elif target in {"today_proposal", "training_menu", "progress"}:
        assert len(runtime.messenger.weekly_plan_links) == 1
        assert runtime.messenger.texts == []
    elif target == "manual_activity":
        assert runtime.messenger.quick_replies == []
        assert "Strava連携が必要です" in runtime.messenger.texts[-1][1]
    elif target == "condition":
        assert runtime.messenger.texts == []
        assert runtime.messenger.quick_replies[-1][1] == CONDITION_HUB_PROMPT
        choices = dict(runtime.messenger.quick_replies[-1][2])
        assert choices["体重"] == "action=weight&op=start"
        assert "コンディション" in choices
    else:
        assert runtime.messenger.texts == [("U-menu", MENU_MESSAGES[target])]


def test_activity_task_endpoint_rejects_unlinked_athlete() -> None:
    response = client.post(
        "/tasks/activities/ingest",
        json={
            "object_type": "activity",
            "object_id": 10,
            "aspect_type": "create",
            "owner_id": 20,
            "subscription_id": 30,
            "event_time": 1_700_000_000,
            "updates": {},
        },
    )
    assert response.status_code == 404
    assert "No Strava token" in response.json()["detail"]


def test_activity_task_endpoint_rejects_non_create_event() -> None:
    response = client.post(
        "/tasks/activities/ingest",
        json={
            "object_type": "activity",
            "object_id": 10,
            "aspect_type": "update",
            "owner_id": 20,
            "subscription_id": 30,
            "event_time": 1_700_000_000,
        },
    )
    assert response.status_code == 422


def test_activity_task_logs_safe_strava_error_metadata(monkeypatch, caplog) -> None:
    async def fail_ingestion(self, activity_id, athlete_id):
        raise StravaApiError(
            "Strava activity fetch failed",
            status_code=404,
            error_kind="http_status",
        )

    monkeypatch.setattr("app.main.ActivityIngestionService.ingest", fail_ingestion)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        response = client.post(
            "/tasks/activities/ingest",
            json={
                "object_type": "activity",
                "object_id": 10,
                "aspect_type": "create",
                "owner_id": 20,
                "subscription_id": 30,
                "event_time": 1_700_000_000,
            },
        )

    assert response.status_code == 502
    assert "activity_id=10" in caplog.text
    assert "athlete_id=20" in caplog.text
    assert "error_kind=http_status" in caplog.text
    assert "strava_status_code=404" in caplog.text
    assert "access_token" not in caplog.text


def test_proposal_decision_sends_completion_message(monkeypatch) -> None:
    from app.main import runtime

    async def fake_decide(self, task):
        return "approved"

    monkeypatch.setattr("app.main.ApprovalService.decide", fake_decide)
    runtime.messenger.texts.clear()

    response = client.post(
        "/tasks/proposals/decide",
        json={
            "proposal_id": "proposal-1",
            "line_user_id": "line-user",
            "decision": "approve",
        },
    )

    assert response.status_code == 200
    assert runtime.messenger.texts[-1] == (
        "line-user",
        "Stravaへの投稿が完了しました。",
    )


def test_proposal_decision_reports_missing_strava_link(monkeypatch) -> None:
    from app.main import runtime

    async def fake_decide(self, task):
        return "missing_strava_link"

    monkeypatch.setattr("app.main.ApprovalService.decide", fake_decide)
    runtime.messenger.texts.clear()

    response = client.post(
        "/tasks/proposals/decide",
        json={
            "proposal_id": "proposal-1",
            "line_user_id": "line-user",
            "decision": "approve",
        },
    )

    assert response.status_code == 200
    assert runtime.messenger.texts[-1] == (
        "line-user",
        (
            "Strava連携がないため投稿できません。"
            "連携後に最新のメッセージから操作してください。"
        ),
    )


def test_line_event_worker_starts_weight_recording() -> None:
    from app.main import runtime

    runtime.messenger.quick_replies.clear()
    runtime.weight_drafts.items.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "weight-start",
            "event": {
                "type": "message",
                "source": {"userId": "U-weight"},
                "message": {"type": "text", "text": "体重"},
            },
        },
    )
    assert response.status_code == 200
    assert runtime.messenger.quick_replies
    assert "今日の体重をkgで送ってください" in runtime.messenger.quick_replies[-1][1]


def test_line_event_worker_records_bare_weight_number() -> None:
    from app.main import runtime

    runtime.messenger.texts.clear()
    runtime.weight_drafts.items.clear()
    runtime.weight_logs.logs.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "weight-bare-number",
            "event": {
                "type": "message",
                "source": {"userId": "U-weight-number"},
                "message": {"type": "text", "text": "70.2"},
            },
        },
    )
    assert response.status_code == 200
    saved = next(iter(runtime.weight_logs.logs.values()))
    assert saved.user_id == "U-weight-number"
    assert saved.kilograms == 70.2
    assert runtime.messenger.texts[-1][1].startswith(
        f"{saved.measured_on.isoformat()} 70.2kgを記録しました。"
    )


def test_line_event_worker_keeps_condition_severity_ahead_of_weight() -> None:
    from app.condition import ConditionDraft
    from app.domain.models import ConditionLevel

    runtime.messenger.texts.clear()
    runtime.weight_logs.logs.clear()
    runtime.condition_drafts.items["U-condition-weight"] = ConditionDraft(
        activity_id="activity-1",
        athlete_id="athlete-1",
        line_user_id="U-condition-weight",
        level=ConditionLevel.DISCOMFORT,
        step="severity",
        body_part="右膝",
    )
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "condition-severity-not-weight",
            "event": {
                "type": "message",
                "source": {"userId": "U-condition-weight"},
                "message": {"type": "text", "text": "8"},
            },
        },
    )
    assert response.status_code == 200
    assert runtime.weight_logs.logs == {}
    assert "悪化しましたか" in runtime.messenger.texts[-1][1]


def test_missing_workout_scan_prompts_and_is_idempotent(monkeypatch) -> None:
    class FakeReconciliationService:
        async def missing_candidates(self, *args, **kwargs):
            return [SimpleNamespace(id="reconciliation-missing-1")]

    monkeypatch.setattr(
        "app.main._reconciliation_service", lambda: FakeReconciliationService()
    )
    runtime.messenger.quick_replies.clear()
    payload = {
        "user_id": "U-missing",
        "line_user_id": "U-missing",
        "local_date": "2026-09-08",
        "provider_sync_confirmed": True,
        "operation_id": "missing-scan-endpoint-1",
    }

    response = client.post("/tasks/plans/reconcile-missing", json=payload)
    duplicate = client.post("/tasks/plans/reconcile-missing", json=payload)

    assert response.status_code == 202
    assert response.json()["candidate_count"] == 1
    assert duplicate.json() == {"status": "duplicate"}
    prompt = runtime.messenger.quick_replies[-1]
    assert prompt[0] == "U-missing"
    assert dict(prompt[2]).keys() == {"未実施", "同期待ち", "予定変更"}


def test_readiness_task_rejects_owner_mismatch() -> None:
    response = client.post(
        "/tasks/plans/evaluate-readiness",
        json={
            "user_id": "U-owner",
            "line_user_id": "U-other",
            "activity_id": "activity-readiness-1",
            "operation_id": "readiness-task-1",
        },
    )

    assert response.status_code == 403


def test_missing_workout_postback_records_user_decision(monkeypatch) -> None:
    decisions = []

    class FakeReconciliationService:
        async def resolve_missing(self, **values):
            decisions.append(values)

    monkeypatch.setattr(
        "app.main._reconciliation_service", lambda: FakeReconciliationService()
    )
    runtime.messenger.texts.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "missing-decision-endpoint-1",
            "event": {
                "type": "postback",
                "source": {"userId": "U-missing"},
                "postback": {
                    "data": (
                        "action=reconciliation_missing&reconciliation_id="
                        "reconciliation-missing-1&decision=schedule_changed"
                    )
                },
            },
        },
    )

    assert response.status_code == 200
    assert decisions == [
        {
            "user_id": "U-missing",
            "expected_reconciliation_id": "reconciliation-missing-1",
            "decision": "schedule_changed",
        }
    ]
    assert runtime.messenger.texts[-1] == (
        "U-missing",
        "予定変更として記録しました。",
    )


def test_line_event_worker_starts_weight_from_condition_menu() -> None:
    runtime.messenger.quick_replies.clear()
    runtime.weight_drafts.items.clear()
    menu = client.post(
        "/tasks/line/events",
        json={
            "event_key": "condition-hub",
            "event": {
                "type": "postback",
                "source": {"userId": "U-condition-hub"},
                "postback": {"data": "action=menu&version=1&target=condition"},
            },
        },
    )
    assert menu.status_code == 200
    choices = dict(runtime.messenger.quick_replies[-1][2])
    start = client.post(
        "/tasks/line/events",
        json={
            "event_key": "condition-hub-weight",
            "event": {
                "type": "postback",
                "source": {"userId": "U-condition-hub"},
                "postback": {"data": choices["体重"]},
            },
        },
    )
    assert start.status_code == 200
    assert "今日の体重をkgで送ってください" in runtime.messenger.quick_replies[-1][1]


def test_line_event_worker_starts_daily_condition_from_condition_menu() -> None:
    runtime.messenger.quick_replies.clear()
    menu = client.post(
        "/tasks/line/events",
        json={
            "event_key": "condition-hub-daily",
            "event": {
                "type": "postback",
                "source": {"userId": "U-condition-daily"},
                "postback": {"data": "action=menu&version=1&target=condition"},
            },
        },
    )
    assert menu.status_code == 200
    choices = dict(runtime.messenger.quick_replies[-1][2])
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "condition-hub-daily-start",
            "event": {
                "type": "postback",
                "source": {"userId": "U-condition-daily"},
                "postback": {"data": choices["コンディション"]},
            },
        },
    )
    assert response.status_code == 200
    prompt = runtime.messenger.quick_replies[-1]
    assert prompt[0] == "U-condition-daily"
    assert "未入力の場合は、計画上は問題なし" in prompt[1]
    assert set(dict(prompt[2])) == {"問題なし", "疲労", "違和感", "痛み"}


def test_line_event_worker_completes_when_line_send_fails(monkeypatch) -> None:
    from app.line import LineApiError
    from app.main import runtime

    async def fail_send(*_args, **_kwargs):
        raise LineApiError("LINE message failed", status_code=429)

    monkeypatch.setattr(runtime.messenger, "send_quick_reply", fail_send)
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "line-429",
            "event": {
                "type": "postback",
                "source": {"userId": "U-line-429"},
                "replyToken": "reply-429",
                "postback": {"data": "action=menu&version=1&target=condition"},
            },
        },
    )
    assert response.status_code == 200
