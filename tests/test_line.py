from datetime import UTC, datetime

import httpx

from app.domain.models import Activity
from app.line import LineConditionPromptSender


async def test_condition_prompt_uses_stable_line_retry_key(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient
    retry_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        retry_keys.append(request.headers["X-Line-Retry-Key"])
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    sender = LineConditionPromptSender("secret")
    activity = Activity(
        id="activity-1",
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        duration_seconds=1800,
        distance_meters=5000,
    )

    await sender.send("line-user", activity)
    await sender.send("line-user", activity)

    assert retry_keys[0] == retry_keys[1]
    assert len(retry_keys[0]) == 36
