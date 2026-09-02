from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import BaseModel, Field

from app.domain.models import (
    Activity,
    ActivityLap,
    ActivityStreamPoint,
)

ACTIVITY_STREAM_KEYS = (
    "time",
    "distance",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "temp",
    "moving",
    "grade_smooth",
)


class StravaApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_kind: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_kind = error_kind


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
    elapsed_time: int | None = Field(default=None, ge=0)
    total_elevation_gain: float | None = Field(default=None, ge=0)
    average_speed: float | None = Field(default=None, ge=0)
    max_speed: float | None = Field(default=None, ge=0)
    has_heartrate: bool = False
    average_heartrate: float | None = Field(default=None, ge=0)
    max_heartrate: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)
    suffer_score: float | None = Field(default=None, ge=0)
    calories: float | None = Field(default=None, ge=0)

    def to_domain(self) -> Activity:
        return Activity(
            id=str(self.id),
            athlete_id=str(self.athlete.id),
            activity_type=self.sport_type,
            started_at=self.start_date,
            duration_seconds=self.moving_time,
            distance_meters=self.distance,
            description=self.description or "",
            elapsed_seconds=self.elapsed_time,
            total_elevation_gain_meters=self.total_elevation_gain,
            average_speed_mps=self.average_speed,
            max_speed_mps=self.max_speed,
            has_heartrate=self.has_heartrate,
            average_heartrate_bpm=self.average_heartrate,
            max_heartrate_bpm=self.max_heartrate,
            average_cadence_per_minute=_cadence_per_minute(
                self.sport_type, self.average_cadence
            ),
            suffer_score=self.suffer_score,
            calories=self.calories,
        )


class StravaLapResponse(BaseModel):
    name: str | None = None
    elapsed_time: int = Field(ge=0)
    moving_time: int = Field(ge=0)
    distance: float = Field(ge=0)
    total_elevation_gain: float | None = Field(default=None, ge=0)
    average_speed: float | None = Field(default=None, ge=0)
    max_speed: float | None = Field(default=None, ge=0)
    average_heartrate: float | None = Field(default=None, ge=0)
    max_heartrate: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)

    def to_domain(
        self,
        activity_id: str,
        athlete_id: str,
        activity_type: str,
        lap_index: int,
    ) -> ActivityLap:
        return ActivityLap(
            activity_id=activity_id,
            athlete_id=athlete_id,
            lap_index=lap_index,
            name=self.name or "",
            elapsed_seconds=self.elapsed_time,
            moving_seconds=self.moving_time,
            distance_meters=self.distance,
            total_elevation_gain_meters=self.total_elevation_gain,
            average_speed_mps=self.average_speed,
            max_speed_mps=self.max_speed,
            average_heartrate_bpm=self.average_heartrate,
            max_heartrate_bpm=self.max_heartrate,
            average_cadence_per_minute=_cadence_per_minute(
                activity_type, self.average_cadence
            ),
        )


@dataclass(frozen=True)
class StoredStravaToken:
    athlete_id: str
    line_user_id: str
    access_token: str
    refresh_token: str
    expires_at: int


@dataclass(frozen=True)
class RouteStreamPoint:
    distance_meters: float
    latitude: float
    longitude: float


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
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava activity fetch failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava activity fetch failed", error_kind="transport"
            ) from exc
        except ValueError as exc:
            raise StravaApiError(
                "Strava activity fetch failed", error_kind="invalid_response"
            ) from exc

    async def get_activity_laps(
        self,
        activity_id: str,
        athlete_id: str,
        activity_type: str,
        access_token: str,
    ) -> list[ActivityLap]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.api_base_url}/activities/{activity_id}/laps",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                values = response.json()
                if not isinstance(values, list):
                    raise TypeError("Expected a list of laps")
                return [
                    StravaLapResponse.model_validate(value).to_domain(
                        activity_id, athlete_id, activity_type, index
                    )
                    for index, value in enumerate(values)
                ]
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava activity laps fetch failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava activity laps fetch failed", error_kind="transport"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise StravaApiError(
                "Strava activity laps fetch failed", error_kind="invalid_response"
            ) from exc

    async def get_activity_streams(
        self, activity_id: str, athlete_id: str, access_token: str
    ) -> list[ActivityStreamPoint]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.api_base_url}/activities/{activity_id}/streams",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "keys": ",".join(ACTIVITY_STREAM_KEYS),
                        "key_by_type": "true",
                    },
                )
                response.raise_for_status()
                return _stream_points(activity_id, athlete_id, response.json())
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava activity streams fetch failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava activity streams fetch failed", error_kind="transport"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise StravaApiError(
                "Strava activity streams fetch failed", error_kind="invalid_response"
            ) from exc

    async def get_activity_route_points(
        self, activity_id: str, access_token: str
    ) -> list[RouteStreamPoint]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self.api_base_url}/activities/{activity_id}/streams",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "keys": "time,distance,latlng",
                        "key_by_type": "true",
                    },
                )
                response.raise_for_status()
                return _route_stream_points(response.json())
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava activity route fetch failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava activity route fetch failed", error_kind="transport"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise StravaApiError(
                "Strava activity route fetch failed", error_kind="invalid_response"
            ) from exc

    async def create_activity(
        self,
        access_token: str,
        *,
        name: str,
        sport_type: str,
        start_date_local: str,
        elapsed_time: int,
        description: str = "",
        distance: float = 0,
    ) -> Activity:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self.api_base_url}/activities",
                    headers={"Authorization": f"Bearer {access_token}"},
                    data={
                        "name": name,
                        "sport_type": sport_type,
                        "start_date_local": start_date_local,
                        "elapsed_time": elapsed_time,
                        "description": description,
                        "distance": distance,
                    },
                )
                response.raise_for_status()
                return StravaActivityResponse.model_validate(
                    response.json()
                ).to_domain()
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava activity create failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava activity create failed", error_kind="transport"
            ) from exc
        except ValueError as exc:
            raise StravaApiError(
                "Strava activity create failed", error_kind="invalid_response"
            ) from exc

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
        except httpx.HTTPStatusError as exc:
            raise StravaApiError(
                "Strava Description update failed",
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaApiError(
                "Strava Description update failed", error_kind="transport"
            ) from exc

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
        except httpx.HTTPStatusError as exc:
            raise StravaOAuthError(
                message,
                status_code=exc.response.status_code,
                error_kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise StravaOAuthError(message, error_kind="transport") from exc
        except ValueError as exc:
            raise StravaOAuthError(message, error_kind="invalid_response") from exc


def _stream_points(
    activity_id: str, athlete_id: str, payload: object
) -> list[ActivityStreamPoint]:
    if not isinstance(payload, dict):
        raise TypeError("Expected keyed stream response")
    streams: dict[str, list[object]] = {}
    for key in ACTIVITY_STREAM_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise TypeError(f"Invalid {key} stream")
        streams[key] = value["data"]
    size = max((len(values) for values in streams.values()), default=0)

    def at(key: str, index: int):
        values = streams.get(key, [])
        return values[index] if index < len(values) else None

    return [
        ActivityStreamPoint(
            activity_id=activity_id,
            athlete_id=athlete_id,
            sample_index=index,
            time_seconds=at("time", index),
            distance_meters=at("distance", index),
            altitude_meters=at("altitude", index),
            velocity_mps=at("velocity_smooth", index),
            heartrate_bpm=at("heartrate", index),
            cadence_rpm=at("cadence", index),
            watts=at("watts", index),
            temperature_celsius=at("temp", index),
            moving=at("moving", index),
            grade_percent=at("grade_smooth", index),
        )
        for index in range(size)
    ]


def _cadence_per_minute(activity_type: str, raw_cadence: float | None) -> float | None:
    if raw_cadence is None:
        return None
    if activity_type in {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}:
        return raw_cadence * 2
    return raw_cadence


def _route_stream_points(payload: object) -> list[RouteStreamPoint]:
    if not isinstance(payload, dict):
        raise TypeError("Expected keyed route stream response")
    distance = payload.get("distance")
    latlng = payload.get("latlng")
    if (
        not isinstance(distance, dict)
        or not isinstance(distance.get("data"), list)
        or not isinstance(latlng, dict)
        or not isinstance(latlng.get("data"), list)
    ):
        raise TypeError("Route streams are missing")
    distances = distance["data"]
    coordinates = latlng["data"]
    if len(distances) != len(coordinates):
        raise ValueError("Route stream lengths do not match")
    points = []
    for meters, coordinate in zip(distances, coordinates, strict=True):
        if (
            not isinstance(meters, (int, float))
            or not isinstance(coordinate, list)
            or len(coordinate) != 2
            or not all(isinstance(value, (int, float)) for value in coordinate)
        ):
            raise TypeError("Invalid route coordinate")
        points.append(
            RouteStreamPoint(
                distance_meters=float(meters),
                latitude=float(coordinate[0]),
                longitude=float(coordinate[1]),
            )
        )
    return points


StravaOAuthClient = StravaClient
