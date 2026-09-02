import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import Goal


class PlannedWorkoutStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    REPLACED = "replaced"


class ReconciliationStatus(StrEnum):
    MATCHED = "matched"
    PARTIAL = "partial"
    UNMATCHED = "unmatched"


class AchievementStatus(StrEnum):
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    NOT_ACHIEVED = "not_achieved"
    UNASSESSED = "unassessed"


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class GoalSnapshot(ImmutableModel):
    id: str
    goal_type: str
    target: str
    target_date: date | None = None
    priority: str
    status: str


class TrainingPlanVersion(ImmutableModel):
    id: str
    athlete_id: str
    line_user_id: str
    week_start: date
    version: int = Field(ge=1)
    goal_snapshot: list[GoalSnapshot]
    change_reason: str = Field(min_length=1)
    supersedes_plan_version_id: str | None = None
    safety_flags: list[str] = Field(default_factory=list)
    ai_model: str | None = None
    prompt_version: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlannedWorkout(ImmutableModel):
    id: str
    plan_version_id: str
    athlete_id: str
    scheduled_date: date
    sequence: int = Field(ge=0)
    workout_type: str = Field(min_length=1)
    target_duration_minutes: int | None = Field(default=None, ge=0)
    target_distance_meters: float | None = Field(default=None, ge=0)
    target_intensity: str
    environment_ids: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    status: PlannedWorkoutStatus = PlannedWorkoutStatus.PLANNED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkoutReconciliation(ImmutableModel):
    id: str
    plan_version_id: str
    planned_workout_id: str
    athlete_id: str
    source_type: str
    activity_id: str | None = None
    status: ReconciliationStatus
    duration_delta_minutes: float | None = None
    distance_delta_meters: float | None = None
    intensity_delta: str | None = None
    matcher_version: str
    objective_factors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkoutReview(ImmutableModel):
    id: str
    plan_version_id: str
    planned_workout_id: str
    reconciliation_id: str | None = None
    athlete_id: str
    achievement_status: AchievementStatus
    objective_factors: list[str] = Field(default_factory=list)
    condition_factors: list[str] = Field(default_factory=list)
    dialogue_factors: list[str] = Field(default_factory=list)
    feedback_codes: list[str] = Field(default_factory=list)
    rule_version: str
    ai_model: str | None = None
    prompt_version: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanningHistoryStore(Protocol):
    async def save_plan(self, plan: TrainingPlanVersion) -> None: ...
    async def get_plan(self, plan_id: str) -> TrainingPlanVersion | None: ...
    async def save_workouts(self, workouts: Sequence[PlannedWorkout]) -> None: ...
    async def save_reconciliation(
        self, reconciliation: WorkoutReconciliation
    ) -> None: ...
    async def save_review(self, review: WorkoutReview) -> None: ...


class ActivePlanPointerStore(Protocol):
    async def get(self, athlete_id: str, week_start: date) -> str | None: ...
    async def set(
        self,
        athlete_id: str,
        week_start: date,
        plan_version_id: str,
        expected_previous_id: str | None,
    ) -> None: ...


class PlanVersionConflict(ValueError):
    pass


class InMemoryPlanningHistoryStore:
    def __init__(self) -> None:
        self.plans: dict[str, TrainingPlanVersion] = {}
        self.workouts: dict[str, PlannedWorkout] = {}
        self.reconciliations: dict[str, WorkoutReconciliation] = {}
        self.reviews: dict[str, WorkoutReview] = {}

    async def save_plan(self, plan: TrainingPlanVersion) -> None:
        _save_immutable(self.plans, plan.id, plan)

    async def get_plan(self, plan_id: str) -> TrainingPlanVersion | None:
        return self.plans.get(plan_id)

    async def save_workouts(self, workouts: Sequence[PlannedWorkout]) -> None:
        for workout in workouts:
            _save_immutable(self.workouts, workout.id, workout)

    async def save_reconciliation(self, reconciliation: WorkoutReconciliation) -> None:
        _save_immutable(self.reconciliations, reconciliation.id, reconciliation)

    async def save_review(self, review: WorkoutReview) -> None:
        _save_immutable(self.reviews, review.id, review)


class InMemoryActivePlanPointerStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, date], str] = {}

    async def get(self, athlete_id: str, week_start: date) -> str | None:
        return self.items.get((athlete_id, week_start))

    async def set(
        self,
        athlete_id: str,
        week_start: date,
        plan_version_id: str,
        expected_previous_id: str | None,
    ) -> None:
        key = (athlete_id, week_start)
        if self.items.get(key) != expected_previous_id:
            raise PlanVersionConflict("Active training plan changed")
        self.items[key] = plan_version_id


class FirestoreActivePlanPointerStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _document(self, athlete_id: str, week_start: date):
        return self._client.collection("active_training_plans").document(
            f"{athlete_id}:{week_start.isoformat()}"
        )

    async def get(self, athlete_id: str, week_start: date) -> str | None:
        snapshot = await self._document(athlete_id, week_start).get()
        return str(snapshot.to_dict()["plan_version_id"]) if snapshot.exists else None

    async def set(
        self,
        athlete_id: str,
        week_start: date,
        plan_version_id: str,
        expected_previous_id: str | None,
    ) -> None:
        from google.cloud import firestore

        document = self._document(athlete_id, week_start)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def update(txn):
            snapshot = await document.get(transaction=txn)
            current = (
                snapshot.to_dict().get("plan_version_id") if snapshot.exists else None
            )
            if current != expected_previous_id:
                raise PlanVersionConflict("Active training plan changed")
            txn.set(
                document,
                {
                    "athlete_id": athlete_id,
                    "week_start": week_start.isoformat(),
                    "plan_version_id": plan_version_id,
                    "updated_at": datetime.now(UTC),
                },
            )

        await update(transaction)


class BigQueryPlanningHistoryStore:
    def __init__(self, client: object, table_prefix: str) -> None:
        self._client = client
        self._prefix = table_prefix

    async def save_plan(self, plan: TrainingPlanVersion) -> None:
        row = plan.model_dump(mode="json")
        await self._insert("training_plan_versions", row, plan.id)

    async def get_plan(self, plan_id: str) -> TrainingPlanVersion | None:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._prefix}.training_plan_versions` "
            "WHERE id = @id LIMIT 1"
        )
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", plan_id)]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        if not rows:
            return None
        values = dict(rows[0].items())
        return TrainingPlanVersion.model_validate(values)

    async def save_workouts(self, workouts: Sequence[PlannedWorkout]) -> None:
        for workout in workouts:
            await self._insert(
                "planned_workouts",
                workout.model_dump(mode="json"),
                workout.id,
            )

    async def save_reconciliation(self, reconciliation: WorkoutReconciliation) -> None:
        await self._insert(
            "workout_reconciliations",
            reconciliation.model_dump(mode="json"),
            reconciliation.id,
        )

    async def save_review(self, review: WorkoutReview) -> None:
        row = review.model_dump(mode="json")
        await self._insert("workout_reviews", row, review.id)

    async def _insert(self, table: str, row: dict, row_id: str) -> None:
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            f"{self._prefix}.{table}",
            [row],
            row_ids=[row_id],
        )
        if errors:
            raise RuntimeError(f"BigQuery insert failed for {table}")


class PlanningService:
    def __init__(
        self,
        history: PlanningHistoryStore,
        pointers: ActivePlanPointerStore,
    ) -> None:
        self._history = history
        self._pointers = pointers

    async def activate_version(
        self,
        plan: TrainingPlanVersion,
        workouts: Sequence[PlannedWorkout],
    ) -> None:
        current_id = await self._pointers.get(plan.athlete_id, plan.week_start)
        if current_id == plan.id:
            existing = await self._history.get_plan(plan.id)
            if existing != plan:
                raise PlanVersionConflict("Immutable plan version conflict")
            await self._history.save_workouts(workouts)
            return
        if current_id != plan.supersedes_plan_version_id:
            raise PlanVersionConflict(
                "supersedes_plan_version_id is not the active plan"
            )
        if current_id is None and plan.version != 1:
            raise PlanVersionConflict("First plan version must be 1")
        if current_id is not None:
            current = await self._history.get_plan(current_id)
            if current is None or plan.version != current.version + 1:
                raise PlanVersionConflict("Plan version must increment by one")
        if any(
            item.plan_version_id != plan.id or item.athlete_id != plan.athlete_id
            for item in workouts
        ):
            raise PlanVersionConflict("Workout does not belong to plan")
        await self._history.save_plan(plan)
        await self._history.save_workouts(workouts)
        await self._pointers.set(
            plan.athlete_id,
            plan.week_start,
            plan.id,
            current_id,
        )


def create_plan_version(
    athlete_id: str,
    line_user_id: str,
    week_start: date,
    version: int,
    goals: Sequence[Goal],
    change_reason: str,
    supersedes_plan_version_id: str | None = None,
    **values,
) -> TrainingPlanVersion:
    plan_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:plan:{athlete_id}:{week_start.isoformat()}:{version}",
        )
    )
    return TrainingPlanVersion(
        id=plan_id,
        athlete_id=athlete_id,
        line_user_id=line_user_id,
        week_start=week_start,
        version=version,
        goal_snapshot=[
            GoalSnapshot.model_validate(goal.model_dump(mode="json")) for goal in goals
        ],
        change_reason=change_reason,
        supersedes_plan_version_id=supersedes_plan_version_id,
        **values,
    )


def create_planned_workout(
    plan: TrainingPlanVersion,
    scheduled_date: date,
    sequence: int,
    workout_type: str,
    target_intensity: str,
    **values,
) -> PlannedWorkout:
    workout_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:workout:{plan.id}:{scheduled_date.isoformat()}:{sequence}",
        )
    )
    return PlannedWorkout(
        id=workout_id,
        plan_version_id=plan.id,
        athlete_id=plan.athlete_id,
        scheduled_date=scheduled_date,
        sequence=sequence,
        workout_type=workout_type,
        target_intensity=target_intensity,
        **values,
    )


def create_reconciliation(
    workout: PlannedWorkout,
    source_type: str,
    matcher_version: str,
    activity_id: str | None = None,
    **values,
) -> WorkoutReconciliation:
    reconciliation_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:reconciliation:{workout.id}:{source_type}:"
            f"{activity_id or 'unmatched'}:{matcher_version}",
        )
    )
    return WorkoutReconciliation(
        id=reconciliation_id,
        plan_version_id=workout.plan_version_id,
        planned_workout_id=workout.id,
        athlete_id=workout.athlete_id,
        source_type=source_type,
        activity_id=activity_id,
        matcher_version=matcher_version,
        **values,
    )


def create_workout_review(
    workout: PlannedWorkout,
    rule_version: str,
    reconciliation_id: str | None = None,
    **values,
) -> WorkoutReview:
    review_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:review:{workout.id}:"
            f"{reconciliation_id or 'unmatched'}:{rule_version}",
        )
    )
    return WorkoutReview(
        id=review_id,
        plan_version_id=workout.plan_version_id,
        planned_workout_id=workout.id,
        reconciliation_id=reconciliation_id,
        athlete_id=workout.athlete_id,
        rule_version=rule_version,
        **values,
    )


def _save_immutable(store: dict, item_id: str, item: ImmutableModel) -> None:
    existing = store.get(item_id)
    if existing is not None and existing != item:
        raise PlanVersionConflict(f"Immutable record {item_id} already exists")
    store[item_id] = item
