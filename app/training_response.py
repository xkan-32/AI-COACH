from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from app.domain.models import Activity

TRAINING_RESPONSE_VERSION = "training-response-v1"
TRAINING_RESPONSE_WINDOW_DAYS = 14


class TrainingResponseSignal(BaseModel):
    version: str = TRAINING_RESPONSE_VERSION
    observed_from: datetime | None = None
    observed_until: datetime | None = None
    evidence_activity_ids: list[str] = Field(default_factory=list, max_length=30)
    evidence_source: str = "all_recent_activities"
    completed_activity_count: int = Field(ge=0)
    hard_rpe_activity_count: int = Field(ge=0)
    recommended_maximum_moderate_days: int | None = Field(default=None, ge=0, le=2)
    reason_codes: list[str] = Field(default_factory=list, max_length=10)


def derive_training_response_signal(
    activities: list[Activity],
    now: datetime,
    confirmed_planned_activity_ids: set[str] | None = None,
) -> TrainingResponseSignal:
    reference = now.astimezone(UTC)
    recent = sorted(
        (
            item
            for item in activities
            if reference - timedelta(days=TRAINING_RESPONSE_WINDOW_DAYS)
            <= item.started_at.astimezone(UTC)
            <= reference
            and item.completion_status != "skipped"
        ),
        key=lambda item: item.started_at,
    )
    if confirmed_planned_activity_ids is not None:
        recent = [item for item in recent if item.id in confirmed_planned_activity_ids]
        source = "confirmed_planned_activities"
        reasons = (
            ["confirmed_planned_activity_reconciliations"]
            if recent
            else ["no_confirmed_planned_response"]
        )
    else:
        source = "all_recent_activities"
        reasons = ["recent_completed_activities"] if recent else ["no_recent_response"]
    hard_rpe = [item for item in recent if item.perceived_intensity == "hard"]
    maximum_moderate_days = None
    if len(hard_rpe) >= 2:
        maximum_moderate_days = 1
        reasons.append("multiple_recent_hard_rpe_reports")
    return TrainingResponseSignal(
        observed_from=recent[0].started_at.astimezone(UTC) if recent else None,
        observed_until=recent[-1].started_at.astimezone(UTC) if recent else None,
        evidence_activity_ids=[item.id for item in recent],
        evidence_source=source,
        completed_activity_count=len(recent),
        hard_rpe_activity_count=len(hard_rpe),
        recommended_maximum_moderate_days=maximum_moderate_days,
        reason_codes=reasons,
    )
