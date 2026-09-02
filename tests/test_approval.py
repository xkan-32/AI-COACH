from datetime import UTC, date, datetime

import pytest

from app.approval import (
    ApprovalService,
    InMemoryProposalStateStore,
    ProposalDecisionTask,
    ProposalExpired,
    ProposalOwnerMismatch,
)
from app.coaching import InMemoryProposalStore
from app.domain.models import Activity, ActivitySource, WorkoutProposal
from app.ingestion import InMemoryActivityStore
from app.state import InMemoryStravaTokenStore
from app.strava import StoredStravaToken


def proposal() -> WorkoutProposal:
    return WorkoutProposal(
        id="proposal-1",
        athlete_id="athlete-1",
        source_activity_id="activity-1",
        target_date=date(2026, 9, 2),
        title="軽いジョグ",
        rationale="回復目的",
        duration_minutes=30,
        intensity="easy",
    )


class FakeStrava:
    def __init__(self, description: str = "既存メモ") -> None:
        self.description = description
        self.updates: list[str] = []

    async def get_activity(self, activity_id: str, access_token: str) -> Activity:
        from datetime import UTC, datetime

        return Activity(
            id=activity_id,
            athlete_id="athlete-1",
            activity_type="Run",
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
            duration_seconds=1800,
            distance_meters=5000,
            description=self.description,
        )

    async def update_description(
        self, activity_id: str, access_token: str, description: str
    ):
        self.description = description
        self.updates.append(description)


async def setup_service(strava=None):
    states = InMemoryProposalStateStore()
    analytics = InMemoryProposalStore()
    tokens = InMemoryStravaTokenStore()
    item = proposal()
    await states.save(item, "line-user")
    await analytics.save(item, "line-user")
    await tokens.save(
        StoredStravaToken("athlete-1", "line-user", "access", "refresh", 2_000_000_000)
    )
    return (
        ApprovalService(states, analytics, tokens, strava or FakeStrava()),
        states,
        analytics,
    )


async def test_approval_preserves_description_and_is_idempotent() -> None:
    strava = FakeStrava()
    service, _states, analytics = await setup_service(strava)
    task = ProposalDecisionTask("proposal-1", "line-user", "approve")
    assert await service.decide(task) == "approved"
    assert strava.description.startswith("既存メモ\n\n")
    assert strava.description.count("[AI-COACH:proposal-1]") == 1
    assert await service.decide(task) == "duplicate"
    assert len(strava.updates) == 1
    assert analytics.items["proposal-1"].status.value == "approved"


async def test_rejection_does_not_touch_strava() -> None:
    strava = FakeStrava()
    service, _, analytics = await setup_service(strava)
    result = await service.decide(
        ProposalDecisionTask("proposal-1", "line-user", "reject")
    )
    assert result == "rejected"
    assert strava.updates == []
    assert analytics.items["proposal-1"].status.value == "rejected"


async def test_manual_activity_approval_does_not_call_strava() -> None:
    strava = FakeStrava()
    activities = InMemoryActivityStore()
    await activities.save(
        Activity(
            id="activity-1",
            athlete_id="line-1",
            activity_type="WeightTraining",
            started_at=datetime(2026, 9, 2, tzinfo=UTC),
            duration_seconds=1800,
            distance_meters=0,
            source_type=ActivitySource.LINE_MANUAL,
        )
    )
    states = InMemoryProposalStateStore()
    analytics = InMemoryProposalStore()
    tokens = InMemoryStravaTokenStore()
    item = proposal()
    item.athlete_id = "line-1"
    await states.save(item, "line-user")
    await analytics.save(item, "line-user")
    service = ApprovalService(states, analytics, tokens, strava, activities=activities)
    assert (
        await service.decide(ProposalDecisionTask("proposal-1", "line-user", "approve"))
        == "recorded"
    )
    assert strava.updates == []
    assert analytics.items["proposal-1"].status.value == "approved"


async def test_missing_strava_token_does_not_raise_or_update_strava() -> None:
    strava = FakeStrava()
    states = InMemoryProposalStateStore()
    analytics = InMemoryProposalStore()
    tokens = InMemoryStravaTokenStore()
    item = proposal()
    await states.save(item, "line-user")
    await analytics.save(item, "line-user")
    service = ApprovalService(states, analytics, tokens, strava)
    result = await service.decide(
        ProposalDecisionTask("proposal-1", "line-user", "approve")
    )
    assert result == "missing_strava_link"
    assert strava.updates == []
    assert analytics.items["proposal-1"].status.value == "pending"


async def test_decision_rejects_different_line_user() -> None:
    service, _, _ = await setup_service()
    with pytest.raises(ProposalOwnerMismatch):
        await service.decide(ProposalDecisionTask("proposal-1", "attacker", "approve"))


async def test_expired_approval_does_not_touch_strava() -> None:
    from datetime import UTC, datetime, timedelta

    strava = FakeStrava()
    service, states, _ = await setup_service(strava)
    states.items["proposal-1"][0].expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ProposalExpired):
        await service.decide(ProposalDecisionTask("proposal-1", "line-user", "approve"))

    assert strava.updates == []
