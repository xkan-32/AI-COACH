from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.planning import (
    ActivePlanPointerStore,
    PlannedWorkout,
    PlanningHistoryStore,
    PlanningService,
    TrainingPlanStatus,
    TrainingSettingsStateStore,
    WorkoutExecutionState,
    WorkoutExecutionStatus,
    create_plan_version,
    stable_planning_id,
)


class DailyWorkoutEditError(ValueError):
    pass


class DailyWorkoutEditKind(StrEnum):
    EDIT = "edit"
    REST = "rest"
    CANCEL = "cancel"
    MOVE_SLOT = "move_slot"


class DailyWorkoutEdit(BaseModel):
    """A bounded user edit which can only preserve or reduce planned load."""

    model_config = ConfigDict(frozen=True)

    planned_workout_id: str = Field(min_length=1, max_length=128)
    kind: DailyWorkoutEditKind
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    scheduled_date: date | None = None
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=240)
    distance_meters: float | None = Field(default=None, ge=0, le=100_000)
    intensity: Literal["rest", "easy", "moderate"] | None = None
    outdoors: bool | None = None
    environment_ids: list[str] | None = Field(default=None, max_length=10)
    note: str = Field(default="", max_length=500)


class DailyWorkoutEditService:
    def __init__(
        self,
        history: PlanningHistoryStore,
        active_plans: ActivePlanPointerStore,
        settings: TrainingSettingsStateStore,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._history = history
        self._active_plans = active_plans
        self._settings = settings
        self._clock = clock

    async def apply(
        self,
        *,
        user_id: str,
        line_user_id: str,
        base_plan_id: str,
        edit: DailyWorkoutEdit,
    ):
        base = await self._history.get_plan(base_plan_id)
        if base is None or base.user_id != user_id or base.line_user_id != line_user_id:
            raise DailyWorkoutEditError("週間計画の所有者を確認できません。")
        active_id = await self._active_plans.get(user_id, base.week_start)
        if active_id != base.id:
            candidate = await self._history.get_plan(
                _plan_id(user_id, line_user_id, base)
            )
            if (
                candidate is not None
                and candidate.input_snapshot.get("operation_id") == edit.operation_id
            ):
                return candidate, True
            raise DailyWorkoutEditError(
                "この週間計画は更新されています。開き直してください。"
            )
        workouts = await self._history.list_workouts(base.id)
        target = next(
            (item for item in workouts if item.id == edit.planned_workout_id), None
        )
        if target is None:
            raise DailyWorkoutEditError("変更対象のメニューが見つかりません。")
        profile = await self._settings.get_profile(user_id)
        timezone = profile.timezone if profile else "Asia/Tokyo"
        now = self._clock()
        local_now = now.astimezone(ZoneInfo(timezone))
        _ensure_not_started(target, local_now)
        replacement = await self._replacement(target, edit, user_id, local_now)
        _ensure_no_collision(workouts, target, replacement)
        candidate = create_plan_version(
            user_id=user_id,
            line_user_id=line_user_id,
            week_start=base.week_start,
            version=base.version + 1,
            goals=[],
            change_reason=f"daily_edit:{edit.kind.value}",
            supersedes_plan_version_id=base.id,
            athlete_id=base.athlete_id,
            status=TrainingPlanStatus.ACTIVE,
            plan_rationale="ユーザーによる日次・時間枠単位の変更",
            safety_flags=list(base.safety_flags),
            input_snapshot={
                "source": "weekly_plan_direct_edit",
                "operation_id": edit.operation_id,
                "planned_workout_id": target.id,
                "kind": edit.kind.value,
                "note": edit.note.strip(),
            },
            created_at=now,
        ).model_copy(
            update={
                "id": _plan_id(user_id, line_user_id, base),
                "goal_snapshot": base.goal_snapshot,
            }
        )
        copied = [
            _copy_workout(
                candidate, item, replacement if item.id == target.id else None, now
            )
            for item in workouts
        ]
        await PlanningService(self._history, self._active_plans).activate_version(
            candidate, copied
        )
        if edit.kind == DailyWorkoutEditKind.CANCEL:
            await self._history.save_execution_state(
                WorkoutExecutionState(
                    id=stable_planning_id(
                        "daily-edit-execution", target.id, edit.operation_id
                    ),
                    user_id=user_id,
                    plan_version_id=base.id,
                    planned_workout_id=target.id,
                    revision=1,
                    status=WorkoutExecutionStatus.CANCELLED,
                    operation_id=edit.operation_id,
                    recorded_at=now,
                )
            )
        return candidate, False

    async def _replacement(
        self,
        target: PlannedWorkout,
        edit: DailyWorkoutEdit,
        user_id: str,
        local_now: datetime,
    ) -> PlannedWorkout:
        if edit.kind in {DailyWorkoutEditKind.REST, DailyWorkoutEditKind.CANCEL}:
            return target.model_copy(
                update={
                    "workout_type": "rest",
                    "target_duration_minutes": 0,
                    "target_distance_meters": None,
                    "target_intensity": "rest",
                    "outdoors": False,
                    "environment_ids": [],
                    "rationale": "ユーザー指定の休養"
                    if edit.kind == DailyWorkoutEditKind.REST
                    else "ユーザー指定の取消",
                }
            )
        scheduled_date = edit.scheduled_date or target.scheduled_date
        start_time = edit.start_time or target.scheduled_start_local_time
        duration = (
            edit.duration_minutes
            if edit.duration_minutes is not None
            else target.target_duration_minutes
        )
        distance = (
            edit.distance_meters
            if edit.distance_meters is not None
            else target.target_distance_meters
        )
        intensity = edit.intensity or target.target_intensity
        outdoors = edit.outdoors if edit.outdoors is not None else target.outdoors
        environments = (
            edit.environment_ids
            if edit.environment_ids is not None
            else target.environment_ids
        )
        if edit.kind == DailyWorkoutEditKind.MOVE_SLOT and (
            edit.scheduled_date is None or edit.start_time is None
        ):
            raise DailyWorkoutEditError("移動には日付と開始時刻が必要です。")
        if scheduled_date < local_now.date():
            raise DailyWorkoutEditError("過去日のメニューは変更できません。")
        _ensure_not_more_demanding(target, duration, distance, intensity)
        await self._ensure_slot_compatible(
            user_id,
            scheduled_date,
            start_time,
            duration,
            outdoors,
            environments,
            local_now,
        )
        return target.model_copy(
            update={
                "scheduled_date": scheduled_date,
                "scheduled_start_local_time": start_time,
                "target_duration_minutes": duration,
                "target_distance_meters": distance,
                "target_intensity": intensity,
                "outdoors": outdoors,
                "environment_ids": list(environments),
                "rationale": "ユーザーによる直接編集",
            }
        )

    async def _ensure_slot_compatible(
        self, user_id, scheduled_date, start_time, duration, outdoors, environments, now
    ):
        availability = await self._settings.get_availability(user_id)
        if availability is None or start_time is None:
            raise DailyWorkoutEditError(
                "時間枠を確認できないため、開始時刻を含む編集はできません。"
            )
        end = datetime.combine(scheduled_date, start_time) + timedelta(
            minutes=duration or 0
        )
        for slot in availability.slots_for(scheduled_date, now):
            if (
                slot.fixed_rest_day
                or slot.start_local_time is None
                or slot.end_local_time is None
            ):
                continue
            slot_start = datetime.combine(
                scheduled_date, slot.start_local_time
            ) + timedelta(minutes=slot.buffer_before_minutes)
            slot_end = datetime.combine(
                scheduled_date, slot.end_local_time
            ) - timedelta(minutes=slot.buffer_after_minutes)
            if (
                slot_start <= datetime.combine(scheduled_date, start_time)
                and end <= slot_end
            ):
                if (
                    slot.max_workout_minutes is not None
                    and (duration or 0) > slot.max_workout_minutes
                ):
                    continue
                if outdoors and not slot.outdoors_allowed:
                    continue
                if set(environments) - set(slot.environment_ids):
                    continue
                return
        raise DailyWorkoutEditError("指定した時間枠・環境では安全に実施できません。")


def _plan_id(user_id, line_user_id, base) -> str:
    return create_plan_version(
        user_id,
        line_user_id,
        base.week_start,
        base.version + 1,
        [],
        "daily_edit",
        supersedes_plan_version_id=base.id,
    ).id


def _ensure_not_started(workout, local_now):
    if workout.scheduled_date < local_now.date() or (
        workout.scheduled_date == local_now.date()
        and workout.scheduled_start_local_time is not None
        and workout.scheduled_start_local_time
        <= local_now.timetz().replace(tzinfo=None)
    ):
        raise DailyWorkoutEditError("開始済みまたは過去のメニューは変更できません。")


def _ensure_not_more_demanding(base, duration, distance, intensity):
    rank = {"rest": 0, "easy": 1, "moderate": 2}
    if (
        (
            base.target_duration_minutes is not None
            and (duration or 0) > base.target_duration_minutes
        )
        or (
            base.target_distance_meters is not None
            and distance is not None
            and distance > base.target_distance_meters
        )
        or rank.get(str(intensity), 99) > rank.get(base.target_intensity, 99)
    ):
        raise DailyWorkoutEditError("直接編集では計画より負荷を上げられません。")


def _ensure_no_collision(workouts, target, replacement):
    if (
        replacement.workout_type == "rest"
        or replacement.scheduled_start_local_time is None
    ):
        return
    for item in workouts:
        if (
            item.id != target.id
            and item.workout_type != "rest"
            and item.scheduled_date == replacement.scheduled_date
            and item.scheduled_start_local_time
            == replacement.scheduled_start_local_time
        ):
            raise DailyWorkoutEditError("同じ時間枠に別のメニューがあります。")


def _copy_workout(plan, source, replacement, now):
    values = (replacement or source).model_dump()
    values.update(
        {
            "id": stable_planning_id(
                "daily-edit-workout", plan.id, source.workout_lineage_id
            ),
            "plan_version_id": plan.id,
            "supersedes_planned_workout_id": source.id,
            "workout_lineage_id": source.workout_lineage_id,
            "created_at": now,
        }
    )
    return PlannedWorkout.model_validate(values)
