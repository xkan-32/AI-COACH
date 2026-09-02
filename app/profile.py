from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from urllib.parse import parse_qs, urlencode

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
GOAL_TYPES = ("大会", "タイム・距離", "運動習慣", "体力づくり", "その他")
GOAL_TARGET_EXAMPLES = {
    "大会": "例: 東京マラソンを完走する",
    "タイム・距離": "例: 10kmを60分以内で走る",
    "運動習慣": "例: 週3回運動する",
    "体力づくり": "例: 疲れにくい身体をつくる",
    "その他": "達成したい状態を具体的に入力してください。",
}


def profile_settings_item_id(
    line_user_id: str, operation_id: str, item_type: str, index: int
) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai-coach:profile-settings:{line_user_id}:{operation_id}:"
            f"{item_type}:{index}",
        )
    )


def profile_settings_fingerprint(
    goals: list[Goal], training_environments: list[TrainingEnvironment]
) -> str:
    payload = {
        "goals": [item.model_dump(mode="json") for item in goals],
        "training_environments": [
            item.model_dump(mode="json") for item in training_environments
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


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


@dataclass(frozen=True)
class ProfileSettingsSnapshot:
    goals: list[Goal]
    training_environments: list[TrainingEnvironment]
    revision: int


class ProfileSettingsConflict(ValueError):
    pass


class ProfileSettingsStore(Protocol):
    async def get(self, line_user_id: str) -> ProfileSettingsSnapshot: ...

    async def replace(
        self,
        line_user_id: str,
        goals: list[Goal],
        training_environments: list[TrainingEnvironment],
        expected_revision: int,
        operation_id: str,
    ) -> int: ...


class ProfileMessenger(Protocol):
    async def send_text(self, line_user_id: str, text: str) -> None: ...
    async def send_quick_reply(
        self, line_user_id: str, text: str, choices: list[tuple[str, str]]
    ) -> None: ...
    async def send_settings_link(self, line_user_id: str, url: str) -> None: ...


def profile_action(section: str, operation: str, **values: str) -> str:
    return urlencode(
        {"action": "profile", "section": section, "operation": operation, **values}
    )


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


class InMemoryProfileSettingsStore:
    def __init__(
        self,
        goals: InMemoryGoalStore,
        training_environments: InMemoryTrainingResourceStore,
    ) -> None:
        self._goals = goals
        self._training_environments = training_environments
        self._revisions: dict[str, int] = {}
        self._operations: dict[str, tuple[str, str]] = {}

    async def get(self, line_user_id: str) -> ProfileSettingsSnapshot:
        return ProfileSettingsSnapshot(
            goals=await self._goals.list(line_user_id),
            training_environments=await self._training_environments.list(line_user_id),
            revision=self._revisions.get(line_user_id, 0),
        )

    async def replace(
        self,
        line_user_id: str,
        goals: list[Goal],
        training_environments: list[TrainingEnvironment],
        expected_revision: int,
        operation_id: str,
    ) -> int:
        revision = self._revisions.get(line_user_id, 0)
        fingerprint = profile_settings_fingerprint(goals, training_environments)
        previous_operation = self._operations.get(line_user_id)
        if previous_operation and previous_operation[0] == operation_id:
            if previous_operation[1] != fingerprint:
                raise ProfileSettingsConflict("Operation payload changed")
            return revision
        if revision != expected_revision:
            raise ProfileSettingsConflict("Profile settings changed")

        current_goal_ids = {item.id for item in await self._goals.list(line_user_id)}
        current_environment_ids = {
            item.id for item in await self._training_environments.list(line_user_id)
        }
        supplied_goal_ids = {item.id for item in goals if item.id in current_goal_ids}
        supplied_environment_ids = {
            item.id
            for item in training_environments
            if item.id in current_environment_ids
        }
        allowed_new_goal_ids = {
            profile_settings_item_id(line_user_id, operation_id, "goal", index)
            for index in range(len(goals))
        }
        allowed_new_environment_ids = {
            profile_settings_item_id(line_user_id, operation_id, "environment", index)
            for index in range(len(training_environments))
        }
        unknown_goal_ids = {
            item.id
            for item in goals
            if item.id not in current_goal_ids and item.id not in allowed_new_goal_ids
        }
        unknown_environment_ids = {
            item.id
            for item in training_environments
            if item.id not in current_environment_ids
            and item.id not in allowed_new_environment_ids
        }
        if unknown_goal_ids or unknown_environment_ids:
            raise ProfileSettingsConflict("Profile settings item changed")

        original_goals = deepcopy(self._goals.items.get(line_user_id, []))
        original_environments = deepcopy(
            self._training_environments.items.get(line_user_id, [])
        )
        try:
            for goal_id in current_goal_ids - supplied_goal_ids:
                await self._goals.deactivate(line_user_id, goal_id)
            for goal in goals:
                await self._goals.save(line_user_id, goal)
            for environment_id in current_environment_ids - supplied_environment_ids:
                await self._training_environments.deactivate(
                    line_user_id, environment_id
                )
            for environment in training_environments:
                await self._training_environments.save(line_user_id, environment)
        except Exception:
            self._goals.items[line_user_id] = original_goals
            self._training_environments.items[line_user_id] = original_environments
            raise

        revision += 1
        self._revisions[line_user_id] = revision
        self._operations[line_user_id] = operation_id, fingerprint
        return revision


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


class FirestoreProfileSettingsStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def _state_document(self, line_user_id: str):
        return self._client.collection("profile_settings_state").document(line_user_id)

    async def get(self, line_user_id: str) -> ProfileSettingsSnapshot:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        await FirestoreTrainingResourceStore(self._client)._ensure_migrated(
            line_user_id
        )
        transaction = self._client.transaction()

        @async_transactional
        async def read_once(active_transaction: object) -> ProfileSettingsSnapshot:
            state = await self._state_document(line_user_id).get(
                transaction=active_transaction
            )
            goal_snapshots = await (
                self._client.collection("goals")
                .where("line_user_id", "==", line_user_id)
                .get(transaction=active_transaction)
            )
            environment_snapshots = await (
                self._client.collection("training_environments")
                .where("line_user_id", "==", line_user_id)
                .get(transaction=active_transaction)
            )
            goals = [
                goal
                for snapshot in goal_snapshots
                if (goal := Goal.model_validate(snapshot.to_dict())).status
                == GoalStatus.ACTIVE
            ]
            training_environments = [
                environment
                for snapshot in environment_snapshots
                if (
                    environment := TrainingEnvironment.model_validate(
                        snapshot.to_dict()
                    )
                ).status
                == TrainingEnvironmentStatus.ACTIVE
            ]
            revision = int(state.to_dict().get("revision", 0)) if state.exists else 0
            return ProfileSettingsSnapshot(goals, training_environments, revision)

        return await read_once(transaction)

    async def replace(
        self,
        line_user_id: str,
        goals: list[Goal],
        training_environments: list[TrainingEnvironment],
        expected_revision: int,
        operation_id: str,
    ) -> int:
        from google.cloud.firestore_v1.async_transaction import async_transactional

        await FirestoreTrainingResourceStore(self._client)._ensure_migrated(
            line_user_id
        )
        transaction = self._client.transaction()

        @async_transactional
        async def replace_once(active_transaction: object) -> int:
            state_document = self._state_document(line_user_id)
            state = await state_document.get(transaction=active_transaction)
            state_values = state.to_dict() if state.exists else {}
            revision = int(state_values.get("revision", 0))
            fingerprint = profile_settings_fingerprint(goals, training_environments)
            if state_values.get("last_operation_id") == operation_id:
                if state_values.get("last_operation_fingerprint") != fingerprint:
                    raise ProfileSettingsConflict("Operation payload changed")
                return revision
            if revision != expected_revision:
                raise ProfileSettingsConflict("Profile settings changed")

            goal_snapshots = await (
                self._client.collection("goals")
                .where("line_user_id", "==", line_user_id)
                .get(transaction=active_transaction)
            )
            environment_snapshots = await (
                self._client.collection("training_environments")
                .where("line_user_id", "==", line_user_id)
                .get(transaction=active_transaction)
            )
            active_goal_documents = {
                snapshot.id: snapshot.reference
                for snapshot in goal_snapshots
                if snapshot.to_dict().get("status") == GoalStatus.ACTIVE.value
            }
            active_environment_documents = {
                snapshot.id: snapshot.reference
                for snapshot in environment_snapshots
                if snapshot.to_dict().get("status")
                == TrainingEnvironmentStatus.ACTIVE.value
            }
            allowed_new_goal_ids = {
                profile_settings_item_id(line_user_id, operation_id, "goal", index)
                for index in range(len(goals))
            }
            allowed_new_environment_ids = {
                profile_settings_item_id(
                    line_user_id, operation_id, "environment", index
                )
                for index in range(len(training_environments))
            }
            if any(
                item.id not in active_goal_documents
                and item.id not in allowed_new_goal_ids
                for item in goals
            ) or any(
                item.id not in active_environment_documents
                and item.id not in allowed_new_environment_ids
                for item in training_environments
            ):
                raise ProfileSettingsConflict("Profile settings item changed")

            supplied_goal_ids = {item.id for item in goals}
            supplied_environment_ids = {item.id for item in training_environments}
            for item_id, document in active_goal_documents.items():
                if item_id not in supplied_goal_ids:
                    active_transaction.update(
                        document, {"status": GoalStatus.PAUSED.value}
                    )
            for goal in goals:
                values = goal.model_dump(mode="json")
                values["line_user_id"] = line_user_id
                active_transaction.set(
                    self._client.collection("goals").document(goal.id), values
                )
            for item_id, document in active_environment_documents.items():
                if item_id not in supplied_environment_ids:
                    active_transaction.update(
                        document,
                        {"status": TrainingEnvironmentStatus.INACTIVE.value},
                    )
            for environment in training_environments:
                values = environment.model_dump(mode="json")
                values["line_user_id"] = line_user_id
                active_transaction.set(
                    self._client.collection("training_environments").document(
                        environment.id
                    ),
                    values,
                )

            next_revision = revision + 1
            active_transaction.set(
                state_document,
                {
                    "line_user_id": line_user_id,
                    "revision": next_revision,
                    "last_operation_id": operation_id,
                    "last_operation_fingerprint": fingerprint,
                    "updated_at": datetime.now(UTC),
                },
            )
            return next_revision

        return await replace_once(transaction)


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
        messenger: ProfileMessenger,
        clock=lambda: datetime.now(UTC),
        on_settings_requested: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._goals = goals
        self._resources = resources
        self._drafts = drafts
        self._messenger = messenger
        self._clock = clock
        self._on_settings_requested = on_settings_requested
        self._commands = ProfileCommandService(goals, resources, messenger)

    async def handle_postback(self, line_user_id: str, data: str) -> bool:
        values = {key: items[0] for key, items in parse_qs(data).items() if items}
        if values.get("action") == "menu" and values.get("version") == "1":
            if values.get("target") == "goals":
                await self._commands.handle(line_user_id, "目標確認")
                return True
            if values.get("target") == "settings":
                if self._on_settings_requested is None:
                    await self._show_settings_menu(line_user_id)
                else:
                    await self._on_settings_requested(line_user_id)
                return True
            return False
        if values.get("action") != "profile":
            return False
        section = values.get("section", "")
        operation = values.get("operation", "")
        if operation == "cancel":
            return await self.handle_text(line_user_id, "キャンセル")
        if section == "goals":
            await self._handle_goal_postback(line_user_id, operation, values)
            return True
        if section == "environments":
            await self._handle_environment_postback(line_user_id, operation, values)
            return True
        raise ProfileCommandError("選択された設定項目を確認できませんでした。")

    async def _show_settings_menu(self, user: str) -> None:
        await self._messenger.send_quick_reply(
            user,
            "設定する項目を選んでください。",
            [
                ("目標", profile_action("goals", "menu")),
                ("運動環境", profile_action("environments", "menu")),
                ("キャンセル", profile_action("settings", "cancel")),
            ],
        )

    async def _show_goal_menu(self, user: str) -> None:
        await self._commands.handle(user, "目標確認")
        goals = await self._goals.list(user)
        choices = [("追加", profile_action("goals", "add"))]
        if goals:
            choices.extend(
                [
                    ("変更", profile_action("goals", "change")),
                    ("無効化", profile_action("goals", "deactivate")),
                ]
            )
        choices.append(("キャンセル", profile_action("goals", "cancel")))
        await self._messenger.send_quick_reply(
            user, "目標の操作を選んでください。", choices
        )

    async def _show_environment_menu(self, user: str) -> None:
        await self._commands.handle(user, "運動環境確認")
        resources = await self._resources.list(user)
        choices = [("追加", profile_action("environments", "add"))]
        if resources:
            choices.extend(
                [
                    ("変更", profile_action("environments", "change")),
                    ("無効化", profile_action("environments", "deactivate")),
                ]
            )
        choices.append(("キャンセル", profile_action("environments", "cancel")))
        await self._messenger.send_quick_reply(
            user, "運動環境の操作を選んでください。", choices
        )

    async def _handle_goal_postback(
        self, user: str, operation: str, values: dict[str, str]
    ) -> None:
        if operation == "menu":
            await self._show_goal_menu(user)
            return
        if operation == "add":
            await self._drafts.delete(user)
            await self.handle_text(user, "目標追加")
            await self._messenger.send_quick_reply(
                user,
                "主目標または副目標を選んでください。",
                [
                    ("主目標", profile_action("goals", "priority", value="主目標")),
                    ("副目標", profile_action("goals", "priority", value="副目標")),
                    ("キャンセル", profile_action("goals", "cancel")),
                ],
            )
            return
        if operation == "priority":
            await self._continue(
                user, values.get("value", ""), await self._active_draft(user)
            )
            await self._messenger.send_quick_reply(
                user,
                "目標の種類を選んでください。",
                [
                    (name, profile_action("goals", "type", value=name))
                    for name in GOAL_TYPES
                ]
                + [("キャンセル", profile_action("goals", "cancel"))],
            )
            return
        if operation == "type":
            await self._continue(
                user, values.get("value", ""), await self._active_draft(user)
            )
            return
        if operation in {"change", "deactivate"}:
            await self._drafts.delete(user)
            await self.handle_text(
                user, "目標変更" if operation == "change" else "目標無効化"
            )
            goals = await self._goals.list(user)
            if not goals:
                await self._drafts.delete(user)
                raise ProfileCommandError("変更できる目標がありません。")
            await self._messenger.send_quick_reply(
                user,
                "対象の目標を選んでください。",
                [
                    (
                        f"{goal.goal_type}: {goal.target}"[:20],
                        profile_action("goals", "select", value=goal.id),
                    )
                    for goal in goals[:12]
                ]
                + [("キャンセル", profile_action("goals", "cancel"))],
            )
            return
        if operation == "select":
            await self._continue(
                user, values.get("value", ""), await self._active_draft(user)
            )
            return
        raise ProfileCommandError("選択された目標操作を確認できませんでした。")

    async def _handle_environment_postback(
        self, user: str, operation: str, values: dict[str, str]
    ) -> None:
        if operation == "menu":
            await self._show_environment_menu(user)
            return
        if operation == "add":
            await self._drafts.delete(user)
            await self._drafts.save(
                ProfileDraft(
                    user,
                    str(uuid.uuid4()),
                    "environment_batch",
                    "multi_select",
                    {"selected": "[]"},
                )
            )
            await self._show_environment_groups(user)
            return
        if operation == "group":
            group = values.get("value")
            presets = (
                sorted(ACTIVITY_PLACES if group == "activity_place" else EQUIPMENT)
                if group in {"activity_place", "equipment"}
                else []
            )
            if not presets:
                raise ProfileCommandError("運動環境の種類を確認できませんでした。")
            draft = await self._active_draft(user)
            selected = self._selected_environments(draft)
            await self._messenger.send_quick_reply(
                user,
                f"項目を選んでください（選択中 {len(selected)}件）。再タップで解除できます。",
                [
                    (
                        ("✓ " if name in selected else "") + name,
                        profile_action(
                            "environments", "toggle", value=name, group=group
                        ),
                    )
                    for name in presets
                ]
                + [
                    ("種類選択へ戻る", profile_action("environments", "groups")),
                    ("選択完了", profile_action("environments", "complete")),
                    ("キャンセル", profile_action("environments", "cancel")),
                ],
            )
            return
        if operation == "groups":
            await self._show_environment_groups(user)
            return
        if operation == "toggle":
            await self._toggle_environment(user, values.get("value", ""))
            await self._handle_environment_postback(
                user, "group", {"value": values.get("group", "")}
            )
            return
        if operation == "other":
            draft = await self._active_draft(user)
            await self._drafts.save(
                ProfileDraft(
                    user,
                    draft.operation_id,
                    draft.action,
                    "multi_other",
                    draft.values,
                    draft.expires_at,
                )
            )
            await self._messenger.send_text(
                user, "その他の運動環境を入力してください。入力後も選択を続けられます。"
            )
            return
        if operation == "complete":
            await self._complete_environment_batch(user)
            return
        if operation in {"change", "deactivate"}:
            await self._drafts.delete(user)
            await self.handle_text(
                user, "運動環境変更" if operation == "change" else "運動環境無効化"
            )
            resources = await self._resources.list(user)
            if not resources:
                await self._drafts.delete(user)
                raise ProfileCommandError("変更できる運動環境がありません。")
            await self._messenger.send_quick_reply(
                user,
                "対象の運動環境を選んでください。",
                [
                    (
                        item.display_name[:20],
                        profile_action("environments", "select", value=item.id),
                    )
                    for item in resources[:12]
                ]
                + [("キャンセル", profile_action("environments", "cancel"))],
            )
            return
        if operation == "select":
            await self._continue(
                user, values.get("value", ""), await self._active_draft(user)
            )
            return
        raise ProfileCommandError("選択された運動環境操作を確認できませんでした。")

    def _selected_environments(self, draft: ProfileDraft) -> list[str]:
        try:
            selected = json.loads(draft.values.get("selected", "[]"))
        except json.JSONDecodeError as exc:
            raise ProfileCommandError(
                "選択状態を確認できません。やり直してください。"
            ) from exc
        return [str(item) for item in selected] if isinstance(selected, list) else []

    async def _show_environment_groups(self, user: str) -> None:
        draft = await self._active_draft(user)
        if draft.action != "environment_batch":
            raise ProfileCommandError("複数選択をメニューからやり直してください。")
        selected = self._selected_environments(draft)
        await self._messenger.send_quick_reply(
            user,
            f"種類を選んでください（選択中 {len(selected)}件）。",
            [
                (
                    "場所・種目",
                    profile_action("environments", "group", value="activity_place"),
                ),
                (
                    "器具",
                    profile_action("environments", "group", value="equipment"),
                ),
                ("その他", profile_action("environments", "other")),
                ("選択完了", profile_action("environments", "complete")),
                ("キャンセル", profile_action("environments", "cancel")),
            ],
        )

    async def _toggle_environment(self, user: str, value: str) -> None:
        draft = await self._active_draft(user)
        item = normalize_environment(value)
        selected = self._selected_environments(draft)
        if item.display_name in selected:
            selected.remove(item.display_name)
        else:
            selected.append(item.display_name)
        if len(selected) > MAX_ITEMS:
            raise ProfileCommandError(f"運動環境は{MAX_ITEMS}件まで選択できます。")
        await self._drafts.save(
            ProfileDraft(
                user,
                draft.operation_id,
                draft.action,
                "multi_select",
                {"selected": json.dumps(selected, ensure_ascii=False)},
                draft.expires_at,
            )
        )

    async def _complete_environment_batch(self, user: str) -> None:
        draft = await self._active_draft(user)
        selected = self._selected_environments(draft)
        if not selected:
            raise ProfileCommandError("運動環境を1件以上選択してください。")
        existing = await self._resources.list(user)
        existing_keys = {environment_key(item) for item in existing}
        additions: list[TrainingEnvironment] = []
        for value in selected:
            item = normalize_environment(value)
            if environment_key(item) not in existing_keys:
                additions.append(item)
                existing_keys.add(environment_key(item))
        if len(existing) + len(additions) > MAX_ITEMS:
            raise ProfileCommandError(f"有効な運動環境は{MAX_ITEMS}件までです。")
        for item in additions:
            item.id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{draft.operation_id}:{environment_key(item)}",
                )
            )
            await self._resources.save(user, item)
        await self._drafts.delete(user)
        skipped = len(selected) - len(additions)
        suffix = f"（登録済み{skipped}件は変更なし）" if skipped else ""
        await self._messenger.send_text(
            user, f"運動環境を{len(additions)}件追加しました。{suffix}"
        )

    async def _active_draft(self, user: str) -> ProfileDraft:
        draft = await self._drafts.get(user)
        if draft is None or draft.expires_at <= self._clock():
            await self._drafts.delete(user)
            raise ProfileCommandError(
                "入力状態がありません。メニューからやり直してください。"
            )
        return draft

    async def handle_text(self, line_user_id: str, text: str) -> bool:
        value = text.strip()
        draft = await self._drafts.get(line_user_id)
        if value.lower() in {"cancel", "キャンセル"}:
            if draft is None:
                return False
            await self._drafts.delete(line_user_id)
            await self._messenger.send_text(line_user_id, "入力をキャンセルしました。")
            return True
        if draft:
            if draft.expires_at <= self._clock():
                await self._drafts.delete(line_user_id)
                raise ProfileCommandError(
                    "入力の有効期限が切れました。メニューから再開してください。"
                )
            if draft.action == "environment_batch" and draft.step == "multi_other":
                if not value:
                    raise ProfileCommandError("その他の運動環境を入力してください。")
                await self._toggle_environment(line_user_id, value)
                await self._show_environment_groups(line_user_id)
                return True
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
                "priority": ("type", "目標の種類を選ぶか入力してください。"),
                "type": ("target", ""),
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
                if draft.step == "type":
                    prompt = (
                        "達成したい内容を入力してください。\n"
                        + GOAL_TARGET_EXAMPLES.get(
                            value, GOAL_TARGET_EXAMPLES["その他"]
                        )
                    )
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
