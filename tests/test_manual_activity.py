from datetime import UTC, date, datetime, timedelta

import pytest

from app.activity_data import InMemoryActivityIngestionStateStore
from app.condition import InMemoryActivityContextStore
from app.domain.models import (
    Activity,
    ActivitySource,
    TrainingEnvironment,
    TrainingEnvironmentCategory,
)
from app.ingestion import InMemoryActivityStore
from app.line import InMemoryConditionPromptSender
from app.line_menu import MENU_MESSAGES, MenuActionRouter
from app.manual_activity import (
    FirestoreManualActivityDraftStore,
    InMemoryManualActivityDraftStore,
    InMemoryManualStravaPublicationStore,
    InvalidManualActivityAction,
    ManualActivityDraft,
    ManualActivityWorkflow,
    create_manual_activity,
    manual_activity_id,
)
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    create_plan_version,
    create_planned_workout,
    create_user_training_profile,
)
from app.profile import InMemoryTrainingResourceStore
from app.state import InMemoryStravaTokenStore
from app.strava import StoredStravaToken, StravaApiError

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
WEEK_START = date(2026, 8, 31)


class FakeStravaClient:
    def __init__(self, activity_id: str = "9001") -> None:
        self.activity_id = activity_id
        self.created: list[dict] = []

    async def create_activity(self, access_token: str, **kwargs):
        self.created.append({"access_token": access_token, **kwargs})
        return Activity(
            id=self.activity_id,
            athlete_id="athlete-1",
            activity_type=str(kwargs.get("sport_type") or "Workout"),
            started_at=NOW,
            duration_seconds=int(kwargs.get("elapsed_time") or 0),
            distance_meters=0,
        )


async def make_workflow(*, linked: bool = True, **overrides):
    drafts = InMemoryManualActivityDraftStore()
    activities = InMemoryActivityStore()
    contexts = InMemoryActivityContextStore()
    messenger = InMemoryConditionPromptSender()
    completed: list[tuple] = []

    async def on_completed(activity, line_user_id):
        completed.append((activity, line_user_id))

    if linked:
        overrides.setdefault("strava", FakeStravaClient())
        overrides.setdefault("publications", InMemoryManualStravaPublicationStore())
        if "tokens" not in overrides:
            tokens = InMemoryStravaTokenStore()
            await tokens.save(
                StoredStravaToken(
                    "athlete-1", "line-1", "access", "refresh", 2_000_000_000
                )
            )
            overrides["tokens"] = tokens

    workflow = ManualActivityWorkflow(
        drafts,
        activities,
        contexts,
        messenger,
        clock=lambda: NOW,
        on_completed=on_completed,
        **overrides,
    )
    return workflow, drafts, activities, contexts, messenger, completed


def choice(messenger: InMemoryConditionPromptSender, label: str) -> str:
    return dict(messenger.quick_replies[-1][2])[label]


async def complete_basic(workflow, messenger, user="line-1"):
    await workflow.start(user)
    await workflow.handle_postback(user, choice(messenger, "ウェイト"))
    await workflow.handle_postback(user, choice(messenger, "今日の夜"))
    await workflow.handle_postback(user, choice(messenger, "45分"))
    await workflow.handle_postback(user, choice(messenger, "中"))
    await workflow.handle_postback(user, choice(messenger, "完了"))
    await workflow.handle_postback(user, choice(messenger, "スキップ"))
    await workflow.handle_postback(user, choice(messenger, "記録する"))


async def test_manual_activity_creates_strava_activity_and_uses_its_id() -> None:
    strava = FakeStravaClient()
    ingestion = InMemoryActivityIngestionStateStore()
    workflow, drafts, activities, contexts, messenger, completed = await make_workflow(
        strava=strava, ingestion_state=ingestion
    )
    await complete_basic(workflow, messenger)
    saved = next(iter(activities.activities.values()))
    assert saved.id == "9001"
    assert saved.source_activity_id == "9001"
    assert saved.source_type == ActivitySource.LINE_MANUAL
    assert saved.user_id == "line-1"
    assert saved.athlete_id == "athlete-1"
    assert saved.activity_type == "WeightTraining"
    assert saved.duration_seconds == 45 * 60
    assert saved.distance_meters == 0
    assert saved.perceived_intensity == "moderate"
    assert saved.completion_status == "completed"
    assert saved.has_heartrate is False
    assert saved.calories is None
    assert saved.average_heartrate_bpm is None
    assert await drafts.get("line-1") is None
    assert await contexts.get(saved.id) is not None
    assert completed == [(saved, "line-1")]
    assert messenger.texts[-1][1].startswith("運動をStravaに記録しました。")
    assert "この内容でStravaに記録します。" in messenger.quick_replies[-1][1]
    assert strava.created == [
        {
            "access_token": "access",
            "name": "ウェイト",
            "sport_type": "WeightTraining",
            "start_date_local": "2026-09-02T19:00:00",
            "elapsed_time": 2700,
            "description": "",
        }
    ]
    assert await ingestion.is_completed("9001", "activity")
    assert await ingestion.is_completed("9001", "prompt")


async def test_unlinked_user_does_not_start_manual_activity() -> None:
    workflow, drafts, activities, _, messenger, completed = await make_workflow(
        linked=False
    )
    await workflow.start("line-1")
    assert await drafts.get("line-1") is None
    assert activities.activities == {}
    assert completed == []
    assert "Strava連携が必要です" in messenger.texts[-1][1]


async def test_menu_without_callback_keeps_preparing_message() -> None:
    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(messenger).handle(
        "U123", "action=menu&version=1&target=manual_activity"
    )
    assert handled is True
    assert messenger.texts == [("U123", MENU_MESSAGES["manual_activity"])]


async def test_menu_callback_starts_manual_activity() -> None:
    started: list[str] = []

    async def on_start(line_user_id: str) -> None:
        started.append(line_user_id)

    messenger = InMemoryConditionPromptSender()
    handled = await MenuActionRouter(
        messenger, on_manual_activity_requested=on_start
    ).handle("U123", "action=menu&version=1&target=manual_activity")
    assert handled is True
    assert started == ["U123"]
    assert messenger.texts == []


async def test_existing_draft_is_resumed() -> None:
    workflow, drafts, _, _, messenger, _ = await make_workflow()
    await workflow.start("line-1")
    await workflow.handle_postback("line-1", choice(messenger, "ランニング"))
    messenger.quick_replies.clear()
    await workflow.start("line-1")
    assert (await drafts.get("line-1")).step == "when"
    assert messenger.quick_replies[-1][1] == "実施した日時を選んでください。"


async def test_cancel_postback_and_text_clear_draft() -> None:
    workflow, drafts, activities, _, messenger, _ = await make_workflow()
    await workflow.start("line-1")
    assert await workflow.handle_postback("line-1", choice(messenger, "キャンセル"))
    assert await drafts.get("line-1") is None
    await workflow.start("line-1")
    assert await workflow.handle_text("line-1", "キャンセル")
    assert await drafts.get("line-1") is None
    assert activities.activities == {}
    assert not await workflow.handle_text("line-1", "キャンセル")


async def test_expired_draft_rejects_late_reply() -> None:
    workflow, drafts, _, _, messenger, _ = await make_workflow()
    await workflow.start("line-1")
    draft = await drafts.get("line-1")
    draft.expires_at = NOW - timedelta(seconds=1)
    await drafts.save(draft)
    with pytest.raises(InvalidManualActivityAction, match="有効期限"):
        await workflow.handle_postback("line-1", choice(messenger, "ウェイト"))


async def test_save_is_idempotent_for_the_same_operation() -> None:
    strava = FakeStravaClient()
    workflow, drafts, activities, _, messenger, completed = await make_workflow(
        strava=strava
    )
    await complete_basic(workflow, messenger)
    saved = next(iter(activities.activities.values()))
    save_data = choice(messenger, "記録する")
    assert await drafts.get("line-1") is None
    assert await workflow.handle_postback("line-1", save_data)
    assert list(activities.activities.values()) == [saved]
    assert len(completed) == 1
    assert len(strava.created) == 1


async def test_strava_create_failure_does_not_save_locally() -> None:
    class FailingStrava:
        async def create_activity(self, access_token: str, **kwargs):
            raise StravaApiError(
                "Strava activity create failed",
                status_code=400,
                error_kind="http_status",
            )

    workflow, drafts, activities, _, messenger, completed = await make_workflow(
        strava=FailingStrava()
    )
    await workflow.start("line-1")
    await workflow.handle_postback("line-1", choice(messenger, "ウェイト"))
    await workflow.handle_postback("line-1", choice(messenger, "今日の夜"))
    await workflow.handle_postback("line-1", choice(messenger, "45分"))
    await workflow.handle_postback("line-1", choice(messenger, "中"))
    await workflow.handle_postback("line-1", choice(messenger, "完了"))
    await workflow.handle_postback("line-1", choice(messenger, "スキップ"))
    with pytest.raises(InvalidManualActivityAction, match="Stravaへの記録に失敗"):
        await workflow.handle_postback("line-1", choice(messenger, "記録する"))
    assert activities.activities == {}
    assert completed == []
    assert await drafts.get("line-1") is not None


async def test_details_and_comment_are_not_logged_or_shown_in_summary(
    caplog,
) -> None:
    workflow, _, activities, _, messenger, _ = await make_workflow()
    await workflow.start("line-1")
    await workflow.handle_postback("line-1", choice(messenger, "ウェイト"))
    await workflow.handle_postback("line-1", choice(messenger, "今日の夜"))
    await workflow.handle_postback("line-1", choice(messenger, "45分"))
    await workflow.handle_postback("line-1", choice(messenger, "中"))
    await workflow.handle_postback("line-1", choice(messenger, "完了"))
    secret = "右膝が痛く3x8 60kg"
    with caplog.at_level("INFO", logger="app.manual_activity"):
        await workflow.handle_text("line-1", f"{secret}\n医師に相談した")
        await workflow.handle_postback("line-1", choice(messenger, "記録する"))
    confirm = messenger.quick_replies[-1][1]
    assert secret not in confirm
    assert "医師に相談した" not in confirm
    assert secret not in caplog.text
    assert "医師に相談した" not in caplog.text
    saved = next(iter(activities.activities.values()))
    assert saved.details == secret
    assert saved.description == "医師に相談した"


async def test_planned_workout_must_belong_to_the_owner() -> None:
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    settings.profiles["line-1"] = create_user_training_profile(
        "line-1", "Asia/Tokyo", 1, "profile-1"
    )
    own_plan = create_plan_version(
        "line-1",
        "line-1",
        WEEK_START,
        1,
        [],
        "initial",
        created_at=NOW,
    )
    other_plan = create_plan_version(
        "line-2",
        "line-2",
        WEEK_START,
        1,
        [],
        "initial",
        created_at=NOW,
    )
    own = create_planned_workout(own_plan, date(2026, 9, 2), 0, "easy_run", "easy")
    other = create_planned_workout(other_plan, date(2026, 9, 2), 0, "tempo", "moderate")
    await history.save_plan(own_plan)
    await history.save_plan(other_plan)
    await history.save_workouts([own, other])
    await pointers.set("line-1", WEEK_START, own_plan.id, None)
    workflow, _, activities, _, messenger, _ = await make_workflow(
        settings=settings,
        planning_history=history,
        active_plans=pointers,
    )
    await workflow.start("line-1")
    await workflow.handle_postback("line-1", choice(messenger, "ランニング"))
    await workflow.handle_postback("line-1", choice(messenger, "今日の夜"))
    await workflow.handle_postback("line-1", choice(messenger, "45分"))
    await workflow.handle_postback("line-1", choice(messenger, "中"))
    await workflow.handle_postback("line-1", choice(messenger, "完了"))
    labels = [label for label, _ in messenger.quick_replies[-1][2]]
    assert "easy_run" in labels
    assert "tempo" not in labels
    with pytest.raises(InvalidManualActivityAction, match="計画メニュー"):
        await workflow.handle_postback("line-1", f"action=manual&op=plan&id={other.id}")
    await workflow.handle_postback("line-1", choice(messenger, "easy_run"))
    await workflow.handle_postback("line-1", choice(messenger, "スキップ"))
    await workflow.handle_postback("line-1", choice(messenger, "記録する"))
    saved = next(iter(activities.activities.values()))
    assert saved.planned_workout_id == own.id


async def test_environment_and_custom_inputs_are_saved() -> None:
    environments = InMemoryTrainingResourceStore()
    await environments.save(
        "line-1",
        TrainingEnvironment(
            id="env-1",
            display_name="ダンベル",
            category=TrainingEnvironmentCategory.EQUIPMENT,
        ),
    )
    strava = FakeStravaClient()
    workflow, _, activities, _, messenger, _ = await make_workflow(
        environments=environments, strava=strava
    )
    await workflow.start("line-1")
    await workflow.handle_postback("line-1", choice(messenger, "その他"))
    await workflow.handle_text("line-1", "ヨガ")
    await workflow.handle_postback("line-1", choice(messenger, "日時を入力"))
    await workflow.handle_text("line-1", "2026-09-01 07:30")
    await workflow.handle_text("line-1", "25分")
    await workflow.handle_postback("line-1", choice(messenger, "弱"))
    await workflow.handle_postback("line-1", choice(messenger, "一部実施"))
    await workflow.handle_postback("line-1", choice(messenger, "ダンベル"))
    await workflow.handle_text("line-1", "太陽礼拝 3回")
    await workflow.handle_postback("line-1", choice(messenger, "記録する"))
    saved = next(iter(activities.activities.values()))
    assert saved.activity_type == "ヨガ"
    assert saved.duration_seconds == 25 * 60
    assert saved.perceived_intensity == "easy"
    assert saved.completion_status == "partial"
    assert saved.environment_ids == ["env-1"]
    assert saved.details == "太陽礼拝 3回"
    assert saved.started_at == datetime(2026, 8, 31, 22, 30, tzinfo=UTC)
    assert strava.created[0]["sport_type"] == "Workout"
    assert strava.created[0]["name"] == "ヨガ"


async def test_create_manual_activity_id_is_stable() -> None:
    draft = ManualActivityDraft(
        line_user_id="line-1",
        user_id="line-1",
        operation_id="op-1",
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=NOW,
        duration_minutes=30,
        perceived_intensity="easy",
        completion_status="completed",
    )
    first = create_manual_activity(draft)
    second = create_manual_activity(draft)
    assert first.id == second.id == manual_activity_id("line-1", "op-1")
    assert first.source_activity_id == first.id

    draft.strava_activity_id = "9001"
    published = create_manual_activity(draft)
    assert published.id == published.source_activity_id == "9001"


class FakeSnapshot:
    exists = True

    def to_dict(self) -> dict[str, object]:
        return {
            "line_user_id": "line-1",
            "user_id": "line-1",
            "operation_id": "op-1",
            "step": "when",
            "athlete_id": "athlete-1",
            "timezone": "Asia/Tokyo",
            "expires_at": NOW + timedelta(hours=1),
        }


class FakeDocument:
    async def get(self) -> FakeSnapshot:
        return FakeSnapshot()


class FakeCollection:
    def document(self, _document_id: str) -> FakeDocument:
        return FakeDocument()


class FakeFirestoreClient:
    def collection(self, _name: str) -> FakeCollection:
        return FakeCollection()


async def test_firestore_draft_round_trip_restores_fields() -> None:
    store = FirestoreManualActivityDraftStore(FakeFirestoreClient())
    draft = await store.get("line-1")
    assert draft is not None
    assert draft.operation_id == "op-1"
    assert draft.step == "when"


async def test_token_store_finds_linked_strava_athlete() -> None:
    tokens = InMemoryStravaTokenStore()
    await tokens.save(
        StoredStravaToken("athlete-1", "line-1", "access", "refresh", 2_000_000_000)
    )
    found = await tokens.get_by_line_user_id("line-1")
    assert found is not None
    assert found.athlete_id == "athlete-1"
    assert await tokens.get_by_line_user_id("line-2") is None
