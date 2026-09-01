from typing import Protocol

from app.strava import StoredStravaToken


class EventStore(Protocol):
    async def reserve(self, provider: str, event_key: str) -> bool: ...
    async def release(self, provider: str, event_key: str) -> None: ...


class OAuthSessionStore(Protocol):
    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None: ...
    async def consume(self, nonce: str) -> str | None: ...


class StravaTokenStore(Protocol):
    async def get(self, athlete_id: str) -> StoredStravaToken | None: ...
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


class InMemoryOAuthSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None:
        self._sessions[nonce] = line_user_id

    async def consume(self, nonce: str) -> str | None:
        return self._sessions.pop(nonce, None)


class InMemoryStravaTokenStore:
    def __init__(self) -> None:
        self.tokens: dict[str, StoredStravaToken] = {}

    async def get(self, athlete_id: str) -> StoredStravaToken | None:
        return self.tokens.get(athlete_id)

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
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._document(provider, event_key).create(
                {"provider": provider, "event_key": event_key, "status": "reserved"}
            )
        except AlreadyExists:
            return False
        return True

    async def release(self, provider: str, event_key: str) -> None:
        await self._document(provider, event_key).delete()


class FirestoreOAuthSessionStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def create(self, nonce: str, line_user_id: str, expires_at: int) -> None:
        await (
            self._client.collection("oauth_sessions")
            .document(nonce)
            .create({"line_user_id": line_user_id, "expires_at": expires_at})
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
            return snapshot.get("line_user_id")

        return await consume_once(transaction)


class FirestoreStravaTokenStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, athlete_id: str) -> StoredStravaToken | None:
        snapshot = (
            await self._client.collection("strava_tokens").document(athlete_id).get()
        )
        if not snapshot.exists:
            return None
        return StoredStravaToken(**snapshot.to_dict())

    async def save(self, token: StoredStravaToken) -> None:
        document = self._client.collection("strava_tokens").document(token.athlete_id)
        await document.set(
            {
                "athlete_id": token.athlete_id,
                "line_user_id": token.line_user_id,
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
            }
        )
