import asyncio
import time
from typing import Protocol

from app.condition import ActivityContext, ActivityContextStore
from app.domain.models import Activity
from app.state import StravaTokenStore
from app.strava import StoredStravaToken, StravaClient


class ActivityStore(Protocol):
    async def get(self, activity_id: str) -> Activity | None: ...
    async def save(self, activity: Activity) -> None: ...


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
        refresh_margin_seconds: int = 300,
        clock=time.time,
    ) -> None:
        self._strava = strava
        self._tokens = tokens
        self._activities = activities
        self._prompts = prompts
        self._contexts = contexts
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
        await self._activities.save(activity)
        await self._contexts.save(
            ActivityContext(activity.id, activity.athlete_id, token.line_user_id)
        )
        await self._prompts.send(token.line_user_id, activity)
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


class BigQueryActivityStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def get(self, activity_id: str) -> Activity | None:
        query = f"SELECT activity_id, athlete_id, activity_type, started_at, duration_seconds, distance_meters, description FROM `{self._table}` WHERE activity_id = @activity_id LIMIT 1"
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
        row = rows[0]
        return Activity(
            id=row.activity_id,
            athlete_id=row.athlete_id,
            activity_type=row.activity_type,
            started_at=row.started_at,
            duration_seconds=row.duration_seconds,
            distance_meters=row.distance_meters,
            description=row.description or "",
        )

    async def save(self, activity: Activity) -> None:
        row = {
            "activity_id": activity.id,
            "athlete_id": activity.athlete_id,
            "activity_type": activity.activity_type,
            "started_at": activity.started_at.isoformat(),
            "duration_seconds": activity.duration_seconds,
            "distance_meters": activity.distance_meters,
            "description": activity.description,
        }
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            self._table,
            [row],
            row_ids=[activity.id],
        )
        if errors:
            raise RuntimeError("BigQuery activity insert failed")
