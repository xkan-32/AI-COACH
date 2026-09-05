from datetime import UTC, date, datetime

import pytest

from app.domain.models import Activity
from app.evaluation import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    BigQueryEvaluationStore,
    InMemoryEvaluationPublicationStore,
    PublicationState,
    create_evaluation,
    merge_managed_block,
    render_managed_block,
)
from app.planning import (
    ReconciliationStatus,
    create_plan_version,
    create_planned_workout,
    create_reconciliation,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class FakeBigQueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict], list[str]]] = []

    def insert_rows_json(
        self, table: str, rows: list[dict], row_ids: list[str]
    ) -> list:
        self.calls.append((table, rows, row_ids))
        return []


def _activity(**changes) -> Activity:
    values = {
        "id": "activity-1",
        "athlete_id": "athlete-1",
        "user_id": "line-1",
        "activity_type": "Run",
        "started_at": NOW,
        "duration_seconds": 1800,
        "distance_meters": 5000,
        "average_heartrate_bpm": 145,
    }
    values.update(changes)
    return Activity(**values)


def _workout():
    plan = create_plan_version(
        "line-1", "line-1", date(2026, 9, 7), 1, [], "test", athlete_id="athlete-1"
    )
    return create_planned_workout(
        plan,
        date(2026, 9, 8),
        0,
        "easy_run",
        "easy",
        target_duration_minutes=25,
        target_distance_meters=4000,
    )


def _reconciliation(workout, *, combined: bool = False):
    return create_reconciliation(
        workout,
        "strava",
        "workout-matcher-v2",
        "activity-1",
        status=ReconciliationStatus.MATCHED,
        confirmed=True,
        matching_evidence=["user_confirmed_planned_workout"]
        + (["combined_activity"] if combined else []),
        operation_id="selection",
        created_at=NOW,
    )


def test_single_planned_activity_has_plan_comparison_and_safe_missing_hr_advice() -> (
    None
):
    workout = _workout()
    evaluation = create_evaluation(
        _activity(average_heartrate_bpm=None), workout, _reconciliation(workout)
    )

    assert evaluation.plan_comparison == ["時間差 +5.0分", "距離差 +1000m"]
    assert evaluation.load_summary["load_band"] == "low"
    assert evaluation.safety_corrections == ["heartrate_missing"]


def test_combined_activity_does_not_allocate_activity_numbers_to_each_workout() -> None:
    workout = _workout()
    evaluation = create_evaluation(
        _activity(), workout, _reconciliation(workout, combined=True)
    )

    assert evaluation.combined_activity is True
    assert evaluation.plan_comparison == []
    assert evaluation.actual_summary["distance_meters"] == 5000
    assert "予定別の数値配分" in render_managed_block(evaluation)


def test_confirmed_unplanned_activity_is_evaluated_without_plan_comparison() -> None:
    reconciliation = create_reconciliation(
        None,
        "strava",
        "workout-matcher-v2",
        "activity-1",
        user_id="line-1",
        athlete_id="athlete-1",
        status=ReconciliationStatus.UNPLANNED,
        confirmed=True,
        matching_evidence=["user_confirmed_unplanned"],
        operation_id="selection-unplanned",
        created_at=NOW,
    )

    evaluation = create_evaluation(_activity(), None, reconciliation)

    assert evaluation.planned_workout_id is None
    assert evaluation.plan_comparison == []
    assert "計画外として記録" in render_managed_block(evaluation)


def test_managed_description_block_replaces_only_its_own_content() -> None:
    workout = _workout()
    block = render_managed_block(
        create_evaluation(_activity(), workout, _reconciliation(workout))
    )
    original = (
        "自分で書いた説明\n\n"
        + MANAGED_BLOCK_START
        + "\n古い評価\n"
        + MANAGED_BLOCK_END
        + "\n末尾メモ"
    )

    updated = merge_managed_block(original, block)

    assert "自分で書いた説明" in updated
    assert "末尾メモ" in updated
    assert updated.count(MANAGED_BLOCK_START) == 1
    assert "古い評価" not in updated


async def test_publication_state_claim_is_idempotent_and_records_failure() -> None:
    workout = _workout()
    evaluation = create_evaluation(_activity(), workout, _reconciliation(workout))
    store = InMemoryEvaluationPublicationStore()

    assert await store.claim(evaluation) is True
    assert await store.claim(evaluation) is False
    await store.fail(evaluation, "strava_http_status_401")

    assert store.items[evaluation.id].state == PublicationState.FAILED
    assert store.items[evaluation.id].error_code == "strava_http_status_401"
    assert await store.claim(evaluation) is True


async def test_bigquery_evaluation_store_serializes_json_columns() -> None:
    workout = _workout()
    evaluation = create_evaluation(_activity(), workout, _reconciliation(workout))
    client = FakeBigQueryClient()

    await BigQueryEvaluationStore(client, "project.dataset.activity_evaluations").save(
        evaluation
    )

    _, rows, row_ids = client.calls[0]
    assert rows[0]["actual_summary"] == (
        '{"duration_minutes":30.0,"distance_meters":5000.0,'
        '"average_heartrate_bpm":145.0,"max_heartrate_bpm":null,'
        '"elevation_gain_meters":null}'
    )
    assert rows[0]["load_summary"].startswith('{"load_score":30.0')
    assert row_ids == [evaluation.id]


def test_unconfirmed_activity_is_rejected() -> None:
    workout = _workout()
    reconciliation = _reconciliation(workout).model_copy(update={"confirmed": False})

    with pytest.raises(ValueError, match="confirmed"):
        create_evaluation(_activity(), workout, reconciliation)
