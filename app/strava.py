from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import BaseModel, Field

from app.domain.models import Activity


class StravaApiError(RuntimeError):
    pass


class StravaOAuthError(StravaApiError):
    pass


class StravaAthlete(BaseModel):
    id: int = Field(gt=0)


class StravaTokenResponse(BaseModel):
    token_type: str
    expires_at: int
    expires_in: int
    refresh_token: str
    access_token: str
    athlete: StravaAthlete


class StravaRefreshResponse(BaseModel):
    token_type: str
    expires_at: int
    expires_in: int
    refresh_token: str
    access_token: str


class StravaActivityResponse(BaseModel):
    id: int = Field(gt=0)
    athlete: StravaAthlete
    sport_type: str
    start_date: datetime
    moving_time: int = Field(ge=0)
    distance: float = Field(ge=0)
    description: str | None = None

    def to_domain(self) -> Activity:
        return Activity(
            id=str(self.id),
            athlete_id=str(self.athlete.id),
            activity_type=self.sport_type,
            started_at=self.start_date,
            duration_seconds=self.moving_time,
            distance_meters=self.distance,
            description=self.description or "",
        )


@dataclass(frozen=True)
class StoredStravaToken:
    athlete_id: str
    line_user_id: str
    access_token: str
    refresh_token: str
    expires_at: int


class StravaClient:
    token_url = "https://www.strava.com/oauth/token"
    api_base_url = "https://www.strava.com/api/v3"

    def __init__(
        self, client_id: str, client_secret: str, timeout_seconds: float = 10.0
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout_seconds

    async def exchange_code(self, code: str) -> StravaTokenResponse:
        data = await self._token_request(
            {"code": code, "grant_type": "authorization_code"},
            "Strava token exchange failed",
        )
        try:
            return StravaTokenResponse.model_validate(data)
        except ValueError as exc:
            raise StravaOAuthError("Invalid Strava token response") from exc

    async def refresh(self, refresh_token: str) -> StravaRefreshResponse:
        data = await self._token_request(
            {"refresh_token": refresh_token, "grant_type": "refresh_token"},
            "Strava token refresh failed",
        )
        try:
            return StravaRefreshResponse.model_validate(data)
        except ValueError as exc:
            raise StravaOAuthError("Invalid Strava refresh response") from exc

    async def get_activity(self, activity_id: str, access_token: str) -> Activity:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.api_base_url}/activities/{activity_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                return StravaActivityResponse.model_validate(
                    response.json()
                ).to_domain()
        except (httpx.HTTPError, ValueError) as exc:
            raise StravaApiError("Strava activity fetch failed") from exc

    async def update_description(
        self, activity_id: str, access_token: str, description: str
    ) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.put(
                    f"{self.api_base_url}/activities/{activity_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    data={"description": description},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StravaApiError("Strava Description update failed") from exc

    async def _token_request(self, values: dict[str, str], message: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.token_url,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        **values,
                    },
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise StravaOAuthError(message) from exc


StravaOAuthClient = StravaClient
