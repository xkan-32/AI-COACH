from datetime import UTC, datetime, timedelta

import pytest

from app.oauth import InvalidOAuthState, OAuthStateSigner, strava_authorization_url
from app.state import FirestoreOAuthSessionStore, InMemoryOAuthSessionStore


def test_state_round_trip_and_expiry_without_user_identifier() -> None:
    signer = OAuthStateSigner("a-long-development-signing-key", ttl_seconds=60)
    token, created = signer.create(now=100)
    verified = signer.verify(token, now=159)
    assert verified.nonce == created.nonce
    assert "line-user" not in token
    with pytest.raises(InvalidOAuthState, match="expired"):
        signer.verify(token, now=161)


def test_state_rejects_tampering() -> None:
    signer = OAuthStateSigner("a-long-development-signing-key")
    token, _ = signer.create(now=100)
    with pytest.raises(InvalidOAuthState):
        signer.verify(token + "x", now=101)


async def test_oauth_session_is_consumed_once() -> None:
    now = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=100)
    store = InMemoryOAuthSessionStore(clock=lambda: now)
    await store.create("nonce", "line-user-1", 200)
    assert await store.consume("nonce") == "line-user-1"
    assert await store.consume("nonce") is None


async def test_expired_oauth_session_is_rejected_and_consumed() -> None:
    now = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=201)
    store = InMemoryOAuthSessionStore(clock=lambda: now)
    await store.create("nonce", "line-user-1", 200)

    assert await store.consume("nonce") is None
    assert await store.consume("nonce") is None


async def test_firestore_oauth_session_uses_timestamp_and_checks_expiry() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    values: dict = {}

    class Snapshot:
        exists = True

        def get(self, key: str):
            return values[key]

    class Document:
        async def create(self, new_values: dict) -> None:
            values.update(new_values)

        async def get(self, transaction=None) -> Snapshot:
            return Snapshot()

    class Collection:
        def document(self, nonce: str) -> Document:
            return Document()

    class Transaction:
        def __init__(self) -> None:
            self.deleted = False
            self.committed = False
            self._id = None
            self._read_only = False
            self._max_attempts = 1

        def _clean_up(self) -> None:
            self._id = None

        async def _begin(self, retry_id=None) -> None:
            self._id = b"transaction-id"

        def delete(self, document) -> None:
            self.deleted = True

        async def _commit(self) -> None:
            self.committed = True

        async def _rollback(self) -> None:
            pass

    transaction = Transaction()

    class Client:
        def collection(self, name: str) -> Collection:
            assert name == "oauth_sessions"
            return Collection()

        def transaction(self) -> Transaction:
            return transaction

    store = FirestoreOAuthSessionStore(Client(), clock=lambda: now)
    await store.create(
        "nonce", "line-user-1", int((now - timedelta(seconds=1)).timestamp())
    )

    assert isinstance(values["expires_at"], datetime)
    assert await store.consume("nonce") is None
    assert transaction.deleted
    assert transaction.committed


def test_authorization_url_has_required_scopes() -> None:
    url = strava_authorization_url("123", "https://example.com/callback", "state")
    assert "activity%3Awrite" in url
    assert "state=state" in url
