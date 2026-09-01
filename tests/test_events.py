from app.domain.events import StravaWebhookEvent
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
    assert activity_event().event_key == "300:activity:100:create:1700000000"
    assert activity_event().is_new_activity


async def test_event_reservation_is_idempotent() -> None:
    store = InMemoryEventStore()
    assert await store.reserve("strava", "event-1")
    assert not await store.reserve("strava", "event-1")
