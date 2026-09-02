import asyncio
from collections.abc import Sequence
from typing import Protocol

from app.domain.models import ActivityLap, ActivityMetrics, ActivityStreamPoint


class ActivityLapStore(Protocol):
    async def save_many(self, laps: Sequence[ActivityLap]) -> None: ...


class ActivityStreamStore(Protocol):
    async def save_many(self, points: Sequence[ActivityStreamPoint]) -> None: ...


class ActivityMetricsStore(Protocol):
    async def save(self, metrics: ActivityMetrics) -> None: ...
    async def get(self, activity_id: str) -> ActivityMetrics | None: ...


class ActivityIngestionStateStore(Protocol):
    async def is_completed(self, activity_id: str, stage: str) -> bool: ...
    async def complete(self, activity_id: str, stage: str) -> None: ...


class InMemoryActivityLapStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, int], ActivityLap] = {}

    async def save_many(self, laps: Sequence[ActivityLap]) -> None:
        for lap in laps:
            self.items[(lap.activity_id, lap.lap_index)] = lap


class InMemoryActivityStreamStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, int], ActivityStreamPoint] = {}

    async def save_many(self, points: Sequence[ActivityStreamPoint]) -> None:
        for point in points:
            self.items[(point.activity_id, point.sample_index)] = point


class InMemoryActivityMetricsStore:
    def __init__(self) -> None:
        self.items: dict[str, ActivityMetrics] = {}

    async def save(self, metrics: ActivityMetrics) -> None:
        self.items[metrics.activity_id] = metrics

    async def get(self, activity_id: str) -> ActivityMetrics | None:
        return self.items.get(activity_id)


class InMemoryActivityIngestionStateStore:
    def __init__(self) -> None:
        self.completed: set[tuple[str, str]] = set()

    async def is_completed(self, activity_id: str, stage: str) -> bool:
        return (activity_id, stage) in self.completed

    async def complete(self, activity_id: str, stage: str) -> None:
        self.completed.add((activity_id, stage))


class FirestoreActivityIngestionStateStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _stage_document(self, activity_id: str, stage: str):
        return (
            self._client.collection("activity_ingestions")
            .document(activity_id)
            .collection("stages")
            .document(stage)
        )

    async def is_completed(self, activity_id: str, stage: str) -> bool:
        return (await self._stage_document(activity_id, stage).get()).exists

    async def complete(self, activity_id: str, stage: str) -> None:
        await self._stage_document(activity_id, stage).set({"completed": True})


class BigQueryActivityLapStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save_many(self, laps: Sequence[ActivityLap]) -> None:
        if any(lap.activity_started_at is None for lap in laps):
            raise ValueError("Activity start time is required for lap storage")
        rows = [
            {
                "activity_id": lap.activity_id,
                "athlete_id": lap.athlete_id,
                "activity_started_at": lap.activity_started_at.isoformat(),
                "lap_index": lap.lap_index,
                "name": lap.name,
                "elapsed_seconds": lap.elapsed_seconds,
                "moving_seconds": lap.moving_seconds,
                "distance_meters": lap.distance_meters,
                "total_elevation_gain_meters": lap.total_elevation_gain_meters,
                "average_speed_mps": lap.average_speed_mps,
                "max_speed_mps": lap.max_speed_mps,
                "average_heartrate_bpm": lap.average_heartrate_bpm,
                "max_heartrate_bpm": lap.max_heartrate_bpm,
                "average_cadence_per_minute": lap.average_cadence_per_minute,
            }
            for lap in laps
        ]
        await _insert_rows(
            self._client,
            self._table,
            rows,
            [f"{lap.activity_id}:{lap.lap_index}" for lap in laps],
        )


class BigQueryActivityStreamStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save_many(self, points: Sequence[ActivityStreamPoint]) -> None:
        if any(point.activity_started_at is None for point in points):
            raise ValueError("Activity start time is required for stream storage")
        rows = [point.model_dump(mode="json") for point in points]
        await _insert_rows(
            self._client,
            self._table,
            rows,
            [f"{point.activity_id}:{point.sample_index}" for point in points],
        )


class BigQueryActivityMetricsStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save(self, metrics: ActivityMetrics) -> None:
        await _insert_rows(
            self._client,
            self._table,
            [metrics.model_dump(mode="json")],
            [f"{metrics.activity_id}:{metrics.computation_version}"],
        )

    async def get(self, activity_id: str) -> ActivityMetrics | None:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._table}` WHERE activity_id = @activity_id "
            "ORDER BY computed_at DESC LIMIT 1"
        )
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
        values = dict(rows[0].items())
        return ActivityMetrics.model_validate(values)


async def _insert_rows(
    client: object, table: str, rows: list[dict], row_ids: list[str]
) -> None:
    for offset in range(0, len(rows), 500):
        chunk = rows[offset : offset + 500]
        errors = await asyncio.to_thread(
            client.insert_rows_json,
            table,
            chunk,
            row_ids=row_ids[offset : offset + 500],
        )
        if errors:
            raise RuntimeError(f"BigQuery insert failed for {table}")
