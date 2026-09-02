from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field


class ConditionLevel(StrEnum):
    GOOD = "good"
    FATIGUED = "fatigued"
    DISCOMFORT = "discomfort"
    PAIN = "pain"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GoalPriority(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"


class TrainingEnvironmentCategory(StrEnum):
    ACTIVITY_PLACE = "activity_place"
    EQUIPMENT = "equipment"
    OTHER = "other"


class TrainingEnvironmentStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Goal(BaseModel):
    id: str
    goal_type: str = Field(min_length=1, max_length=50)
    target: str = Field(min_length=1, max_length=200)
    target_date: date | None = None
    priority: GoalPriority
    status: GoalStatus = GoalStatus.ACTIVE


class TrainingEnvironment(BaseModel):
    id: str
    display_name: str = Field(min_length=1, max_length=100)
    category: TrainingEnvironmentCategory
    status: TrainingEnvironmentStatus = TrainingEnvironmentStatus.ACTIVE
    detail: str | None = Field(default=None, max_length=200)


class Activity(BaseModel):
    id: str
    athlete_id: str
    activity_type: str
    started_at: datetime
    duration_seconds: int = Field(ge=0)
    distance_meters: float = Field(ge=0)
    description: str = ""
    elapsed_seconds: int | None = Field(default=None, ge=0)
    total_elevation_gain_meters: float | None = Field(default=None, ge=0)
    average_speed_mps: float | None = Field(default=None, ge=0)
    max_speed_mps: float | None = Field(default=None, ge=0)
    has_heartrate: bool = False
    average_heartrate_bpm: float | None = Field(default=None, ge=0)
    max_heartrate_bpm: float | None = Field(default=None, ge=0)
    average_cadence_per_minute: float | None = Field(default=None, ge=0)
    suffer_score: float | None = Field(default=None, ge=0)
    calories: float | None = Field(default=None, ge=0)


class ActivityLap(BaseModel):
    activity_id: str
    athlete_id: str
    activity_started_at: datetime | None = None
    lap_index: int = Field(ge=0)
    name: str = ""
    elapsed_seconds: int = Field(ge=0)
    moving_seconds: int = Field(ge=0)
    distance_meters: float = Field(ge=0)
    total_elevation_gain_meters: float | None = Field(default=None, ge=0)
    average_speed_mps: float | None = Field(default=None, ge=0)
    max_speed_mps: float | None = Field(default=None, ge=0)
    average_heartrate_bpm: float | None = Field(default=None, ge=0)
    max_heartrate_bpm: float | None = Field(default=None, ge=0)
    average_cadence_per_minute: float | None = Field(default=None, ge=0)


class ActivityStreamPoint(BaseModel):
    activity_id: str
    athlete_id: str
    activity_started_at: datetime | None = None
    sample_index: int = Field(ge=0)
    time_seconds: int | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    altitude_meters: float | None = None
    velocity_mps: float | None = Field(default=None, ge=0)
    heartrate_bpm: float | None = Field(default=None, ge=0)
    cadence_rpm: float | None = Field(default=None, ge=0)
    watts: float | None = Field(default=None, ge=0)
    temperature_celsius: float | None = None
    moving: bool | None = None
    grade_percent: float | None = None


class ActivityMetrics(BaseModel):
    activity_id: str
    athlete_id: str
    computation_version: str
    metric_quality: str
    quality_reasons: list[str] = Field(default_factory=list)
    average_pace_seconds_per_km: float | None = Field(default=None, ge=0)
    ascent_meters: float | None = Field(default=None, ge=0)
    descent_meters: float | None = Field(default=None, ge=0)
    uphill_seconds: int | None = Field(default=None, ge=0)
    flat_seconds: int | None = Field(default=None, ge=0)
    downhill_seconds: int | None = Field(default=None, ge=0)
    uphill_meters: float | None = Field(default=None, ge=0)
    flat_meters: float | None = Field(default=None, ge=0)
    downhill_meters: float | None = Field(default=None, ge=0)
    pace_variability_percent: float | None = Field(default=None, ge=0)
    lap_pace_variability_percent: float | None = Field(default=None, ge=0)
    average_heartrate_bpm: float | None = Field(default=None, ge=0)
    max_heartrate_bpm: float | None = Field(default=None, ge=0)
    heartrate_drift_percent: float | None = None
    average_cadence_per_minute: float | None = Field(default=None, ge=0)
    suffer_score: float | None = Field(default=None, ge=0)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActivitySegmentMetrics(BaseModel):
    activity_id: str
    athlete_id: str
    activity_started_at: datetime
    computation_version: str
    segment_index: int = Field(ge=0)
    start_distance_meters: float = Field(ge=0)
    end_distance_meters: float = Field(ge=0)
    elapsed_seconds: int | None = Field(default=None, ge=0)
    pace_seconds_per_km: float | None = Field(default=None, ge=0)
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    elevation_loss_meters: float | None = Field(default=None, ge=0)
    average_grade_percent: float | None = None
    average_heartrate_bpm: float | None = Field(default=None, ge=0)
    max_heartrate_bpm: float | None = Field(default=None, ge=0)
    average_cadence_per_minute: float | None = Field(default=None, ge=0)
    relative_load_rank_percentile: float | None = Field(default=None, ge=0, le=100)
    high_load_reasons: list[str] = Field(default_factory=list)
    metric_quality: str
    quality_reasons: list[str] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RouteFingerprint(BaseModel):
    activity_id: str
    athlete_id: str
    activity_started_at: datetime
    fingerprint_version: str
    route_hash: str
    covered_distance_meters: float = Field(ge=0)
    sampled_point_count: int = Field(ge=0)
    trim_start_meters: float = Field(ge=0)
    trim_end_meters: float = Field(ge=0)
    quantization_decimals: int = Field(ge=0, le=6)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RouteComparisonSummary(BaseModel):
    activity_id: str
    athlete_id: str
    activity_started_at: datetime
    route_hash: str
    comparison_version: str
    baseline_activity_count: int = Field(ge=0)
    previous_activity_id: str | None = None
    pace_delta_percent: float | None = None
    heartrate_delta_bpm: float | None = None
    cadence_delta_per_minute: float | None = None
    high_load_segment_indexes: list[int] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConditionReport(BaseModel):
    athlete_id: str
    activity_id: str
    level: ConditionLevel
    body_part: str | None = None
    severity: int | None = Field(default=None, ge=1, le=10)
    worsened_during_activity: bool | None = None
    comment: str = ""
    reported_at: datetime


class CoachingContext(BaseModel):
    goals: list[Goal] = Field(default_factory=list)
    training_resources: list[TrainingEnvironment] = Field(default_factory=list)
    recent_activities: list[Activity] = Field(default_factory=list)
    recent_conditions: list[ConditionReport] = Field(default_factory=list)
    current_activity_metrics: ActivityMetrics | None = None
    high_load_segments: list[ActivitySegmentMetrics] = Field(default_factory=list)
    current_route_comparison: RouteComparisonSummary | None = None


class WorkoutProposal(BaseModel):
    id: str
    athlete_id: str
    source_activity_id: str
    plan_version_id: str | None = None
    planned_workout_id: str | None = None
    review_id: str | None = None
    target_date: date
    title: str
    rationale: str
    duration_minutes: int = Field(ge=0)
    intensity: str
    safety_notes: list[str] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=24)
    )
