from datetime import UTC, datetime

from app.coaching import (
    CoachingService,
    CoachOutput,
    InMemoryProposalStore,
    build_coaching_input,
    enforce_safety,
)
from app.domain.models import (
    Activity,
    ActivityMetrics,
    CoachingContext,
    ConditionLevel,
    ConditionReport,
    WorkoutProposal,
)
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


def test_coaching_input_contains_metrics_but_no_raw_location_data() -> None:
    context = CoachingContext(
        recent_activities=[activity()],
        current_activity_metrics=ActivityMetrics(
            activity_id="activity-1",
            athlete_id="athlete-1",
            computation_version="v1",
            metric_quality="partial_streams",
            average_pace_seconds_per_km=360,
            ascent_meters=120,
        ),
    )

    payload = build_coaching_input(activity(), report(ConditionLevel.GOOD), context)

    assert (
        payload["coaching_context"]["current_activity_metrics"]["ascent_meters"] == 120
    )
    assert "latlng" not in str(payload)
    assert "stream_points" not in str(payload)


def test_legacy_workout_proposal_has_nullable_planning_links() -> None:
    proposal = WorkoutProposal.model_validate(
        {
            "id": "proposal-1",
            "athlete_id": "athlete-1",
            "source_activity_id": "activity-1",
            "target_date": "2026-09-03",
            "title": "休養",
            "rationale": "回復",
            "duration_minutes": 0,
            "intensity": "rest",
        }
    )

    assert proposal.plan_version_id is None
    assert proposal.planned_workout_id is None
    assert proposal.review_id is None
