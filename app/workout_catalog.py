from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

CATALOG_VERSION = "workout-catalog-v2"
RUNNING_ENVIRONMENT_KEYWORDS = ["ラン", "run", "公園", "屋外", "トレッドミル"]


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
        id="run-recovery-v1",
        sport="running",
        title="リカバリーラン",
        intensity="easy",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=20,
        description="疲労を残さない、ごく楽な回復目的のラン",
    ),
    WorkoutTemplate(
        id="run-free-v1",
        sport="running",
        title="フリーランニング",
        intensity="easy",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=20,
        description="会話できる楽な強度で、気分と体調に合わせて自由に走る",
    ),
    WorkoutTemplate(
        id="run-pace-light-v1",
        sport="running",
        title="ペース走（控えめ）",
        intensity="moderate",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=30,
        description="ウォームアップとクールダウンを含め、持続可能なやや速いペースを短く保つ",
    ),
    WorkoutTemplate(
        id="run-pace-steady-v1",
        sport="running",
        title="ペース走（しっかり）",
        intensity="moderate",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=40,
        description="十分な準備・整理運動を含め、過去の実績に見合う一定ペースを保つ",
    ),
    WorkoutTemplate(
        id="run-wave-v1",
        sport="running",
        title="ウェーブ走",
        intensity="moderate",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=35,
        description="楽な区間とやや速い区間を交互にし、無理なくペース変化へ慣れる",
    ),
    WorkoutTemplate(
        id="run-interval-400-v1",
        sport="running",
        title="400mインターバル",
        intensity="moderate",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=35,
        description=(
            "十分な準備・整理運動を含め、400mの高負荷走と60秒の休憩を"
            "交互に行う。回数とペースは過去の実績・体調・利用時間に合わせて抑える"
        ),
    ),
    WorkoutTemplate(
        id="run-lsd-v1",
        sport="running",
        title="LSD（ロングスローディスタンス）",
        intensity="easy",
        required_environment_keywords=["ラン", "run", "公園", "屋外", "トレッドミル"],
        outdoors_allowed=None,
        minimum_minutes=60,
        description="会話できる余裕を保つゆっくりした長めのラン。距離より時間と継続を優先する",
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
        id="bike-recovery-v1",
        sport="cycling",
        title="インドアバイク・リカバリー",
        intensity="easy",
        required_environment_keywords=["バイク", "bike", "cycling", "ローラー"],
        outdoors_allowed=False,
        minimum_minutes=20,
        description="軽い回転で脚をほぐす回復ライド",
    ),
    WorkoutTemplate(
        id="bike-tempo-v1",
        sport="cycling",
        title="インドアバイク・テンポ",
        intensity="moderate",
        required_environment_keywords=["バイク", "bike", "cycling", "ローラー"],
        outdoors_allowed=False,
        minimum_minutes=35,
        description="ウォームアップ後、持続可能だが会話は短くなる強度を短いブロックで行う",
    ),
    WorkoutTemplate(
        id="bike-cadence-v1",
        sport="cycling",
        title="インドアバイク・ケイデンス",
        intensity="easy",
        required_environment_keywords=["バイク", "bike", "cycling", "ローラー"],
        outdoors_allowed=False,
        minimum_minutes=25,
        description="軽めの負荷で回転数の変化を練習し、フォームと呼吸を整える",
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
    WorkoutTemplate(
        id="bodyweight-core-v1",
        sport="bodyweight",
        title="自重・体幹ベーシック",
        intensity="easy",
        required_environment_keywords=["自宅", "自重", "bodyweight", "ホーム", "ジム"],
        outdoors_allowed=False,
        minimum_minutes=15,
        description="呼吸とフォームを優先する体幹・安定性の基礎トレーニング",
    ),
]


def catalog_payload() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in CATALOG]


def compatible_templates(
    environments: list[dict[str, str]] | dict[str, str],
    enabled_template_ids: list[str] | None = None,
    custom_running_candidates: list[dict[str, Any]] | None = None,
) -> list[WorkoutTemplate]:
    names = " ".join(
        environments.values()
        if isinstance(environments, dict)
        else (item["name"] for item in environments)
    ).lower()
    enabled = set(enabled_template_ids) if enabled_template_ids is not None else None
    custom_templates = [
        WorkoutTemplate(
            id=item["id"],
            sport="running",
            title=item["title"],
            intensity=item["intensity"],
            required_environment_keywords=RUNNING_ENVIRONMENT_KEYWORDS,
            outdoors_allowed=None,
            minimum_minutes=int(item["minimum_minutes"]),
            description=item["description"],
        )
        for item in (custom_running_candidates or [])
    ]
    return [
        item
        for item in [*CATALOG, *custom_templates]
        if (enabled is None or item.id in enabled)
        and any(keyword in names for keyword in item.required_environment_keywords)
    ]


def prescribe(
    slot: dict[str, Any],
    environments: dict[str, str],
    profiles: list[dict[str, Any]],
    templates: list[WorkoutTemplate] | None = None,
) -> dict[str, Any] | None:
    names = " ".join(
        environments.get(item, "").lower() for item in slot["environment_ids"]
    )
    max_minutes = int(slot["max_workout_minutes"])
    template = next(
        (
            item
            for item in (templates if templates is not None else CATALOG)
            if _matches(item, names, slot, max_minutes)
        ),
        None,
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
