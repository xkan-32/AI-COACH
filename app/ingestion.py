import asyncio
import time
from typing import Protocol

from app.activity_data import (
    ActivityIngestionStateStore,
    ActivityLapStore,
    ActivityMetricsStore,
    ActivityStreamStore,
)
from app.activity_metrics import compute_activity_metrics
from app.condition import ActivityContext, ActivityContextStore
from app.domain.models import Activity
from app.state import StravaTokenStore
from app.strava import StoredStravaToken, StravaClient


class ActivityStore(Protocol):
    async def get(self, activity_id: str) -> Activity | None: ...
    async def save(self, activity: Activity) -> None: ...
    async def list_recent(self, athlete_id: str, limit: int) -> list[Activity]: ...


class ConditionPromptSender(Protocol):
    async def send(self, line_user_id: str, activity: Activity) -> None: ...


class UnknownAthleteToken(ValueError):
    pass


class ActivityIngestionService:
    def __init__(
        self,
        strava: StravaClient,
        tokens: StravaTokenStore,
        activities: ActivityStore,
        prompts: ConditionPromptSender,
        contexts: ActivityContextStore,
        laps: ActivityLapStore | None = None,
        streams: ActivityStreamStore | None = None,
        metrics: ActivityMetricsStore | None = None,
        ingestion_state: ActivityIngestionStateStore | None = None,
        refresh_margin_seconds: int = 300,
        clock=time.time,
    ) -> None:
        self._strava = strava
        self._tokens = tokens
        self._activities = activities
        self._prompts = prompts
        self._contexts = contexts
        self._laps = laps
        self._streams = streams
        self._metrics = metrics
        self._ingestion_state = ingestion_state
        self._refresh_margin = refresh_margin_seconds
        self._clock = clock

    async def ingest(self, activity_id: str, athlete_id: str) -> Activity:
        token = await self._tokens.get(athlete_id)
        if token is None:
            raise UnknownAthleteToken(f"No Strava token for athlete {athlete_id}")
        token = await self._refresh_if_needed(token)
        activity = await self._strava.get_activity(activity_id, token.access_token)
        if activity.athlete_id != athlete_id:
            raise ValueError("Strava activity owner mismatch")
        if (
            self._ingestion_state is None
            or not await self._ingestion_state.is_completed(activity_id, "activity")
        ):
            await self._activities.save(activity)
            if self._ingestion_state is not None:
                await self._ingestion_state.complete(activity_id, "activity")
        if (
            self._laps is not None
            and self._streams is not None
            and self._metrics is not None
            and self._ingestion_state is not None
        ):
            laps = []
            points = []
            if _supports_detailed_streams(activity.activity_type):
                laps = await self._strava.get_activity_laps(
                    activity_id,
                    athlete_id,
                    activity.activity_type,
                    token.access_token,
                )
                laps = [
                    lap.model_copy(update={"activity_started_at": activity.started_at})
                    for lap in laps
                ]
                if not await self._ingestion_state.is_completed(activity_id, "laps"):
                    await self._laps.save_many(laps)
                    await self._ingestion_state.complete(activity_id, "laps")
                points = await self._strava.get_activity_streams(
                    activity_id, athlete_id, token.access_token
                )
                points = [
                    point.model_copy(
                        update={"activity_started_at": activity.started_at}
                    )
                    for point in points
                ]
                if not await self._ingestion_state.is_completed(activity_id, "streams"):
                    await self._streams.save_many(points)
                    await self._ingestion_state.complete(activity_id, "streams")
            if not await self._ingestion_state.is_completed(activity_id, "metrics"):
                await self._metrics.save(
                    compute_activity_metrics(activity, laps, points)
                )
                await self._ingestion_state.complete(activity_id, "metrics")
        await self._contexts.save(
            ActivityContext(activity.id, activity.athlete_id, token.line_user_id)
        )
        if (
            self._ingestion_state is None
            or not await self._ingestion_state.is_completed(activity_id, "prompt")
        ):
            await self._prompts.send(token.line_user_id, activity)
            if self._ingestion_state is not None:
                await self._ingestion_state.complete(activity_id, "prompt")
        return activity

    async def _refresh_if_needed(self, token: StoredStravaToken) -> StoredStravaToken:
        if token.expires_at > int(self._clock()) + self._refresh_margin:
            return token
        refreshed = await self._strava.refresh(token.refresh_token)
        updated = StoredStravaToken(
            athlete_id=token.athlete_id,
            line_user_id=token.line_user_id,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            expires_at=refreshed.expires_at,
        )
        await self._tokens.save(updated)
        return updated


class InMemoryActivityStore:
    def __init__(self) -> None:
        self.activities: dict[str, Activity] = {}

    async def get(self, activity_id: str) -> Activity | None:
        return self.activities.get(activity_id)

    async def save(self, activity: Activity) -> None:
        self.activities[activity.id] = activity

    async def list_recent(self, athlete_id: str, limit: int) -> list[Activity]:
        matches = [
            activity
            for activity in self.activities.values()
            if activity.athlete_id == athlete_id
        ]
        return sorted(matches, key=lambda item: item.started_at, reverse=True)[:limit]


class BigQueryActivityStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def get(self, activity_id: str) -> Activity | None:
        query = (
            f"SELECT * FROM `{self._table}` WHERE activity_id = @activity_id LIMIT 1"
        )
        from google.cloud import bigquery

        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("activity_id", "STRING", activity_id)
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        if not rows:
            return None
        return _activity_from_row(rows[0])

    async def save(self, activity: Activity) -> None:
        row = {
            "activity_id": activity.id,
            "athlete_id": activity.athlete_id,
            "activity_type": activity.activity_type,
            "started_at": activity.started_at.isoformat(),
            "duration_seconds": activity.duration_seconds,
            "distance_meters": activity.distance_meters,
            "description": activity.description,
            "elapsed_seconds": activity.elapsed_seconds,
            "total_elevation_gain_meters": activity.total_elevation_gain_meters,
            "average_speed_mps": activity.average_speed_mps,
            "max_speed_mps": activity.max_speed_mps,
            "has_heartrate": activity.has_heartrate,
            "average_heartrate_bpm": activity.average_heartrate_bpm,
            "max_heartrate_bpm": activity.max_heartrate_bpm,
            "average_cadence_per_minute": activity.average_cadence_per_minute,
            "suffer_score": activity.suffer_score,
            "calories": activity.calories,
        }
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            self._table,
            [row],
            row_ids=[activity.id],
        )
        if errors:
            raise RuntimeError("BigQuery activity insert failed")

    async def list_recent(self, athlete_id: str, limit: int) -> list[Activity]:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._table}` WHERE athlete_id = @athlete_id "
            "ORDER BY started_at DESC LIMIT @limit"
        )
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("athlete_id", "STRING", athlete_id),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        return [_activity_from_row(row) for row in rows]


def _supports_detailed_streams(activity_type: str) -> bool:
    return activity_type in {
        "Run",
        "TrailRun",
        "VirtualRun",
        "Walk",
        "Hike",
        "Ride",
        "MountainBikeRide",
        "GravelRide",
        "VirtualRide",
    }


def _activity_from_row(row) -> Activity:
    values = dict(row.items())
    return Activity(
        id=values["activity_id"],
        athlete_id=values["athlete_id"],
        activity_type=values["activity_type"],
        started_at=values["started_at"],
        duration_seconds=values["duration_seconds"],
        distance_meters=values["distance_meters"],
        description=values.get("description") or "",
        elapsed_seconds=values.get("elapsed_seconds"),
        total_elevation_gain_meters=values.get("total_elevation_gain_meters"),
        average_speed_mps=values.get("average_speed_mps"),
        max_speed_mps=values.get("max_speed_mps"),
        has_heartrate=values.get("has_heartrate") or False,
        average_heartrate_bpm=values.get("average_heartrate_bpm"),
        max_heartrate_bpm=values.get("max_heartrate_bpm"),
        average_cadence_per_minute=values.get("average_cadence_per_minute"),
        suffer_score=values.get("suffer_score"),
        calories=values.get("calories"),
    )
