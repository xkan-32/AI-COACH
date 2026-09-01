from __future__ import annotations

import uuid
from datetime import date
from typing import Protocol

from app.domain.models import Goal, GoalPriority


class GoalStore(Protocol):
    async def list(self, line_user_id: str) -> list[Goal]: ...
    async def save(self, line_user_id: str, goal: Goal) -> None: ...


class TrainingResourceStore(Protocol):
    async def list(self, line_user_id: str) -> list[str]: ...
    async def replace(self, line_user_id: str, resources: list[str]) -> None: ...


class InMemoryGoalStore:
    def __init__(self) -> None:
        self.items: dict[str, list[Goal]] = {}

    async def list(self, line_user_id: str) -> list[Goal]:
        return list(self.items.get(line_user_id, []))

    async def save(self, line_user_id: str, goal: Goal) -> None:
        goals = self.items.setdefault(line_user_id, [])
        if goal.priority == GoalPriority.PRIMARY:
            for existing in goals:
                if existing.priority == GoalPriority.PRIMARY:
                    existing.priority = GoalPriority.SECONDARY
        goals.append(goal)


class InMemoryTrainingResourceStore:
    def __init__(self) -> None:
        self.items: dict[str, list[str]] = {}

    async def list(self, line_user_id: str) -> list[str]:
        return list(self.items.get(line_user_id, []))

    async def replace(self, line_user_id: str, resources: list[str]) -> None:
        self.items[line_user_id] = list(resources)


class FirestoreGoalStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def list(self, line_user_id: str) -> list[Goal]:
        snapshots = (
            await self._client.collection("goals")
            .where("line_user_id", "==", line_user_id)
            .get()
        )
        return [Goal.model_validate(snapshot.to_dict()) for snapshot in snapshots]

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


class FirestoreTrainingResourceStore:
    def __init__(self, client: object) -> None:
        self._client = client

    async def list(self, line_user_id: str) -> list[str]:
        snapshot = (
            await self._client.collection("training_resources")
            .document(line_user_id)
            .get()
        )
        return list(snapshot.to_dict().get("resources", [])) if snapshot.exists else []

    async def replace(self, line_user_id: str, resources: list[str]) -> None:
        await (
            self._client.collection("training_resources")
            .document(line_user_id)
            .set({"resources": resources})
        )


class ProfileCommandError(ValueError):
    pass


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
                "利用可能な運動環境: " + "、".join(resources)
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
