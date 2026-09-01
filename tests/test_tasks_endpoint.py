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
