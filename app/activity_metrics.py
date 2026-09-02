import statistics
from collections.abc import Sequence
from itertools import pairwise

from app.domain.models import (
    Activity,
    ActivityLap,
    ActivityMetrics,
    ActivityStreamPoint,
)

ACTIVITY_METRICS_VERSION = "v1"


def compute_activity_metrics(
    activity: Activity,
    laps: Sequence[ActivityLap],
    points: Sequence[ActivityStreamPoint],
) -> ActivityMetrics:
    analysis_points = list(points)
    altitudes = [
        point.altitude_meters
        for point in analysis_points
        if point.altitude_meters is not None
    ]
    paces = [
        1000 / point.velocity_mps
        for point in analysis_points
        if point.velocity_mps is not None and point.velocity_mps > 0
    ]
    heartrates = [
        point.heartrate_bpm
        for point in analysis_points
        if point.heartrate_bpm is not None
    ]
    cadences = [
        point.cadence_rpm for point in analysis_points if point.cadence_rpm is not None
    ]
    raw_ascent, raw_descent = _elevation_change(altitudes)
    ascent = activity.total_elevation_gain_meters or raw_ascent
    altitude_change = altitudes[-1] - altitudes[0] if len(altitudes) >= 2 else None
    descent = (
        max(0.0, ascent - altitude_change)
        if ascent is not None and altitude_change is not None
        else raw_descent
    )
    (
        uphill_seconds,
        flat_seconds,
        downhill_seconds,
        uphill_meters,
        flat_meters,
        downhill_meters,
    ) = _grade_totals(points)
    reasons = [
        reason
        for present, reason in (
            (bool(points), "streams_missing"),
            (bool(altitudes), "altitude_missing"),
            (bool(paces), "velocity_missing"),
            (bool(heartrates), "heartrate_missing"),
            (bool(cadences), "cadence_missing"),
            (bool(laps), "laps_missing"),
            (
                any(point.moving is not None for point in points),
                "moving_status_missing",
            ),
        )
        if not present
    ]
    quality = (
        "summary_only" if not points else "full" if not reasons else "partial_streams"
    )
    return ActivityMetrics(
        activity_id=activity.id,
        athlete_id=activity.athlete_id,
        computation_version=ACTIVITY_METRICS_VERSION,
        metric_quality=quality,
        quality_reasons=reasons,
        average_pace_seconds_per_km=_pace(
            activity.duration_seconds, activity.distance_meters
        ),
        ascent_meters=ascent,
        descent_meters=descent,
        uphill_seconds=uphill_seconds,
        flat_seconds=flat_seconds,
        downhill_seconds=downhill_seconds,
        uphill_meters=uphill_meters,
        flat_meters=flat_meters,
        downhill_meters=downhill_meters,
        pace_variability_percent=_coefficient_of_variation(paces),
        lap_pace_variability_percent=_lap_pace_variability(laps),
        average_heartrate_bpm=(
            statistics.fmean(heartrates)
            if heartrates
            else activity.average_heartrate_bpm
        ),
        max_heartrate_bpm=(
            max(heartrates) if heartrates else activity.max_heartrate_bpm
        ),
        heartrate_drift_percent=_half_drift(heartrates),
        average_cadence_per_minute=(
            _cadence_per_minute(activity.activity_type, cadences)
            if cadences
            else activity.average_cadence_per_minute
        ),
        suffer_score=activity.suffer_score,
    )


def _pace(seconds: int, distance_meters: float) -> float | None:
    if distance_meters <= 0:
        return None
    return seconds / (distance_meters / 1000)


def _elevation_change(values: Sequence[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    ascent = 0.0
    descent = 0.0
    for before, after in pairwise(values):
        change = after - before
        if change > 0:
            ascent += change
        else:
            descent -= change
    return ascent, descent


def _grade_totals(
    points: Sequence[ActivityStreamPoint],
) -> tuple[
    int | None,
    int | None,
    int | None,
    float | None,
    float | None,
    float | None,
]:
    seconds = {"uphill": 0, "flat": 0, "downhill": 0}
    meters = {"uphill": 0.0, "flat": 0.0, "downhill": 0.0}
    samples = 0
    for before, after in pairwise(points):
        if (
            before.time_seconds is None
            or after.time_seconds is None
            or before.grade_percent is None
            or before.distance_meters is None
            or after.distance_meters is None
        ):
            continue
        distance = max(0.0, after.distance_meters - before.distance_meters)
        if distance == 0:
            continue
        duration = max(0, after.time_seconds - before.time_seconds)
        band = (
            "uphill"
            if before.grade_percent >= 3
            else "downhill"
            if before.grade_percent <= -3
            else "flat"
        )
        seconds[band] += duration
        meters[band] += distance
        samples += 1
    if not samples:
        return None, None, None, None, None, None
    return (
        seconds["uphill"],
        seconds["flat"],
        seconds["downhill"],
        meters["uphill"],
        meters["flat"],
        meters["downhill"],
    )


def _coefficient_of_variation(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / mean * 100 if mean else None


def _lap_pace_variability(laps: Sequence[ActivityLap]) -> float | None:
    paces = [
        pace
        for lap in laps
        if (pace := _pace(lap.moving_seconds, lap.distance_meters)) is not None
    ]
    return _coefficient_of_variation(paces)


def _half_drift(values: Sequence[float]) -> float | None:
    if len(values) < 4:
        return None
    midpoint = len(values) // 2
    first = statistics.fmean(values[:midpoint])
    second = statistics.fmean(values[midpoint:])
    return (second - first) / first * 100 if first else None


def _cadence_per_minute(activity_type: str, raw_cadences: Sequence[float]) -> float:
    average = statistics.fmean(raw_cadences)
    if activity_type in {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}:
        return average * 2
    return average
