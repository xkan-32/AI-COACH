import pytest

from app.oauth import InvalidOAuthState, OAuthStateSigner, strava_authorization_url
from app.state import InMemoryOAuthSessionStore


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
    store = InMemoryOAuthSessionStore()
    await store.create("nonce", "line-user-1", 200)
    assert await store.consume("nonce") == "line-user-1"
    assert await store.consume("nonce") is None


def test_authorization_url_has_required_scopes() -> None:
    url = strava_authorization_url("123", "https://example.com/callback", "state")
    assert "activity%3Awrite" in url
    assert "state=state" in url
