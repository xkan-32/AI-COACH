from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

from app.activity_data import ActivityIngestionStateStore
from app.condition import ActivityContext, ActivityContextStore, FollowUpMessenger
from app.domain.models import Activity, ActivitySource, TrainingEnvironment
from app.ingestion import ActivityStore
from app.planning import (
    ActivePlanPointerStore,
    PlannedWorkout,
    PlanningHistoryStore,
    TrainingSettingsStateStore,
    UserTrainingProfile,
)
from app.profile import TrainingResourceStore
from app.state import StravaTokenStore
from app.strava import StoredStravaToken, StravaApiError, StravaClient

logger = logging.getLogger(__name__)

MANUAL_ACTIVITY_TYPES = (
    ("ウェイト", "WeightTraining"),
    ("自宅トレーニング", "Workout"),
    ("ランニング", "Run"),
    ("バイク", "Ride"),
    ("その他", "other"),
)
COMPLETION_STATUSES = (
    ("完了", "completed"),
    ("一部実施", "partial"),
    ("予定変更", "replaced"),
    ("未実施", "skipped"),
)
INTENSITIES = (("弱", "easy"), ("中", "moderate"), ("強", "hard"))
KNOWN_STRAVA_SPORT_TYPES = {
    "WeightTraining",
    "Workout",
    "Run",
    "Ride",
    "Walk",
    "Yoga",
    "TrailRun",
    "VirtualRun",
    "VirtualRide",
}
MAX_DETAILS_CHARS = 200
MAX_COMMENT_CHARS = 200
MAX_TYPE_CHARS = 40
DRAFT_TTL = timedelta(hours=24)


class InvalidManualActivityAction(ValueError):
    pass


class ManualActivityDraftStore(Protocol):
    async def save(self, draft: ManualActivityDraft) -> None: ...
    async def get(self, line_user_id: str) -> ManualActivityDraft | None: ...
    async def delete(self, line_user_id: str) -> None: ...


class ManualStravaPublicationStore(Protocol):
    async def get(self, operation_key: str) -> str | None: ...
    async def save(self, operation_key: str, strava_activity_id: str) -> None: ...


@dataclass
class ManualActivityDraft:
    line_user_id: str
    user_id: str
    operation_id: str
    step: str = "type"
    athlete_id: str | None = None
    timezone: str = "Asia/Tokyo"
    activity_type: str | None = None
    started_at: datetime | None = None
    duration_minutes: int | None = None
    perceived_intensity: str | None = None
    completion_status: str | None = None
    planned_workout_id: str | None = None
    environment_ids: list[str] = field(default_factory=list)
    details: str = ""
    comment: str = ""
    strava_activity_id: str | None = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + DRAFT_TTL)


class ManualActivityWorkflow:
    def __init__(
        self,
        drafts: ManualActivityDraftStore,
        activities: ActivityStore,
        contexts: ActivityContextStore,
        messenger: FollowUpMessenger,
        settings: TrainingSettingsStateStore | None = None,
        environments: TrainingResourceStore | None = None,
        planning_history: PlanningHistoryStore | None = None,
        active_plans: ActivePlanPointerStore | None = None,
        tokens: StravaTokenStore | None = None,
        strava: StravaClient | None = None,
        publications: ManualStravaPublicationStore | None = None,
        ingestion_state: ActivityIngestionStateStore | None = None,
        on_saved: Callable[[Activity, str], Awaitable[None]] | None = None,
        on_completed: Callable[[Activity, str], Awaitable[None]] | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._drafts = drafts
        self._activities = activities
        self._contexts = contexts
        self._messenger = messenger
        self._settings = settings
        self._environments = environments
        self._planning_history = planning_history
        self._active_plans = active_plans
        self._tokens = tokens
        self._strava = strava
        self._publications = publications
        self._ingestion_state = ingestion_state
        self._on_saved = on_saved
        self._on_completed = on_completed
        self._clock = clock

    async def start(self, line_user_id: str) -> None:
        if await self._require_token(line_user_id) is None:
            return
        existing = await self._drafts.get(line_user_id)
        if existing is not None and existing.expires_at > self._clock():
            await self._prompt(existing)
            return
        profile = await self._profile(line_user_id)
        draft = ManualActivityDraft(
            line_user_id=line_user_id,
            user_id=line_user_id,
            operation_id=str(uuid.uuid4()),
            athlete_id=profile.provider_athlete_id or line_user_id,
            timezone=profile.timezone,
            expires_at=self._clock() + DRAFT_TTL,
        )
        await self._drafts.save(draft)
        await self._prompt(draft)

    async def handle_postback(self, line_user_id: str, data: str) -> bool:
        values = {key: items[0] for key, items in parse_qs(data).items() if items}
        if values.get("action") != "manual":
            return False
        if values.get("op") == "cancel":
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(
                line_user_id, "運動の手動記録をキャンセルしました。"
            )
            return True
        if values.get("op") == "save":
            return await self._handle_save(line_user_id, values.get("oid"))
        draft = await self._require_draft(line_user_id)
        self._ensure_operation(draft, values.get("oid"))
        operation = values.get("op", "")
        if operation == "type":
            selected = values.get("v", "")
            if selected == "other":
                draft.step = "custom_type"
                await self._drafts.save(draft)
                await self._messenger.send_text(
                    line_user_id, "種目名を40文字以内で入力してください。"
                )
                return True
            if selected not in {item[1] for item in MANUAL_ACTIVITY_TYPES}:
                raise InvalidManualActivityAction("種目を選択してください。")
            draft.activity_type = selected
            draft.step = "when"
        elif operation == "when":
            draft.started_at = self._started_at_choice(draft, values.get("v", ""))
            if draft.started_at is None:
                draft.step = "custom_when"
                await self._drafts.save(draft)
                await self._messenger.send_text(
                    line_user_id,
                    "開始日時を「2026-09-02 19:00」の形式で入力してください。",
                )
                return True
            draft.step = "duration"
        elif operation == "dur":
            draft.duration_minutes = _parse_duration(values.get("v", ""))
            draft.step = "intensity"
        elif operation == "int":
            if values.get("v") not in {item[1] for item in INTENSITIES}:
                raise InvalidManualActivityAction("強度を選択してください。")
            draft.perceived_intensity = values.get("v")
            draft.step = "status"
        elif operation == "stat":
            if values.get("v") not in {item[1] for item in COMPLETION_STATUSES}:
                raise InvalidManualActivityAction("完了状態を選択してください。")
            draft.completion_status = values.get("v")
            draft.step = await self._next_after_status(draft)
        elif operation == "plan":
            if values.get("v") != "skip":
                planned_id = values.get("id", "")
                if not any(
                    item.id == planned_id for item in await self._todays_workouts(draft)
                ):
                    raise InvalidManualActivityAction(
                        "この計画メニューは現在選択できません。"
                    )
                draft.planned_workout_id = planned_id
            draft.step = await self._next_after_plan(draft)
        elif operation == "env":
            if values.get("v") != "skip":
                environment_id = values.get("id", "")
                if not any(
                    item.id == environment_id
                    for item in await self._environments_for(draft)
                ):
                    raise InvalidManualActivityAction(
                        "この運動環境は現在選択できません。"
                    )
                draft.environment_ids = [environment_id]
            draft.step = "details"
        elif operation == "skip":
            if draft.step != "details":
                raise InvalidManualActivityAction("この項目はスキップできません。")
            draft.step = "confirm"
        else:
            raise InvalidManualActivityAction("選択された項目を確認できませんでした。")
        await self._drafts.save(draft)
        await self._prompt(draft)
        return True

    async def handle_text(self, line_user_id: str, text: str) -> bool:
        value = text.strip()
        if value.lower() in {"cancel", "キャンセル"}:
            draft = await self._drafts.get(line_user_id)
            if draft is None:
                return False
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(
                line_user_id, "運動の手動記録をキャンセルしました。"
            )
            return True
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            return False
        self._ensure_fresh(draft)
        if value in {"スキップ", "skip"}:
            if draft.step == "details":
                draft.step = "confirm"
                await self._drafts.save(draft)
                await self._prompt(draft)
                return True
            raise InvalidManualActivityAction("この項目はスキップできません。")
        if draft.step == "custom_type":
            if not value or len(value) > MAX_TYPE_CHARS:
                raise InvalidManualActivityAction(
                    "種目名を40文字以内で入力してください。"
                )
            draft.activity_type = value
            draft.step = "when"
        elif draft.step == "custom_when":
            draft.started_at = _parse_local_datetime(
                value, draft.timezone, self._clock()
            )
            self._validate_started_at(draft.started_at)
            draft.step = "duration"
        elif draft.step == "duration":
            draft.duration_minutes = _parse_duration(value)
            draft.step = "intensity"
        elif draft.step == "details":
            if len(value) > MAX_DETAILS_CHARS + MAX_COMMENT_CHARS:
                raise InvalidManualActivityAction(
                    "内容は400文字以内で入力してください。"
                )
            details, _, comment = value.partition("\n")
            draft.details = details[:MAX_DETAILS_CHARS]
            draft.comment = comment.strip()[:MAX_COMMENT_CHARS]
            draft.step = "confirm"
        else:
            raise InvalidManualActivityAction(
                "ボタンから選択するか、キャンセルしてください。"
            )
        await self._drafts.save(draft)
        await self._prompt(draft)
        return True

    async def _handle_save(self, line_user_id: str, operation_id: str | None) -> bool:
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            if not operation_id:
                raise InvalidManualActivityAction(
                    "入力状態がありません。メニューからやり直してください。"
                )
            existing = await self._activities.get(
                manual_activity_id(line_user_id, operation_id)
            )
            if existing is None and self._publications is not None:
                strava_id = await self._publications.get(
                    manual_activity_id(line_user_id, operation_id)
                )
                if strava_id:
                    existing = await self._activities.get(strava_id)
            if existing is None:
                raise InvalidManualActivityAction(
                    "入力状態がありません。メニューからやり直してください。"
                )
            await self._messenger.send_text(
                line_user_id,
                "運動をStravaに記録しました。体調確認のあと、提案を同じActivityへ投稿できます。",
            )
            return True
        self._ensure_fresh(draft)
        self._ensure_operation(draft, operation_id)
        await self._complete(draft)
        return True

    async def _complete(self, draft: ManualActivityDraft) -> None:
        if (
            draft.activity_type is None
            or draft.started_at is None
            or draft.duration_minutes is None
        ):
            raise InvalidManualActivityAction("必要な項目が揃っていません。")
        token = await self._linked_token(draft.line_user_id)
        if token is None:
            raise InvalidManualActivityAction(
                "Strava連携が必要です。「Strava連携」と送って接続してください。"
            )
        await self._publish_to_strava(draft, token)
        activity = create_manual_activity(draft)
        existing = await self._activities.get(activity.id)
        if existing is not None and existing != activity:
            raise InvalidManualActivityAction("同じ操作の記録内容が一致しません。")
        first_save = existing is None
        if first_save:
            await self._activities.save(activity)
            await self._contexts.save(
                ActivityContext(
                    activity_id=activity.id,
                    athlete_id=activity.athlete_id,
                    line_user_id=draft.line_user_id,
                )
            )
            if self._ingestion_state is not None:
                await self._ingestion_state.complete(activity.id, "activity")
                await self._ingestion_state.complete(activity.id, "prompt")
        await self._drafts.delete(draft.line_user_id)
        logger.info(
            "manual_activity_saved activity_id=%s source_type=%s completion_status=%s",
            activity.id,
            activity.source_type.value,
            activity.completion_status,
        )
        await self._messenger.send_text(
            draft.line_user_id,
            "運動をStravaに記録しました。体調確認のあと、提案を同じActivityへ投稿できます。",
        )
        if self._on_saved is not None:
            await self._on_saved(activity, draft.line_user_id)
        if first_save and self._on_completed is not None:
            await self._on_completed(activity, draft.line_user_id)

    async def _publish_to_strava(
        self, draft: ManualActivityDraft, token: StoredStravaToken
    ) -> None:
        if self._strava is None:
            raise InvalidManualActivityAction("Stravaへの記録を開始できません。")
        operation_key = manual_activity_id(draft.user_id, draft.operation_id)
        strava_id = draft.strava_activity_id
        if strava_id is None and self._publications is not None:
            strava_id = await self._publications.get(operation_key)
        if strava_id is None:
            sport_type, name = _strava_sport_and_name(draft.activity_type or "")
            local_start = draft.started_at.astimezone(ZoneInfo(draft.timezone)).replace(
                tzinfo=None
            )
            try:
                created_activity = await self._strava.create_activity(
                    token.access_token,
                    name=name,
                    sport_type=sport_type,
                    start_date_local=local_start.isoformat(timespec="seconds"),
                    elapsed_time=(draft.duration_minutes or 0) * 60,
                    description=draft.comment,
                )
            except StravaApiError as exc:
                logger.info(
                    "manual_activity_strava_create_failed error_kind=%s strava_status_code=%s",
                    exc.error_kind,
                    exc.status_code,
                )
                raise InvalidManualActivityAction(
                    "Stravaへの記録に失敗しました。時間をおいてやり直してください。"
                ) from exc
            strava_id = created_activity.id
            draft.strava_activity_id = strava_id
            if self._publications is not None:
                await self._publications.save(operation_key, strava_id)
            await self._drafts.save(draft)
        draft.strava_activity_id = strava_id
        draft.athlete_id = token.athlete_id

    async def _require_token(self, line_user_id: str) -> StoredStravaToken | None:
        token = await self._linked_token(line_user_id)
        if token is not None:
            return token
        await self._messenger.send_text(
            line_user_id,
            "Strava連携が必要です。「Strava連携」と送って接続してから、"
            "もう一度メニューの運動を記録を押してください。",
        )
        return None

    async def _linked_token(self, line_user_id: str) -> StoredStravaToken | None:
        if self._tokens is None:
            return None
        token = await self._tokens.get_by_line_user_id(line_user_id)
        if token is None:
            return None
        if self._strava is None:
            return token
        if token.expires_at > int(self._clock().timestamp()) + 300:
            return token
        refreshed = await self._strava.refresh(token.refresh_token)
        updated = StoredStravaToken(
            athlete_id=token.athlete_id,
            line_user_id=token.line_user_id,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            expires_at=refreshed.expires_at,
        )
        await self._tokens.save(updated)
        return updated

    async def _prompt(self, draft: ManualActivityDraft) -> None:
        if draft.step == "type":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "記録する運動の種目を選んでください。途中保存は自動で、キャンセルもできます。",
                [
                    (label, _manual_data(draft, "type", v=value))
                    for label, value in MANUAL_ACTIVITY_TYPES
                ]
                + [("キャンセル", _manual_data(draft, "cancel"))],
            )
            return
        if draft.step == "when":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "実施した日時を選んでください。",
                [
                    ("今日の朝", _manual_data(draft, "when", v="today_morning")),
                    ("今日の夜", _manual_data(draft, "when", v="today_evening")),
                    ("昨日", _manual_data(draft, "when", v="yesterday")),
                    ("日時を入力", _manual_data(draft, "when", v="custom")),
                    ("キャンセル", _manual_data(draft, "cancel")),
                ],
            )
            return
        if draft.step == "duration":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "実施時間を選ぶか、分で入力してください。",
                [
                    (f"{minutes}分", _manual_data(draft, "dur", v=str(minutes)))
                    for minutes in (20, 30, 45, 60)
                ]
                + [("キャンセル", _manual_data(draft, "cancel"))],
            )
            return
        if draft.step == "intensity":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "主観的な強度を選んでください。",
                [
                    (label, _manual_data(draft, "int", v=value))
                    for label, value in INTENSITIES
                ]
                + [("キャンセル", _manual_data(draft, "cancel"))],
            )
            return
        if draft.step == "status":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "実施の完了状態を選んでください。",
                [
                    (label, _manual_data(draft, "stat", v=value))
                    for label, value in COMPLETION_STATUSES
                ]
                + [("キャンセル", _manual_data(draft, "cancel"))],
            )
            return
        if draft.step == "plan":
            choices = [
                (item.workout_type[:12], _manual_data(draft, "plan", id=item.id))
                for item in await self._todays_workouts(draft)
            ][:11]
            choices.append(("計画と紐づけない", _manual_data(draft, "plan", v="skip")))
            choices.append(("キャンセル", _manual_data(draft, "cancel")))
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "対応する本日の計画メニューがあれば選んでください。",
                choices,
            )
            return
        if draft.step == "environment":
            choices = [
                (item.display_name[:12], _manual_data(draft, "env", id=item.id))
                for item in await self._environments_for(draft)
            ][:11]
            choices.append(("指定しない", _manual_data(draft, "env", v="skip")))
            choices.append(("キャンセル", _manual_data(draft, "cancel")))
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "使った運動環境や器具があれば選んでください。",
                choices,
            )
            return
        if draft.step == "details":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "セット・回数・重量などの内容があれば入力してください。なければスキップできます。",
                [
                    ("スキップ", _manual_data(draft, "skip")),
                    ("キャンセル", _manual_data(draft, "cancel")),
                ],
            )
            return
        summary = (
            f"{draft.activity_type} / {_duration_label(draft.duration_minutes)} / "
            f"{draft.perceived_intensity} / {draft.completion_status}"
        )
        await self._messenger.send_quick_reply(
            draft.line_user_id,
            f"この内容でStravaに記録します。\n{summary}",
            [
                ("記録する", _manual_data(draft, "save")),
                ("キャンセル", _manual_data(draft, "cancel")),
            ],
        )

    async def _next_after_status(self, draft: ManualActivityDraft) -> str:
        if await self._todays_workouts(draft):
            return "plan"
        return await self._next_after_plan(draft)

    async def _next_after_plan(self, draft: ManualActivityDraft) -> str:
        if await self._environments_for(draft):
            return "environment"
        return "details"

    async def _todays_workouts(
        self, draft: ManualActivityDraft
    ) -> list[PlannedWorkout]:
        if self._planning_history is None or self._active_plans is None:
            return []
        profile = await self._profile(draft.user_id)
        local_now = self._clock().astimezone(ZoneInfo(profile.timezone))
        plan_id = await self._active_plans.get(
            draft.user_id, profile.local_week_start(self._clock())
        )
        if plan_id is None:
            return []
        return [
            item
            for item in await self._planning_history.list_workouts(plan_id)
            if item.scheduled_date == local_now.date()
        ]

    async def _environments_for(
        self, draft: ManualActivityDraft
    ) -> list[TrainingEnvironment]:
        if self._environments is None:
            return []
        return await self._environments.list(draft.line_user_id)

    async def _profile(self, user_id: str) -> UserTrainingProfile:
        if self._settings is None:
            return UserTrainingProfile(user_id=user_id, operation_id="manual-default")
        return await self._settings.get_profile(user_id) or UserTrainingProfile(
            user_id=user_id, operation_id="manual-default"
        )

    def _started_at_choice(
        self, draft: ManualActivityDraft, choice: str
    ) -> datetime | None:
        local_now = self._clock().astimezone(ZoneInfo(draft.timezone))
        if choice == "today_morning":
            started = datetime.combine(local_now.date(), time(7), local_now.tzinfo)
        elif choice == "today_evening":
            started = datetime.combine(local_now.date(), time(19), local_now.tzinfo)
        elif choice == "yesterday":
            started = datetime.combine(
                local_now.date() - timedelta(days=1), time(19), local_now.tzinfo
            )
        elif choice == "custom":
            return None
        else:
            raise InvalidManualActivityAction("実施日時を選択してください。")
        self._validate_started_at(started)
        return started.astimezone(UTC)

    def _validate_started_at(self, started_at: datetime) -> None:
        now = self._clock()
        if started_at > now + timedelta(days=1):
            raise InvalidManualActivityAction("未来すぎる日時は記録できません。")
        if started_at < now - timedelta(days=365):
            raise InvalidManualActivityAction("1年以上前の日時は記録できません。")

    async def _require_draft(self, line_user_id: str) -> ManualActivityDraft:
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            raise InvalidManualActivityAction(
                "入力状態がありません。メニューからやり直してください。"
            )
        self._ensure_fresh(draft)
        return draft

    def _ensure_fresh(self, draft: ManualActivityDraft) -> None:
        if draft.expires_at <= self._clock():
            raise InvalidManualActivityAction(
                "入力の有効期限が切れました。メニューから再開してください。"
            )

    def _ensure_operation(
        self, draft: ManualActivityDraft, operation_id: str | None
    ) -> None:
        if operation_id and operation_id != draft.operation_id:
            raise InvalidManualActivityAction(
                "入力状態が更新されています。最新のメッセージから操作してください。"
            )


class InMemoryManualActivityDraftStore:
    def __init__(self) -> None:
        self.items: dict[str, ManualActivityDraft] = {}

    async def save(self, draft: ManualActivityDraft) -> None:
        self.items[draft.line_user_id] = draft

    async def get(self, line_user_id: str) -> ManualActivityDraft | None:
        return self.items.get(line_user_id)

    async def delete(self, line_user_id: str) -> None:
        self.items.pop(line_user_id, None)


class FirestoreManualActivityDraftStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def save(self, draft: ManualActivityDraft) -> None:
        await (
            self._client.collection("manual_activity_drafts")
            .document(draft.line_user_id)
            .set(_draft_payload(draft))
        )

    async def get(self, line_user_id: str) -> ManualActivityDraft | None:
        snapshot = (
            await self._client.collection("manual_activity_drafts")
            .document(line_user_id)
            .get()
        )
        if not snapshot.exists:
            return None
        return _draft_from_values(snapshot.to_dict())

    async def delete(self, line_user_id: str) -> None:
        await (
            self._client.collection("manual_activity_drafts")
            .document(line_user_id)
            .delete()
        )


class InMemoryManualStravaPublicationStore:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    async def get(self, operation_key: str) -> str | None:
        return self.items.get(operation_key)

    async def save(self, operation_key: str, strava_activity_id: str) -> None:
        self.items[operation_key] = strava_activity_id


class FirestoreManualStravaPublicationStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, operation_key: str) -> str | None:
        snapshot = (
            await self._client.collection("manual_strava_publications")
            .document(operation_key)
            .get()
        )
        if not snapshot.exists:
            return None
        values = snapshot.to_dict() or {}
        stored = values.get("strava_activity_id")
        return str(stored) if stored else None

    async def save(self, operation_key: str, strava_activity_id: str) -> None:
        await (
            self._client.collection("manual_strava_publications")
            .document(operation_key)
            .set({"strava_activity_id": strava_activity_id})
        )


def create_manual_activity(draft: ManualActivityDraft) -> Activity:
    if (
        draft.activity_type is None
        or draft.started_at is None
        or draft.duration_minutes is None
        or draft.perceived_intensity is None
        or draft.completion_status is None
        or draft.athlete_id is None
    ):
        raise InvalidManualActivityAction("必要な項目が揃っていません。")
    activity_id = draft.strava_activity_id or manual_activity_id(
        draft.user_id, draft.operation_id
    )
    return Activity(
        id=activity_id,
        athlete_id=draft.athlete_id,
        activity_type=draft.activity_type,
        started_at=draft.started_at,
        duration_seconds=draft.duration_minutes * 60,
        distance_meters=0,
        description=draft.comment,
        user_id=draft.user_id,
        source_type=ActivitySource.LINE_MANUAL,
        source_activity_id=activity_id,
        planned_workout_id=draft.planned_workout_id,
        perceived_intensity=draft.perceived_intensity,
        environment_ids=list(draft.environment_ids),
        completion_status=draft.completion_status,
        details=draft.details,
    )


def manual_activity_id(user_id: str, operation_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:manual-activity:{user_id}:{operation_id}",
        )
    )


def _manual_data(draft: ManualActivityDraft, operation: str, **values: str) -> str:
    payload = {
        "action": "manual",
        "op": operation,
        "oid": draft.operation_id,
        **values,
    }
    encoded = urlencode(payload)
    if len(encoded.encode()) > 300:
        raise InvalidManualActivityAction("選択データが長すぎます。")
    return encoded


def _parse_duration(value: str) -> int:
    normalized = value.strip().replace("分", "")
    try:
        minutes = int(normalized)
    except ValueError as exc:
        raise InvalidManualActivityAction(
            "実施時間は1〜300分で入力してください。"
        ) from exc
    if not 1 <= minutes <= 300:
        raise InvalidManualActivityAction("実施時間は1〜300分で入力してください。")
    return minutes


def _parse_local_datetime(value: str, timezone: str, now: datetime) -> datetime:
    normalized = value.strip().replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(normalized, fmt).replace(
                tzinfo=ZoneInfo(timezone)
            )
        except ValueError:
            continue
        if fmt == "%m-%d %H:%M":
            parsed = parsed.replace(year=now.astimezone(ZoneInfo(timezone)).year)
        return parsed.astimezone(UTC)
    raise InvalidManualActivityAction(
        "開始日時を「2026-09-02 19:00」の形式で入力してください。"
    )


def _duration_label(minutes: int | None) -> str:
    return f"{minutes}分" if minutes else "時間未設定"


def _draft_payload(draft: ManualActivityDraft) -> dict:
    return asdict(draft)


def _draft_from_values(values: dict) -> ManualActivityDraft:
    return ManualActivityDraft(
        line_user_id=str(values["line_user_id"]),
        user_id=str(values["user_id"]),
        operation_id=str(values["operation_id"]),
        step=str(values.get("step", "type")),
        athlete_id=values.get("athlete_id"),
        timezone=str(values.get("timezone") or "Asia/Tokyo"),
        activity_type=values.get("activity_type"),
        started_at=values.get("started_at"),
        duration_minutes=values.get("duration_minutes"),
        perceived_intensity=values.get("perceived_intensity"),
        completion_status=values.get("completion_status"),
        planned_workout_id=values.get("planned_workout_id"),
        environment_ids=list(values.get("environment_ids") or []),
        details=values.get("details") or "",
        comment=values.get("comment") or "",
        strava_activity_id=values.get("strava_activity_id"),
        expires_at=values["expires_at"],
    )


def _strava_sport_and_name(activity_type: str) -> tuple[str, str]:
    labels = {value: label for label, value in MANUAL_ACTIVITY_TYPES}
    if activity_type in KNOWN_STRAVA_SPORT_TYPES:
        return activity_type, labels.get(activity_type, activity_type)
    return "Workout", activity_type
