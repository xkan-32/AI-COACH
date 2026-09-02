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


class WorkoutProposal(BaseModel):
    id: str
    athlete_id: str
    source_activity_id: str
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
