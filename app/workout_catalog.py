from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

CATALOG_VERSION = "workout-catalog-v1"


class WorkoutTemplate(BaseModel):
    id: str
    sport: str
    title: str
    intensity: str
    required_environment_keywords: list[str]
    outdoors_allowed: bool | None = None
    minimum_minutes: int = Field(ge=1)
    description: str


CATALOG = [
    WorkoutTemplate(
        id="run-easy-v1",
        sport="running",
        title="イージーラン",
        intensity="easy",
        required_environment_keywords=["ラン", "run", "公園", "屋外"],
        outdoors_allowed=True,
        minimum_minutes=20,
        description="会話できる余裕を残す一定走",
    ),
    WorkoutTemplate(
        id="bike-endurance-v1",
        sport="cycling",
        title="インドアバイク有酸素",
        intensity="easy",
        required_environment_keywords=["バイク", "bike", "cycling"],
        outdoors_allowed=False,
        minimum_minutes=20,
        description="会話可能な心拍で回す",
    ),
    WorkoutTemplate(
        id="bodyweight-full-v1",
        sport="bodyweight",
        title="自重・全身ベーシック",
        intensity="easy",
        required_environment_keywords=["自宅", "自重", "bodyweight", "ホーム"],
        outdoors_allowed=False,
        minimum_minutes=20,
        description="フォームを優先する全身サーキット",
    ),
]


def catalog_payload() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in CATALOG]


def prescribe(
    slot: dict[str, Any], environments: dict[str, str], profiles: list[dict[str, Any]]
) -> dict[str, Any] | None:
    names = " ".join(
        environments.get(item, "").lower() for item in slot["environment_ids"]
    )
    max_minutes = int(slot["max_workout_minutes"])
    template = next(
        (item for item in CATALOG if _matches(item, names, slot, max_minutes)), None
    )
    if template is None:
        return None
    duration = min(max_minutes, 35 if template.sport == "running" else 30)
    profile = next(
        (
            item
            for item in profiles
            if item.get("sport")
            == {"running": "running", "cycling": "cycling"}.get(template.sport)
        ),
        None,
    )
    rationale = template.description
    if (
        template.id == "run-easy-v1"
        and profile
        and profile.get("pace_seconds_per_km", {}).get("easy")
    ):
        pace = profile["pace_seconds_per_km"]["easy"]
        rationale += f"。目安は{pace['lower']}〜{pace['upper']}秒/km、苦しくなれば歩きへ切り替えます。"
    elif (
        template.id.startswith("bike")
        and profile
        and profile.get("heartrate_bpm", {}).get("easy")
    ):
        heart = profile["heartrate_bpm"]["easy"]
        rationale += f"。目安心拍は{heart['lower']}〜{heart['upper']}bpm、会話可能度を優先します。"
    elif template.sport == "bodyweight":
        rationale += "。スクワット8回、ランジ各6回、プッシュアップ6回、デッドバグ各6回を2周、各種目の間は30秒休みます。"
    return {
        "template_id": template.id,
        "workout_type": template.title,
        "intensity": template.intensity,
        "duration": duration,
        "rationale": rationale,
        "outdoors": template.outdoors_allowed is True,
    }


def _matches(
    template: WorkoutTemplate, names: str, slot: dict[str, Any], max_minutes: int
) -> bool:
    return (
        max_minutes >= template.minimum_minutes
        and any(keyword in names for keyword in template.required_environment_keywords)
        and (template.outdoors_allowed is not True or slot["outdoors_allowed"])
    )
