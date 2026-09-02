import asyncio
from collections.abc import Sequence
from typing import Protocol

from app.domain.models import (
    ActivityLap,
    ActivityMetrics,
    ActivitySegmentMetrics,
    ActivityStreamPoint,
    RouteComparisonSummary,
    RouteFingerprint,
)


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


class ActivitySegmentStore(Protocol):
    async def save_many(self, segments: Sequence[ActivitySegmentMetrics]) -> None: ...
    async def list_by_activity(
        self, activity_id: str
    ) -> list[ActivitySegmentMetrics]: ...
    async def list_high_load(
        self, activity_id: str, limit: int
    ) -> list[ActivitySegmentMetrics]: ...


class RouteFingerprintStore(Protocol):
    async def save(self, fingerprint: RouteFingerprint) -> None: ...
    async def list_recent_same_route(
        self,
        athlete_id: str,
        route_hash: str,
        exclude_activity_id: str,
        limit: int,
    ) -> list[RouteFingerprint]: ...


class RouteComparisonStore(Protocol):
    async def save(self, comparison: RouteComparisonSummary) -> None: ...
    async def get(self, activity_id: str) -> RouteComparisonSummary | None: ...


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


class InMemoryActivitySegmentStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, int], ActivitySegmentMetrics] = {}

    async def save_many(self, segments: Sequence[ActivitySegmentMetrics]) -> None:
        for segment in segments:
            self.items[(segment.activity_id, segment.segment_index)] = segment

    async def list_by_activity(self, activity_id: str) -> list[ActivitySegmentMetrics]:
        return sorted(
            [
                segment
                for (stored_id, _), segment in self.items.items()
                if stored_id == activity_id
            ],
            key=lambda item: item.segment_index,
        )

    async def list_high_load(
        self, activity_id: str, limit: int
    ) -> list[ActivitySegmentMetrics]:
        segments = await self.list_by_activity(activity_id)
        return sorted(
            [item for item in segments if item.high_load_reasons],
            key=lambda item: item.relative_load_rank_percentile or 0,
            reverse=True,
        )[:limit]


class InMemoryRouteFingerprintStore:
    def __init__(self) -> None:
        self.items: dict[str, RouteFingerprint] = {}

    async def save(self, fingerprint: RouteFingerprint) -> None:
        self.items[fingerprint.activity_id] = fingerprint

    async def list_recent_same_route(
        self,
        athlete_id: str,
        route_hash: str,
        exclude_activity_id: str,
        limit: int,
    ) -> list[RouteFingerprint]:
        matches = [
            item
            for activity_id, item in self.items.items()
            if activity_id != exclude_activity_id
            and item.athlete_id == athlete_id
            and item.route_hash == route_hash
        ]
        return sorted(matches, key=lambda item: item.activity_started_at, reverse=True)[
            :limit
        ]


class InMemoryRouteComparisonStore:
    def __init__(self) -> None:
        self.items: dict[str, RouteComparisonSummary] = {}

    async def save(self, comparison: RouteComparisonSummary) -> None:
        self.items[comparison.activity_id] = comparison

    async def get(self, activity_id: str) -> RouteComparisonSummary | None:
        return self.items.get(activity_id)


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


class BigQueryActivitySegmentStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save_many(self, segments: Sequence[ActivitySegmentMetrics]) -> None:
        rows = [segment.model_dump(mode="json") for segment in segments]
        await _insert_rows(
            self._client,
            self._table,
            rows,
            [
                f"{item.activity_id}:{item.segment_index}:{item.computation_version}"
                for item in segments
            ],
        )

    async def list_by_activity(self, activity_id: str) -> list[ActivitySegmentMetrics]:
        return await self._query(
            "activity_id = @activity_id ORDER BY segment_index",
            [("activity_id", "STRING", activity_id)],
        )

    async def list_high_load(
        self, activity_id: str, limit: int
    ) -> list[ActivitySegmentMetrics]:
        return await self._query(
            "activity_id = @activity_id AND ARRAY_LENGTH(high_load_reasons) > 0 "
            "ORDER BY relative_load_rank_percentile DESC LIMIT @limit",
            [
                ("activity_id", "STRING", activity_id),
                ("limit", "INT64", limit),
            ],
        )

    async def _query(
        self, predicate: str, parameters: list[tuple[str, str, object]]
    ) -> list[ActivitySegmentMetrics]:
        from google.cloud import bigquery

        query = f"SELECT * FROM `{self._table}` WHERE {predicate}"
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, kind, value)
                for name, kind, value in parameters
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        return [
            ActivitySegmentMetrics.model_validate(dict(row.items())) for row in rows
        ]


class BigQueryRouteFingerprintStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save(self, fingerprint: RouteFingerprint) -> None:
        await _insert_rows(
            self._client,
            self._table,
            [fingerprint.model_dump(mode="json")],
            [f"{fingerprint.activity_id}:{fingerprint.fingerprint_version}"],
        )

    async def list_recent_same_route(
        self,
        athlete_id: str,
        route_hash: str,
        exclude_activity_id: str,
        limit: int,
    ) -> list[RouteFingerprint]:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._table}` "
            "WHERE athlete_id = @athlete_id AND route_hash = @route_hash "
            "AND activity_id != @exclude_activity_id "
            "ORDER BY activity_started_at DESC LIMIT @limit"
        )
        parameters = [
            bigquery.ScalarQueryParameter("athlete_id", "STRING", athlete_id),
            bigquery.ScalarQueryParameter("route_hash", "STRING", route_hash),
            bigquery.ScalarQueryParameter(
                "exclude_activity_id", "STRING", exclude_activity_id
            ),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
        rows = await asyncio.to_thread(
            lambda: list(
                self._client.query(
                    query,
                    job_config=bigquery.QueryJobConfig(query_parameters=parameters),
                ).result()
            )
        )
        return [RouteFingerprint.model_validate(dict(row.items())) for row in rows]


class BigQueryRouteComparisonStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save(self, comparison: RouteComparisonSummary) -> None:
        await _insert_rows(
            self._client,
            self._table,
            [comparison.model_dump(mode="json")],
            [f"{comparison.activity_id}:{comparison.comparison_version}"],
        )

    async def get(self, activity_id: str) -> RouteComparisonSummary | None:
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
        return (
            RouteComparisonSummary.model_validate(dict(rows[0].items()))
            if rows
            else None
        )


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
