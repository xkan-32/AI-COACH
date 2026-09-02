from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.line import InMemoryConditionPromptSender
from app.profile import (
    InMemoryGoalStore,
    InMemoryProfileDraftStore,
    InMemoryTrainingResourceStore,
    ProfileWorkflow,
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


def test_settings_api_rejects_unauthenticated_request() -> None:
    from app.main import app

    assert TestClient(app).get("/settings/profile/api").status_code == 401


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
