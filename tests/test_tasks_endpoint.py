from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
