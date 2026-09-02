import base64
import binascii
import hashlib
import hmac
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from itertools import pairwise

from app.domain.models import (
    Activity,
    ActivitySegmentMetrics,
    ActivityStreamPoint,
    RouteComparisonSummary,
    RouteFingerprint,
)

SEGMENT_LENGTH_METERS = 250
SEGMENT_METRICS_VERSION = "v1"
ROUTE_FINGERPRINT_VERSION = "v1"
ROUTE_COMPARISON_VERSION = "v1"
ROUTE_TRIM_METERS = 500
ROUTE_SAMPLE_METERS = 250
ROUTE_QUANTIZATION_DECIMALS = 3


class InvalidRouteFingerprintKey(ValueError):
    pass


class RouteFingerprintHasher:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(encoded_key.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidRouteFingerprintKey(
                "Route fingerprint key must be valid base64"
            ) from exc
        if len(key) != 32:
            raise InvalidRouteFingerprintKey(
                "Route fingerprint key must decode to exactly 32 bytes"
            )
        self._key = key

    def create(
        self,
        activity: Activity,
        points: Sequence[tuple[float, float, float]],
    ) -> RouteFingerprint | None:
        selected = _sample_route(points)
        if len(selected) < 2:
            return None
        canonical = ";".join(
            f"{latitude:.{ROUTE_QUANTIZATION_DECIMALS}f},"
            f"{longitude:.{ROUTE_QUANTIZATION_DECIMALS}f}"
            for _, latitude, longitude in selected
        )
        payload = (
            f"{ROUTE_FINGERPRINT_VERSION}:{activity.athlete_id}:"
            f"{round(activity.distance_meters / ROUTE_SAMPLE_METERS)}:{canonical}"
        )
        route_hash = hmac.new(
            self._key, payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return RouteFingerprint(
            activity_id=activity.id,
            athlete_id=activity.athlete_id,
            activity_started_at=activity.started_at,
            fingerprint_version=ROUTE_FINGERPRINT_VERSION,
            route_hash=route_hash,
            covered_distance_meters=selected[-1][0] - selected[0][0],
            sampled_point_count=len(selected),
            trim_start_meters=ROUTE_TRIM_METERS,
            trim_end_meters=ROUTE_TRIM_METERS,
            quantization_decimals=ROUTE_QUANTIZATION_DECIMALS,
        )


def compute_segment_metrics(
    activity: Activity,
    points: Sequence[ActivityStreamPoint],
) -> list[ActivitySegmentMetrics]:
    bins: dict[int, list[ActivityStreamPoint]] = defaultdict(list)
    for point in points:
        if point.distance_meters is None:
            continue
        index = min(
            max(0, math.ceil(point.distance_meters / SEGMENT_LENGTH_METERS) - 1),
            max(
                0,
                int(max(activity.distance_meters - 0.001, 0) // SEGMENT_LENGTH_METERS),
            ),
        )
        bins[index].append(point)
        if (
            point.distance_meters > 0
            and point.distance_meters < activity.distance_meters
            and point.distance_meters % SEGMENT_LENGTH_METERS == 0
        ):
            bins[index + 1].append(point)
    segments = [
        _summarize_segment(activity, index, samples)
        for index, samples in sorted(bins.items())
        if samples
    ]
    _assign_relative_load(segments)
    return segments


def compare_route_segments(
    activity: Activity,
    route_hash: str,
    current: Sequence[ActivitySegmentMetrics],
    baselines: Sequence[Sequence[ActivitySegmentMetrics]],
) -> RouteComparisonSummary | None:
    usable = [list(items) for items in baselines if items]
    if len(usable) < 2:
        return None
    current_pace = _mean_metric(current, "pace_seconds_per_km")
    baseline_paces = [
        value
        for items in usable
        if (value := _mean_metric(items, "pace_seconds_per_km")) is not None
    ]
    current_hr = _mean_metric(current, "average_heartrate_bpm")
    baseline_hrs = [
        value
        for items in usable
        if (value := _mean_metric(items, "average_heartrate_bpm")) is not None
    ]
    current_cadence = _mean_metric(current, "average_cadence_per_minute")
    baseline_cadences = [
        value
        for items in usable
        if (value := _mean_metric(items, "average_cadence_per_minute")) is not None
    ]
    baseline_pace = statistics.median(baseline_paces) if baseline_paces else None
    return RouteComparisonSummary(
        activity_id=activity.id,
        athlete_id=activity.athlete_id,
        activity_started_at=activity.started_at,
        route_hash=route_hash,
        comparison_version=ROUTE_COMPARISON_VERSION,
        baseline_activity_count=len(usable),
        previous_activity_id=usable[0][0].activity_id,
        pace_delta_percent=(
            (current_pace - baseline_pace) / baseline_pace * 100
            if current_pace is not None and baseline_pace
            else None
        ),
        heartrate_delta_bpm=(
            current_hr - statistics.median(baseline_hrs)
            if current_hr is not None and baseline_hrs
            else None
        ),
        cadence_delta_per_minute=(
            current_cadence - statistics.median(baseline_cadences)
            if current_cadence is not None and baseline_cadences
            else None
        ),
        high_load_segment_indexes=[
            segment.segment_index for segment in current if segment.high_load_reasons
        ],
    )


def _summarize_segment(
    activity: Activity,
    index: int,
    samples: Sequence[ActivityStreamPoint],
) -> ActivitySegmentMetrics:
    ordered = sorted(samples, key=lambda item: item.sample_index)
    distances = [
        value for point in ordered if (value := point.distance_meters) is not None
    ]
    times = [value for point in ordered if (value := point.time_seconds) is not None]
    altitudes = [
        value for point in ordered if (value := point.altitude_meters) is not None
    ]
    grades = [value for point in ordered if (value := point.grade_percent) is not None]
    heartrates = [
        value for point in ordered if (value := point.heartrate_bpm) is not None
    ]
    cadences = [value for point in ordered if (value := point.cadence_rpm) is not None]
    start = max(index * SEGMENT_LENGTH_METERS, min(distances, default=0))
    end = min(
        (index + 1) * SEGMENT_LENGTH_METERS,
        max(distances, default=start),
        activity.distance_meters,
    )
    distance = max(0.0, end - start)
    elapsed = max(times) - min(times) if len(times) >= 2 else None
    gain, loss = _elevation_change(altitudes)
    reasons = [
        reason
        for present, reason in (
            (len(distances) >= 2, "distance_missing"),
            (len(times) >= 2, "time_missing"),
            (bool(grades), "grade_missing"),
            (bool(heartrates), "heartrate_missing"),
            (bool(cadences), "cadence_missing"),
        )
        if not present
    ]
    return ActivitySegmentMetrics(
        activity_id=activity.id,
        athlete_id=activity.athlete_id,
        activity_started_at=activity.started_at,
        computation_version=SEGMENT_METRICS_VERSION,
        segment_index=index,
        start_distance_meters=start,
        end_distance_meters=end,
        elapsed_seconds=elapsed,
        pace_seconds_per_km=(
            elapsed / (distance / 1000)
            if elapsed is not None and distance > 0
            else None
        ),
        elevation_gain_meters=gain,
        elevation_loss_meters=loss,
        average_grade_percent=statistics.fmean(grades) if grades else None,
        average_heartrate_bpm=(statistics.fmean(heartrates) if heartrates else None),
        max_heartrate_bpm=max(heartrates) if heartrates else None,
        average_cadence_per_minute=(
            _cadence_per_minute(activity.activity_type, cadences) if cadences else None
        ),
        metric_quality="full" if not reasons else "partial",
        quality_reasons=reasons,
    )


def _assign_relative_load(segments: list[ActivitySegmentMetrics]) -> None:
    if not segments:
        return
    median_pace = _median_metric(segments, "pace_seconds_per_km")
    median_hr = _median_metric(segments, "average_heartrate_bpm")
    median_cadence = _median_metric(segments, "average_cadence_per_minute")
    scored: list[tuple[int, float, list[str]]] = []
    for index, segment in enumerate(segments):
        reasons: list[str] = []
        score = 0.0
        if (
            segment.average_heartrate_bpm is not None
            and median_hr
            and segment.average_heartrate_bpm >= median_hr * 1.05
        ):
            reasons.append("heart_rate_above_session")
            score += (segment.average_heartrate_bpm / median_hr - 1) * 10
        if (
            segment.average_grade_percent is not None
            and segment.average_grade_percent >= 3
        ):
            reasons.append("sustained_climb")
            score += segment.average_grade_percent / 3
        if (
            segment.pace_seconds_per_km is not None
            and median_pace
            and segment.pace_seconds_per_km >= median_pace * 1.1
        ):
            reasons.append("pace_drop")
            score += (segment.pace_seconds_per_km / median_pace - 1) * 5
        if (
            segment.average_cadence_per_minute is not None
            and median_cadence
            and segment.average_cadence_per_minute <= median_cadence * 0.95
        ):
            reasons.append("cadence_drop")
            score += (1 - segment.average_cadence_per_minute / median_cadence) * 5
        scored.append((index, score, reasons))
    order = sorted(scored, key=lambda item: (item[1], item[0]))
    denominator = max(1, len(order) - 1)
    for rank, (index, _, reasons) in enumerate(order):
        percentile = rank / denominator * 100
        segments[index].relative_load_rank_percentile = percentile
        segments[index].high_load_reasons = reasons if percentile >= 75 else []


def _sample_route(
    points: Sequence[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    ordered = sorted(points, key=lambda item: item[0])
    if not ordered:
        return []
    minimum = ROUTE_TRIM_METERS
    maximum = ordered[-1][0] - ROUTE_TRIM_METERS
    if maximum <= minimum:
        return []
    selected: list[tuple[float, float, float]] = []
    target = minimum
    candidates = [point for point in ordered if minimum <= point[0] <= maximum]
    for point in candidates:
        if point[0] >= target:
            selected.append(
                (
                    point[0],
                    round(point[1], ROUTE_QUANTIZATION_DECIMALS),
                    round(point[2], ROUTE_QUANTIZATION_DECIMALS),
                )
            )
            target += ROUTE_SAMPLE_METERS
    return selected


def _elevation_change(
    values: Sequence[float],
) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    gain = 0.0
    loss = 0.0
    for before, after in pairwise(values):
        change = after - before
        if change > 0:
            gain += change
        else:
            loss -= change
    return gain, loss


def _cadence_per_minute(activity_type: str, values: Sequence[float]) -> float:
    value = statistics.fmean(values)
    return (
        value * 2
        if activity_type in {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}
        else value
    )


def _median_metric(
    segments: Sequence[ActivitySegmentMetrics], name: str
) -> float | None:
    values = [
        getattr(item, name) for item in segments if getattr(item, name) is not None
    ]
    return statistics.median(values) if values else None


def _mean_metric(segments: Sequence[ActivitySegmentMetrics], name: str) -> float | None:
    values = [
        getattr(item, name) for item in segments if getattr(item, name) is not None
    ]
    return statistics.fmean(values) if values else None
