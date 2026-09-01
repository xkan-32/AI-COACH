import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode


class InvalidOAuthState(ValueError):
    pass


@dataclass(frozen=True)
class OAuthState:
    nonce: str
    expires_at: int


class OAuthStateSigner:
    def __init__(self, key: str, ttl_seconds: int = 600) -> None:
        if len(key) < 16:
            raise ValueError("OAuth state signing key must be at least 16 characters")
        self._key = key.encode()
        self._ttl_seconds = ttl_seconds

    def create(self, now: int | None = None) -> tuple[str, OAuthState]:
        issued_at = int(time.time() if now is None else now)
        state = OAuthState(
            nonce=secrets.token_urlsafe(24),
            expires_at=issued_at + self._ttl_seconds,
        )
        encoded = self._encode(
            json.dumps(state.__dict__, separators=(",", ":")).encode()
        )
        return f"{encoded}.{self._sign(encoded)}", state

    def verify(self, token: str, now: int | None = None) -> OAuthState:
        try:
            encoded, supplied = token.split(".", 1)
            if not hmac.compare_digest(supplied, self._sign(encoded)):
                raise InvalidOAuthState("Invalid state signature")
            state = OAuthState(**json.loads(self._decode(encoded)))
        except InvalidOAuthState:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise InvalidOAuthState("Malformed OAuth state") from exc
        current = int(time.time() if now is None else now)
        if state.expires_at < current:
            raise InvalidOAuthState("OAuth state expired")
        return state

    def _sign(self, encoded: str) -> str:
        return self._encode(
            hmac.new(self._key, encoded.encode(), hashlib.sha256).digest()
        )

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def strava_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": "read,activity:read_all,activity:write",
            "state": state,
        }
    )
    return f"https://www.strava.com/oauth/authorize?{query}"
