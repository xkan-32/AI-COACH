from datetime import UTC, datetime

import pytest

from app.activity_data import (
    InMemoryActivityIngestionStateStore,
    InMemoryActivityLapStore,
    InMemoryActivityMetricsStore,
    InMemoryActivityStreamStore,
)
from app.condition import InMemoryActivityContextStore
from app.domain.models import Activity, ActivityLap, ActivityStreamPoint
from app.ingestion import (
    ActivityIngestionService,
    InMemoryActivityStore,
    UnknownAthleteToken,
    _supports_detailed_streams,
)
from app.line import InMemoryConditionPromptSender
from app.state import InMemoryStravaTokenStore
from app.strava import StoredStravaToken, StravaRefreshResponse


class FakeStrava:
    def __init__(self) -> None:
        self.refreshed = False
        self.access_token_used = ""

    async def refresh(self, refresh_token: str) -> StravaRefreshResponse:
        self.refreshed = True
        assert refresh_token == "old-refresh"
        return StravaRefreshResponse(
            token_type="Bearer",
            expires_at=20_000,
            expires_in=10_000,
            refresh_token="new-refresh",
            access_token="new-access",
        )

    async def get_activity(self, activity_id: str, access_token: str) -> Activity:
        self.access_token_used = access_token
        return Activity(
            id=activity_id,
            athlete_id="42",
            activity_type="Run",
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
            duration_seconds=1800,
            distance_meters=5000,
        )


@pytest.mark.parametrize(
    ("activity_type", "expected"),
    [
        ("Run", True),
        ("Walk", True),
        ("Ride", True),
        ("WeightTraining", False),
        ("Workout", False),
    ],
)
def test_detailed_stream_policy(activity_type: str, expected: bool) -> None:
    assert _supports_detailed_streams(activity_type) is expected


async def test_ingestion_refreshes_saves_then_prompts() -> None:
    tokens = InMemoryStravaTokenStore()
    await tokens.save(
        StoredStravaToken(
            athlete_id="42",
            line_user_id="line-user",
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=100,
        )
    )
    strava = FakeStrava()
    activities = InMemoryActivityStore()
    prompts = InMemoryConditionPromptSender()
    service = ActivityIngestionService(
        strava,
        tokens,
        activities,
        prompts,
        InMemoryActivityContextStore(),
        clock=lambda: 1_000,
    )

    activity = await service.ingest("99", "42")

    assert strava.refreshed
    assert strava.access_token_used == "new-access"
    assert activities.activities["99"] == activity
    assert prompts.sent == [("line-user", activity)]
    assert (await tokens.get("42")).refresh_token == "new-refresh"


async def test_ingestion_rejects_unknown_athlete() -> None:
    service = ActivityIngestionService(
        FakeStrava(),
        InMemoryStravaTokenStore(),
        InMemoryActivityStore(),
        InMemoryConditionPromptSender(),
        InMemoryActivityContextStore(),
    )
    with pytest.raises(UnknownAthleteToken):
        await service.ingest("99", "missing")


async def test_ingestion_rejects_owner_mismatch_before_saving() -> None:
    tokens = InMemoryStravaTokenStore()
    await tokens.save(
        StoredStravaToken("not-42", "line-user", "access", "refresh", 20_000)
    )
    activities = InMemoryActivityStore()
    prompts = InMemoryConditionPromptSender()
    service = ActivityIngestionService(
        FakeStrava(),
        tokens,
        activities,
        prompts,
        InMemoryActivityContextStore(),
        clock=lambda: 1_000,
    )
    with pytest.raises(ValueError, match="owner mismatch"):
        await service.ingest("99", "not-42")
    assert activities.activities == {}
    assert prompts.sent == []


async def test_detailed_ingestion_saves_metrics_without_duplicate_prompt() -> None:
    class DetailedStrava(FakeStrava):
        async def get_activity_laps(
            self,
            activity_id: str,
            athlete_id: str,
            activity_type: str,
            access_token: str,
        ) -> list[ActivityLap]:
            return [
                ActivityLap(
                    activity_id=activity_id,
                    athlete_id=athlete_id,
                    lap_index=0,
                    moving_seconds=300,
                    elapsed_seconds=310,
                    distance_meters=1000,
                )
            ]

        async def get_activity_streams(
            self, activity_id: str, athlete_id: str, access_token: str
        ) -> list[ActivityStreamPoint]:
            return [
                ActivityStreamPoint(
                    activity_id=activity_id,
                    athlete_id=athlete_id,
                    sample_index=index,
                    time_seconds=index * 10,
                    distance_meters=index * 30,
                    altitude_meters=100 + index,
                    velocity_mps=3 + index / 10,
                    heartrate_bpm=130 + index,
                    cadence_rpm=85 + index,
                    moving=True,
                    grade_percent=4 if index == 0 else 0,
                )
                for index in range(4)
            ]

    tokens = InMemoryStravaTokenStore()
    await tokens.save(StoredStravaToken("42", "line-user", "access", "refresh", 20_000))
    laps = InMemoryActivityLapStore()
    streams = InMemoryActivityStreamStore()
    metrics = InMemoryActivityMetricsStore()
    state = InMemoryActivityIngestionStateStore()
    prompts = InMemoryConditionPromptSender()
    service = ActivityIngestionService(
        DetailedStrava(),
        tokens,
        InMemoryActivityStore(),
        prompts,
        InMemoryActivityContextStore(),
        laps,
        streams,
        metrics,
        state,
        clock=lambda: 1_000,
    )

    await service.ingest("99", "42")
    await service.ingest("99", "42")

    assert len(laps.items) == 1
    assert len(streams.items) == 4
    assert metrics.items["99"].metric_quality == "full"
    assert metrics.items["99"].uphill_seconds == 10
    assert len(prompts.sent) == 1
    assert ("99", "metrics") in state.completed


async def test_partial_stream_failure_resumes_without_duplicate_laps() -> None:
    class FlakyDetailedStrava(FakeStrava):
        def __init__(self) -> None:
            super().__init__()
            self.stream_calls = 0

        async def get_activity_laps(
            self,
            activity_id: str,
            athlete_id: str,
            activity_type: str,
            access_token: str,
        ) -> list[ActivityLap]:
            return [
                ActivityLap(
                    activity_id=activity_id,
                    athlete_id=athlete_id,
                    lap_index=0,
                    elapsed_seconds=300,
                    moving_seconds=300,
                    distance_meters=1000,
                )
            ]

        async def get_activity_streams(
            self, activity_id: str, athlete_id: str, access_token: str
        ) -> list[ActivityStreamPoint]:
            self.stream_calls += 1
            if self.stream_calls == 1:
                raise RuntimeError("temporary stream failure")
            return []

    tokens = InMemoryStravaTokenStore()
    await tokens.save(StoredStravaToken("42", "line-user", "access", "refresh", 20_000))
    strava = FlakyDetailedStrava()
    laps = InMemoryActivityLapStore()
    state = InMemoryActivityIngestionStateStore()
    prompts = InMemoryConditionPromptSender()
    service = ActivityIngestionService(
        strava,
        tokens,
        InMemoryActivityStore(),
        prompts,
        InMemoryActivityContextStore(),
        laps,
        InMemoryActivityStreamStore(),
        InMemoryActivityMetricsStore(),
        state,
        clock=lambda: 1_000,
    )

    with pytest.raises(RuntimeError, match="temporary"):
        await service.ingest("99", "42")
    await service.ingest("99", "42")

    assert len(laps.items) == 1
    assert strava.stream_calls == 2
    assert len(prompts.sent) == 1
    assert ("99", "streams") in state.completed


async def test_failed_prompt_is_retried_before_marking_complete() -> None:
    class SummaryOnlyStrava(FakeStrava):
        async def get_activity(self, activity_id: str, access_token: str) -> Activity:
            activity = await super().get_activity(activity_id, access_token)
            return activity.model_copy(update={"activity_type": "WeightTraining"})

    class FlakyPrompt:
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, line_user_id: str, activity: Activity) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary LINE failure")

    tokens = InMemoryStravaTokenStore()
    await tokens.save(StoredStravaToken("42", "line-user", "access", "refresh", 20_000))
    state = InMemoryActivityIngestionStateStore()
    prompt = FlakyPrompt()
    service = ActivityIngestionService(
        SummaryOnlyStrava(),
        tokens,
        InMemoryActivityStore(),
        prompt,
        InMemoryActivityContextStore(),
        InMemoryActivityLapStore(),
        InMemoryActivityStreamStore(),
        InMemoryActivityMetricsStore(),
        state,
        clock=lambda: 1_000,
    )

    with pytest.raises(RuntimeError, match="LINE"):
        await service.ingest("99", "42")
    assert ("99", "prompt") not in state.completed

    await service.ingest("99", "42")

    assert prompt.calls == 2
    assert ("99", "prompt") in state.completed
