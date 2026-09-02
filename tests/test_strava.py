import httpx
import pytest

from app.strava import ACTIVITY_STREAM_KEYS, StravaApiError, StravaClient


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


async def test_activity_laps_and_streams_are_normalized_without_gps(
    monkeypatch,
) -> None:
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/laps"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "Lap 1",
                        "elapsed_time": 310,
                        "moving_time": 300,
                        "distance": 1000,
                        "total_elevation_gain": 20,
                        "average_speed": 3.33,
                        "average_heartrate": 145,
                        "average_cadence": 83,
                    }
                ],
                request=request,
            )
        assert "latlng" not in request.url.params["keys"]
        assert tuple(request.url.params["keys"].split(",")) == ACTIVITY_STREAM_KEYS
        return httpx.Response(
            200,
            json={
                "time": {"data": [0, 10]},
                "distance": {"data": [0.0, 30.0]},
                "altitude": {"data": [100.0, 101.5]},
                "heartrate": {"data": [130, 135]},
                "latlng": {"data": [[35.0, 139.0], [35.1, 139.1]]},
            },
            request=request,
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    client = StravaClient("client-id", "client-secret")

    laps = await client.get_activity_laps("activity-1", "athlete-1", "Run", "secret")
    points = await client.get_activity_streams("activity-1", "athlete-1", "secret")

    assert laps[0].distance_meters == 1000
    assert laps[0].average_heartrate_bpm == 145
    assert laps[0].average_cadence_per_minute == 166
    assert points[1].altitude_meters == 101.5
    assert points[1].heartrate_bpm == 135
    assert "lat" not in str(points)
    assert "139" not in str(points)


async def test_stream_fetch_classifies_rate_limit(monkeypatch) -> None:
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limit"}, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(StravaApiError) as raised:
        await StravaClient("client-id", "client-secret").get_activity_streams(
            "activity-1", "athlete-1", "secret"
        )

    assert raised.value.status_code == 429
    assert raised.value.error_kind == "http_status"
