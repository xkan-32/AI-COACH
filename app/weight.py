from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

from app.condition import FollowUpMessenger
from app.planning import TrainingSettingsStateStore, UserTrainingProfile

logger = logging.getLogger(__name__)

MIN_KG = Decimal("25.0")
MAX_KG = Decimal("250.0")
KG_QUANTUM = Decimal("0.1")
MAX_LOOKBACK_DAYS = 365
DRAFT_TTL = timedelta(hours=24)
START_COMMANDS = {"体重", "体重記録"}
TARGET_COMMANDS = {"目標体重", "体重目標"}


class InvalidWeightAction(ValueError):
    pass


class WeightLogStore(Protocol):
    async def get(self, log_id: str) -> WeightLog | None: ...
    async def save(self, log: WeightLog) -> None: ...
    async def list_since(self, user_id: str, since: date) -> list[WeightLog]: ...


class WeightTargetStore(Protocol):
    async def get(self, user_id: str) -> float | None: ...
    async def save(self, user_id: str, kilograms: float | None) -> None: ...


class WeightDraftStore(Protocol):
    async def save(self, draft: WeightDraft) -> None: ...
    async def get(self, line_user_id: str) -> WeightDraft | None: ...
    async def delete(self, line_user_id: str) -> None: ...


@dataclass(frozen=True)
class WeightLog:
    id: str
    user_id: str
    measured_on: date
    kilograms: float
    recorded_at: datetime
    operation_id: str
    source: str = "line"
    supersedes_log_id: str | None = None


@dataclass
class WeightDraft:
    line_user_id: str
    user_id: str
    operation_id: str
    step: str = "date"
    timezone: str = "Asia/Tokyo"
    measured_on: date | None = None
    kilograms: float | None = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + DRAFT_TTL)


@dataclass(frozen=True)
class WeightSummary:
    latest: WeightLog | None
    average_7d: float | None
    days_7d: int
    average_30d: float | None
    days_30d: int
    target_kg: float | None


class WeightWorkflow:
    def __init__(
        self,
        drafts: WeightDraftStore,
        logs: WeightLogStore,
        targets: WeightTargetStore,
        messenger: FollowUpMessenger,
        settings: TrainingSettingsStateStore | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._drafts = drafts
        self._logs = logs
        self._targets = targets
        self._messenger = messenger
        self._settings = settings
        self._clock = clock

    async def start(
        self,
        line_user_id: str,
        text: str = "体重",
        operation_id: str | None = None,
    ) -> bool:
        value = text.strip()
        if not _is_weight_command(value):
            return False
        inline_target = _inline_target(value)
        if inline_target is not None:
            await self._save_target(line_user_id, inline_target)
            return True
        if value in TARGET_COMMANDS:
            profile = await self._profile(line_user_id)
            draft = WeightDraft(
                line_user_id=line_user_id,
                user_id=line_user_id,
                operation_id=str(uuid.uuid4()),
                step="target",
                timezone=profile.timezone,
                expires_at=self._clock() + DRAFT_TTL,
            )
            await self._drafts.save(draft)
            await self._prompt(draft)
            return True
        inline_kg = _inline_kilograms(value)
        if inline_kg is not None:
            await self.record_kilograms(
                line_user_id, inline_kg, operation_id=operation_id
            )
            return True
        await self.start_today_entry(line_user_id)
        return True

    async def start_today_entry(self, line_user_id: str) -> None:
        profile = await self._profile(line_user_id)
        draft = WeightDraft(
            line_user_id=line_user_id,
            user_id=line_user_id,
            operation_id=str(uuid.uuid4()),
            step="kg",
            timezone=profile.timezone,
            measured_on=self._local_today(profile.timezone),
            expires_at=self._clock() + DRAFT_TTL,
        )
        await self._drafts.save(draft)
        await self._prompt(draft)

    async def record_kilograms(
        self,
        line_user_id: str,
        kilograms: float,
        measured_on: date | None = None,
        operation_id: str | None = None,
    ) -> None:
        profile = await self._profile(line_user_id)
        timezone = profile.timezone
        measured = measured_on or self._local_today(timezone)
        self._validate_measured_on(measured, timezone)
        draft = WeightDraft(
            line_user_id=line_user_id,
            user_id=line_user_id,
            operation_id=operation_id or str(uuid.uuid4()),
            step="confirm",
            timezone=timezone,
            measured_on=measured,
            kilograms=kilograms,
            expires_at=self._clock() + DRAFT_TTL,
        )
        await self._complete(draft)

    async def handle_postback(self, line_user_id: str, data: str) -> bool:
        values = {key: items[0] for key, items in parse_qs(data).items() if items}
        if values.get("action") != "weight":
            return False
        if values.get("op") == "cancel":
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(
                line_user_id, "体重の記録をキャンセルしました。"
            )
            return True
        if values.get("op") == "save":
            return await self._handle_save(line_user_id, values.get("oid"))
        if values.get("op") == "start":
            await self.start_today_entry(line_user_id)
            return True
        draft = await self._require_draft(line_user_id)
        self._ensure_operation(draft, values.get("oid"))
        operation = values.get("op", "")
        if operation == "when":
            selected = values.get("v", "")
            if selected == "custom":
                draft.step = "custom_date"
            elif selected == "target":
                draft.step = "target"
            else:
                draft.measured_on = self._date_choice(draft, selected)
                draft.step = "kg"
        else:
            raise InvalidWeightAction("選択された項目を確認できませんでした。")
        await self._drafts.save(draft)
        await self._prompt(draft)
        return True

    async def handle_text(
        self,
        line_user_id: str,
        text: str,
        operation_id: str | None = None,
    ) -> bool:
        value = text.strip()
        if value.lower() in {"cancel", "キャンセル"}:
            draft = await self._drafts.get(line_user_id)
            if draft is None:
                return False
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(
                line_user_id, "体重の記録をキャンセルしました。"
            )
            return True
        if await self.start(line_user_id, value, operation_id=operation_id):
            return True
        draft = await self._drafts.get(line_user_id)
        if looks_like_kilograms(value) and (
            draft is None or draft.step in {"date", "kg", "confirm"}
        ):
            measured = draft.measured_on if draft and draft.measured_on else None
            await self.record_kilograms(
                line_user_id,
                parse_kilograms(value),
                measured_on=measured,
                operation_id=operation_id,
            )
            return True
        if draft is None:
            return False
        self._ensure_fresh(draft)
        if draft.step == "custom_date":
            draft.measured_on = _parse_local_date(value, draft.timezone, self._clock())
            self._validate_measured_on(draft.measured_on, draft.timezone)
            draft.step = "kg"
        elif draft.step == "target":
            await self._save_target(line_user_id, parse_kilograms(value))
            await self._drafts.delete(line_user_id)
            return True
        else:
            raise InvalidWeightAction("ボタンから選択するか、キャンセルしてください。")
        await self._drafts.save(draft)
        await self._prompt(draft)
        return True

    async def _handle_save(self, line_user_id: str, operation_id: str | None) -> bool:
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            if not operation_id:
                raise InvalidWeightAction(
                    "入力状態がありません。体重と送ってやり直してください。"
                )
            existing = await self._logs.get(weight_log_id(line_user_id, operation_id))
            if existing is None:
                raise InvalidWeightAction(
                    "入力状態がありません。体重と送ってやり直してください。"
                )
            await self._messenger.send_text(
                line_user_id, await self._summary_message(line_user_id, existing, False)
            )
            return True
        self._ensure_fresh(draft)
        self._ensure_operation(draft, operation_id)
        await self._complete(draft)
        return True

    async def _complete(self, draft: WeightDraft) -> None:
        if draft.measured_on is None or draft.kilograms is None:
            raise InvalidWeightAction("日付と体重を入力してください。")
        log = create_weight_log(draft, recorded_at=self._clock())
        existing = await self._logs.get(log.id)
        if existing is not None:
            if (
                existing.user_id != log.user_id
                or existing.measured_on != log.measured_on
                or existing.kilograms != log.kilograms
            ):
                raise InvalidWeightAction("同じ操作の記録内容が一致しません。")
            log = existing
        else:
            previous = await self._current_for_day(draft.user_id, draft.measured_on)
            if previous is not None:
                log = WeightLog(
                    id=log.id,
                    user_id=log.user_id,
                    measured_on=log.measured_on,
                    kilograms=log.kilograms,
                    recorded_at=log.recorded_at,
                    operation_id=log.operation_id,
                    source=log.source,
                    supersedes_log_id=previous.id,
                )
            await self._logs.save(log)
        await self._drafts.delete(draft.line_user_id)
        logger.info(
            "weight_log_saved user_id=%s measured_on=%s correction=%s",
            log.user_id,
            log.measured_on.isoformat(),
            log.supersedes_log_id is not None,
        )
        await self._messenger.send_text(
            draft.line_user_id,
            await self._summary_message(
                draft.user_id, log, log.supersedes_log_id is not None
            ),
        )

    async def _save_target(self, user_id: str, kilograms: float) -> None:
        await self._targets.save(user_id, kilograms)
        logger.info("weight_target_saved user_id=%s", user_id)
        await self._messenger.send_text(
            user_id, f"目標体重を{_format_kg(kilograms)}に設定しました。"
        )

    async def _prompt(self, draft: WeightDraft) -> None:
        if draft.step == "date":
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                "体重を記録する日付を選ぶか、数値だけ送って今日の分として記録できます。",
                [
                    ("今日", _weight_data(draft, "when", v="today")),
                    ("昨日", _weight_data(draft, "when", v="yesterday")),
                    ("日付を入力", _weight_data(draft, "when", v="custom")),
                    ("目標体重", _weight_data(draft, "when", v="target")),
                    ("キャンセル", _weight_data(draft, "cancel")),
                ],
            )
            return
        if draft.step == "custom_date":
            await self._messenger.send_text(
                draft.line_user_id,
                "日付を「2026-09-02」の形式で入力してください。",
            )
            return
        if draft.step == "kg":
            today = self._local_today(draft.timezone)
            if draft.measured_on is None or draft.measured_on == today:
                prompt = (
                    "今日の体重をkgで送ってください。"
                    "数字だけ送るとそのまま記録されます。"
                )
            else:
                prompt = (
                    f"{draft.measured_on.isoformat()}の体重をkgで送ってください。"
                    "数字だけ送るとそのまま記録されます。"
                )
            await self._messenger.send_quick_reply(
                draft.line_user_id,
                prompt,
                [
                    ("昨日", _weight_data(draft, "when", v="yesterday")),
                    ("日付を入力", _weight_data(draft, "when", v="custom")),
                    ("目標体重", _weight_data(draft, "when", v="target")),
                    ("キャンセル", _weight_data(draft, "cancel")),
                ],
            )
            return
        if draft.step == "target":
            await self._messenger.send_text(
                draft.line_user_id,
                "目標体重をkgで入力してください。",
            )
            return
        current = await self._current_for_day(draft.user_id, draft.measured_on)
        correction = ""
        if current is not None:
            correction = (
                f"\nこの日はすでに{_format_kg(current.kilograms)}です。"
                f"{_format_kg(draft.kilograms or 0)}へ訂正します。"
            )
        await self._messenger.send_quick_reply(
            draft.line_user_id,
            (
                f"この内容で体重を記録します。\n日付: {draft.measured_on.isoformat()}"
                f"\n体重: {_format_kg(draft.kilograms or 0)}{correction}"
            ),
            [
                ("記録する", _weight_data(draft, "save")),
                ("キャンセル", _weight_data(draft, "cancel")),
            ],
        )

    async def _summary_message(
        self, user_id: str, latest: WeightLog, corrected: bool
    ) -> str:
        summary = await build_weight_summary(
            self._logs, self._targets, user_id, latest.measured_on, latest
        )
        verb = "訂正しました" if corrected else "記録しました"
        lines = [
            f"{latest.measured_on.isoformat()} {_format_kg(latest.kilograms)}を{verb}。"
        ]
        lines.append(_average_line("7日平均", summary.average_7d, summary.days_7d))
        lines.append(_average_line("30日平均", summary.average_30d, summary.days_30d))
        if summary.target_kg is not None:
            delta = round(latest.kilograms - summary.target_kg, 1)
            if delta == 0:
                lines.append(f"目標 {_format_kg(summary.target_kg)} と同じです。")
            elif delta > 0:
                lines.append(
                    f"目標 {_format_kg(summary.target_kg)} まで"
                    f" {_format_kg(delta)}減量が目安です。"
                )
            else:
                lines.append(
                    f"目標 {_format_kg(summary.target_kg)} を"
                    f" {_format_kg(abs(delta))}下回っています。"
                )
        return "\n".join(lines)

    async def _current_for_day(
        self, user_id: str, measured_on: date
    ) -> WeightLog | None:
        logs = await self._logs.list_since(user_id, measured_on)
        current = current_logs_by_day(logs)
        return current.get(measured_on)

    async def _profile(self, user_id: str) -> UserTrainingProfile:
        if self._settings is None:
            return UserTrainingProfile(user_id=user_id, operation_id="weight-default")
        return await self._settings.get_profile(user_id) or UserTrainingProfile(
            user_id=user_id, operation_id="weight-default"
        )

    def _local_today(self, timezone: str) -> date:
        return self._clock().astimezone(ZoneInfo(timezone)).date()

    def _date_choice(self, draft: WeightDraft, choice: str) -> date:
        today = self._local_today(draft.timezone)
        if choice == "today":
            measured = today
        elif choice == "yesterday":
            measured = today - timedelta(days=1)
        else:
            raise InvalidWeightAction("日付を選択してください。")
        self._validate_measured_on(measured, draft.timezone)
        return measured

    def _validate_measured_on(self, measured_on: date, timezone: str) -> None:
        today = self._local_today(timezone)
        if measured_on > today:
            raise InvalidWeightAction("未来の日付は記録できません。")
        if measured_on < today - timedelta(days=MAX_LOOKBACK_DAYS):
            raise InvalidWeightAction("1年以上前の日付は記録できません。")

    async def _require_draft(self, line_user_id: str) -> WeightDraft:
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            raise InvalidWeightAction(
                "入力状態がありません。体重と送ってやり直してください。"
            )
        self._ensure_fresh(draft)
        return draft

    def _ensure_fresh(self, draft: WeightDraft) -> None:
        if draft.expires_at <= self._clock():
            raise InvalidWeightAction(
                "入力の有効期限が切れました。体重と送ってやり直してください。"
            )

    def _ensure_operation(self, draft: WeightDraft, operation_id: str | None) -> None:
        if operation_id and operation_id != draft.operation_id:
            raise InvalidWeightAction(
                "入力状態が更新されています。最新のメッセージから操作してください。"
            )


class InMemoryWeightLogStore:
    def __init__(self) -> None:
        self.logs: dict[str, WeightLog] = {}

    async def get(self, log_id: str) -> WeightLog | None:
        return self.logs.get(log_id)

    async def save(self, log: WeightLog) -> None:
        self.logs[log.id] = log

    async def list_since(self, user_id: str, since: date) -> list[WeightLog]:
        return [
            log
            for log in self.logs.values()
            if log.user_id == user_id and log.measured_on >= since
        ]


class BigQueryWeightLogStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def get(self, log_id: str) -> WeightLog | None:
        from google.cloud import bigquery

        query = f"SELECT * FROM `{self._table}` WHERE log_id = @log_id LIMIT 1"
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("log_id", "STRING", log_id)]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        if not rows:
            return None
        return _log_from_row(rows[0])

    async def save(self, log: WeightLog) -> None:
        row = {
            "log_id": log.id,
            "user_id": log.user_id,
            "measured_on": log.measured_on.isoformat(),
            "kilograms": log.kilograms,
            "unit": "kg",
            "recorded_at": log.recorded_at.isoformat(),
            "operation_id": log.operation_id,
            "source": log.source,
            "supersedes_log_id": log.supersedes_log_id,
        }
        errors = await asyncio.to_thread(
            self._client.insert_rows_json, self._table, [row], row_ids=[log.id]
        )
        if errors:
            raise RuntimeError("BigQuery weight log insert failed")

    async def list_since(self, user_id: str, since: date) -> list[WeightLog]:
        from google.cloud import bigquery

        query = (
            f"SELECT * FROM `{self._table}` WHERE user_id = @user_id "
            "AND measured_on >= @since"
        )
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("since", "DATE", since.isoformat()),
            ]
        )
        rows = await asyncio.to_thread(
            lambda: list(self._client.query(query, job_config=config).result())
        )
        return [_log_from_row(row) for row in rows]


class InMemoryWeightTargetStore:
    def __init__(self) -> None:
        self.targets: dict[str, float] = {}

    async def get(self, user_id: str) -> float | None:
        return self.targets.get(user_id)

    async def save(self, user_id: str, kilograms: float | None) -> None:
        if kilograms is None:
            self.targets.pop(user_id, None)
            return
        self.targets[user_id] = kilograms


class FirestoreWeightTargetStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, user_id: str) -> float | None:
        snapshot = (
            await self._client.collection("weight_targets").document(user_id).get()
        )
        if not snapshot.exists:
            return None
        values = snapshot.to_dict() or {}
        stored = values.get("kilograms")
        return float(stored) if stored is not None else None

    async def save(self, user_id: str, kilograms: float | None) -> None:
        document = self._client.collection("weight_targets").document(user_id)
        if kilograms is None:
            await document.delete()
            return
        await document.set({"kilograms": kilograms})


class InMemoryWeightDraftStore:
    def __init__(self) -> None:
        self.items: dict[str, WeightDraft] = {}

    async def save(self, draft: WeightDraft) -> None:
        self.items[draft.line_user_id] = draft

    async def get(self, line_user_id: str) -> WeightDraft | None:
        return self.items.get(line_user_id)

    async def delete(self, line_user_id: str) -> None:
        self.items.pop(line_user_id, None)


class FirestoreWeightDraftStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def save(self, draft: WeightDraft) -> None:
        await (
            self._client.collection("weight_drafts")
            .document(draft.line_user_id)
            .set(_draft_payload(draft))
        )

    async def get(self, line_user_id: str) -> WeightDraft | None:
        snapshot = (
            await self._client.collection("weight_drafts").document(line_user_id).get()
        )
        if not snapshot.exists:
            return None
        return _draft_from_values(snapshot.to_dict() or {})

    async def delete(self, line_user_id: str) -> None:
        await self._client.collection("weight_drafts").document(line_user_id).delete()


def create_weight_log(
    draft: WeightDraft, *, recorded_at: datetime, source: str = "line"
) -> WeightLog:
    if draft.measured_on is None or draft.kilograms is None:
        raise InvalidWeightAction("日付と体重を入力してください。")
    return WeightLog(
        id=weight_log_id(draft.user_id, draft.operation_id),
        user_id=draft.user_id,
        measured_on=draft.measured_on,
        kilograms=draft.kilograms,
        recorded_at=recorded_at,
        operation_id=draft.operation_id,
        source=source,
    )


def weight_log_id(user_id: str, operation_id: str) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"ai-coach:weight-log:{user_id}:{operation_id}")
    )


def parse_kilograms(value: str) -> float:
    normalized = _normalize_kg_input(value)
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise InvalidWeightAction("体重は25.0〜250.0kgで入力してください。") from exc
    kilograms = amount.quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
    if kilograms < MIN_KG or kilograms > MAX_KG:
        raise InvalidWeightAction("体重は25.0〜250.0kgで入力してください。")
    return float(kilograms)


def looks_like_kilograms(value: str) -> bool:
    try:
        Decimal(_normalize_kg_input(value))
    except InvalidOperation:
        return False
    return True


def current_logs_by_day(logs: list[WeightLog]) -> dict[date, WeightLog]:
    current: dict[date, WeightLog] = {}
    for log in sorted(logs, key=lambda item: item.recorded_at):
        current[log.measured_on] = log
    return current


async def build_weight_summary(
    logs: WeightLogStore,
    targets: WeightTargetStore,
    user_id: str,
    as_of: date,
    latest: WeightLog | None = None,
) -> WeightSummary:
    window = await logs.list_since(user_id, as_of - timedelta(days=29))
    current = current_logs_by_day(window)
    if latest is not None:
        newer = current.get(latest.measured_on)
        if newer is None or latest.recorded_at >= newer.recorded_at:
            current[latest.measured_on] = latest
    average_7d, days_7d = _window_average(current, as_of, 7)
    average_30d, days_30d = _window_average(current, as_of, 30)
    return WeightSummary(
        latest=current.get(as_of) or latest,
        average_7d=average_7d,
        days_7d=days_7d,
        average_30d=average_30d,
        days_30d=days_30d,
        target_kg=await targets.get(user_id),
    )


def _window_average(
    current: dict[date, WeightLog], as_of: date, days: int
) -> tuple[float | None, int]:
    start = as_of - timedelta(days=days - 1)
    values = [log.kilograms for day, log in current.items() if start <= day <= as_of]
    if not values:
        return None, 0
    total = sum((Decimal(str(value)) for value in values), Decimal(0))
    average = (total / Decimal(len(values))).quantize(
        KG_QUANTUM, rounding=ROUND_HALF_UP
    )
    return float(average), len(values)


def _normalize_kg_input(value: str) -> str:
    return (
        value.strip()
        .replace("ｋｇ", "kg")
        .replace("ＫＧ", "kg")
        .replace("KG", "kg")
        .replace("Kg", "kg")
        .replace("kg", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("．", ".")
        .translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    )


def _is_weight_command(value: str) -> bool:
    if value in START_COMMANDS or value in TARGET_COMMANDS:
        return True
    return _inline_kilograms(value) is not None or _inline_target(value) is not None


def _inline_kilograms(value: str) -> float | None:
    parts = value.split()
    if len(parts) != 2 or parts[0] not in START_COMMANDS:
        return None
    return parse_kilograms(parts[1])


def _inline_target(value: str) -> float | None:
    parts = value.split()
    if len(parts) != 2 or parts[0] not in TARGET_COMMANDS:
        return None
    return parse_kilograms(parts[1])


def _parse_local_date(value: str, timezone: str, now: datetime) -> date:
    normalized = value.strip().replace("/", "-")
    today = now.astimezone(ZoneInfo(timezone)).date()
    if len(normalized) == 10:
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            pass
    parts = normalized.split("-")
    if len(parts) == 2:
        try:
            return date(today.year, int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    raise InvalidWeightAction("日付を「2026-09-02」の形式で入力してください。")


def _format_kg(value: float) -> str:
    return f"{Decimal(str(value)).quantize(KG_QUANTUM)}kg"


def _average_line(label: str, average: float | None, days: int) -> str:
    if average is None:
        return f"{label}: データ不足"
    return f"{label}: {_format_kg(average)}（{days}日分）"


def _weight_data(draft: WeightDraft, operation: str, **values: str) -> str:
    payload = {
        "action": "weight",
        "op": operation,
        "oid": draft.operation_id,
        **values,
    }
    encoded = urlencode(payload)
    if len(encoded.encode()) > 300:
        raise InvalidWeightAction("選択データが長すぎます。")
    return encoded


def _draft_payload(draft: WeightDraft) -> dict:
    return asdict(draft)


def _draft_from_values(values: dict) -> WeightDraft:
    measured = values.get("measured_on")
    if isinstance(measured, datetime):
        measured = measured.date()
    elif isinstance(measured, str):
        measured = date.fromisoformat(measured)
    return WeightDraft(
        line_user_id=str(values["line_user_id"]),
        user_id=str(values["user_id"]),
        operation_id=str(values["operation_id"]),
        step=str(values.get("step", "date")),
        timezone=str(values.get("timezone") or "Asia/Tokyo"),
        measured_on=measured,
        kilograms=values.get("kilograms"),
        expires_at=values["expires_at"],
    )


def _log_from_row(row: object) -> WeightLog:
    measured = row.measured_on
    if isinstance(measured, datetime):
        measured = measured.date()
    elif not isinstance(measured, date):
        measured = date.fromisoformat(str(measured))
    return WeightLog(
        id=str(row.log_id),
        user_id=str(row.user_id),
        measured_on=measured,
        kilograms=float(row.kilograms),
        recorded_at=row.recorded_at,
        operation_id=str(row.operation_id),
        source=str(getattr(row, "source", None) or "line"),
        supersedes_log_id=getattr(row, "supersedes_log_id", None),
    )
