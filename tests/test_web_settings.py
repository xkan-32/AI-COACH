from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.models import (
    Goal,
    GoalPriority,
    TrainingEnvironment,
    TrainingEnvironmentCategory,
)
from app.line import InMemoryConditionPromptSender
from app.profile import (
    FirestoreProfileSettingsStore,
    InMemoryGoalStore,
    InMemoryProfileDraftStore,
    InMemoryTrainingResourceStore,
    ProfileWorkflow,
    profile_settings_item_id,
)
from app.web_settings import (
    FirestoreSettingsLinkStore,
    InMemorySettingsLinkStore,
    InvalidSettingsToken,
    SettingsTokenSigner,
)


@pytest.mark.asyncio
async def test_firestore_settings_link_reads_through_client_transaction() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)

    class Snapshot:
        exists = True

        def to_dict(self):
            return {
                "line_user_id": "U-firestore",
                "expires_at": now + timedelta(minutes=1),
                "used_at": None,
            }

    class Transaction:
        def __init__(self) -> None:
            self.updated = False
            self.committed = False
            self._id = None
            self._read_only = False
            self._max_attempts = 1

        def _clean_up(self) -> None:
            self._id = None

        async def _begin(self, retry_id=None) -> None:
            self._id = b"transaction-id"

        def update(self, document, values) -> None:
            self.updated = values["used_at"] == now

        async def _commit(self) -> None:
            self.committed = True

        async def _rollback(self) -> None:
            pass

    class Document:
        pass

    class Collection:
        def document(self, nonce):
            return Document()

    transaction = Transaction()

    class Client:
        def collection(self, name):
            return Collection()

        def transaction(self):
            return transaction

        async def get_all(self, documents, transaction):
            assert len(documents) == 1
            assert transaction is transaction_instance
            yield Snapshot()

    transaction_instance = transaction
    store = FirestoreSettingsLinkStore(Client())
    assert await store.consume("nonce", now) == "U-firestore"
    assert transaction.updated
    assert transaction.committed


@pytest.mark.asyncio
async def test_firestore_profile_settings_are_written_in_one_transaction() -> None:
    class Snapshot:
        exists = False

        def to_dict(self):
            return {}

    class Document:
        def __init__(self, collection: str, item_id: str) -> None:
            self.collection = collection
            self.id = item_id
            self.reference = self

        async def get(self, transaction=None):
            return Snapshot()

    class Query:
        def __init__(self, collection: str) -> None:
            self.collection = collection

        def where(self, *args):
            return self

        async def get(self, transaction=None):
            return []

    class Collection(Query):
        def document(self, item_id: str):
            return Document(self.collection, item_id)

    class Transaction:
        def __init__(self) -> None:
            self._id = None
            self._read_only = False
            self._max_attempts = 1
            self.writes: list[tuple[str, str]] = []
            self.committed = False

        def _clean_up(self) -> None:
            self._id = None

        async def _begin(self, retry_id=None) -> None:
            self._id = b"transaction-id"

        def set(self, document, values) -> None:
            self.writes.append((document.collection, document.id))

        def update(self, document, values) -> None:
            self.writes.append((document.collection, document.id))

        async def _commit(self) -> None:
            self.committed = True

        async def _rollback(self) -> None:
            pass

    transaction = Transaction()

    class Client:
        def collection(self, name: str):
            return Collection(name)

        def transaction(self):
            return transaction

    operation_id = "firestore-atomic-1"
    store = FirestoreProfileSettingsStore(Client())
    revision = await store.replace(
        "U-firestore-profile",
        [
            Goal(
                id=profile_settings_item_id(
                    "U-firestore-profile", operation_id, "goal", 0
                ),
                goal_type="大会",
                target="完走",
                priority=GoalPriority.PRIMARY,
            )
        ],
        [
            TrainingEnvironment(
                id=profile_settings_item_id(
                    "U-firestore-profile", operation_id, "environment", 0
                ),
                display_name="ダンベル",
                category=TrainingEnvironmentCategory.EQUIPMENT,
            )
        ],
        expected_revision=0,
        operation_id=operation_id,
    )

    assert revision == 1
    assert transaction.committed
    assert transaction.writes == [
        (
            "goals",
            profile_settings_item_id("U-firestore-profile", operation_id, "goal", 0),
        ),
        (
            "training_environments",
            profile_settings_item_id(
                "U-firestore-profile", operation_id, "environment", 0
            ),
        ),
        ("profile_settings_state", "U-firestore-profile"),
    ]


def test_settings_page_requires_one_time_link_and_saves_multiple_items() -> None:
    from app.main import app, runtime

    client = TestClient(app)
    runtime.messenger.settings_links.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "web-settings-test",
            "event": {
                "type": "postback",
                "source": {"userId": "U-web"},
                "postback": {"data": "action=menu&version=1&target=settings"},
            },
        },
    )
    assert response.status_code == 200
    url = runtime.messenger.settings_links[-1][1]
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 400

    saved = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": 0,
            "operation_id": "web-settings-save-1",
            "goals": [
                {
                    "goal_type": "大会",
                    "target": "秋の大会を完走",
                    "target_date": None,
                    "priority": "primary",
                },
                {
                    "goal_type": "運動習慣",
                    "target": "週3回運動",
                    "target_date": None,
                    "priority": "secondary",
                },
            ],
            "training_environments": [
                {
                    "display_name": "インドアバイク",
                    "category": "activity_place",
                },
                {"display_name": "ダンベル", "category": "equipment"},
            ],
        },
    )
    assert saved.status_code == 200
    current = client.get("/settings/profile/api").json()
    assert len(current["goals"]) == 2
    assert {item["display_name"] for item in current["training_environments"]} == {
        "インドアバイク",
        "ダンベル",
    }
    assert current["revision"] == 1
    assert current["target_weight_kg"] is None


def test_settings_api_saves_and_clears_target_weight() -> None:
    from app.main import app, runtime

    client = TestClient(app)
    runtime.messenger.settings_links.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "web-settings-target-weight",
            "event": {
                "type": "postback",
                "source": {"userId": "U-web-weight"},
                "postback": {"data": "action=menu&version=1&target=settings"},
            },
        },
    )
    assert response.status_code == 200
    assert client.get(runtime.messenger.settings_links[-1][1]).status_code == 200

    saved = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": 0,
            "operation_id": "web-settings-weight-1",
            "goals": [],
            "training_environments": [],
            "target_weight_kg": 68.24,
        },
    )
    assert saved.status_code == 200
    current = client.get("/settings/profile/api").json()
    assert current["target_weight_kg"] == 68.2

    omitted = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": current["revision"],
            "operation_id": "web-settings-weight-2",
            "goals": [],
            "training_environments": [],
        },
    )
    assert omitted.status_code == 200
    assert client.get("/settings/profile/api").json()["target_weight_kg"] == 68.2

    cleared = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": omitted.json()["revision"],
            "operation_id": "web-settings-weight-3",
            "goals": [],
            "training_environments": [],
            "target_weight_kg": None,
        },
    )
    assert cleared.status_code == 200
    assert client.get("/settings/profile/api").json()["target_weight_kg"] is None

    rejected = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": cleared.json()["revision"],
            "operation_id": "web-settings-weight-4",
            "goals": [],
            "training_environments": [],
            "target_weight_kg": 20,
        },
    )
    assert rejected.status_code == 422


def test_settings_api_normalizes_alias_preserves_other_detail_and_is_idempotent() -> (
    None
):
    from app.main import app, runtime

    client = TestClient(app)
    runtime.messenger.settings_links.clear()
    response = client.post(
        "/tasks/line/events",
        json={
            "event_key": "web-settings-normalization-test",
            "event": {
                "type": "postback",
                "source": {"userId": "U-web-normalization"},
                "postback": {"data": "action=menu&version=1&target=settings"},
            },
        },
    )
    assert response.status_code == 200
    assert client.get(runtime.messenger.settings_links[-1][1]).status_code == 200

    payload = {
        "expected_revision": 0,
        "operation_id": "web-settings-normalize-1",
        "goals": [],
        "training_environments": [
            {"display_name": "ルームバイク", "category": "other"},
            {"display_name": "河川敷の階段", "category": "activity_place"},
        ],
    }
    first = client.put("/settings/profile/api", json=payload)
    retry = client.put("/settings/profile/api", json=payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json()["revision"] == retry.json()["revision"] == 1
    current = client.get("/settings/profile/api").json()
    assert current["training_environments"] == [
        {
            "id": current["training_environments"][0]["id"],
            "display_name": "インドアバイク",
            "category": "activity_place",
            "status": "active",
            "detail": None,
        },
        {
            "id": current["training_environments"][1]["id"],
            "display_name": "河川敷の階段",
            "category": "other",
            "status": "active",
            "detail": "河川敷の階段",
        },
    ]

    changed_retry = client.put(
        "/settings/profile/api",
        json={**payload, "training_environments": []},
    )
    assert changed_retry.status_code == 409

    conflict = client.put(
        "/settings/profile/api",
        json={**payload, "operation_id": "web-settings-normalize-2"},
    )
    assert conflict.status_code == 409


def test_settings_api_rejects_unauthenticated_request() -> None:
    from app.main import app

    assert TestClient(app).get("/settings/profile/api").status_code == 401


def test_planning_settings_api_saves_multiple_slots_and_clears_preference() -> None:
    from app.main import app, runtime

    client = TestClient(app)
    runtime.messenger.settings_links.clear()
    opened = client.post(
        "/tasks/line/events",
        json={
            "event_key": "planning-settings-test",
            "event": {
                "type": "postback",
                "source": {"userId": "U-planning-settings"},
                "postback": {"data": "action=menu&version=1&target=settings"},
            },
        },
    )
    assert opened.status_code == 200
    assert client.get(runtime.messenger.settings_links[-1][1]).status_code == 200
    assert client.get("/settings/planning").status_code == 200

    profile = client.put(
        "/settings/profile/api",
        json={
            "expected_revision": 0,
            "operation_id": "planning-settings-profile-1",
            "goals": [],
            "training_environments": [
                {"display_name": "インドアバイク", "category": "activity_place"}
            ],
        },
    )
    assert profile.status_code == 200
    environment_id = client.get("/settings/profile/api").json()[
        "training_environments"
    ][0]["id"]
    payload = {
        "expected_availability_version": None,
        "operation_id": "planning-settings-save-1",
        "slots": [
            {
                "weekday": 0,
                "start_local_time": "06:00:00",
                "end_local_time": "07:00:00",
                "max_workout_minutes": 60,
                "environment_ids": [],
                "outdoors_allowed": True,
                "split_allowed": False,
            },
            {
                "weekday": 0,
                "start_local_time": "20:00:00",
                "end_local_time": "21:00:00",
                "max_workout_minutes": 60,
                "environment_ids": [environment_id],
                "outdoors_allowed": False,
                "split_allowed": False,
            },
        ],
        "preferences": [
            {
                "preference_type": "weekend_intensity",
                "value": {"intensity": "moderate", "weekdays": [5, 6]},
                "strength": "soft",
            }
        ],
    }
    saved = client.put("/settings/planning/api", json=payload)
    assert saved.status_code == 200
    current = client.get("/settings/planning/api").json()
    assert len(current["availability"]["slots"]) == 2
    assert current["availability"]["slots"][1]["outdoors_allowed"] is False
    assert current["availability"]["slots"][1]["environment_ids"] == [environment_id]
    assert [item["preference_type"] for item in current["preferences"]] == [
        "weekend_intensity"
    ]

    cleared = client.put(
        "/settings/planning/api",
        json={
            **payload,
            "expected_availability_version": current["availability"]["version"],
            "operation_id": "planning-settings-save-2",
            "preferences": [],
        },
    )
    assert cleared.status_code == 200
    assert client.get("/settings/planning/api").json()["preferences"] == []


def test_settings_page_has_mobile_goal_controls() -> None:
    page = (Path(__file__).parents[1] / "app/static/profile-settings.html").read_text()

    assert "＋ 主目標を登録" in page
    assert "＋ 副目標を追加" in page
    assert "主目標に設定" in page
    assert 'id="target-weight"' in page
    assert "目標体重" in page
    assert "target_weight_kg" in page
    assert "input:not([type=radio]):not([type=checkbox])" in page
    assert "input[type=date]{min-height:46px" in page
    assert "font-size:16px" in page
    assert "maximum-scale" not in page
    assert "detail:entered" in page
    assert "apiError" in page
    assert "expected_revision:state.revision" in page


def test_planning_settings_page_has_availability_controls() -> None:
    page = (Path(__file__).parents[1] / "app/static/planning-settings.html").read_text()

    assert "時間枠を追加" in page
    assert "/settings/planning/api" in page
    assert "屋外可" in page
    assert "土日は強度を高めに希望する" in page


def test_settings_link_is_signed_expires_and_does_not_expose_user_id() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    signer = SettingsTokenSigner("secret", clock=lambda: now)
    token, link = signer.create_link("U-sensitive")

    assert "U-sensitive" not in token
    assert signer.verify_link(token) == link.nonce
    with pytest.raises(InvalidSettingsToken):
        SettingsTokenSigner(
            "secret", clock=lambda: now + timedelta(minutes=11)
        ).verify_link(token)
    with pytest.raises(InvalidSettingsToken):
        signer.verify_link(token + "x")


@pytest.mark.asyncio
async def test_settings_link_can_only_be_consumed_once() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    store = InMemorySettingsLinkStore()
    _, link = SettingsTokenSigner("secret", clock=lambda: now).create_link("U1")
    await store.create(link)

    assert await store.consume(link.nonce, now) == "U1"
    assert await store.consume(link.nonce, now) is None


@pytest.mark.asyncio
async def test_rich_menu_goal_is_read_only_and_settings_opens_web() -> None:
    messenger = InMemoryConditionPromptSender()
    opened: list[str] = []

    async def open_settings(user: str) -> None:
        opened.append(user)

    workflow = ProfileWorkflow(
        InMemoryGoalStore(),
        InMemoryTrainingResourceStore(),
        InMemoryProfileDraftStore(),
        messenger,
        on_settings_requested=open_settings,
    )

    assert await workflow.handle_postback("U1", "action=menu&version=1&target=goals")
    assert messenger.texts == [("U1", "目標は未登録です。")]
    assert messenger.quick_replies == []
    assert await workflow.handle_postback("U1", "action=menu&version=1&target=settings")
    assert opened == ["U1"]
