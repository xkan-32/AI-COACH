from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from urllib.parse import parse_qs

from app.domain.models import (
    Goal,
    GoalPriority,
    GoalStatus,
    TrainingEnvironment,
    TrainingEnvironmentCategory,
    TrainingEnvironmentStatus,
)

MAX_ITEMS = 20
ACTIVITY_PLACES = {
    "屋外ランニング",
    "トレッドミル",
    "屋外サイクリング",
    "インドアバイク",
    "プール",
    "ジム",
    "自宅トレーニング",
}
EQUIPMENT = {
    "自重",
    "ダンベル",
    "バーベル",
    "ケトルベル",
    "マシン",
    "チューブ",
    "ローラー台",
}
ENVIRONMENT_ALIASES = {
    "ルームバイク": "インドアバイク",
    "エアロバイク": "インドアバイク",
    "フィットネスバイク": "インドアバイク",
}


class GoalStore(Protocol):
    async def list(self, line_user_id: str) -> list[Goal]: ...
    async def save(self, line_user_id: str, goal: Goal) -> None: ...
    async def deactivate(self, line_user_id: str, goal_id: str) -> bool: ...


class TrainingResourceStore(Protocol):
    async def list(self, line_user_id: str) -> list[TrainingEnvironment]: ...
    async def replace(self, line_user_id: str, resources: list[str]) -> None: ...
    async def save(self, line_user_id: str, resource: TrainingEnvironment) -> None: ...
    async def deactivate(self, line_user_id: str, resource_id: str) -> bool: ...


@dataclass(frozen=True)
class ProfileDraft:
    line_user_id: str
    operation_id: str
    action: str
    step: str
    values: dict[str, str] = field(default_factory=dict)
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=24)
    )


class ProfileDraftStore(Protocol):
    async def get(self, line_user_id: str) -> ProfileDraft | None: ...
    async def save(self, draft: ProfileDraft) -> None: ...
    async def delete(self, line_user_id: str) -> None: ...


class InMemoryGoalStore:
    def __init__(self) -> None:
        self.items: dict[str, list[Goal]] = {}

    async def list(self, line_user_id: str) -> list[Goal]:
        return [
            goal
            for goal in self.items.get(line_user_id, [])
            if goal.status == GoalStatus.ACTIVE
        ]

    async def save(self, line_user_id: str, goal: Goal) -> None:
        goals = self.items.setdefault(line_user_id, [])
        if goal.priority == GoalPriority.PRIMARY:
            for existing in goals:
                if existing.priority == GoalPriority.PRIMARY:
                    existing.priority = GoalPriority.SECONDARY
        for index, existing in enumerate(goals):
            if existing.id == goal.id:
                goals[index] = goal
                return
        goals.append(goal)

    async def deactivate(self, line_user_id: str, goal_id: str) -> bool:
        for goal in self.items.get(line_user_id, []):
            if goal.id == goal_id and goal.status == GoalStatus.ACTIVE:
                goal.status = GoalStatus.PAUSED
                return True
        return False


class InMemoryTrainingResourceStore:
    def __init__(self) -> None:
        self.items: dict[str, list[TrainingEnvironment]] = {}

    async def list(self, line_user_id: str) -> list[TrainingEnvironment]:
        return [
            item
            for item in self.items.get(line_user_id, [])
            if item.status == TrainingEnvironmentStatus.ACTIVE
        ]

    async def replace(self, line_user_id: str, resources: list[str]) -> None:
        self.items[line_user_id] = normalize_environments(resources, line_user_id)

    async def save(self, line_user_id: str, resource: TrainingEnvironment) -> None:
        items = self.items.setdefault(line_user_id, [])
        for existing in items:
            if (
                existing.id != resource.id
                and existing.status == TrainingEnvironmentStatus.ACTIVE
                and environment_key(existing) == environment_key(resource)
            ):
                raise ProfileCommandError("同じ運動環境がすでに登録されています。")
        for index, existing in enumerate(items):
            if existing.id == resource.id:
                items[index] = resource
                return
        items.append(resource)

    async def deactivate(self, line_user_id: str, resource_id: str) -> bool:
        for item in self.items.get(line_user_id, []):
            if (
                item.id == resource_id
                and item.status == TrainingEnvironmentStatus.ACTIVE
            ):
                item.status = TrainingEnvironmentStatus.INACTIVE
                return True
        return False


class InMemoryProfileDraftStore:
    def __init__(self) -> None:
        self.items: dict[str, ProfileDraft] = {}

    async def get(self, line_user_id: str) -> ProfileDraft | None:
        return self.items.get(line_user_id)

    async def save(self, draft: ProfileDraft) -> None:
        self.items[draft.line_user_id] = draft

    async def delete(self, line_user_id: str) -> None:
        self.items.pop(line_user_id, None)


class FirestoreGoalStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def list(self, line_user_id: str) -> list[Goal]:
        snapshots = (
            await self._client.collection("goals")
            .where("line_user_id", "==", line_user_id)
            .get()
        )
        return [
            goal
            for snapshot in snapshots
            if (goal := Goal.model_validate(snapshot.to_dict())).status
            == GoalStatus.ACTIVE
        ]

    async def save(self, line_user_id: str, goal: Goal) -> None:
        if goal.priority == GoalPriority.PRIMARY:
            query = self._client.collection("goals").where(
                "line_user_id", "==", line_user_id
            )
            for snapshot in await query.get():
                if snapshot.to_dict().get("priority") == GoalPriority.PRIMARY.value:
                    await snapshot.reference.update(
                        {"priority": GoalPriority.SECONDARY.value}
                    )
        values = goal.model_dump(mode="json")
        values["line_user_id"] = line_user_id
        await self._client.collection("goals").document(goal.id).set(values)

    async def deactivate(self, line_user_id: str, goal_id: str) -> bool:
        document = self._client.collection("goals").document(goal_id)
        snapshot = await document.get()
        if (
            not snapshot.exists
            or snapshot.to_dict().get("line_user_id") != line_user_id
        ):
            return False
        await document.update({"status": GoalStatus.PAUSED.value})
        return True


class FirestoreTrainingResourceStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def list(self, line_user_id: str) -> list[TrainingEnvironment]:
        snapshots = (
            await self._client.collection("training_environments")
            .where("line_user_id", "==", line_user_id)
            .get()
        )
        if snapshots:
            return [
                item
                for snapshot in snapshots
                if (
                    item := TrainingEnvironment.model_validate(snapshot.to_dict())
                ).status
                == TrainingEnvironmentStatus.ACTIVE
            ]
        snapshot = (
            await self._client.collection("training_resources")
            .document(line_user_id)
            .get()
        )
        return (
            normalize_environments(
                list(snapshot.to_dict().get("resources", [])), line_user_id
            )
            if snapshot.exists
            else []
        )

    async def replace(self, line_user_id: str, resources: list[str]) -> None:
        snapshots = (
            await self._client.collection("training_environments")
            .where("line_user_id", "==", line_user_id)
            .get()
        )
        for snapshot in snapshots:
            await snapshot.reference.update(
                {"status": TrainingEnvironmentStatus.INACTIVE.value}
            )
        for resource in normalize_environments(resources, line_user_id):
            values = resource.model_dump(mode="json")
            values["line_user_id"] = line_user_id
            await (
                self._client.collection("training_environments")
                .document(resource.id)
                .set(values)
            )

    async def save(self, line_user_id: str, resource: TrainingEnvironment) -> None:
        await self._ensure_migrated(line_user_id)
        for existing in await self.list(line_user_id):
            if existing.id != resource.id and environment_key(
                existing
            ) == environment_key(resource):
                raise ProfileCommandError("同じ運動環境がすでに登録されています。")
        values = resource.model_dump(mode="json")
        values["line_user_id"] = line_user_id
        await (
            self._client.collection("training_environments")
            .document(resource.id)
            .set(values)
        )

    async def deactivate(self, line_user_id: str, resource_id: str) -> bool:
        await self._ensure_migrated(line_user_id)
        document = self._client.collection("training_environments").document(
            resource_id
        )
        snapshot = await document.get()
        if (
            not snapshot.exists
            or snapshot.to_dict().get("line_user_id") != line_user_id
        ):
            return False
        await document.update({"status": TrainingEnvironmentStatus.INACTIVE.value})
        return True

    async def _ensure_migrated(self, line_user_id: str) -> None:
        structured = (
            await self._client.collection("training_environments")
            .where("line_user_id", "==", line_user_id)
            .get()
        )
        if structured:
            return
        legacy = (
            await self._client.collection("training_resources")
            .document(line_user_id)
            .get()
        )
        if not legacy.exists:
            return
        for item in normalize_environments(
            list(legacy.to_dict().get("resources", [])), line_user_id
        ):
            values = item.model_dump(mode="json")
            values["line_user_id"] = line_user_id
            await (
                self._client.collection("training_environments")
                .document(item.id)
                .set(values)
            )


class FirestoreProfileDraftStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def get(self, line_user_id: str) -> ProfileDraft | None:
        snapshot = (
            await self._client.collection("profile_drafts").document(line_user_id).get()
        )
        return ProfileDraft(**snapshot.to_dict()) if snapshot.exists else None

    async def save(self, draft: ProfileDraft) -> None:
        await (
            self._client.collection("profile_drafts")
            .document(draft.line_user_id)
            .set(asdict(draft))
        )

    async def delete(self, line_user_id: str) -> None:
        await self._client.collection("profile_drafts").document(line_user_id).delete()


class ProfileCommandError(ValueError):
    pass


def normalize_environment(
    value: str, stable_seed: str | None = None
) -> TrainingEnvironment:
    raw = value.strip()
    if not raw:
        raise ProfileCommandError("運動環境を入力してください。")
    display_name = ENVIRONMENT_ALIASES.get(raw, raw)
    if display_name in ACTIVITY_PLACES:
        category = TrainingEnvironmentCategory.ACTIVITY_PLACE
        detail = None
    elif display_name in EQUIPMENT:
        category = TrainingEnvironmentCategory.EQUIPMENT
        detail = None
    else:
        category = TrainingEnvironmentCategory.OTHER
        detail = raw
    resource_id = (
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, f"{stable_seed}:{display_name}:{detail or ''}"
            )
        )
        if stable_seed
        else str(uuid.uuid4())
    )
    return TrainingEnvironment(
        id=resource_id, display_name=display_name, category=category, detail=detail
    )


def environment_key(item: TrainingEnvironment) -> tuple[str, str, str]:
    return item.category.value, item.display_name, item.detail or ""


def normalize_environments(
    values: list[str], stable_seed: str | None = None
) -> list[TrainingEnvironment]:
    result: list[TrainingEnvironment] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        item = normalize_environment(value, stable_seed)
        if environment_key(item) not in seen:
            result.append(item)
            seen.add(environment_key(item))
    return result


class ProfileCommandService:
    def __init__(
        self, goals: GoalStore, resources: TrainingResourceStore, messenger: object
    ):
        self._goals = goals
        self._resources = resources
        self._messenger = messenger

    async def handle(self, line_user_id: str, text: str) -> bool:
        command = text.strip()
        if command == "目標確認":
            goals = await self._goals.list(line_user_id)
            lines = ["登録中の目標:"]
            for goal in sorted(goals, key=lambda item: item.priority.value):
                due = goal.target_date.isoformat() if goal.target_date else "期限なし"
                lines.append(
                    f"・{goal.priority.value}: {goal.goal_type} / {goal.target} / {due}"
                )
            await self._messenger.send_text(
                line_user_id, "\n".join(lines) if goals else "目標は未登録です。"
            )
            return True
        if command.startswith("目標登録 "):
            parts = command.split(maxsplit=4)
            if len(parts) != 5:
                raise ProfileCommandError(
                    "形式: 目標登録 primary|secondary 種別 内容 YYYY-MM-DD|なし"
                )
            _, priority_text, goal_type, target, date_text = parts
            try:
                priority_aliases = {
                    "primary": GoalPriority.PRIMARY,
                    "secondary": GoalPriority.SECONDARY,
                    "主目標": GoalPriority.PRIMARY,
                    "副目標": GoalPriority.SECONDARY,
                }
                priority = priority_aliases[priority_text.lower()]
                target_date = (
                    None if date_text == "なし" else date.fromisoformat(date_text)
                )
            except (KeyError, ValueError) as exc:
                raise ProfileCommandError("優先度または日付の形式が不正です。") from exc
            await self._goals.save(
                line_user_id,
                Goal(
                    id=str(uuid.uuid4()),
                    goal_type=goal_type,
                    target=target,
                    target_date=target_date,
                    priority=priority,
                ),
            )
            await self._messenger.send_text(line_user_id, "目標を登録しました。")
            return True
        if command == "運動環境確認":
            resources = await self._resources.list(line_user_id)
            message = (
                "利用可能な運動環境:\n"
                + "\n".join(
                    f"・{item.id[:8]} {item.display_name} ({item.category.value})"
                    for item in resources
                )
                if resources
                else "運動環境は未登録です。"
            )
            await self._messenger.send_text(line_user_id, message)
            return True
        if command.startswith("運動環境登録 "):
            raw = command.removeprefix("運動環境登録 ")
            resources = list(
                dict.fromkeys(
                    item.strip()
                    for item in raw.replace("、", ",").split(",")
                    if item.strip()
                )
            )
            if not resources or len(resources) > 20:
                raise ProfileCommandError("運動環境は1〜20件で指定してください。")
            await self._resources.replace(line_user_id, resources)
            await self._messenger.send_text(line_user_id, "運動環境を更新しました。")
            return True
        return False


class ProfileWorkflow:
    """Firestore-backed conversational editor for goals and training environments."""

    def __init__(
        self,
        goals: GoalStore,
        resources: TrainingResourceStore,
        drafts: ProfileDraftStore,
        messenger: object,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._goals = goals
        self._resources = resources
        self._drafts = drafts
        self._messenger = messenger
        self._clock = clock
        self._commands = ProfileCommandService(goals, resources, messenger)

    async def handle_postback(self, line_user_id: str, data: str) -> bool:
        values = {key: items[0] for key, items in parse_qs(data).items() if items}
        if values.get("action") != "menu" or values.get("version") != "1":
            return False
        if values.get("target") == "goals":
            await self._commands.handle(line_user_id, "目標確認")
            await self._messenger.send_text(
                line_user_id, "操作: 目標追加 / 目標変更 / 目標無効化 / キャンセル"
            )
            return True
        if values.get("target") == "settings":
            await self._messenger.send_text(
                line_user_id, "設定を選んでください。\n目標 / 運動環境 / Strava連携"
            )
            return True
        return False

    async def handle_text(self, line_user_id: str, text: str) -> bool:
        value = text.strip()
        if value.lower() in {"cancel", "キャンセル"}:
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(line_user_id, "入力をキャンセルしました。")
            return True
        draft = await self._drafts.get(line_user_id)
        if draft:
            if draft.expires_at <= self._clock():
                await self._drafts.delete(line_user_id)
                raise ProfileCommandError(
                    "入力の有効期限が切れました。メニューから再開してください。"
                )
            await self._continue(line_user_id, value, draft)
            return True
        if value == "目標":
            return await self._commands.handle(line_user_id, "目標確認")
        if value == "運動環境":
            await self._commands.handle(line_user_id, "運動環境確認")
            await self._messenger.send_text(
                line_user_id, "操作: 運動環境追加 / 運動環境変更 / 運動環境無効化"
            )
            return True
        starts = {
            "目標追加": (
                "goal_add",
                "priority",
                "主目標または副目標を入力してください。",
            ),
            "目標変更": (
                "goal_change",
                "id",
                "変更する目標のID（先頭8文字）を入力してください。",
            ),
            "目標無効化": (
                "goal_deactivate",
                "id",
                "無効化する目標のID（先頭8文字）を入力してください。",
            ),
            "運動環境追加": (
                "environment_add",
                "name",
                "追加する場所・種目・器具を入力してください。",
            ),
            "運動環境変更": (
                "environment_change",
                "id",
                "変更する運動環境のID（先頭8文字）を入力してください。",
            ),
            "運動環境無効化": (
                "environment_deactivate",
                "id",
                "無効化する運動環境のID（先頭8文字）を入力してください。",
            ),
        }
        if value in starts:
            action, step, prompt = starts[value]
            await self._drafts.save(
                ProfileDraft(line_user_id, str(uuid.uuid4()), action, step)
            )
            await self._messenger.send_text(line_user_id, prompt)
            return True
        return await self._commands.handle(line_user_id, value)

    async def _continue(self, user: str, value: str, draft: ProfileDraft) -> None:
        if not value:
            raise ProfileCommandError(
                "未入力です。値を入力するか「キャンセル」と送信してください。"
            )
        values = dict(draft.values)
        if draft.action == "goal_add":
            steps = {
                "priority": ("type", "目標の種別を入力してください。"),
                "type": ("target", "目標の内容を入力してください。"),
                "target": (
                    "date",
                    "期限をYYYY-MM-DDまたは「なし」で入力してください。",
                ),
            }
            if draft.step == "priority" and value not in {
                "主目標",
                "副目標",
                "primary",
                "secondary",
            }:
                raise ProfileCommandError("主目標または副目標を入力してください。")
            if draft.step != "date":
                values[draft.step] = value
                next_step, prompt = steps[draft.step]
                await self._drafts.save(
                    ProfileDraft(
                        user,
                        draft.operation_id,
                        draft.action,
                        next_step,
                        values,
                        draft.expires_at,
                    )
                )
                await self._messenger.send_text(user, prompt)
                return
            try:
                target_date = None if value == "なし" else date.fromisoformat(value)
            except ValueError as exc:
                raise ProfileCommandError(
                    "日付はYYYY-MM-DDまたは「なし」で入力してください。"
                ) from exc
            if len(await self._goals.list(user)) >= MAX_ITEMS:
                raise ProfileCommandError(f"有効な目標は{MAX_ITEMS}件までです。")
            priority = (
                GoalPriority.PRIMARY
                if values["priority"] in {"主目標", "primary"}
                else GoalPriority.SECONDARY
            )
            await self._goals.save(
                user,
                Goal(
                    id=draft.operation_id,
                    goal_type=values["type"],
                    target=values["target"],
                    target_date=target_date,
                    priority=priority,
                ),
            )
            await self._finish(user, "目標を登録しました。")
            return
        if draft.action.startswith("goal_"):
            matches = [
                goal
                for goal in await self._goals.list(user)
                if goal.id.startswith(value if draft.step == "id" else values["id"])
            ]
            if len(matches) != 1:
                raise ProfileCommandError("対象の目標IDを確認できませんでした。")
            if draft.action == "goal_deactivate":
                await self._goals.deactivate(user, matches[0].id)
                await self._finish(user, "目標を無効化しました。")
            elif draft.step == "id":
                await self._drafts.save(
                    ProfileDraft(
                        user,
                        draft.operation_id,
                        draft.action,
                        "target",
                        {"id": matches[0].id},
                        draft.expires_at,
                    )
                )
                await self._messenger.send_text(
                    user, "新しい目標内容を入力してください。"
                )
            else:
                await self._goals.save(
                    user, matches[0].model_copy(update={"target": value})
                )
                await self._finish(user, "目標を変更しました。")
            return
        if draft.step == "id":
            matches = [
                item
                for item in await self._resources.list(user)
                if item.id.startswith(value)
            ]
            if len(matches) != 1:
                raise ProfileCommandError("対象の運動環境IDを確認できませんでした。")
            if draft.action == "environment_deactivate":
                await self._resources.deactivate(user, matches[0].id)
                await self._finish(user, "運動環境を無効化しました。")
            else:
                await self._drafts.save(
                    ProfileDraft(
                        user,
                        draft.operation_id,
                        draft.action,
                        "name",
                        {"id": matches[0].id},
                        draft.expires_at,
                    )
                )
                await self._messenger.send_text(
                    user, "新しい場所・種目・器具名を入力してください。"
                )
            return
        if (
            draft.action == "environment_add"
            and len(await self._resources.list(user)) >= MAX_ITEMS
        ):
            raise ProfileCommandError(f"有効な運動環境は{MAX_ITEMS}件までです。")
        resource = normalize_environment(value)
        resource.id = values.get("id", draft.operation_id)
        await self._resources.save(user, resource)
        await self._finish(user, "運動環境を保存しました。")

    async def _finish(self, user: str, message: str) -> None:
        await self._drafts.delete(user)
        await self._messenger.send_text(user, message)
