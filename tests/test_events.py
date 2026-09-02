from fastapi.testclient import TestClient

from app.domain.events import StravaWebhookEvent
from app.main import app, runtime
from app.state import InMemoryEventStore


def activity_event() -> StravaWebhookEvent:
    return StravaWebhookEvent(
        object_type="activity",
        object_id=100,
        aspect_type="create",
        owner_id=200,
        subscription_id=300,
        event_time=1_700_000_000,
    )


def test_event_key_is_stable() -> None:
    assert activity_event().event_key == "300:activity:100:create"
    assert activity_event().is_new_activity


def test_activity_create_retry_uses_same_key_when_event_time_changes() -> None:
    first = activity_event()
    retry = first.model_copy(update={"event_time": first.event_time + 120})

    assert retry.event_key == first.event_key


def test_update_events_keep_event_time_in_key() -> None:
    first = activity_event().model_copy(update={"aspect_type": "update"})
    later = first.model_copy(update={"event_time": first.event_time + 120})

    assert first.event_key != later.event_key


async def test_event_reservation_is_idempotent() -> None:
    store = InMemoryEventStore()
    assert await store.reserve("strava", "event-1")
    assert not await store.reserve("strava", "event-1")


def test_strava_webhook_returns_200_and_enqueues_activity_once() -> None:
    client = TestClient(app)
    before = len(runtime.tasks.events)
    payload = {
        "object_type": "activity",
        "object_id": 9_000_001,
        "aspect_type": "create",
        "owner_id": 9_000_002,
        "subscription_id": 9_000_003,
        "event_time": 1_700_000_000,
    }

    accepted = client.post("/webhooks/strava", json=payload)
    duplicate = client.post(
        "/webhooks/strava",
        json={**payload, "event_time": payload["event_time"] + 120},
    )
    another = client.post(
        "/webhooks/strava",
        json={**payload, "object_id": payload["object_id"] + 1},
    )

    assert accepted.status_code == 200
    assert accepted.json() == {"status": "accepted"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    assert another.status_code == 200
    assert len(runtime.tasks.events) == before + 2
