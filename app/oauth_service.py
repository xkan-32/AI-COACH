from app.state import OAuthSessionStore, StravaTokenStore
from app.strava import StoredStravaToken, StravaOAuthClient


class UnknownOAuthSession(ValueError):
    pass


class StravaOAuthService:
    def __init__(
        self,
        client: StravaOAuthClient,
        sessions: OAuthSessionStore,
        tokens: StravaTokenStore,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._tokens = tokens

    async def complete(self, code: str, nonce: str) -> str:
        line_user_id = await self._sessions.consume(nonce)
        if line_user_id is None:
            raise UnknownOAuthSession("OAuth state is unknown or already used")
        response = await self._client.exchange_code(code)
        athlete_id = str(response.athlete.id)
        await self._tokens.save(
            StoredStravaToken(
                athlete_id=athlete_id,
                line_user_id=line_user_id,
                access_token=response.access_token,
                refresh_token=response.refresh_token,
                expires_at=response.expires_at,
            )
        )
        return athlete_id
