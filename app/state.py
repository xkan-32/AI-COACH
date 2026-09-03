from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.strava import StoredStravaToken
from app.token_crypto import (
    AesGcmTokenCipher,
    EncryptedTokenValue,
    TokenDecryptionError,
)


class EventStore(Protocol):
    async def reserve(self, provider: str, event_key: str) -> bool: ...
    async def release(self, provider: str, event_key: str) -> None: ...
    async def complete(self, provider: str, event_key: str) -> None: ...


class OAuthSessionStore(Protocol):
    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None: ...
    async def consume(self, nonce: str) -> str | None: ...


class StravaTokenStore(Protocol):
    async def get(self, athlete_id: str) -> StoredStravaToken | None: ...
    async def get_by_line_user_id(
        self, line_user_id: str
    ) -> StoredStravaToken | None: ...
    async def save(self, token: StoredStravaToken) -> None: ...


class InMemoryEventStore:
    def __init__(self) -> None:
        self._keys: set[tuple[str, str]] = set()

    async def reserve(self, provider: str, event_key: str) -> bool:
        key = (provider, event_key)
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    async def release(self, provider: str, event_key: str) -> None:
        self._keys.discard((provider, event_key))

    async def complete(self, provider: str, event_key: str) -> None:
        return None


class InMemoryOAuthSessionStore:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sessions: dict[str, tuple[str, datetime]] = {}

    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None:
        self._sessions[nonce] = (line_user_id, _expiry_datetime(expires_at))

    async def consume(self, nonce: str) -> str | None:
        session = self._sessions.pop(nonce, None)
        if session is None:
            return None
        line_user_id, expires_at = session
        return line_user_id if expires_at > self._clock() else None


class InMemoryStravaTokenStore:
    def __init__(self) -> None:
        self.tokens: dict[str, StoredStravaToken] = {}

    async def get(self, athlete_id: str) -> StoredStravaToken | None:
        return self.tokens.get(athlete_id)

    async def get_by_line_user_id(self, line_user_id: str) -> StoredStravaToken | None:
        for token in self.tokens.values():
            if token.line_user_id == line_user_id:
                return token
        return None

    async def save(self, token: StoredStravaToken) -> None:
        self.tokens[token.athlete_id] = token


class FirestoreEventStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _document(self, provider: str, event_key: str):
        return self._client.collection("webhook_events").document(
            f"{provider}:{event_key}"
        )

    async def reserve(self, provider: str, event_key: str) -> bool:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        document = self._document(provider, event_key)
        now = datetime.now(UTC)

        @async_transactional
        async def reserve_once(transaction: object) -> bool:
            snapshot = await document.get(transaction=transaction)
            values = snapshot.to_dict() if snapshot.exists else None
            if values is not None and not _reservation_is_stale(values, now):
                return False
            payload = {
                "provider": provider,
                "event_key": event_key,
                "status": "reserved",
                "reserved_at": now,
            }
            if values is None:
                transaction.create(document, payload)
            else:
                transaction.set(document, payload)
            return True

        return await reserve_once(self._client.transaction())

    async def release(self, provider: str, event_key: str) -> None:
        await self._document(provider, event_key).delete()

    async def complete(self, provider: str, event_key: str) -> None:
        from datetime import UTC, datetime

        await self._document(provider, event_key).update(
            {"status": "completed", "processed_at": datetime.now(UTC)}
        )


EVENT_RESERVATION_LEASE = timedelta(minutes=10)


def _reservation_is_stale(values: dict, now: datetime) -> bool:
    """Treat only in-progress reservations as recoverable after their lease."""
    if values.get("status") != "reserved":
        return False
    reserved_at = values.get("reserved_at")
    if not isinstance(reserved_at, datetime):
        return True
    if reserved_at.tzinfo is None:
        reserved_at = reserved_at.replace(tzinfo=UTC)
    return reserved_at.astimezone(UTC) + EVENT_RESERVATION_LEASE <= now


class FirestoreOAuthSessionStore:
    def __init__(
        self, client: object, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None:
        await (
            self._client.collection("oauth_sessions")
            .document(nonce)
            .create(
                {
                    "line_user_id": line_user_id,
                    "expires_at": _expiry_datetime(expires_at),
                }
            )
        )

    async def consume(self, nonce: str) -> str | None:
        from google.cloud import firestore

        document = self._client.collection("oauth_sessions").document(nonce)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def consume_once(txn):
            snapshot = await document.get(transaction=txn)
            if not snapshot.exists:
                return None
            txn.delete(document)
            expires_at = _expiry_datetime(snapshot.get("expires_at"))
            if expires_at <= self._clock():
                return None
            return snapshot.get("line_user_id")

        return await consume_once(transaction)


class FirestoreStravaTokenStore:
    def __init__(self, client: object, cipher: AesGcmTokenCipher) -> None:
        self._client = client
        self._cipher = cipher

    @staticmethod
    def _associated_data(athlete_id: str, line_user_id: str, token_kind: str) -> str:
        return f"strava-token:v1:{athlete_id}:{line_user_id}:{token_kind}"

    async def get(self, athlete_id: str) -> StoredStravaToken | None:
        snapshot = (
            await self._client.collection("strava_tokens").document(athlete_id).get()
        )
        if not snapshot.exists:
            return None
        values = snapshot.to_dict()
        if values.get("athlete_id") != athlete_id:
            raise TokenDecryptionError("Stored token athlete does not match document")
        if "access_token" in values and "refresh_token" in values:
            token = StoredStravaToken(**values)
            await self.save(token)
            return token

        line_user_id = values.get("line_user_id")
        version = values.get("token_encryption_version")
        if not isinstance(line_user_id, str) or not isinstance(version, int):
            raise TokenDecryptionError("Stored token encryption metadata is invalid")
        access = EncryptedTokenValue(
            ciphertext=values.get("access_token_ciphertext", ""),
            nonce=values.get("access_token_nonce", ""),
            version=version,
        )
        refresh = EncryptedTokenValue(
            ciphertext=values.get("refresh_token_ciphertext", ""),
            nonce=values.get("refresh_token_nonce", ""),
            version=version,
        )
        return StoredStravaToken(
            athlete_id=athlete_id,
            line_user_id=line_user_id,
            access_token=self._cipher.decrypt(
                access,
                self._associated_data(athlete_id, line_user_id, "access"),
            ),
            refresh_token=self._cipher.decrypt(
                refresh,
                self._associated_data(athlete_id, line_user_id, "refresh"),
            ),
            expires_at=values["expires_at"],
        )

    async def get_by_line_user_id(self, line_user_id: str) -> StoredStravaToken | None:
        snapshots = (
            await self._client.collection("strava_tokens")
            .where("line_user_id", "==", line_user_id)
            .limit(1)
            .get()
        )
        if not snapshots:
            return None
        return await self.get(str(snapshots[0].id))

    async def save(self, token: StoredStravaToken) -> None:
        document = self._client.collection("strava_tokens").document(token.athlete_id)
        access = self._cipher.encrypt(
            token.access_token,
            self._associated_data(token.athlete_id, token.line_user_id, "access"),
        )
        refresh = self._cipher.encrypt(
            token.refresh_token,
            self._associated_data(token.athlete_id, token.line_user_id, "refresh"),
        )
        await document.set(
            {
                "athlete_id": token.athlete_id,
                "line_user_id": token.line_user_id,
                "expires_at": token.expires_at,
                "token_encryption_version": access.version,
                "access_token_ciphertext": access.ciphertext,
                "access_token_nonce": access.nonce,
                "refresh_token_ciphertext": refresh.ciphertext,
                "refresh_token_nonce": refresh.nonce,
            }
        )


def _expiry_datetime(value: int | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.fromtimestamp(value, tz=UTC)
