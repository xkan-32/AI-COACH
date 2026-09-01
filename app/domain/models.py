from datetime import date, datetime
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
