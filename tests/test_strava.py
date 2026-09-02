import httpx
import pytest

from app.strava import StravaApiError, StravaClient


async def test_activity_fetch_exposes_safe_http_error_metadata(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"message": "private upstream response"},
            request=request,
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(StravaApiError) as raised:
        await StravaClient("client-id", "client-secret").get_activity(
            "activity-1", "access-secret"
        )

    assert raised.value.status_code == 404
    assert raised.value.error_kind == "http_status"
    assert "private upstream response" not in str(raised.value)
    assert "access-secret" not in str(raised.value)


async def test_activity_fetch_classifies_invalid_response(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(StravaApiError) as raised:
        await StravaClient("client-id", "client-secret").get_activity(
            "activity-1", "access-secret"
        )

    assert raised.value.status_code is None
    assert raised.value.error_kind == "invalid_response"
