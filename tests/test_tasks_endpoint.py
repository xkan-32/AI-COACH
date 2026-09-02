import logging

import pytest
from fastapi.testclient import TestClient

from app.line_menu import MENU_MESSAGES
from app.main import app, runtime
from app.strava import StravaApiError

client = TestClient(app)


@pytest.mark.parametrize("target", sorted(MENU_MESSAGES))
def test_line_event_worker_routes_every_rich_menu_action(target: str) -> None:
    runtime.messenger.texts.clear()
    runtime.messenger.quick_replies.clear()
    runtime.messenger.settings_links.clear()
    runtime.messenger.weekly_plan_links.clear()
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
    elif target == "progress":
        assert runtime.messenger.weekly_plan_links == []
        assert runtime.messenger.texts == [
            ("U-menu", "確認できる週間計画はまだありません。")
        ]
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
