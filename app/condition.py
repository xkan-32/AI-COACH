import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import parse_qs

from app.domain.models import ConditionLevel, ConditionReport


@dataclass(frozen=True)
class ActivityContext:
    activity_id: str
    athlete_id: str
    line_user_id: str


@dataclass(frozen=True)
class ConditionDraft:
    activity_id: str
    athlete_id: str
    line_user_id: str
    level: ConditionLevel
    step: str = "body_part"
    body_part: str | None = None
    severity: int | None = None


class ActivityContextStore(Protocol):
    async def save(self, context: ActivityContext) -> None: ...
    async def get(self, activity_id: str) -> ActivityContext | None: ...


class ConditionDraftStore(Protocol):
    async def save(self, draft: ConditionDraft) -> None: ...
    async def get(self, line_user_id: str) -> ConditionDraft | None: ...
    async def delete(self, line_user_id: str) -> None: ...


class ConditionReportStore(Protocol):
    async def save(self, report: ConditionReport) -> None: ...


class FollowUpMessenger(Protocol):
    async def send_text(self, line_user_id: str, text: str) -> None: ...


class InvalidConditionAction(ValueError):
    pass


class ConditionWorkflow:
    def __init__(
        self,
        contexts: ActivityContextStore,
        drafts: ConditionDraftStore,
        reports: ConditionReportStore,
        messenger: FollowUpMessenger,
        on_completed: Callable[[ConditionReport], Awaitable[None]] | None = None,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._contexts = contexts
        self._drafts = drafts
        self._reports = reports
        self._messenger = messenger
        self._on_completed = on_completed
        self._clock = clock

    async def handle_postback(self, line_user_id: str, data: str) -> str:
        values = {key: items[0] for key, items in parse_qs(data).items() if items}
        if values.get("action") != "condition":
            return "ignored"
        activity_id = values.get("activity_id", "")
        try:
            level = ConditionLevel(values.get("level", ""))
        except ValueError as exc:
            raise InvalidConditionAction("Unknown condition level") from exc
        context = await self._contexts.get(activity_id)
        if context is None or context.line_user_id != line_user_id:
            raise InvalidConditionAction("Activity does not belong to this LINE user")
        if level in {ConditionLevel.GOOD, ConditionLevel.FATIGUED}:
            report = ConditionReport(
                athlete_id=context.athlete_id,
                activity_id=activity_id,
                level=level,
                reported_at=self._clock(),
            )
            await self._reports.save(report)
            if self._on_completed is not None:
                await self._on_completed(report)
            await self._messenger.send_text(
                line_user_id, "体調を記録しました。ありがとうございます。"
            )
            return "completed"
        await self._drafts.save(
            ConditionDraft(
                activity_id=activity_id,
                athlete_id=context.athlete_id,
                line_user_id=line_user_id,
                level=level,
            )
        )
        await self._messenger.send_text(
            line_user_id, "違和感や痛みがある部位を教えてください。"
        )
        return "follow_up"

    async def handle_text(self, line_user_id: str, text: str) -> str:
        draft = await self._drafts.get(line_user_id)
        if draft is None:
            return "ignored"
        value = text.strip()
        if draft.step == "body_part":
            if not value or len(value) > 100:
                raise InvalidConditionAction("部位を100文字以内で入力してください")
            updated = ConditionDraft(
                **{
                    **asdict(draft),
                    "level": draft.level,
                    "step": "severity",
                    "body_part": value,
                }
            )
            await self._drafts.save(updated)
            await self._messenger.send_text(
                line_user_id, "程度を1（軽い）〜10（強い）の数字で教えてください。"
            )
            return "follow_up"
        if draft.step == "severity":
            try:
                severity = int(value)
            except ValueError as exc:
                raise InvalidConditionAction(
                    "程度は1〜10の数字で入力してください"
                ) from exc
            if not 1 <= severity <= 10:
                raise InvalidConditionAction("程度は1〜10の数字で入力してください")
            updated = ConditionDraft(
                **{
                    **asdict(draft),
                    "level": draft.level,
                    "step": "worsened",
                    "severity": severity,
                }
            )
            await self._drafts.save(updated)
            await self._messenger.send_text(
                line_user_id,
                "運動中に悪化しましたか？「はい」または「いいえ」で答えてください。",
            )
            return "follow_up"
        normalized = value.lower()
        answers = {"はい": True, "yes": True, "いいえ": False, "no": False}
        if normalized not in answers:
            raise InvalidConditionAction("「はい」または「いいえ」で答えてください")
        report = ConditionReport(
            athlete_id=draft.athlete_id,
            activity_id=draft.activity_id,
            level=draft.level,
            body_part=draft.body_part,
            severity=draft.severity,
            worsened_during_activity=answers[normalized],
            reported_at=self._clock(),
        )
        await self._reports.save(report)
        if self._on_completed is not None:
            await self._on_completed(report)
        await self._drafts.delete(line_user_id)
        await self._messenger.send_text(
            line_user_id, "体調を記録しました。無理をせず休息を優先してください。"
        )
        return "completed"


class InMemoryActivityContextStore:
    def __init__(self) -> None:
        self.items: dict[str, ActivityContext] = {}

    async def save(self, context: ActivityContext) -> None:
        self.items[context.activity_id] = context

    async def get(self, activity_id: str) -> ActivityContext | None:
        return self.items.get(activity_id)


class InMemoryConditionDraftStore:
    def __init__(self) -> None:
        self.items: dict[str, ConditionDraft] = {}

    async def save(self, draft: ConditionDraft) -> None:
        self.items[draft.line_user_id] = draft

    async def get(self, line_user_id: str) -> ConditionDraft | None:
        return self.items.get(line_user_id)

    async def delete(self, line_user_id: str) -> None:
        self.items.pop(line_user_id, None)


class InMemoryConditionReportStore:
    def __init__(self) -> None:
        self.items: list[ConditionReport] = []

    async def save(self, report: ConditionReport) -> None:
        self.items.append(report)


class FirestoreActivityContextStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def save(self, context: ActivityContext) -> None:
        await (
            self._client.collection("activity_contexts")
            .document(context.activity_id)
            .set(asdict(context))
        )

    async def get(self, activity_id: str) -> ActivityContext | None:
        snapshot = (
            await self._client.collection("activity_contexts")
            .document(activity_id)
            .get()
        )
        return ActivityContext(**snapshot.to_dict()) if snapshot.exists else None


class FirestoreConditionDraftStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def save(self, draft: ConditionDraft) -> None:
        values = asdict(draft)
        values["level"] = draft.level.value
        await (
            self._client.collection("condition_drafts")
            .document(draft.line_user_id)
            .set(values)
        )

    async def get(self, line_user_id: str) -> ConditionDraft | None:
        snapshot = (
            await self._client.collection("condition_drafts")
            .document(line_user_id)
            .get()
        )
        return ConditionDraft(**snapshot.to_dict()) if snapshot.exists else None

    async def delete(self, line_user_id: str) -> None:
        await (
            self._client.collection("condition_drafts").document(line_user_id).delete()
        )


class BigQueryConditionReportStore:
    def __init__(self, client: object, table: str) -> None:
        self._client = client
        self._table = table

    async def save(self, report: ConditionReport) -> None:
        row = {
            "athlete_id": report.athlete_id,
            "activity_id": report.activity_id,
            "condition_level": report.level.value,
            "body_part": report.body_part,
            "severity": report.severity,
            "worsened_during_activity": report.worsened_during_activity,
            "comment": report.comment,
            "reported_at": report.reported_at.isoformat(),
        }
        row_id = f"{report.athlete_id}:{report.activity_id}"
        errors = await asyncio.to_thread(
            self._client.insert_rows_json, self._table, [row], row_ids=[row_id]
        )
        if errors:
            raise RuntimeError("BigQuery condition report insert failed")
