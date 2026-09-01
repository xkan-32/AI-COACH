import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, runtime

client = TestClient(app)


def test_line_strava_command_sends_authorization_url(monkeypatch) -> None:
    monkeypatch.setenv("STRAVA_CLIENT_ID", "12345")
    monkeypatch.setenv("STRAVA_REDIRECT_URI", "https://coach.example/callback")
    monkeypatch.setenv("OAUTH_STATE_SIGNING_KEY", "test-signing-key-long-enough")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "line-secret")
    get_settings.cache_clear()
    runtime.messenger.texts.clear()
    runtime.line_tasks.items.clear()
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "source": {"userId": "U123"},
                    "message": {"type": "text", "text": "Strava連携"},
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    signature = base64.b64encode(
        hmac.new(b"line-secret", body, hashlib.sha256).digest()
    ).decode()

    response = client.post(
        "/webhooks/line", content=body, headers={"x-line-signature": signature}
    )

    assert response.status_code == 200
    assert len(runtime.line_tasks.items) == 1
    event_key, event = runtime.line_tasks.items[0]
    task_response = client.post(
        "/tasks/line/events", json={"event_key": event_key, "event": event}
    )
    assert task_response.status_code == 200
    assert len(runtime.messenger.texts) == 1
    line_user_id, message = runtime.messenger.texts[0]
    assert line_user_id == "U123"
    authorization_url = message.rsplit("\n", 1)[1]
    parsed = urlparse(authorization_url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.strava.com"
    assert query["client_id"] == ["12345"]
    assert query["redirect_uri"] == ["https://coach.example/callback"]
    assert query["scope"] == ["read,activity:read_all,activity:write"]
    assert query["state"][0]
    get_settings.cache_clear()


def test_line_webhook_event_is_enqueued_once(monkeypatch) -> None:
    import uuid

    monkeypatch.setenv("LINE_CHANNEL_SECRET", "line-secret")
    get_settings.cache_clear()
    runtime.line_tasks.items.clear()
    event_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "events": [
                {
                    "webhookEventId": event_id,
                    "type": "message",
                    "source": {"userId": "U123"},
                    "message": {"type": "text", "text": "目標確認"},
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    signature = base64.b64encode(
        hmac.new(b"line-secret", body, hashlib.sha256).digest()
    ).decode()

    first = client.post(
        "/webhooks/line", content=body, headers={"x-line-signature": signature}
    )
    second = client.post(
        "/webhooks/line", content=body, headers={"x-line-signature": signature}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert [key for key, _ in runtime.line_tasks.items].count(event_id) == 1
    get_settings.cache_clear()
