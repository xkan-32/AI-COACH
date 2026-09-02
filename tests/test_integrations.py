import httpx
import pytest

from app.config import Settings
from app.domain.events import StravaWebhookEvent
from app.oauth_service import StravaOAuthService
from app.runtime import build_runtime
from app.state import InMemoryOAuthSessionStore, InMemoryStravaTokenStore
from app.strava import StravaOAuthClient
from app.tasks import (
    CloudTasksActivityPublisher,
    CloudTasksLineEventPublisher,
    InMemoryActivityTaskPublisher,
)


def test_production_requires_token_encryption_key() -> None:
    settings = Settings(
        app_env="production",
        gcp_project_id="project",
        cloud_tasks_queue_path="projects/p/locations/r/queues/q",
        worker_url="https://example.test",
        task_service_account_email="tasks@example.test",
        line_channel_access_token="line-secret",
        token_encryption_key="",
    )

    with pytest.raises(RuntimeError, match="token_encryption_key"):
        build_runtime(settings)


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


async def test_line_event_cloud_task_targets_worker() -> None:
    calls = []

    class FakeClient:
        async def create_task(self, **kwargs) -> None:
            calls.append(kwargs)

    publisher = CloudTasksLineEventPublisher(
        FakeClient,
        "projects/project/locations/region/queues/queue",
        "https://coach.example",
        "tasks@example.iam.gserviceaccount.com",
    )

    await publisher.publish(
        "line-event-1",
        {"type": "message", "message": {"type": "text", "text": "目標確認"}},
    )

    request = calls[0]["task"].http_request
    assert request.url == "https://coach.example/tasks/line/events"
    assert b"line-event-1" in request.body
