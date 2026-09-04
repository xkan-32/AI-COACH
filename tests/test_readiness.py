from datetime import UTC, date, datetime

import pytest

from app.domain.models import Activity, ActivitySource, ConditionLevel, ConditionReport
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    ReadinessStatus,
    ReconciliationStatus,
    UserTrainingProfile,
    create_plan_version,
    create_planned_workout,
    create_reconciliation,
)
from app.readiness import (
    InMemoryActiveReadinessPointerStore,
    ReadinessOutput,
    WorkoutFeedbackService,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class CapturingGenerator:
    model_name = "test-model"

    def __init__(self, status=ReadinessStatus.AS_PLANNED) -> None:
        self.status = status
        self.inputs = []

    async def generate(self, input_snapshot):
        self.inputs.append(input_snapshot)
        return ReadinessOutput(
            status=self.status,
            reason_codes=["model_assessment"],
            display_reason="モデル判定",
        )


async def setup_feedback(next_type="easy_run"):
    history = InMemoryPlanningHistoryStore()
    plans = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    readiness = InMemoryActiveReadinessPointerStore()
    await settings.save_profile(
        UserTrainingProfile(
            user_id="line-1",
            timezone="Asia/Tokyo",
            operation_id="profile-1",
            updated_at=NOW,
        ),
        None,
    )
    plan = create_plan_version(
        "line-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [],
        "test",
        athlete_id="athlete-1",
        created_at=NOW,
    )
    completed = create_planned_workout(
        plan,
        date(2026, 9, 8),
        0,
        "run",
        "easy",
        target_duration_minutes=30,
        created_at=NOW,
    )
    upcoming = create_planned_workout(
        plan,
        date(2026, 9, 9),
        1,
        next_type,
        "easy" if next_type != "rest" else "rest",
        target_duration_minutes=30 if next_type != "rest" else 0,
        created_at=NOW,
    )
    await history.save_plan(plan)
    await history.save_workouts([completed, upcoming])
    await plans.set("line-1", date(2026, 9, 7), plan.id, None)
    return history, plans, settings, readiness, completed, upcoming


def activity(activity_id="activity-1"):
    return Activity(
        id=activity_id,
        athlete_id="athlete-1",
        user_id="line-1",
        source_type=ActivitySource.STRAVA,
        activity_type="Run",
        started_at=NOW,
        duration_seconds=1800,
        distance_meters=5000,
        description="private description",
        details="private health note",
    )


def report(level=ConditionLevel.GOOD, **values):
    return ConditionReport(
        athlete_id="athlete-1",
        activity_id=values.pop("activity_id", "activity-1"),
        level=level,
        reported_at=NOW,
        **values,
    )


async def reconcile(history, workout, actual, status=ReconciliationStatus.MATCHED):
    item = create_reconciliation(
        workout,
        "strava",
        "test-matcher",
        actual.id,
        operation_id=f"reconcile:{actual.id}",
        status=status,
        confirmed=True,
        objective_factors=["duration_within_tolerance"],
    )
    await history.save_reconciliation(item)
    return item


async def test_review_and_readiness_keep_separate_factors_and_safe_input() -> None:
    history, plans, settings, pointers, completed, upcoming = await setup_feedback()
    actual = activity()
    await reconcile(history, completed, actual)
    generator = CapturingGenerator()
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, generator, clock=lambda: NOW
    )

    result = await service.evaluate(
        actual,
        report(
            ConditionLevel.DISCOMFORT,
            body_part="secret knee text",
            severity=8,
            comment="secret free text",
        ),
    )

    assert result.review.achievement_status.value == "achieved"
    assert result.review.objective_factors == ["duration_within_tolerance"]
    assert result.review.condition_factors == [
        "condition:discomfort",
        "severity:high",
    ]
    assert result.next_workout == upcoming
    assert result.assessment.status == ReadinessStatus.WITH_ADJUSTMENT
    serialized = str(generator.inputs)
    assert "secret" not in serialized
    assert "description" not in serialized
    assert "details" not in serialized
    assert "route_hash" not in serialized
    assert "stream" not in serialized


async def test_missing_condition_uses_healthy_default_and_calls_model() -> None:
    history, plans, settings, pointers, completed, _ = await setup_feedback()
    actual = activity()
    await reconcile(history, completed, actual)
    generator = CapturingGenerator()
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, generator, clock=lambda: NOW
    )

    result = await service.evaluate(actual, None)

    assert result.assessment.status == ReadinessStatus.AS_PLANNED
    assert "condition_healthy_default" in result.assessment.reason_codes
    assert len(generator.inputs) == 1


async def test_pain_blocks_model_and_block_remains_sticky() -> None:
    history, plans, settings, pointers, completed, _ = await setup_feedback()
    actual = activity()
    await reconcile(history, completed, actual)
    generator = CapturingGenerator()
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, generator, clock=lambda: NOW
    )

    blocked = await service.evaluate(actual, report(ConditionLevel.PAIN), "pain")
    later = await service.evaluate(actual, report(ConditionLevel.GOOD), "later")

    assert blocked.assessment.status == ReadinessStatus.BLOCKED
    assert later.assessment.status == ReadinessStatus.BLOCKED
    assert "previous_safety_block" in later.assessment.reason_codes
    assert generator.inputs == []


async def test_same_day_activities_append_revision_and_retry_is_idempotent() -> None:
    history, plans, settings, pointers, completed, _ = await setup_feedback()
    first = activity("activity-1")
    second = activity("activity-2")
    await reconcile(history, completed, first)
    await reconcile(history, completed, second, ReconciliationStatus.PARTIAL)
    generator = CapturingGenerator()
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, generator, clock=lambda: NOW
    )

    one = await service.evaluate(first, report(activity_id=first.id), "first")
    two = await service.evaluate(second, report(activity_id=second.id), "second")
    retry = await service.evaluate(first, report(activity_id=first.id), "first")

    assert one.assessment.revision == 1
    assert two.assessment.revision == 2
    assert set(two.assessment.referenced_review_ids) == {
        one.review.id,
        two.review.id,
    }
    assert retry.assessment.id == one.assessment.id
    active = await pointers.get("line-1", date(2026, 9, 8), two.next_workout.id)
    assert active == two.assessment.id
    assert len(history.readiness_assessments) == 2


async def test_later_condition_answer_appends_review_without_mutating_healthy_default() -> (
    None
):
    history, plans, settings, pointers, completed, _ = await setup_feedback()
    actual = activity()
    await reconcile(history, completed, actual)
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, CapturingGenerator(), clock=lambda: NOW
    )

    missing = await service.evaluate(actual, None, "grace-period")
    answered = await service.evaluate(actual, report(), "condition-answer")

    assert missing.review.condition_factors == ["condition:healthy_default"]
    assert answered.review.condition_factors == ["condition:good"]
    assert answered.review.supersedes_review_id == missing.review.id
    assert missing.review.condition_factors == ["condition:healthy_default"]
    assert answered.assessment.referenced_review_ids == [answered.review.id]


async def test_rest_day_is_preserved_as_next_planned_workout() -> None:
    history, plans, settings, pointers, completed, rest = await setup_feedback("rest")
    actual = activity()
    await reconcile(history, completed, actual)
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, CapturingGenerator(), clock=lambda: NOW
    )

    result = await service.evaluate(actual, report())

    assert result.next_workout == rest
    assert result.next_workout.workout_type == "rest"


async def test_provider_failure_falls_back_conservatively() -> None:
    class FailingGenerator:
        model_name = "failing-model"

        async def generate(self, input_snapshot):
            raise TimeoutError("provider unavailable")

    history, plans, settings, pointers, completed, _ = await setup_feedback()
    actual = activity()
    await reconcile(history, completed, actual)
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, FailingGenerator(), clock=lambda: NOW
    )

    result = await service.evaluate(actual, report())

    assert result.assessment.status == ReadinessStatus.NEEDS_INFORMATION
    assert "readiness_provider_unavailable" in result.assessment.reason_codes


async def test_retry_repairs_pointer_after_history_was_saved() -> None:
    class FailOncePointer(InMemoryActiveReadinessPointerStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        async def set(self, *args, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient pointer failure")
            await super().set(*args, **kwargs)

    history, plans, settings, _, completed, upcoming = await setup_feedback()
    pointers = FailOncePointer()
    actual = activity()
    await reconcile(history, completed, actual)
    service = WorkoutFeedbackService(
        history, plans, settings, pointers, CapturingGenerator(), clock=lambda: NOW
    )

    with pytest.raises(RuntimeError, match="transient pointer failure"):
        await service.evaluate(actual, report(), "recoverable-operation")
    recovered = await service.evaluate(actual, report(), "recoverable-operation")

    assert len(history.readiness_assessments) == 1
    assert await pointers.get("line-1", date(2026, 9, 8), upcoming.id) == (
        recovered.assessment.id
    )
