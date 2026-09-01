import httpx

from app.domain.events import StravaWebhookEvent
from app.oauth_service import StravaOAuthService
from app.state import InMemoryOAuthSessionStore, InMemoryStravaTokenStore
from app.strava import StravaOAuthClient
from app.tasks import CloudTasksActivityPublisher, InMemoryActivityTaskPublisher


async def test_oauth_exchange_is_saved_without_returning_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_at": 2_000_000_000,
                "expires_in": 21600,
                "refresh_token": "refresh-secret",
                "access_token": "access-secret",
                "athlete": {"id": 42},
            },
        )

    class FakeClient(StravaOAuthClient):
        async def exchange_code(self, code: str):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                response = await client.post("https://example.test/oauth/token")
                from app.strava import StravaTokenResponse

                return StravaTokenResponse.model_validate(response.json())

    sessions = InMemoryOAuthSessionStore()
    tokens = InMemoryStravaTokenStore()
    await sessions.create("nonce", "line-user", 2_000_000_000)
    service = StravaOAuthService(FakeClient("id", "secret"), sessions, tokens)

    athlete_id = await service.complete("authorization-code", "nonce")

    assert athlete_id == "42"
    assert tokens.tokens["42"].line_user_id == "line-user"
    assert tokens.tokens["42"].refresh_token == "refresh-secret"
    assert await sessions.consume("nonce") is None


async def test_activity_event_is_published() -> None:
    publisher = InMemoryActivityTaskPublisher()
    event = StravaWebhookEvent(
        object_type="activity",
        object_id=10,
        aspect_type="create",
        owner_id=20,
        subscription_id=30,
        event_time=1_700_000_000,
    )
    await publisher.publish(event)
    assert publisher.events == [event]


async def test_cloud_task_client_is_created_inside_publish() -> None:
    calls = []

    class FakeClient:
        async def create_task(self, **kwargs) -> None:
            calls.append(kwargs)

    publisher = CloudTasksActivityPublisher(
        FakeClient,
        "projects/project/locations/region/queues/queue",
        "https://coach.example",
        "tasks@example.iam.gserviceaccount.com",
    )
    event = StravaWebhookEvent(
        object_type="activity",
        object_id=10,
        aspect_type="create",
        owner_id=20,
        subscription_id=30,
        event_time=1_700_000_000,
    )

    await publisher.publish(event)

    assert len(calls) == 1
    assert calls[0]["parent"].endswith("/queues/queue")
