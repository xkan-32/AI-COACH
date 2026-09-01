from datetime import date

import pytest

from app.approval import (
    ApprovalService,
    InMemoryProposalStateStore,
    ProposalDecisionTask,
    ProposalOwnerMismatch,
)
from app.coaching import InMemoryProposalStore
from app.domain.models import Activity, WorkoutProposal
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


async def test_decision_rejects_different_line_user() -> None:
    service, _, _ = await setup_service()
    with pytest.raises(ProposalOwnerMismatch):
        await service.decide(ProposalDecisionTask("proposal-1", "attacker", "approve"))
