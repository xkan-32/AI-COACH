import base64
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.condition import InMemoryActivityContextStore
from app.domain.models import Activity
from app.ingestion import ActivityIngestionService, InMemoryActivityStore
from app.line import InMemoryConditionPromptSender
from app.state import FirestoreStravaTokenStore
from app.strava import StoredStravaToken, StravaRefreshResponse
from app.token_crypto import (
    AesGcmTokenCipher,
    InvalidTokenEncryptionKey,
    TokenDecryptionError,
)


def encryption_key() -> str:
    return base64.b64encode(b"k" * 32).decode("ascii")


def test_aes_gcm_round_trip_and_authentication() -> None:
    cipher = AesGcmTokenCipher(f"\n{encryption_key()}\n")
    encrypted = cipher.encrypt("access-secret", "athlete:42:access")

    assert encrypted.ciphertext != "access-secret"
    assert cipher.decrypt(encrypted, "athlete:42:access") == "access-secret"
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(encrypted, "athlete:43:access")
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(
            replace(encrypted, ciphertext=encrypted.ciphertext[:-2] + "AA"),
            "athlete:42:access",
        )


@pytest.mark.parametrize("key", ["not-base64", base64.b64encode(b"short").decode()])
def test_cipher_rejects_invalid_keys(key: str) -> None:
    with pytest.raises(InvalidTokenEncryptionKey):
        AesGcmTokenCipher(key)


class Snapshot:
    def __init__(self, values: dict | None) -> None:
        self._values = values
        self.exists = values is not None

    def to_dict(self) -> dict:
        return dict(self._values or {})


class Document:
    def __init__(self, documents: dict[str, dict], document_id: str) -> None:
        self._documents = documents
        self._document_id = document_id

    async def get(self) -> Snapshot:
        return Snapshot(self._documents.get(self._document_id))

    async def set(self, values: dict) -> None:
        self._documents[self._document_id] = dict(values)


class Collection:
    def __init__(self, documents: dict[str, dict]) -> None:
        self._documents = documents

    def document(self, document_id: str) -> Document:
        return Document(self._documents, document_id)


class Client:
    def __init__(self, documents: dict[str, dict] | None = None) -> None:
        self.documents = documents or {}

    def collection(self, name: str) -> Collection:
        assert name == "strava_tokens"
        return Collection(self.documents)


def stored_token() -> StoredStravaToken:
    return StoredStravaToken(
        athlete_id="42",
        line_user_id="U1",
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=2_000_000_000,
    )


async def test_firestore_store_encrypts_tokens_at_rest() -> None:
    client = Client()
    store = FirestoreStravaTokenStore(client, AesGcmTokenCipher(encryption_key()))

    await store.save(stored_token())

    persisted = client.documents["42"]
    assert "access_token" not in persisted
    assert "refresh_token" not in persisted
    assert "access-secret" not in str(persisted)
    assert "refresh-secret" not in str(persisted)
    assert await store.get("42") == stored_token()


async def test_firestore_store_migrates_legacy_plaintext_on_read() -> None:
    token = stored_token()
    client = Client(
        {
            "42": {
                "athlete_id": token.athlete_id,
                "line_user_id": token.line_user_id,
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
            }
        }
    )
    store = FirestoreStravaTokenStore(client, AesGcmTokenCipher(encryption_key()))

    assert await store.get("42") == token
    assert "access_token" not in client.documents["42"]
    assert "refresh_token" not in client.documents["42"]


async def test_firestore_store_rejects_tampered_owner_metadata() -> None:
    client = Client()
    store = FirestoreStravaTokenStore(client, AesGcmTokenCipher(encryption_key()))
    await store.save(stored_token())
    client.documents["42"]["line_user_id"] = "attacker"

    with pytest.raises(TokenDecryptionError):
        await store.get("42")


async def test_refreshed_tokens_remain_encrypted() -> None:
    class FakeStrava:
        async def refresh(self, refresh_token: str) -> StravaRefreshResponse:
            assert refresh_token == "refresh-secret"
            return StravaRefreshResponse(
                token_type="Bearer",
                expires_at=20_000,
                expires_in=10_000,
                refresh_token="rotated-refresh",
                access_token="rotated-access",
            )

        async def get_activity(self, activity_id: str, access_token: str) -> Activity:
            assert access_token == "rotated-access"
            return Activity(
                id=activity_id,
                athlete_id="42",
                activity_type="Run",
                started_at=datetime(2026, 9, 2, tzinfo=UTC),
                duration_seconds=1800,
                distance_meters=5000,
            )

    client = Client()
    store = FirestoreStravaTokenStore(client, AesGcmTokenCipher(encryption_key()))
    await store.save(stored_token())
    client.documents["42"]["expires_at"] = 100
    service = ActivityIngestionService(
        FakeStrava(),
        store,
        InMemoryActivityStore(),
        InMemoryConditionPromptSender(),
        InMemoryActivityContextStore(),
        clock=lambda: 1_000,
    )

    await service.ingest("activity-1", "42")

    persisted = client.documents["42"]
    assert "rotated-access" not in str(persisted)
    assert "rotated-refresh" not in str(persisted)
    assert (await store.get("42")).refresh_token == "rotated-refresh"
