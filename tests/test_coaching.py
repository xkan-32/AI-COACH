from datetime import UTC, datetime

from app.coaching import (
    CoachingService,
    CoachOutput,
    InMemoryProposalStore,
    enforce_safety,
)
from app.domain.models import Activity, ConditionLevel, ConditionReport
from app.line import InMemoryConditionPromptSender


def activity() -> Activity:
    return Activity(
        id="activity-1",
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_seconds=3600,
        distance_meters=10000,
    )


def report(level: ConditionLevel) -> ConditionReport:
    return ConditionReport(
        athlete_id="athlete-1",
        activity_id="activity-1",
        level=level,
        reported_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def unsafe_output() -> CoachOutput:
    return CoachOutput(
        title="高強度ラン",
        rationale="モデル提案",
        duration_minutes=90,
        intensity="moderate",
    )


def test_pain_forces_rest_regardless_of_model_output() -> None:
    safe = enforce_safety(unsafe_output(), report(ConditionLevel.PAIN))
    assert safe.title == "休養"
    assert safe.duration_minutes == 0
    assert safe.intensity == "rest"
    assert any("Do not prescribe running" in note for note in safe.safety_notes)


def test_discomfort_caps_duration_and_intensity() -> None:
    safe = enforce_safety(unsafe_output(), report(ConditionLevel.DISCOMFORT))
    assert safe.duration_minutes == 45
    assert safe.intensity == "easy"


async def test_coaching_service_saves_then_sends_safe_proposal() -> None:
    class UnsafeGenerator:
        async def generate(self, activity, report, context):
            return unsafe_output()

    proposals = InMemoryProposalStore()
    sender = InMemoryConditionPromptSender()
    service = CoachingService(UnsafeGenerator(), proposals, sender)
    proposal = await service.create_proposal(
        activity(), report(ConditionLevel.PAIN), "line-user"
    )
    assert proposal.status.value == "pending"
    assert proposal.duration_minutes == 0
    assert proposals.items[proposal.id] == proposal
    assert sender.proposals == [("line-user", proposal)]
