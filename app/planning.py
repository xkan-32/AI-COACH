import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import Goal


class PlannedWorkoutStatus(StrEnum):
    """Legacy marker; execution outcomes live in WorkoutExecutionState."""

    PLANNED = "planned"


class WorkoutExecutionStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    REPLACED = "replaced"
    NOT_PERFORMED = "not_performed"
    CANCELLED = "cancelled"


class ReconciliationStatus(StrEnum):
    MATCHED = "matched"
    PARTIAL = "partial"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    UNPLANNED = "unplanned"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    NOT_PERFORMED = "not_performed"


class TrainingPlanStatus(StrEnum):
    GENERATING = "generating"
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    REJECTED = "rejected"
    REPROPOSAL_REQUESTED = "reproposal_requested"
    EXPIRED = "expired"
    GENERATION_FAILED = "generation_failed"
    SUPERSEDED = "superseded"


class ReadinessStatus(StrEnum):
    AS_PLANNED = "as_planned"
    WITH_ADJUSTMENT = "with_adjustment"
    BLOCKED = "blocked"
    NEEDS_INFORMATION = "needs_information"


class SafetyGateStatus(StrEnum):
    ALLOWED = "allowed"
    ADJUSTMENT_REQUIRED = "adjustment_required"
    BLOCKED = "blocked"


class PreferenceSource(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"


class PreferenceStrength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class PreferenceConfirmationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DatedRequestStatus(StrEnum):
    ACTIVE = "active"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AchievementStatus(StrEnum):
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    NOT_ACHIEVED = "not_achieved"
    UNASSESSED = "unassessed"


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


def _validate_iana_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _require_utc(value) if value is not None else None


def _require_local_time(value: time | None) -> time | None:
    if value is not None and value.tzinfo is not None:
        raise ValueError("local time must not contain a UTC offset")
    return value


class UserTrainingProfile(ImmutableModel):
    user_id: str = Field(min_length=1)
    timezone: str = "Asia/Tokyo"
    week_starts_on: Literal[0] = 0
    weekly_generation_local_time: time = time(21, 0)
    provider_athlete_id: str | None = None
    experience_level: Literal["beginner", "intermediate", "advanced"] | None = None
    notifications_enabled: bool = False
    automatic_evaluation_publishing_enabled: bool = False
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    version: int = Field(default=1, ge=1)
    operation_id: str = Field(min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _timezone_is_iana = field_validator("timezone")(_validate_iana_timezone)
    _updated_at_is_aware = field_validator("updated_at")(_require_utc)
    _local_times_have_no_offset = field_validator(
        "weekly_generation_local_time", "quiet_hours_start", "quiet_hours_end"
    )(_require_local_time)

    @model_validator(mode="after")
    def quiet_hours_are_complete(self) -> "UserTrainingProfile":
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours require both start and end")
        return self

    def local_week_start(self, instant: datetime) -> date:
        local_date = _require_utc(instant).astimezone(ZoneInfo(self.timezone)).date()
        return local_date - timedelta(days=local_date.weekday())


class AvailabilitySlot(ImmutableModel):
    id: str = Field(min_length=1)
    weekday: int = Field(ge=0, le=6)
    start_local_time: time | None = None
    end_local_time: time | None = None
    max_workout_minutes: int | None = Field(default=None, ge=1, le=1440)
    buffer_before_minutes: int = Field(default=0, ge=0, le=720)
    buffer_after_minutes: int = Field(default=0, ge=0, le=720)
    environment_ids: list[str] = Field(default_factory=list, max_length=20)
    outdoors_allowed: bool = True
    split_allowed: bool = False
    fixed_rest_day: bool = False

    _local_times_have_no_offset = field_validator("start_local_time", "end_local_time")(
        _require_local_time
    )

    @model_validator(mode="after")
    def validate_local_window(self) -> "AvailabilitySlot":
        if self.fixed_rest_day:
            if self.start_local_time is not None or self.end_local_time is not None:
                raise ValueError("fixed rest slots must not have a time window")
            if self.environment_ids or self.max_workout_minutes is not None:
                raise ValueError("fixed rest slots must not prescribe availability")
            return self
        if self.start_local_time is None or self.end_local_time is None:
            raise ValueError("availability slots require start and end local time")
        if self.end_local_time <= self.start_local_time:
            raise ValueError("slots crossing midnight must be split into two days")
        occupied = (
            datetime.combine(date.min, self.end_local_time)
            - datetime.combine(date.min, self.start_local_time)
        ).seconds // 60
        usable = occupied - self.buffer_before_minutes - self.buffer_after_minutes
        if usable <= 0:
            raise ValueError("slot buffers leave no workout time")
        if self.max_workout_minutes is not None and self.max_workout_minutes > usable:
            raise ValueError("max workout time exceeds usable slot duration")
        return self


class DatedAvailabilityOverride(ImmutableModel):
    id: str = Field(min_length=1)
    local_date: date
    slots: list[AvailabilitySlot] = Field(default_factory=list)
    unavailable: bool = False
    expires_at: datetime | None = None

    _expires_at_is_aware = field_validator("expires_at")(_optional_utc)

    @model_validator(mode="after")
    def unavailable_has_no_slots(self) -> "DatedAvailabilityOverride":
        if self.unavailable and self.slots:
            raise ValueError("unavailable override cannot contain slots")
        if any(item.weekday != self.local_date.weekday() for item in self.slots):
            raise ValueError("override slot weekday must match its local date")
        return self


class WeeklyAvailabilityVersion(ImmutableModel):
    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    timezone: str
    version: int = Field(ge=1)
    slots: list[AvailabilitySlot] = Field(default_factory=list, max_length=50)
    overrides: list[DatedAvailabilityOverride] = Field(
        default_factory=list, max_length=50
    )
    supersedes_version_id: str | None = None
    operation_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _timezone_is_iana = field_validator("timezone")(_validate_iana_timezone)
    _created_at_is_aware = field_validator("created_at")(_require_utc)

    @model_validator(mode="after")
    def ids_and_rest_days_are_consistent(self) -> "WeeklyAvailabilityVersion":
        ids = [slot.id for slot in self.slots]
        override_ids = [override.id for override in self.overrides]
        if len(ids) != len(set(ids)) or len(override_ids) != len(set(override_ids)):
            raise ValueError("availability IDs must be unique within a version")
        rest_days = {slot.weekday for slot in self.slots if slot.fixed_rest_day}
        available_days = {
            slot.weekday for slot in self.slots if not slot.fixed_rest_day
        }
        if rest_days & available_days:
            raise ValueError("fixed rest day cannot also contain availability")
        return self

    def slots_for(
        self, local_date: date, now: datetime | None = None
    ) -> list[AvailabilitySlot]:
        reference = _require_utc(now or datetime.now(UTC))
        override = next(
            (
                item
                for item in self.overrides
                if item.local_date == local_date
                and (item.expires_at is None or item.expires_at > reference)
            ),
            None,
        )
        if override is not None:
            return [] if override.unavailable else list(override.slots)
        return [slot for slot in self.slots if slot.weekday == local_date.weekday()]


class WorkoutPreference(ImmutableModel):
    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    preference_type: str = Field(min_length=1, max_length=80)
    value: dict[str, Any]
    strength: PreferenceStrength = PreferenceStrength.SOFT
    source: PreferenceSource
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=50)
    confirmation_status: PreferenceConfirmationStatus = (
        PreferenceConfirmationStatus.NOT_REQUIRED
    )
    expires_at: datetime | None = None
    supersedes_preference_id: str | None = None
    operation_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _expires_at_is_aware = field_validator("expires_at")(_optional_utc)
    _created_at_is_aware = field_validator("created_at")(_require_utc)

    @model_validator(mode="after")
    def inferred_preferences_have_evidence(self) -> "WorkoutPreference":
        if self.source == PreferenceSource.INFERRED:
            if self.confidence is None or not self.evidence_event_ids:
                raise ValueError("inferred preference requires confidence and evidence")
            if self.confirmation_status == PreferenceConfirmationStatus.NOT_REQUIRED:
                raise ValueError("inferred preference requires a confirmation state")
        elif self.evidence_event_ids:
            raise ValueError("explicit preference must not contain inferred evidence")
        return self

    def is_effective(self, now: datetime) -> bool:
        reference = _require_utc(now)
        return self.confirmation_status != PreferenceConfirmationStatus.REJECTED and (
            self.expires_at is None or self.expires_at > reference
        )


class DatedWorkoutRequest(ImmutableModel):
    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    local_date: date
    request_type: str = Field(min_length=1, max_length=80)
    value: dict[str, Any]
    priority: int = Field(default=50, ge=0, le=100)
    status: DatedRequestStatus = DatedRequestStatus.ACTIVE
    operation_id: str = Field(min_length=1)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _expires_at_is_aware = field_validator("expires_at")(_optional_utc)
    _created_at_is_aware = field_validator("created_at")(_require_utc)


class GoalSnapshot(ImmutableModel):
    id: str
    goal_type: str
    target: str
    target_date: date | None = None
    priority: str
    status: str


class TrainingPlanVersion(ImmutableModel):
    id: str
    user_id: str
    athlete_id: str | None = None
    line_user_id: str | None = None
    week_start: date
    version: int = Field(ge=1)
    status: TrainingPlanStatus = TrainingPlanStatus.ACTIVE
    goal_snapshot: list[GoalSnapshot]
    change_reason: str = Field(min_length=1)
    plan_rationale: str = Field(default="", max_length=1000)
    supersedes_plan_version_id: str | None = None
    safety_flags: list[str] = Field(default_factory=list)
    ai_model: str | None = None
    prompt_version: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at_is_aware = field_validator("created_at")(_require_utc)


class PlannedWorkout(ImmutableModel):
    id: str
    plan_version_id: str
    user_id: str
    athlete_id: str | None = None
    scheduled_date: date
    scheduled_start_local_time: time | None = None
    availability_slot_id: str | None = None
    sequence: int = Field(ge=0)
    workout_type: str = Field(min_length=1)
    target_duration_minutes: int | None = Field(default=None, ge=0)
    target_distance_meters: float | None = Field(default=None, ge=0)
    target_intensity: str
    outdoors: bool = False
    split_allowed: bool = False
    environment_ids: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=500)
    workout_lineage_id: str
    supersedes_planned_workout_id: str | None = None
    status: PlannedWorkoutStatus = PlannedWorkoutStatus.PLANNED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at_is_aware = field_validator("created_at")(_require_utc)
    _start_time_has_no_offset = field_validator("scheduled_start_local_time")(
        _require_local_time
    )


class WorkoutReconciliation(ImmutableModel):
    id: str
    plan_version_id: str | None = None
    planned_workout_id: str | None = None
    user_id: str
    athlete_id: str | None = None
    source_type: str
    activity_id: str | None = None
    status: ReconciliationStatus
    candidate_planned_workout_ids: list[str] = Field(default_factory=list)
    match_confidence: float | None = Field(default=None, ge=0, le=1)
    matching_evidence: list[str] = Field(default_factory=list)
    confirmed: bool = False
    duration_delta_minutes: float | None = None
    distance_delta_meters: float | None = None
    intensity_delta: str | None = None
    matcher_version: str
    objective_factors: list[str] = Field(default_factory=list)
    manual_correction: bool = False
    correction_reason: str | None = Field(default=None, max_length=200)
    supersedes_reconciliation_id: str | None = None
    operation_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at_is_aware = field_validator("created_at")(_require_utc)


class WorkoutReview(ImmutableModel):
    id: str
    plan_version_id: str
    planned_workout_id: str
    reconciliation_id: str | None = None
    activity_id: str | None = None
    user_id: str
    athlete_id: str | None = None
    achievement_status: AchievementStatus
    objective_factors: list[str] = Field(default_factory=list)
    condition_factors: list[str] = Field(default_factory=list)
    dialogue_factors: list[str] = Field(default_factory=list)
    feedback_codes: list[str] = Field(default_factory=list)
    rule_version: str
    ai_model: str | None = None
    prompt_version: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    supersedes_review_id: str | None = None
    operation_id: str = Field(default="legacy", min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at_is_aware = field_validator("created_at")(_require_utc)


class WorkoutExecutionState(ImmutableModel):
    id: str
    user_id: str
    plan_version_id: str
    planned_workout_id: str
    revision: int = Field(ge=1)
    status: WorkoutExecutionStatus
    source_reconciliation_ids: list[str] = Field(default_factory=list)
    supersedes_execution_state_id: str | None = None
    operation_id: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _recorded_at_is_aware = field_validator("recorded_at")(_require_utc)


class SafetyGateResult(ImmutableModel):
    id: str
    user_id: str
    planned_workout_id: str | None = None
    status: SafetyGateStatus
    reason_codes: list[str] = Field(default_factory=list)
    rule_version: str = Field(min_length=1)
    input_snapshot_digest: str = Field(min_length=32, max_length=128)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _evaluated_at_is_aware = field_validator("evaluated_at")(_require_utc)

    @model_validator(mode="after")
    def blocked_requires_reason(self) -> "SafetyGateResult":
        if self.status == SafetyGateStatus.BLOCKED and not self.reason_codes:
            raise ValueError("blocked safety gate requires a reason code")
        return self


class NextWorkoutReadinessAssessment(ImmutableModel):
    id: str
    user_id: str
    local_date: date
    planned_workout_id: str
    revision: int = Field(ge=1)
    status: ReadinessStatus
    safety_gate_result_id: str
    reason_codes: list[str] = Field(default_factory=list)
    display_reason: str = Field(default="", max_length=500)
    referenced_review_ids: list[str] = Field(default_factory=list)
    supersedes_assessment_id: str | None = None
    rule_version: str = Field(min_length=1)
    ai_model: str | None = None
    prompt_version: str | None = None
    operation_id: str = Field(default="legacy", min_length=1)
    input_snapshot_digest: str = Field(min_length=32, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _created_at_is_aware = field_validator("created_at")(_require_utc)

    @model_validator(mode="after")
    def blocked_requires_reason(self) -> "NextWorkoutReadinessAssessment":
        if self.status == ReadinessStatus.BLOCKED and not self.reason_codes:
            raise ValueError("blocked readiness requires a reason code")
        return self


class TrainingPlanLifecycleEvent(ImmutableModel):
    id: str
    user_id: str
    plan_version_id: str
    from_status: TrainingPlanStatus
    to_status: TrainingPlanStatus
    reason_code: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _occurred_at_is_aware = field_validator("occurred_at")(_require_utc)


class TrainingSettingsStateStore(Protocol):
    async def get_profile(self, user_id: str) -> UserTrainingProfile | None: ...
    async def list_profiles(self) -> list[UserTrainingProfile]: ...
    async def save_profile(
        self, profile: UserTrainingProfile, expected_version: int | None
    ) -> None: ...
    async def get_availability(
        self, user_id: str
    ) -> WeeklyAvailabilityVersion | None: ...
    async def save_availability(
        self,
        availability: WeeklyAvailabilityVersion,
        expected_version: int | None,
    ) -> None: ...
    async def list_preferences(self, user_id: str) -> list[WorkoutPreference]: ...
    async def save_preference(self, preference: WorkoutPreference) -> None: ...
    async def list_dated_requests(
        self, user_id: str, start_date: date, end_date: date
    ) -> list[DatedWorkoutRequest]: ...
    async def save_dated_request(self, request: DatedWorkoutRequest) -> None: ...


class TrainingSettingsHistoryStore(Protocol):
    async def save_profile(self, profile: UserTrainingProfile) -> None: ...
    async def save_availability(
        self, availability: WeeklyAvailabilityVersion
    ) -> None: ...
    async def save_preference(self, preference: WorkoutPreference) -> None: ...
    async def save_dated_request(self, request: DatedWorkoutRequest) -> None: ...


class InMemoryTrainingSettingsStore(
    TrainingSettingsStateStore, TrainingSettingsHistoryStore
):
    def __init__(self) -> None:
        self.profiles: dict[str, UserTrainingProfile] = {}
        self.availability: dict[str, WeeklyAvailabilityVersion] = {}
        self.profile_history: dict[str, UserTrainingProfile] = {}
        self.availability_history: dict[str, WeeklyAvailabilityVersion] = {}
        self.preferences: dict[str, WorkoutPreference] = {}
        self.dated_requests: dict[str, DatedWorkoutRequest] = {}

    async def get_profile(self, user_id: str) -> UserTrainingProfile | None:
        return self.profiles.get(user_id)

    async def list_profiles(self) -> list[UserTrainingProfile]:
        return sorted(self.profiles.values(), key=lambda item: item.user_id)

    async def save_profile(
        self, profile: UserTrainingProfile, expected_version: int | None = None
    ) -> None:
        current = self.profiles.get(profile.user_id)
        if current is not None and current.operation_id == profile.operation_id:
            if current != profile:
                raise PlanVersionConflict("Operation payload changed")
            return
        current_version = current.version if current else None
        if current_version != expected_version:
            raise PlanVersionConflict("Training profile version changed")
        if profile.version != (current_version or 0) + 1:
            raise PlanVersionConflict("Training profile version must increment by one")
        self.profiles[profile.user_id] = profile
        _save_immutable(
            self.profile_history,
            _version_key(profile.user_id, profile.version),
            profile,
        )

    async def get_availability(self, user_id: str) -> WeeklyAvailabilityVersion | None:
        return self.availability.get(user_id)

    async def save_availability(
        self,
        availability: WeeklyAvailabilityVersion,
        expected_version: int | None = None,
    ) -> None:
        current = self.availability.get(availability.user_id)
        if current is not None and current.operation_id == availability.operation_id:
            if current != availability:
                raise PlanVersionConflict("Operation payload changed")
            return
        current_version = current.version if current else None
        if current_version != expected_version:
            raise PlanVersionConflict("Weekly availability version changed")
        if availability.version != (current_version or 0) + 1:
            raise PlanVersionConflict(
                "Weekly availability version must increment by one"
            )
        if availability.supersedes_version_id != (current.id if current else None):
            raise PlanVersionConflict("Weekly availability supersedes stale version")
        self.availability[availability.user_id] = availability
        _save_immutable(self.availability_history, availability.id, availability)

    async def list_preferences(self, user_id: str) -> list[WorkoutPreference]:
        return [item for item in self.preferences.values() if item.user_id == user_id]

    async def save_preference(self, preference: WorkoutPreference) -> None:
        _save_immutable(self.preferences, preference.id, preference)

    async def list_dated_requests(
        self, user_id: str, start_date: date, end_date: date
    ) -> list[DatedWorkoutRequest]:
        return sorted(
            [
                item
                for item in self.dated_requests.values()
                if item.user_id == user_id
                and start_date <= item.local_date <= end_date
                and item.status == DatedRequestStatus.ACTIVE
            ],
            key=lambda item: (-item.priority, item.local_date, item.id),
        )

    async def save_dated_request(self, request: DatedWorkoutRequest) -> None:
        _save_immutable(self.dated_requests, request.id, request)


class FirestoreTrainingSettingsStateStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get_profile(self, user_id: str) -> UserTrainingProfile | None:
        snapshot = (
            await self._client.collection("user_training_profiles")
            .document(user_id)
            .get()
        )
        return (
            UserTrainingProfile.model_validate(snapshot.to_dict())
            if snapshot.exists
            else None
        )

    async def list_profiles(self) -> list[UserTrainingProfile]:
        snapshots = await self._client.collection("user_training_profiles").get()
        return sorted(
            [UserTrainingProfile.model_validate(item.to_dict()) for item in snapshots],
            key=lambda item: item.user_id,
        )

    async def save_profile(
        self, profile: UserTrainingProfile, expected_version: int | None
    ) -> None:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        document = self._client.collection("user_training_profiles").document(
            profile.user_id
        )
        transaction = self._client.transaction()

        @async_transactional
        async def save_once(active_transaction: object) -> None:
            snapshot = await document.get(transaction=active_transaction)
            current = snapshot.to_dict() if snapshot.exists else None
            if current and current.get("operation_id") == profile.operation_id:
                if UserTrainingProfile.model_validate(current) != profile:
                    raise PlanVersionConflict("Operation payload changed")
                return
            current_version = int(current["version"]) if current else None
            if (
                current_version != expected_version
                or profile.version != (current_version or 0) + 1
            ):
                raise PlanVersionConflict("Training profile version changed")
            active_transaction.set(document, _firestore_payload(profile))

        await save_once(transaction)

    def _availability_pointer(self, user_id: str):
        return self._client.collection("active_weekly_availability").document(user_id)

    async def get_availability(self, user_id: str) -> WeeklyAvailabilityVersion | None:
        pointer = await self._availability_pointer(user_id).get()
        if not pointer.exists:
            return None
        version_id = pointer.to_dict()["availability_version_id"]
        snapshot = (
            await self._client.collection("weekly_availability_versions")
            .document(version_id)
            .get()
        )
        return (
            WeeklyAvailabilityVersion.model_validate(snapshot.to_dict())
            if snapshot.exists
            else None
        )

    async def save_availability(
        self,
        availability: WeeklyAvailabilityVersion,
        expected_version: int | None,
    ) -> None:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        pointer = self._availability_pointer(availability.user_id)
        version_document = self._client.collection(
            "weekly_availability_versions"
        ).document(availability.id)
        transaction = self._client.transaction()

        @async_transactional
        async def save_once(active_transaction: object) -> None:
            current_pointer = await pointer.get(transaction=active_transaction)
            current_values = (
                current_pointer.to_dict() if current_pointer.exists else None
            )
            current_version = int(current_values["version"]) if current_values else None
            if (
                current_values
                and current_values.get("operation_id") == availability.operation_id
            ):
                existing = await version_document.get(transaction=active_transaction)
                if (
                    not existing.exists
                    or WeeklyAvailabilityVersion.model_validate(existing.to_dict())
                    != availability
                ):
                    raise PlanVersionConflict("Operation payload changed")
                return
            if (
                current_version != expected_version
                or availability.version != (current_version or 0) + 1
            ):
                raise PlanVersionConflict("Weekly availability version changed")
            current_id = (
                current_values.get("availability_version_id")
                if current_values
                else None
            )
            if availability.supersedes_version_id != current_id:
                raise PlanVersionConflict(
                    "Weekly availability supersedes stale version"
                )
            active_transaction.create(
                version_document, _firestore_payload(availability)
            )
            active_transaction.set(
                pointer,
                {
                    "user_id": availability.user_id,
                    "availability_version_id": availability.id,
                    "version": availability.version,
                    "operation_id": availability.operation_id,
                    "updated_at": availability.created_at,
                },
            )

        await save_once(transaction)

    async def list_preferences(self, user_id: str) -> list[WorkoutPreference]:
        snapshots = (
            await self._client.collection("workout_preferences")
            .where("user_id", "==", user_id)
            .get()
        )
        return [WorkoutPreference.model_validate(item.to_dict()) for item in snapshots]

    async def save_preference(self, preference: WorkoutPreference) -> None:
        document = self._client.collection("workout_preferences").document(
            preference.id
        )
        snapshot = await document.get()
        if (
            snapshot.exists
            and WorkoutPreference.model_validate(snapshot.to_dict()) != preference
        ):
            raise PlanVersionConflict("Immutable preference already exists")
        await document.set(_firestore_payload(preference))

    async def list_dated_requests(
        self, user_id: str, start_date: date, end_date: date
    ) -> list[DatedWorkoutRequest]:
        snapshots = (
            await self._client.collection("dated_workout_requests")
            .where("user_id", "==", user_id)
            .get()
        )
        return sorted(
            [
                item
                for snapshot in snapshots
                if (item := DatedWorkoutRequest.model_validate(snapshot.to_dict()))
                and start_date <= item.local_date <= end_date
                and item.status == DatedRequestStatus.ACTIVE
            ],
            key=lambda item: (-item.priority, item.local_date, item.id),
        )

    async def save_dated_request(self, request: DatedWorkoutRequest) -> None:
        document = self._client.collection("dated_workout_requests").document(
            request.id
        )
        snapshot = await document.get()
        if (
            snapshot.exists
            and DatedWorkoutRequest.model_validate(snapshot.to_dict()) != request
        ):
            raise PlanVersionConflict("Immutable dated request already exists")
        await document.set(_firestore_payload(request))


class BigQueryTrainingSettingsHistoryStore:
    def __init__(self, client: object, table_prefix: str) -> None:
        self._client = client
        self._prefix = table_prefix

    async def save_profile(self, profile: UserTrainingProfile) -> None:
        await self._insert(
            "user_training_profile_versions",
            profile.model_dump(mode="json"),
            _version_key(profile.user_id, profile.version),
        )

    async def save_availability(self, availability: WeeklyAvailabilityVersion) -> None:
        row = availability.model_dump(mode="json")
        for field in ("slots", "overrides"):
            row[field] = json.dumps(
                row[field], ensure_ascii=False, separators=(",", ":")
            )
        await self._insert(
            "weekly_availability_versions",
            row,
            availability.id,
        )

    async def save_preference(self, preference: WorkoutPreference) -> None:
        row = preference.model_dump(mode="json")
        row["value"] = json.dumps(
            row["value"], ensure_ascii=False, separators=(",", ":")
        )
        await self._insert("workout_preferences", row, preference.id)

    async def save_dated_request(self, request: DatedWorkoutRequest) -> None:
        await self._insert(
            "dated_workout_requests", request.model_dump(mode="json"), request.id
        )

    async def _insert(self, table: str, row: dict, row_id: str) -> None:
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            f"{self._prefix}.{table}",
            [row],
            row_ids=[row_id],
        )
        if errors:
            raise RuntimeError(f"BigQuery insert failed for {table}")


class TrainingSettingsService:
    def __init__(
        self,
        state: TrainingSettingsStateStore,
        history: TrainingSettingsHistoryStore,
    ) -> None:
        self._state = state
        self._history = history

    async def save_profile(
        self, profile: UserTrainingProfile, expected_version: int | None
    ) -> None:
        if self._history is not self._state:
            await self._history.save_profile(profile)
        await self._state.save_profile(profile, expected_version)

    async def save_availability(
        self,
        availability: WeeklyAvailabilityVersion,
        expected_version: int | None,
    ) -> None:
        if self._history is not self._state:
            await self._history.save_availability(availability)
        await self._state.save_availability(availability, expected_version)

    async def save_preference(self, preference: WorkoutPreference) -> None:
        if self._history is not self._state:
            await self._history.save_preference(preference)
        await self._state.save_preference(preference)

    async def save_dated_request(self, request: DatedWorkoutRequest) -> None:
        if self._history is not self._state:
            await self._history.save_dated_request(request)
        await self._state.save_dated_request(request)

    async def get_profile(self, user_id: str) -> UserTrainingProfile | None:
        return await self._state.get_profile(user_id)

    async def get_availability(self, user_id: str) -> WeeklyAvailabilityVersion | None:
        return await self._state.get_availability(user_id)

    async def list_dated_requests(
        self, user_id: str, start_date: date, end_date: date
    ) -> list[DatedWorkoutRequest]:
        return await self._state.list_dated_requests(user_id, start_date, end_date)

    async def effective_preferences(
        self, user_id: str, now: datetime
    ) -> list[WorkoutPreference]:
        preferences = await self._state.list_preferences(user_id)
        latest_explicit = {
            preference_type: max(
                (
                    item
                    for item in preferences
                    if item.source == PreferenceSource.EXPLICIT
                    and item.preference_type == preference_type
                ),
                key=lambda item: item.version,
            )
            for preference_type in {
                item.preference_type
                for item in preferences
                if item.source == PreferenceSource.EXPLICIT
            }
        }
        resolved = [
            item
            for item in preferences
            if item.is_effective(now)
            and (
                latest_explicit.get(item.preference_type) == item
                or item.preference_type not in latest_explicit
            )
        ]
        return sorted(
            resolved,
            key=lambda item: (
                item.preference_type,
                item.source != PreferenceSource.EXPLICIT,
                -(item.confidence or 1.0),
                -item.version,
            ),
        )


class PlanningHistoryStore(Protocol):
    async def save_plan(self, plan: TrainingPlanVersion) -> None: ...
    async def get_plan(self, plan_id: str) -> TrainingPlanVersion | None: ...
    async def list_workouts(self, plan_id: str) -> list[PlannedWorkout]: ...
    async def save_workouts(self, workouts: Sequence[PlannedWorkout]) -> None: ...
    async def save_reconciliation(
        self, reconciliation: WorkoutReconciliation
    ) -> None: ...
    async def get_reconciliation(
        self, reconciliation_id: str
    ) -> WorkoutReconciliation | None: ...
    async def list_activity_reconciliations(
        self, activity_id: str
    ) -> list[WorkoutReconciliation]: ...
    async def list_workout_reconciliations(
        self, planned_workout_id: str
    ) -> list[WorkoutReconciliation]: ...
    async def list_plan_reconciliations(
        self, plan_version_id: str
    ) -> list[WorkoutReconciliation]: ...
    async def save_review(self, review: WorkoutReview) -> None: ...
    async def get_review(self, review_id: str) -> WorkoutReview | None: ...
    async def list_reconciliation_reviews(
        self, reconciliation_id: str
    ) -> list[WorkoutReview]: ...
    async def list_plan_reviews(self, plan_version_id: str) -> list[WorkoutReview]: ...
    async def save_execution_state(self, state: WorkoutExecutionState) -> None: ...
    async def save_safety_gate(self, result: SafetyGateResult) -> None: ...
    async def save_readiness(
        self, assessment: NextWorkoutReadinessAssessment
    ) -> None: ...
    async def list_readiness_assessments(
        self, user_id: str, planned_workout_id: str
    ) -> list[NextWorkoutReadinessAssessment]: ...
    async def save_lifecycle_event(self, event: TrainingPlanLifecycleEvent) -> None: ...


class ActivePlanPointerStore(Protocol):
    async def get(self, user_id: str, week_start: date) -> str | None: ...
    async def set(
        self,
        user_id: str,
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
        self.execution_states: dict[str, WorkoutExecutionState] = {}
        self.safety_gate_results: dict[str, SafetyGateResult] = {}
        self.readiness_assessments: dict[str, NextWorkoutReadinessAssessment] = {}
        self.lifecycle_events: dict[str, TrainingPlanLifecycleEvent] = {}

    async def save_plan(self, plan: TrainingPlanVersion) -> None:
        _save_immutable(self.plans, plan.id, plan)

    async def get_plan(self, plan_id: str) -> TrainingPlanVersion | None:
        return self.plans.get(plan_id)

    async def list_workouts(self, plan_id: str) -> list[PlannedWorkout]:
        return sorted(
            [
                item
                for item in self.workouts.values()
                if item.plan_version_id == plan_id
            ],
            key=lambda item: (item.scheduled_date, item.sequence),
        )

    async def save_workouts(self, workouts: Sequence[PlannedWorkout]) -> None:
        for workout in workouts:
            _save_immutable(self.workouts, workout.id, workout)

    async def save_reconciliation(self, reconciliation: WorkoutReconciliation) -> None:
        _save_immutable(self.reconciliations, reconciliation.id, reconciliation)

    async def get_reconciliation(
        self, reconciliation_id: str
    ) -> WorkoutReconciliation | None:
        return self.reconciliations.get(reconciliation_id)

    async def list_activity_reconciliations(
        self, activity_id: str
    ) -> list[WorkoutReconciliation]:
        return sorted(
            [
                item
                for item in self.reconciliations.values()
                if item.activity_id == activity_id
            ],
            key=lambda item: item.created_at,
        )

    async def list_workout_reconciliations(
        self, planned_workout_id: str
    ) -> list[WorkoutReconciliation]:
        return sorted(
            [
                item
                for item in self.reconciliations.values()
                if item.planned_workout_id == planned_workout_id
            ],
            key=lambda item: item.created_at,
        )

    async def list_plan_reconciliations(
        self, plan_version_id: str
    ) -> list[WorkoutReconciliation]:
        return sorted(
            [
                item
                for item in self.reconciliations.values()
                if item.plan_version_id == plan_version_id
            ],
            key=lambda item: item.created_at,
        )

    async def save_review(self, review: WorkoutReview) -> None:
        _save_immutable(self.reviews, review.id, review)

    async def get_review(self, review_id: str) -> WorkoutReview | None:
        return self.reviews.get(review_id)

    async def list_reconciliation_reviews(
        self, reconciliation_id: str
    ) -> list[WorkoutReview]:
        return sorted(
            [
                item
                for item in self.reviews.values()
                if item.reconciliation_id == reconciliation_id
            ],
            key=lambda item: item.created_at,
        )

    async def list_plan_reviews(self, plan_version_id: str) -> list[WorkoutReview]:
        return sorted(
            [
                item
                for item in self.reviews.values()
                if item.plan_version_id == plan_version_id
            ],
            key=lambda item: item.created_at,
        )

    async def save_execution_state(self, state: WorkoutExecutionState) -> None:
        _save_immutable(self.execution_states, state.id, state)

    async def save_safety_gate(self, result: SafetyGateResult) -> None:
        _save_immutable(self.safety_gate_results, result.id, result)

    async def save_readiness(self, assessment: NextWorkoutReadinessAssessment) -> None:
        _save_immutable(self.readiness_assessments, assessment.id, assessment)

    async def list_readiness_assessments(
        self, user_id: str, planned_workout_id: str
    ) -> list[NextWorkoutReadinessAssessment]:
        return sorted(
            [
                item
                for item in self.readiness_assessments.values()
                if item.user_id == user_id
                and item.planned_workout_id == planned_workout_id
            ],
            key=lambda item: item.revision,
        )

    async def save_lifecycle_event(self, event: TrainingPlanLifecycleEvent) -> None:
        _save_immutable(self.lifecycle_events, event.id, event)


class InMemoryActivePlanPointerStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, date], str] = {}

    async def get(self, user_id: str, week_start: date) -> str | None:
        return self.items.get((user_id, week_start))

    async def set(
        self,
        user_id: str,
        week_start: date,
        plan_version_id: str,
        expected_previous_id: str | None,
    ) -> None:
        key = (user_id, week_start)
        if self.items.get(key) != expected_previous_id:
            raise PlanVersionConflict("Active training plan changed")
        self.items[key] = plan_version_id


class FirestoreActivePlanPointerStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _document(self, user_id: str, week_start: date):
        return self._client.collection("active_training_plans").document(
            f"{user_id}:{week_start.isoformat()}"
        )

    async def get(self, user_id: str, week_start: date) -> str | None:
        snapshot = await self._document(user_id, week_start).get()
        return str(snapshot.to_dict()["plan_version_id"]) if snapshot.exists else None

    async def set(
        self,
        user_id: str,
        week_start: date,
        plan_version_id: str,
        expected_previous_id: str | None,
    ) -> None:
        from google.cloud import firestore

        document = self._document(user_id, week_start)
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
                    "user_id": user_id,
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
        # BigQuery's streaming insert API expects values for JSON columns as
        # JSON-formatted strings, unlike the Python objects returned by the
        # query API. Keep the conversion at this persistence boundary.
        for field in ("goal_snapshot", "input_snapshot"):
            row[field] = json.dumps(
                row[field], ensure_ascii=False, separators=(",", ":")
            )
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
        if not values.get("user_id"):
            values["user_id"] = values.get("athlete_id")
        if not values.get("status"):
            values["status"] = TrainingPlanStatus.ACTIVE.value
        for field in ("goal_snapshot", "input_snapshot"):
            if isinstance(values.get(field), str):
                values[field] = json.loads(values[field])
        return TrainingPlanVersion.model_validate(values)

    async def list_workouts(self, plan_id: str) -> list[PlannedWorkout]:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._prefix}.planned_workouts` "
            "WHERE plan_version_id = @plan_id "
            "ORDER BY scheduled_date, sequence"
        )
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("plan_id", "STRING", plan_id)
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        workouts = []
        for row in rows:
            values = dict(row.items())
            values["split_allowed"] = bool(values.get("split_allowed"))
            workouts.append(PlannedWorkout.model_validate(values))
        return workouts

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

    async def get_reconciliation(
        self, reconciliation_id: str
    ) -> WorkoutReconciliation | None:
        rows = await self._query_reconciliations("id", reconciliation_id, limit=1)
        return rows[0] if rows else None

    async def list_activity_reconciliations(
        self, activity_id: str
    ) -> list[WorkoutReconciliation]:
        return await self._query_reconciliations("activity_id", activity_id)

    async def list_workout_reconciliations(
        self, planned_workout_id: str
    ) -> list[WorkoutReconciliation]:
        return await self._query_reconciliations(
            "planned_workout_id", planned_workout_id
        )

    async def list_plan_reconciliations(
        self, plan_version_id: str
    ) -> list[WorkoutReconciliation]:
        return await self._query_reconciliations("plan_version_id", plan_version_id)

    async def _query_reconciliations(
        self, field: str, value: str, limit: int | None = None
    ) -> list[WorkoutReconciliation]:
        from google.cloud import bigquery

        if field not in {
            "id",
            "activity_id",
            "planned_workout_id",
            "plan_version_id",
        }:
            raise ValueError("Unsupported reconciliation lookup")
        query = (
            f"SELECT * FROM `{self._prefix}.workout_reconciliations` "
            f"WHERE {field} = @value ORDER BY created_at, id"
            + (" LIMIT @limit" if limit is not None else "")
        )
        parameters = [bigquery.ScalarQueryParameter("value", "STRING", value)]
        if limit is not None:
            parameters.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
        config = bigquery.QueryJobConfig(query_parameters=parameters)
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        reconciliations = []
        for row in rows:
            values = dict(row.items())
            values["candidate_planned_workout_ids"] = list(
                values.get("candidate_planned_workout_ids") or []
            )
            values["matching_evidence"] = list(values.get("matching_evidence") or [])
            values["objective_factors"] = list(values.get("objective_factors") or [])
            values["operation_id"] = values.get("operation_id") or (
                f"legacy:{values['id']}"
            )
            if values.get("confirmed") is None:
                values["confirmed"] = values.get("status") in {
                    ReconciliationStatus.MATCHED.value,
                    ReconciliationStatus.PARTIAL.value,
                    ReconciliationStatus.NOT_PERFORMED.value,
                }
            values["manual_correction"] = bool(values.get("manual_correction"))
            reconciliations.append(WorkoutReconciliation.model_validate(values))
        return reconciliations

    async def save_review(self, review: WorkoutReview) -> None:
        row = review.model_dump(mode="json")
        await self._insert("workout_reviews", row, review.id)

    async def get_review(self, review_id: str) -> WorkoutReview | None:
        rows = await self._query_reviews("id", review_id, limit=1)
        return rows[0] if rows else None

    async def list_reconciliation_reviews(
        self, reconciliation_id: str
    ) -> list[WorkoutReview]:
        return await self._query_reviews("reconciliation_id", reconciliation_id)

    async def list_plan_reviews(self, plan_version_id: str) -> list[WorkoutReview]:
        return await self._query_reviews("plan_version_id", plan_version_id)

    async def _query_reviews(
        self, field: str, value: str, limit: int | None = None
    ) -> list[WorkoutReview]:
        rows = await self._query_rows("workout_reviews", field, value, limit)
        reviews = []
        for row in rows:
            values = dict(row.items())
            values["operation_id"] = values.get("operation_id") or (
                f"legacy:{values['id']}"
            )
            for name in (
                "objective_factors",
                "condition_factors",
                "dialogue_factors",
                "feedback_codes",
            ):
                values[name] = list(values.get(name) or [])
            reviews.append(WorkoutReview.model_validate(values))
        return reviews

    async def save_execution_state(self, state: WorkoutExecutionState) -> None:
        await self._insert(
            "workout_execution_states", state.model_dump(mode="json"), state.id
        )

    async def save_safety_gate(self, result: SafetyGateResult) -> None:
        await self._insert(
            "safety_gate_results", result.model_dump(mode="json"), result.id
        )

    async def save_readiness(self, assessment: NextWorkoutReadinessAssessment) -> None:
        await self._insert(
            "readiness_assessments", assessment.model_dump(mode="json"), assessment.id
        )

    async def list_readiness_assessments(
        self, user_id: str, planned_workout_id: str
    ) -> list[NextWorkoutReadinessAssessment]:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._prefix}.readiness_assessments` "
            "WHERE user_id = @user_id AND planned_workout_id = @workout_id "
            "ORDER BY revision"
        )
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter(
                    "workout_id", "STRING", planned_workout_id
                ),
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        assessments = []
        for row in rows:
            values = dict(row.items())
            values["reason_codes"] = list(values.get("reason_codes") or [])
            values["referenced_review_ids"] = list(
                values.get("referenced_review_ids") or []
            )
            values["operation_id"] = values.get("operation_id") or (
                f"legacy:{values['id']}"
            )
            assessments.append(NextWorkoutReadinessAssessment.model_validate(values))
        return assessments

    async def _query_rows(
        self, table: str, field: str, value: str, limit: int | None = None
    ) -> list[Any]:
        from google.cloud import bigquery

        allowed = {"workout_reviews": {"id", "reconciliation_id", "plan_version_id"}}
        if field not in allowed.get(table, set()):
            raise ValueError("Unsupported planning history lookup")
        query = (
            f"SELECT * FROM `{self._prefix}.{table}` WHERE {field} = @value "
            "ORDER BY created_at, id" + (" LIMIT @limit" if limit is not None else "")
        )
        parameters = [bigquery.ScalarQueryParameter("value", "STRING", value)]
        if limit is not None:
            parameters.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
        config = bigquery.QueryJobConfig(query_parameters=parameters)
        return await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )

    async def save_lifecycle_event(self, event: TrainingPlanLifecycleEvent) -> None:
        await self._insert(
            "training_plan_lifecycle_events", event.model_dump(mode="json"), event.id
        )

    async def _insert(self, table: str, row: dict, row_id: str) -> None:
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            f"{self._prefix}.{table}",
            [row],
            row_ids=[row_id],
        )
        if errors:
            raise RuntimeError(f"BigQuery insert failed for {table}: {errors!r}")


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
        if plan.status != TrainingPlanStatus.ACTIVE:
            raise PlanVersionConflict(
                "Only an explicitly approved active plan can be activated"
            )
        current_id = await self._pointers.get(plan.user_id, plan.week_start)
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
        if current_id is None and plan.supersedes_plan_version_id is not None:
            raise PlanVersionConflict(
                "A plan revision requires its predecessor to be active"
            )
        if current_id is not None:
            current = await self._history.get_plan(current_id)
            if current is None or plan.version != current.version + 1:
                raise PlanVersionConflict("Plan version must increment by one")
        if any(
            item.plan_version_id != plan.id or item.user_id != plan.user_id
            for item in workouts
        ):
            raise PlanVersionConflict("Workout does not belong to plan")
        await self._history.save_plan(plan)
        await self._history.save_workouts(workouts)
        await self._pointers.set(
            plan.user_id,
            plan.week_start,
            plan.id,
            current_id,
        )

    async def activate_approved_version(
        self,
        plan: TrainingPlanVersion,
        workouts: Sequence[PlannedWorkout],
        approval_event: TrainingPlanLifecycleEvent,
    ) -> None:
        if (
            approval_event.user_id != plan.user_id
            or approval_event.plan_version_id != plan.id
            or approval_event.from_status != TrainingPlanStatus.PENDING_APPROVAL
            or approval_event.to_status != TrainingPlanStatus.ACTIVE
        ):
            raise PlanVersionConflict("Plan activation requires its approval event")
        stored = await self._history.get_plan(plan.id)
        if stored != plan:
            raise PlanVersionConflict("Approved plan must already exist unchanged")
        stored_workouts = await self._history.list_workouts(plan.id)
        if list(workouts) != stored_workouts:
            raise PlanVersionConflict("Approved workouts must already exist unchanged")
        current_id = await self._pointers.get(plan.user_id, plan.week_start)
        if current_id == plan.id:
            await self._history.save_lifecycle_event(approval_event)
            return
        if current_id != plan.supersedes_plan_version_id:
            raise PlanVersionConflict(
                "supersedes_plan_version_id is not the active plan"
            )
        if current_id is None and plan.supersedes_plan_version_id is not None:
            raise PlanVersionConflict(
                "A plan revision requires its predecessor to be active"
            )
        if current_id is not None:
            current = await self._history.get_plan(current_id)
            if current is None or plan.version != current.version + 1:
                raise PlanVersionConflict("Plan version must increment by one")
        await self._history.save_lifecycle_event(approval_event)
        await self._pointers.set(
            plan.user_id,
            plan.week_start,
            plan.id,
            current_id,
        )


def create_plan_version(
    user_id: str,
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
            f"ai-coach:plan:{user_id}:{week_start.isoformat()}:{version}",
        )
    )
    return TrainingPlanVersion(
        id=plan_id,
        user_id=user_id,
        athlete_id=values.pop("athlete_id", user_id),
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


def create_user_training_profile(
    user_id: str,
    timezone: str,
    version: int,
    operation_id: str,
    **values: Any,
) -> UserTrainingProfile:
    return UserTrainingProfile(
        user_id=user_id,
        timezone=timezone,
        version=version,
        operation_id=operation_id,
        **values,
    )


def create_weekly_availability(
    user_id: str,
    timezone: str,
    version: int,
    operation_id: str,
    slots: Sequence[AvailabilitySlot] = (),
    overrides: Sequence[DatedAvailabilityOverride] = (),
    **values: Any,
) -> WeeklyAvailabilityVersion:
    return WeeklyAvailabilityVersion(
        id=stable_planning_id("availability", user_id, version),
        user_id=user_id,
        timezone=timezone,
        version=version,
        slots=list(slots),
        overrides=list(overrides),
        operation_id=operation_id,
        **values,
    )


def create_workout_preference(
    user_id: str,
    preference_key: str,
    version: int,
    operation_id: str,
    **values: Any,
) -> WorkoutPreference:
    return WorkoutPreference(
        id=stable_planning_id("preference", user_id, preference_key, version),
        user_id=user_id,
        version=version,
        operation_id=operation_id,
        **values,
    )


def create_dated_workout_request(
    user_id: str,
    local_date: date,
    operation_id: str,
    **values: Any,
) -> DatedWorkoutRequest:
    return DatedWorkoutRequest(
        id=stable_planning_id("dated-request", user_id, local_date, operation_id),
        user_id=user_id,
        local_date=local_date,
        operation_id=operation_id,
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
        user_id=plan.user_id,
        athlete_id=plan.athlete_id,
        scheduled_date=scheduled_date,
        sequence=sequence,
        workout_type=workout_type,
        target_intensity=target_intensity,
        workout_lineage_id=values.pop("workout_lineage_id", workout_id),
        **values,
    )


def create_reconciliation(
    workout: PlannedWorkout | None,
    source_type: str,
    matcher_version: str,
    activity_id: str | None = None,
    *,
    user_id: str | None = None,
    athlete_id: str | None = None,
    plan_version_id: str | None = None,
    operation_id: str = "automatic",
    **values,
) -> WorkoutReconciliation:
    owner_id = workout.user_id if workout is not None else user_id
    if not owner_id:
        raise ValueError("reconciliation requires a user owner")
    workout_id = workout.id if workout is not None else None
    resolved_plan_id = (
        workout.plan_version_id if workout is not None else plan_version_id
    )
    reconciliation_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:reconciliation:{workout_id or 'none'}:{source_type}:"
            f"{activity_id or 'unmatched'}:{matcher_version}:{operation_id}",
        )
    )
    return WorkoutReconciliation(
        id=reconciliation_id,
        plan_version_id=resolved_plan_id,
        planned_workout_id=workout_id,
        user_id=owner_id,
        athlete_id=(workout.athlete_id if workout is not None else athlete_id),
        source_type=source_type,
        activity_id=activity_id,
        matcher_version=matcher_version,
        operation_id=operation_id,
        **values,
    )


def create_workout_review(
    workout: PlannedWorkout,
    rule_version: str,
    reconciliation_id: str | None = None,
    operation_id: str = "automatic",
    **values,
) -> WorkoutReview:
    review_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:review:{workout.id}:"
            f"{reconciliation_id or 'unmatched'}:{rule_version}:{operation_id}",
        )
    )
    return WorkoutReview(
        id=review_id,
        plan_version_id=workout.plan_version_id,
        planned_workout_id=workout.id,
        reconciliation_id=reconciliation_id,
        user_id=workout.user_id,
        athlete_id=workout.athlete_id,
        rule_version=rule_version,
        operation_id=operation_id,
        **values,
    )


def create_readiness_assessment(
    user_id: str,
    local_date: date,
    workout: PlannedWorkout,
    revision: int,
    status: ReadinessStatus,
    safety_gate_result_id: str,
    rule_version: str,
    operation_id: str,
    input_snapshot: dict[str, Any],
    **values: Any,
) -> NextWorkoutReadinessAssessment:
    return NextWorkoutReadinessAssessment(
        id=stable_planning_id(
            "readiness", user_id, local_date, workout.id, revision, operation_id
        ),
        user_id=user_id,
        local_date=local_date,
        planned_workout_id=workout.id,
        revision=revision,
        status=status,
        safety_gate_result_id=safety_gate_result_id,
        rule_version=rule_version,
        operation_id=operation_id,
        input_snapshot_digest=planning_input_digest(input_snapshot),
        **values,
    )


PLAN_TRANSITIONS: dict[TrainingPlanStatus, frozenset[TrainingPlanStatus]] = {
    TrainingPlanStatus.GENERATING: frozenset(
        {TrainingPlanStatus.DRAFT, TrainingPlanStatus.GENERATION_FAILED}
    ),
    TrainingPlanStatus.DRAFT: frozenset({TrainingPlanStatus.PENDING_APPROVAL}),
    TrainingPlanStatus.PENDING_APPROVAL: frozenset(
        {
            TrainingPlanStatus.ACTIVE,
            TrainingPlanStatus.REJECTED,
            TrainingPlanStatus.REPROPOSAL_REQUESTED,
            TrainingPlanStatus.EXPIRED,
        }
    ),
    TrainingPlanStatus.ACTIVE: frozenset({TrainingPlanStatus.SUPERSEDED}),
    TrainingPlanStatus.REJECTED: frozenset(),
    TrainingPlanStatus.REPROPOSAL_REQUESTED: frozenset(),
    TrainingPlanStatus.EXPIRED: frozenset(),
    TrainingPlanStatus.GENERATION_FAILED: frozenset(),
    TrainingPlanStatus.SUPERSEDED: frozenset(),
}


def create_plan_lifecycle_event(
    plan: TrainingPlanVersion,
    from_status: TrainingPlanStatus,
    to_status: TrainingPlanStatus,
    reason_code: str,
    operation_id: str,
    **values: Any,
) -> TrainingPlanLifecycleEvent:
    if to_status not in PLAN_TRANSITIONS[from_status]:
        raise PlanVersionConflict(
            f"Invalid training plan transition: {from_status} -> {to_status}"
        )
    event_id = stable_planning_id(
        "plan-lifecycle", plan.user_id, plan.id, operation_id, to_status.value
    )
    return TrainingPlanLifecycleEvent(
        id=event_id,
        user_id=plan.user_id,
        plan_version_id=plan.id,
        from_status=from_status,
        to_status=to_status,
        reason_code=reason_code,
        operation_id=operation_id,
        **values,
    )


def create_workout_execution_state(
    workout: PlannedWorkout,
    revision: int,
    status: WorkoutExecutionStatus,
    operation_id: str,
    **values: Any,
) -> WorkoutExecutionState:
    return WorkoutExecutionState(
        id=stable_planning_id("execution", workout.id, revision, operation_id),
        user_id=workout.user_id,
        plan_version_id=workout.plan_version_id,
        planned_workout_id=workout.id,
        revision=revision,
        status=status,
        operation_id=operation_id,
        **values,
    )


def create_safety_gate_result(
    user_id: str,
    operation_id: str,
    status: SafetyGateStatus,
    reason_codes: Sequence[str],
    rule_version: str,
    input_snapshot: dict[str, Any],
    planned_workout_id: str | None = None,
    **values: Any,
) -> SafetyGateResult:
    digest = planning_input_digest(input_snapshot)
    return SafetyGateResult(
        id=stable_planning_id("safety-gate", user_id, operation_id, rule_version),
        user_id=user_id,
        planned_workout_id=planned_workout_id,
        status=status,
        reason_codes=list(reason_codes),
        rule_version=rule_version,
        input_snapshot_digest=digest,
        **values,
    )


def readiness_status_for_gate(
    gate: SafetyGateResult, requested: ReadinessStatus
) -> ReadinessStatus:
    if gate.status == SafetyGateStatus.BLOCKED:
        return ReadinessStatus.BLOCKED
    if gate.status == SafetyGateStatus.ADJUSTMENT_REQUIRED:
        return ReadinessStatus.WITH_ADJUSTMENT
    return requested


def stable_planning_id(kind: str, *parts: object) -> str:
    encoded = ":".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-coach:{kind}:{encoded}"))


def planning_input_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _firestore_payload(model: BaseModel) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, datetime):
            return _require_utc(value)
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return convert(model.model_dump(mode="python"))


def _version_key(user_id: str, version: int) -> str:
    return f"{user_id}:{version}"


def _save_immutable(store: dict, item_id: str, item: ImmutableModel) -> None:
    existing = store.get(item_id)
    if existing is not None and existing != item:
        raise PlanVersionConflict(f"Immutable record {item_id} already exists")
    store[item_id] = item
