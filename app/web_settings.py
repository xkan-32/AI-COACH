from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class InvalidSettingsToken(ValueError):
    pass


@dataclass(frozen=True)
class SettingsLink:
    nonce: str
    line_user_id: str
    expires_at: datetime
    used_at: datetime | None = None


class SettingsLinkStore(Protocol):
    async def create(self, link: SettingsLink) -> None: ...
    async def consume(self, nonce: str, now: datetime) -> str | None: ...


class InMemorySettingsLinkStore:
    def __init__(self) -> None:
        self.items: dict[str, SettingsLink] = {}

    async def create(self, link: SettingsLink) -> None:
        self.items[link.nonce] = link

    async def consume(self, nonce: str, now: datetime) -> str | None:
        link = self.items.get(nonce)
        if link is None or link.used_at is not None or link.expires_at <= now:
            return None
        self.items[nonce] = SettingsLink(
            nonce=link.nonce,
            line_user_id=link.line_user_id,
            expires_at=link.expires_at,
            used_at=now,
        )
        return link.line_user_id


class FirestoreSettingsLinkStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def create(self, link: SettingsLink) -> None:
        await (
            self._client.collection("profile_settings_links")
            .document(link.nonce)
            .create(
                {
                    "line_user_id": link.line_user_id,
                    "expires_at": link.expires_at,
                    "used_at": None,
                }
            )
        )

    async def consume(self, nonce: str, now: datetime) -> str | None:
        document = self._client.collection("profile_settings_links").document(nonce)
        transaction = self._client.transaction()
        snapshots = [snapshot async for snapshot in transaction.get(document)]
        snapshot = snapshots[0] if snapshots else None
        if snapshot is None or not snapshot.exists:
            return None
        values = snapshot.to_dict()
        expires_at = values.get("expires_at")
        if values.get("used_at") is not None or expires_at is None or expires_at <= now:
            return None
        transaction.update(document, {"used_at": now})
        await transaction.commit()
        return str(values["line_user_id"])


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SettingsTokenSigner:
    def __init__(self, key: str, clock=lambda: datetime.now(UTC)) -> None:
        self._key = key.encode()
        self._clock = clock

    def create_link(self, line_user_id: str) -> tuple[str, SettingsLink]:
        expires_at = self._clock() + timedelta(minutes=10)
        nonce = secrets.token_urlsafe(24)
        token = self._sign("link", {"n": nonce, "exp": int(expires_at.timestamp())})
        return token, SettingsLink(nonce, line_user_id, expires_at)

    def verify_link(self, token: str) -> str:
        payload = self._verify("link", token)
        nonce = payload.get("n")
        if not isinstance(nonce, str) or not nonce:
            raise InvalidSettingsToken("Invalid settings link")
        return nonce

    def create_session(self, line_user_id: str) -> str:
        expires_at = self._clock() + timedelta(minutes=30)
        return self._sign(
            "session", {"u": line_user_id, "exp": int(expires_at.timestamp())}
        )

    def verify_session(self, token: str) -> str:
        payload = self._verify("session", token)
        line_user_id = payload.get("u")
        if not isinstance(line_user_id, str) or not line_user_id:
            raise InvalidSettingsToken("Invalid settings session")
        return line_user_id

    def _sign(self, purpose: str, payload: dict[str, object]) -> str:
        encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = hmac.new(
            self._key, f"profile-settings:{purpose}:{encoded}".encode(), hashlib.sha256
        ).digest()
        return f"{encoded}.{_encode(signature)}"

    def _verify(self, purpose: str, token: str) -> dict[str, object]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = hmac.new(
                self._key,
                f"profile-settings:{purpose}:{encoded}".encode(),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _decode(supplied)):
                raise InvalidSettingsToken("Invalid settings token")
            payload = json.loads(_decode(encoded))
            if int(payload["exp"]) <= int(self._clock().timestamp()):
                raise InvalidSettingsToken("Settings token expired")
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            if isinstance(exc, InvalidSettingsToken):
                raise
            raise InvalidSettingsToken("Invalid settings token") from exc
