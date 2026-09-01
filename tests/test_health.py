from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    for path in ("/health", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_strava_verification(monkeypatch) -> None:
    monkeypatch.setenv("STRAVA_VERIFY_TOKEN", "secret")
    from app.config import get_settings

    get_settings.cache_clear()
    response = client.get(
        "/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "challenge-value",
            "hub.verify_token": "secret",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "challenge-value"}
    get_settings.cache_clear()
