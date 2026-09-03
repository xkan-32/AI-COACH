import json
from datetime import UTC, date, datetime, time, timedelta

from fastapi.testclient import TestClient

from app.condition import InMemoryConditionReportStore
from app.domain.models import (
    Activity,
    ConditionLevel,
    ConditionReport,
    Goal,
    GoalPriority,
    TrainingEnvironment,
    TrainingEnvironmentCategory,
)
from app.ingestion import InMemoryActivityStore
from app.main import app
from app.plan_generation import (
    WeeklyPlanGenerationService,
    WeeklyPlanOutput,
    WeeklyWorkoutOutput,
)
from app.planning import (
    AvailabilitySlot,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    SafetyGateStatus,
    TrainingPlanStatus,
    TrainingSettingsService,
    UserTrainingProfile,
    WeeklyAvailabilityVersion,
)
from app.profile import InMemoryGoalStore, InMemoryTrainingResourceStore

NOW = datetime(2026, 9, 5, 0, tzinfo=UTC)
WEEK_START = date(2026, 9, 7)


class CapturingGenerator:
    def __init__(self, output: WeeklyPlanOutput) -> None:
        self.output = output
        self.inputs: list[dict] = []

    async def generate(self, plan_input: dict) -> WeeklyPlanOutput:
        self.inputs.append(plan_input)
        return self.output


class FailingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, plan_input: dict) -> WeeklyPlanOutput:
        self.calls += 1
        raise RuntimeError("provider unavailable")


def valid_output(duration: int = 20) -> WeeklyPlanOutput:
    return WeeklyPlanOutput(
        plan_rationale="負荷日を分散し、利用可能時間内で継続性を優先します。",
        workouts=[
            WeeklyWorkoutOutput(
                scheduled_date=WEEK_START + timedelta(days=offset),
                workout_type="イージーラン",
                template_id="run-easy-v1",
                target_duration_minutes=duration,
                target_intensity="easy",
                availability_slot_id=f"slot-{offset}",
                scheduled_start_local_time=time(6),
                environment_ids=["park"],
                outdoors=True,
                rationale="朝の利用可能時間内で行う軽い有酸素運動です。",
            )
            for offset in range(7)
        ],
    )


async def build_service(generator, *, add_evening_slot: bool = False):
    settings_store = InMemoryTrainingSettingsStore()
    settings = TrainingSettingsService(settings_store, settings_store)
    await settings.save_profile(
        UserTrainingProfile(
            user_id="line-1",
            provider_athlete_id="athlete-1",
            operation_id="profile-op",
            updated_at=NOW,
        ),
        expected_version=None,
    )
    availability = WeeklyAvailabilityVersion(
        id="availability-1",
        user_id="line-1",
        timezone="Asia/Tokyo",
        version=1,
        slots=[
            AvailabilitySlot(
                id=f"slot-{weekday}",
                weekday=weekday,
                start_local_time=time(6),
                end_local_time=time(7),
                max_workout_minutes=60,
                environment_ids=["park"],
            )
            for weekday in range(7)
        ]
        + (
            [
                AvailabilitySlot(
                    id="slot-0-evening",
                    weekday=0,
                    start_local_time=time(20),
                    end_local_time=time(21),
                    max_workout_minutes=60,
                    environment_ids=["indoor"],
                    outdoors_allowed=False,
                )
            ]
            if add_evening_slot
            else []
        ),
        operation_id="availability-op",
        created_at=NOW,
    )
    await settings.save_availability(availability, expected_version=None)
    goals = InMemoryGoalStore()
    await goals.save(
        "line-1",
        Goal(
            id="goal-1",
            goal_type="タイム・距離",
            target="10kmを60分以内",
            priority=GoalPriority.PRIMARY,
        ),
    )
    environments = InMemoryTrainingResourceStore()
    await environments.save(
        "line-1",
        TrainingEnvironment(
            id="park",
            display_name="公園",
            category=TrainingEnvironmentCategory.ACTIVITY_PLACE,
        ),
    )
    if add_evening_slot:
        await environments.save(
            "line-1",
            TrainingEnvironment(
                id="indoor",
                display_name="インドアバイク",
                category=TrainingEnvironmentCategory.ACTIVITY_PLACE,
            ),
        )
    activities = InMemoryActivityStore()
    await activities.save(
        Activity(
            id="activity-1",
            athlete_id="athlete-1",
            activity_type="Run",
            started_at=NOW - timedelta(days=2),
            duration_seconds=7_200,
            distance_meters=15_000,
            description="保存済み説明に位置情報らしき自由記述",
        )
    )
    conditions = InMemoryConditionReportStore()
    await conditions.save(
        ConditionReport(
            athlete_id="athlete-1",
            activity_id="activity-1",
            level=ConditionLevel.GOOD,
            comment="AI入力へ送らない体調自由記述",
            reported_at=NOW - timedelta(days=1),
        )
    )
    history = InMemoryPlanningHistoryStore()
    return (
        WeeklyPlanGenerationService(
            generator,
            history,
            settings,
            goals,
            environments,
            activities,
            conditions,
            "test-model",
        ),
        history,
    )


async def generate(service: WeeklyPlanGenerationService):
    return await service.generate_shadow_plan(
        user_id="line-1",
        line_user_id="line-1",
        week_start=WEEK_START,
        plan_version=1,
        generation_reason="scheduled_shadow",
        input_revision="settings-1",
        operation_id="generation-op",
        now=NOW,
    )


async def test_valid_shadow_plan_is_saved_without_sensitive_free_text() -> None:
    generator = CapturingGenerator(valid_output())
    service, history = await build_service(generator)

    result = await generate(service)

    plan = await history.get_plan(result.plan_id)
    workouts = await history.list_workouts(result.plan_id)
    encoded_input = json.dumps(generator.inputs[0], ensure_ascii=False)
    assert result.status == TrainingPlanStatus.DRAFT
    assert result.used_fallback is False
    assert plan is not None
    assert plan.plan_rationale.startswith("負荷日")
    assert len(workouts) == 7
    assert workouts[0].availability_slot_id == "slot-0"
    profiles = generator.inputs[0]["performance_profiles"]
    assert {item["sport"] for item in profiles} == {"running", "cycling"}
    assert profiles[0]["evidence_activity_ids"] == ["activity-1"]
    assert "位置情報らしき自由記述" not in encoded_input
    assert "体調自由記述" not in encoded_input
    assert history.safety_gate_results.popitem()[1].status == SafetyGateStatus.ALLOWED


async def test_unsafe_ai_output_is_replaced_by_conservative_fallback() -> None:
    generator = CapturingGenerator(valid_output(duration=120))
    service, history = await build_service(generator)

    result = await generate(service)

    plan = await history.get_plan(result.plan_id)
    workouts = await history.list_workouts(result.plan_id)
    assert result.used_fallback is True
    assert plan is not None
    assert "duration_exceeds_slot" in plan.safety_flags
    assert sum(item.target_duration_minutes or 0 for item in workouts) <= 180
    assert all(item.target_intensity in {"rest", "easy"} for item in workouts)


async def test_multiple_workouts_on_one_date_use_distinct_slots() -> None:
    output = valid_output()
    output.workouts.append(
        WeeklyWorkoutOutput(
            scheduled_date=WEEK_START,
            workout_type="自重・全身ベーシック",
            template_id="bodyweight-full-v1",
            target_duration_minutes=20,
            target_intensity="easy",
            availability_slot_id="slot-0-evening",
            scheduled_start_local_time=time(20),
            environment_ids=["indoor"],
            outdoors=False,
            rationale="夜の屋内時間枠に合わせた補強運動です。",
        )
    )
    service, history = await build_service(
        CapturingGenerator(output), add_evening_slot=True
    )

    result = await generate(service)

    workouts = await history.list_workouts(result.plan_id)
    assert result.used_fallback is False
    assert len(workouts) == 8
    assert {
        item.availability_slot_id
        for item in workouts
        if item.scheduled_date == WEEK_START
    } == {"slot-0", "slot-0-evening"}


async def test_multiple_workouts_in_unsplittable_slot_use_fallback() -> None:
    output = valid_output()
    output.workouts.append(
        WeeklyWorkoutOutput(
            scheduled_date=WEEK_START,
            workout_type="easy_strength",
            target_duration_minutes=20,
            target_intensity="easy",
            availability_slot_id="slot-0",
            scheduled_start_local_time=time(6, 30),
            environment_ids=["park"],
            outdoors=False,
            rationale="同じ枠へ重ねた補強運動です。",
        )
    )
    service, history = await build_service(CapturingGenerator(output))

    result = await generate(service)

    plan = await history.get_plan(result.plan_id)
    assert result.used_fallback is True
    assert plan is not None
    assert "multiple_workouts_in_unsplittable_slot" in plan.safety_flags


async def test_generator_failure_falls_back_and_retry_is_idempotent() -> None:
    generator = FailingGenerator()
    service, history = await build_service(generator)

    first = await generate(service)
    second = await generate(service)

    assert first == second
    assert first.used_fallback is True
    assert generator.calls == 1
    assert len(history.plans) == 1
    assert len(history.workouts) == 7
    assert len(history.lifecycle_events) == 1


def test_manual_shadow_worker_endpoint_is_idempotent() -> None:
    payload = {
        "user_id": "endpoint-user-unique",
        "line_user_id": "endpoint-user-unique",
        "week_start": "2026-09-07",
        "plan_version": 1,
        "generation_reason": "manual_shadow",
        "input_revision": "endpoint-revision-1",
        "operation_id": "endpoint-op-1",
        "requested_at": "2026-09-05T00:00:00Z",
    }
    with TestClient(app) as client:
        first = client.post("/tasks/plans/generate", json=payload)
        second = client.post("/tasks/plans/generate", json=payload)

    assert first.status_code == 202
    assert first.json()["status"] == "completed"
    assert first.json()["plan_status"] == "draft"
    assert first.json()["workout_count"] == 7
    assert second.status_code == 202
    assert second.json() == {"status": "duplicate"}


def test_manual_shadow_worker_rejects_naive_requested_at() -> None:
    payload = {
        "user_id": "naive-time-user",
        "line_user_id": "naive-time-user",
        "week_start": "2026-09-07",
        "plan_version": 1,
        "generation_reason": "manual_shadow",
        "input_revision": "naive-time-revision",
        "operation_id": "naive-time-op",
        "requested_at": "2026-09-05T00:00:00",
    }

    with TestClient(app) as client:
        response = client.post("/tasks/plans/generate", json=payload)

    assert response.status_code == 422
