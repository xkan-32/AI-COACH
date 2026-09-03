from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import Activity

PROFILE_VERSION = "performance-profile-v1"
PROFILE_WINDOW_DAYS = 84
MIN_SAMPLES_FOR_RANGES = 3


class NumericRange(BaseModel):
    lower: int = Field(ge=0)
    upper: int = Field(ge=0)


class PerformanceProfile(BaseModel):
    sport: Literal["running", "cycling"]
    profile_version: str = PROFILE_VERSION
    observed_from: datetime | None = None
    observed_until: datetime | None = None
    evidence_activity_ids: list[str] = Field(default_factory=list, max_length=60)
    candidate_activity_count: int = Field(ge=0)
    valid_activity_count: int = Field(ge=0)
    confidence: Literal["insufficient", "low", "moderate"]
    pace_seconds_per_km: dict[str, NumericRange] = Field(default_factory=dict)
    heartrate_bpm: dict[str, NumericRange] = Field(default_factory=dict)
    duration_minutes: dict[str, NumericRange] = Field(default_factory=dict)
    quality_reasons: list[str] = Field(default_factory=list, max_length=20)


def derive_performance_profiles(
    activities: list[Activity], now: datetime
) -> list[PerformanceProfile]:
    reference = now.astimezone(UTC)
    return [
        _running_profile(activities, reference),
        _cycling_profile(activities, reference),
    ]


def _running_profile(activities: list[Activity], now: datetime) -> PerformanceProfile:
    candidates = [item for item in activities if _is_running(item, now)]
    valid = [item for item in candidates if _valid_run(item)]
    paces = [_pace_seconds_per_km(item) for item in valid]
    paces = [item for item in paces if item is not None]
    return _profile(
        sport="running",
        candidates=candidates,
        valid=valid,
        now=now,
        pace_values=paces,
        heart_rates=_valid_heart_rates(valid),
        duration_values=[item.duration_seconds / 60 for item in valid],
        quality_reasons=_quality_reasons(candidates, valid, "run"),
    )


def _cycling_profile(activities: list[Activity], now: datetime) -> PerformanceProfile:
    candidates = [item for item in activities if _is_cycling(item, now)]
    valid = [
        item
        for item in candidates
        if item.duration_seconds >= 15 * 60
        and (
            item.elapsed_seconds is None
            or item.elapsed_seconds <= item.duration_seconds * 1.3
        )
    ]
    reasons = _quality_reasons(candidates, valid, "cycling")
    if valid and not any("virtual" in item.activity_type.lower() for item in valid):
        reasons.append("indoor_status_unverified")
    return _profile(
        sport="cycling",
        candidates=candidates,
        valid=valid,
        now=now,
        pace_values=[],
        heart_rates=_valid_heart_rates(valid),
        duration_values=[item.duration_seconds / 60 for item in valid],
        quality_reasons=reasons,
    )


def _profile(
    *,
    sport: Literal["running", "cycling"],
    candidates: list[Activity],
    valid: list[Activity],
    now: datetime,
    pace_values: list[float],
    heart_rates: list[float],
    duration_values: list[float],
    quality_reasons: list[str],
) -> PerformanceProfile:
    observed = [item.started_at.astimezone(UTC) for item in valid]
    count = len(valid)
    confidence: Literal["insufficient", "low", "moderate"] = (
        "moderate"
        if count >= 5
        else "low"
        if count >= MIN_SAMPLES_FOR_RANGES
        else "insufficient"
    )
    ranges_available = count >= MIN_SAMPLES_FOR_RANGES
    return PerformanceProfile(
        sport=sport,
        observed_from=min(observed) if observed else None,
        observed_until=max(observed) if observed else None,
        evidence_activity_ids=[item.id for item in valid],
        candidate_activity_count=len(candidates),
        valid_activity_count=count,
        confidence=confidence,
        pace_seconds_per_km=_pace_ranges(pace_values) if ranges_available else {},
        heartrate_bpm=_heart_rate_ranges(heart_rates) if ranges_available else {},
        duration_minutes=_duration_ranges(duration_values) if ranges_available else {},
        quality_reasons=list(dict.fromkeys(quality_reasons)),
    )


def _is_running(activity: Activity, now: datetime) -> bool:
    return activity.started_at.astimezone(UTC) >= now - timedelta(
        days=PROFILE_WINDOW_DAYS
    ) and activity.activity_type.lower().replace(" ", "") in {
        "run",
        "virtualrun",
        "trailrun",
    }


def _is_cycling(activity: Activity, now: datetime) -> bool:
    return activity.started_at.astimezone(UTC) >= now - timedelta(
        days=PROFILE_WINDOW_DAYS
    ) and activity.activity_type.lower().replace(" ", "") in {
        "ride",
        "virtualride",
        "ebikeride",
        "mountainbikeride",
        "gravelride",
    }


def _valid_run(activity: Activity) -> bool:
    pace = _pace_seconds_per_km(activity)
    return (
        activity.duration_seconds >= 15 * 60
        and activity.distance_meters >= 2_000
        and pace is not None
        and 210 <= pace <= 720
        and (
            activity.elapsed_seconds is None
            or activity.elapsed_seconds <= activity.duration_seconds * 1.3
        )
        and (
            activity.total_elevation_gain_meters is None
            or activity.total_elevation_gain_meters
            <= activity.distance_meters / 1_000 * 45
        )
    )


def _pace_seconds_per_km(activity: Activity) -> float | None:
    if activity.distance_meters <= 0:
        return None
    return activity.duration_seconds / (activity.distance_meters / 1_000)


def _valid_heart_rates(activities: list[Activity]) -> list[float]:
    return [
        item.average_heartrate_bpm
        for item in activities
        if item.average_heartrate_bpm is not None
        and 80 <= item.average_heartrate_bpm <= 220
    ]


def _pace_ranges(values: list[float]) -> dict[str, NumericRange]:
    if not values:
        return {}
    baseline = median(values)
    return {
        "easy": _range(baseline * 1.08, baseline * 1.25),
        "steady": _range(baseline * 0.97, baseline * 1.08),
        "quality": _range(baseline * 0.88, baseline * 0.98),
    }


def _heart_rate_ranges(values: list[float]) -> dict[str, NumericRange]:
    if len(values) < MIN_SAMPLES_FOR_RANGES:
        return {}
    baseline = median(values)
    return {
        "easy": _range(max(80, baseline - 15), baseline),
        "steady": _range(baseline, min(220, baseline + 10)),
        "quality": _range(baseline + 5, min(220, baseline + 20)),
    }


def _duration_ranges(values: list[float]) -> dict[str, NumericRange]:
    if not values:
        return {}
    baseline = median(values)
    return {
        "recovery": _range(max(15, baseline * 0.5), max(20, baseline * 0.7)),
        "endurance": _range(max(20, baseline * 0.8), max(25, baseline * 1.1)),
        "quality": _range(max(20, baseline * 0.6), max(25, baseline * 0.85)),
    }


def _range(lower: float, upper: float) -> NumericRange:
    return NumericRange(lower=round(lower), upper=max(round(lower), round(upper)))


def _quality_reasons(
    candidates: list[Activity], valid: list[Activity], sport: str
) -> list[str]:
    reasons = [f"{sport}_activity_window_84_days"]
    if not candidates:
        reasons.append("no_recent_activity")
    elif len(valid) < len(candidates):
        reasons.append("outliers_or_low_quality_activities_excluded")
    if len(valid) < MIN_SAMPLES_FOR_RANGES:
        reasons.append("insufficient_valid_samples")
    return reasons
