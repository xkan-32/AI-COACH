from datetime import UTC, datetime

import pytest

from app.activity_data import BigQueryActivityStreamStore
from app.domain.models import ActivityStreamPoint


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict], list[str]]] = []

    def insert_rows_json(
        self, table: str, rows: list[dict], row_ids: list[str]
    ) -> list:
        self.calls.append((table, rows, row_ids))
        return []


async def test_stream_store_uses_stable_ids_and_has_no_gps_fields() -> None:
    client = FakeBigQueryClient()
    store = BigQueryActivityStreamStore(client, "project.dataset.streams")
    point = ActivityStreamPoint(
        activity_id="activity-1",
        athlete_id="athlete-1",
        activity_started_at=datetime(2026, 9, 2, tzinfo=UTC),
        sample_index=7,
        time_seconds=70,
        altitude_meters=123.4,
    )

    await store.save_many([point])

    _, rows, row_ids = client.calls[0]
    assert row_ids == ["activity-1:7"]
    assert rows[0]["altitude_meters"] == 123.4
    assert "latlng" not in rows[0]
    assert "latitude" not in rows[0]
    assert "longitude" not in rows[0]


async def test_stream_store_requires_partition_timestamp() -> None:
    store = BigQueryActivityStreamStore(FakeBigQueryClient(), "project.dataset.streams")
    point = ActivityStreamPoint(
        activity_id="activity-1",
        athlete_id="athlete-1",
        sample_index=0,
    )

    with pytest.raises(ValueError, match="start time"):
        await store.save_many([point])
