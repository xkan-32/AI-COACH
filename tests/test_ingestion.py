from datetime import UTC, datetime

import pytest

from app.condition import InMemoryActivityContextStore
from app.domain.models import Activity
from app.ingestion import (
    ActivityIngestionService,
    InMemoryActivityStore,
    UnknownAthleteToken,
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
