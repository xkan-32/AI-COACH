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
    allowed_intensities: list[str] = Field(default_factory=list)
    structure: dict[str, Any] | None = None


def _running_structure(
    *,
    maximum_distance_km: float | None,
    fastest_pace_seconds_per_km: int | None,
    steps: list[dict[str, Any]],
    adjustment_guidance: str,
) -> dict[str, Any]:
    return {
        "sport": "running",
        "maximum_distance_km": maximum_distance_km,
        "fastest_pace_seconds_per_km": fastest_pace_seconds_per_km,
        "steps": steps,
        "adjustment_guidance": adjustment_guidance,
    }


def _timed_structure(
    *,
    sport: str,
    maximum_duration_minutes: int,
    steps: list[dict[str, Any]],
    adjustment_guidance: str,
) -> dict[str, Any]:
    return {
        "sport": sport,
        "maximum_duration_minutes": maximum_duration_minutes,
        "steps": steps,
        "adjustment_guidance": adjustment_guidance,
    }


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
        allowed_intensities=["easy"],
        structure=_running_structure(
            maximum_distance_km=10,
            fastest_pace_seconds_per_km=360,
            steps=[
                {
                    "name": "フリーラン",
                    "distance_km": "体調に合わせる",
                    "pace": "会話できる楽なペース",
                }
            ],
            adjustment_guidance="AIは体調・利用時間に応じて距離とペースを安全側へ調整します。",
        ),
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
        allowed_intensities=["easy"],
        structure=_running_structure(
            maximum_distance_km=6,
            fastest_pace_seconds_per_km=390,
            steps=[
                {
                    "name": "リカバリーラン",
                    "distance_km": "3〜6km",
                    "pace": "会話できる楽なペース",
                }
            ],
            adjustment_guidance="疲労・痛みの情報があれば距離を短縮または休養へ切り替えます。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_running_structure(
            maximum_distance_km=None,
            fastest_pace_seconds_per_km=None,
            steps=[
                {
                    "name": "フリーランニング",
                    "distance_km": "指定なし",
                    "pace": "気分・体調に合わせる",
                }
            ],
            adjustment_guidance="距離・ペースを固定しない候補です。AIは所要時間だけを提案しても構いません。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_running_structure(
            maximum_distance_km=8,
            fastest_pace_seconds_per_km=370,
            steps=[
                {
                    "name": "ウォーミングアップ",
                    "distance_km": "0〜2km",
                    "pace": "7:00/km の例",
                },
                {
                    "name": "ペース走",
                    "distance_km": "2〜5km",
                    "pace": "6:20〜6:40/km の例",
                },
                {
                    "name": "クールダウン",
                    "distance_km": "1〜2km",
                    "pace": "7:00/km の例",
                },
            ],
            adjustment_guidance="AIは距離を短縮し、過去の走行ペースを超えない範囲でペースを設定します。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_running_structure(
            maximum_distance_km=10,
            fastest_pace_seconds_per_km=350,
            steps=[
                {
                    "name": "ウォーミングアップ",
                    "distance_km": "0〜2km",
                    "pace": "7:00/km の例",
                },
                {"name": "ペース走", "distance_km": "2〜7km", "pace": "6:10/km の例"},
                {
                    "name": "クールダウン",
                    "distance_km": "7〜8km",
                    "pace": "7:00/km の例",
                },
            ],
            adjustment_guidance="これは8kmの例です。AIは利用時間・体調・過去実績から距離とペースを範囲内で調整します。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_running_structure(
            maximum_distance_km=10,
            fastest_pace_seconds_per_km=330,
            steps=[
                {
                    "name": "ウォーミングアップ",
                    "distance_km": "0〜2km",
                    "pace": "7:00/km の例",
                },
                {
                    "name": "速い区間",
                    "distance_km": "各1km × 3",
                    "pace": "5:30/km の例",
                },
                {
                    "name": "ゆっくり区間",
                    "distance_km": "各1km × 2",
                    "pace": "6:00/km の例",
                },
                {"name": "クールダウン", "distance_km": "1km", "pace": "7:00/km の例"},
            ],
            adjustment_guidance="これは9kmの例です。AIは繰り返し回数・距離・速い区間のペースを安全な範囲で調整します。",
        ),
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
        allowed_intensities=["moderate"],
        structure=_running_structure(
            maximum_distance_km=9,
            fastest_pace_seconds_per_km=315,
            steps=[
                {
                    "name": "ウォーミングアップ",
                    "distance_km": "2km",
                    "pace": "楽なペース",
                },
                {
                    "name": "高負荷走",
                    "distance_km": "400m × 4〜6本",
                    "pace": "過去実績からAIが設定",
                },
                {"name": "休憩", "duration_seconds": 60, "detail": "ジョグまたは歩き"},
                {"name": "クールダウン", "distance_km": "1〜2km", "pace": "楽なペース"},
            ],
            adjustment_guidance="AIは本数・休憩・ペースを調整し、疲労や痛みがあれば別メニューへ変更します。",
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
        allowed_intensities=["easy"],
        structure=_running_structure(
            maximum_distance_km=20,
            fastest_pace_seconds_per_km=420,
            steps=[
                {
                    "name": "LSD",
                    "duration_minutes": "60〜120分",
                    "pace": "会話できるゆっくりしたペース",
                }
            ],
            adjustment_guidance="AIは直近走行時間と利用可能時間から、まず時間を決めて距離は目安として扱います。",
        ),
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
        allowed_intensities=["easy"],
        structure=_timed_structure(
            sport="cycling",
            maximum_duration_minutes=90,
            steps=[
                {
                    "name": "ウォームアップ",
                    "duration_minutes": 10,
                    "heart_rate": "楽に会話できる",
                },
                {
                    "name": "有酸素",
                    "duration_minutes": "10〜70",
                    "heart_rate": "会話可能な心拍",
                },
                {
                    "name": "クールダウン",
                    "duration_minutes": 5,
                    "heart_rate": "徐々に下げる",
                },
            ],
            adjustment_guidance="AIは利用時間と過去心拍から各ブロック時間・目安心拍を調整します。",
        ),
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
        allowed_intensities=["easy"],
        structure=_timed_structure(
            sport="cycling",
            maximum_duration_minutes=45,
            steps=[
                {
                    "name": "リカバリー",
                    "duration_minutes": "20〜45",
                    "heart_rate": "楽に会話できる低い心拍",
                }
            ],
            adjustment_guidance="疲労・違和感があれば時間を短縮または休養に変更します。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_timed_structure(
            sport="cycling",
            maximum_duration_minutes=60,
            steps=[
                {
                    "name": "ウォームアップ",
                    "duration_minutes": 10,
                    "heart_rate": "楽に会話できる",
                },
                {
                    "name": "テンポ",
                    "duration_minutes": "5〜10分 × 2〜3",
                    "heart_rate": "短い会話ができる範囲",
                },
                {
                    "name": "レスト",
                    "duration_minutes": "各5分",
                    "heart_rate": "回復するまで下げる",
                },
                {
                    "name": "クールダウン",
                    "duration_minutes": 5,
                    "heart_rate": "徐々に下げる",
                },
            ],
            adjustment_guidance="AIはブロック数と目安心拍を過去心拍・当日の体調に合わせて調整します。",
        ),
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
        allowed_intensities=["easy"],
        structure=_timed_structure(
            sport="cycling",
            maximum_duration_minutes=45,
            steps=[
                {
                    "name": "ウォームアップ",
                    "duration_minutes": 10,
                    "heart_rate": "楽に会話できる",
                },
                {
                    "name": "ケイデンス変化",
                    "duration_minutes": "5分 × 3",
                    "heart_rate": "楽な範囲を維持",
                },
                {
                    "name": "レスト",
                    "duration_minutes": "各3分",
                    "heart_rate": "回復するまで下げる",
                },
            ],
            adjustment_guidance="AIは利用時間に合わせてブロック数を調整し、心拍を上げ過ぎないようにします。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_timed_structure(
            sport="bodyweight",
            maximum_duration_minutes=40,
            steps=[
                {"name": "準備", "duration_minutes": 5, "detail": "関節を動かす"},
                {
                    "name": "サーキット",
                    "detail": "スクワット8回、ランジ各6回、プッシュアップ6回、デッドバグ各6回を2〜3周",
                },
                {"name": "休憩", "duration_seconds": 30, "detail": "種目間"},
            ],
            adjustment_guidance="AIは回数・周回数・休憩を体調と利用時間に合わせて調整します。",
        ),
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
        allowed_intensities=["easy", "moderate"],
        structure=_timed_structure(
            sport="bodyweight",
            maximum_duration_minutes=30,
            steps=[
                {
                    "name": "体幹",
                    "detail": "デッドバグ、バードドッグ、プランクをフォーム優先で2〜3周",
                },
                {"name": "休憩", "duration_seconds": 30, "detail": "種目間"},
            ],
            adjustment_guidance="AIは回数・保持時間を調整し、痛みが出る種目を除外します。",
        ),
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
            allowed_intensities=["easy", "moderate"],
            structure={
                "sport": "running",
                "maximum_distance_km": item.get("maximum_distance_km"),
                "fastest_pace_seconds_per_km": item.get("fastest_pace_seconds_per_km"),
                "steps": [],
                "freeform_example": item.get("example_structure", ""),
                "adjustment_guidance": "AIは最大距離・最速ペースを上限として、体調・利用時間・過去実績に合わせて組み立てます。",
            },
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
