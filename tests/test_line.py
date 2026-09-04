from datetime import UTC, datetime

import httpx
import pytest

from app.domain.models import Activity
from app.line import (
    LineApiError,
    LineConditionPromptSender,
    set_line_reply_token,
)


def _mock_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


async def test_condition_prompt_uses_stable_line_retry_key(monkeypatch) -> None:
    retry_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        retry_keys.append(request.headers["X-Line-Retry-Key"])
        return httpx.Response(200, json={}, request=request)

    set_line_reply_token(None)
    _mock_client(monkeypatch, handler)
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


async def test_reply_token_uses_reply_api_once(monkeypatch) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert "X-Line-Retry-Key" not in request.headers
        return httpx.Response(200, json={}, request=request)

    _mock_client(monkeypatch, handler)
    sender = LineConditionPromptSender("secret")
    set_line_reply_token("reply-1")
    await sender.send_text("line-user", "hello")
    with pytest.raises(LineApiError, match="reply token"):
        await sender.send_text("line-user", "again")
    assert urls == ["https://api.line.me/v2/bot/message/reply"]


async def test_expired_reply_token_never_falls_back_to_push(monkeypatch) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            400, json={"message": "Invalid reply token"}, request=request
        )

    _mock_client(monkeypatch, handler)
    sender = LineConditionPromptSender("secret")
    set_line_reply_token("stale")
    with pytest.raises(LineApiError):
        await sender.send_text("line-user", "hello")
    assert urls == ["https://api.line.me/v2/bot/message/reply"]


async def test_second_webhook_response_never_becomes_push(monkeypatch) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={}, request=request)

    _mock_client(monkeypatch, handler)
    sender = LineConditionPromptSender("secret")
    set_line_reply_token("reply-1")
    await sender.send_text("line-user", "hello")
    with pytest.raises(LineApiError, match="reply token"):
        await sender.send_text("line-user", "again")
    assert urls == ["https://api.line.me/v2/bot/message/reply"]


async def test_push_429_becomes_line_api_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"message": "Too Many Requests"}, request=request
        )

    set_line_reply_token(None)
    _mock_client(monkeypatch, handler)
    sender = LineConditionPromptSender("secret")
    with pytest.raises(LineApiError) as caught:
        await sender.send_text("line-user", "hello")
    assert caught.value.status_code == 429
