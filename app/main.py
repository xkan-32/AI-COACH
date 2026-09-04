import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.approval import (
    ApprovalService,
    ProposalDecisionTask,
    ProposalExpired,
    ProposalOwnerMismatch,
)
from app.coaching import CoachingService
from app.condition import ConditionWorkflow, InvalidConditionAction
from app.config import get_settings
from app.domain.events import StravaWebhookEvent
from app.domain.models import (
    CoachingContext,
    Goal,
    GoalPriority,
    TrainingEnvironment,
    TrainingEnvironmentCategory,
)
from app.ingestion import ActivityIngestionService, UnknownAthleteToken
from app.line import LineApiError, set_line_reply_token
from app.line_menu import MenuActionError, MenuActionRouter
from app.manual_activity import InvalidManualActivityAction, ManualActivityWorkflow
from app.oauth import InvalidOAuthState, OAuthStateSigner, strava_authorization_url
from app.oauth_service import StravaOAuthService, UnknownOAuthSession
from app.plan_approval import PlanApprovalError, PlanApprovalService
from app.plan_generation import WeeklyPlanGenerationService
from app.plan_revision import (
    PlanRevisionError,
    PlanRevisionService,
    RequestedAdjustment,
    RevisionReason,
    RevisionScope,
)
from app.planning import (
    AvailabilitySlot,
    PlanVersionConflict,
    PreferenceConfirmationStatus,
    PreferenceSource,
    PreferenceStrength,
    ReadinessStatus,
    ReconciliationStatus,
    TrainingSettingsService,
    UserTrainingProfile,
    WeeklyAvailabilityVersion,
    WorkoutPreference,
    stable_planning_id,
)
from app.profile import (
    ACTIVITY_PLACES,
    ENVIRONMENT_ALIASES,
    EQUIPMENT,
    GOAL_TYPES,
    MAX_ITEMS,
    ProfileCommandError,
    ProfileSettingsConflict,
    ProfileWorkflow,
    profile_settings_item_id,
)
from app.readiness import WorkoutFeedbackService
from app.reconciliation import ReconciliationError, WorkoutReconciliationService
from app.runtime import build_runtime
from app.security import (
    ApprovalActionError,
    ApprovalActionSigner,
    verify_cloud_task_request,
)
from app.strava import StravaApiError, StravaOAuthClient, StravaOAuthError
from app.web_settings import InvalidSettingsToken, SettingsTokenSigner
from app.web_weekly_plan import (
    InvalidWeeklyPlanToken,
    WeeklyPlanWebSigner,
    build_training_dashboard_dto,
    build_weekly_plan_dto,
)
from app.webhooks import verify_line_signature
from app.weight import InvalidWeightAction, WeightWorkflow, parse_kilograms
from app.workout_catalog import CATALOG, compatible_templates, normalize_template_id

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Training Coach", version="0.3.0")
runtime = build_runtime(get_settings())
SETTINGS_COOKIE = "profile_settings_session"
SETTINGS_PAGE = Path(__file__).parent / "static" / "profile-settings.html"
PROFILE_SETTINGS_CANDIDATES_SCRIPT = (
    Path(__file__).parent / "static" / "profile-settings-candidates.js"
)
PROFILE_SETTINGS_CUSTOM_CANDIDATES_SCRIPT = (
    Path(__file__).parent / "static" / "profile-settings-custom-candidates.js"
)
PLANNING_SETTINGS_PAGE = Path(__file__).parent / "static" / "planning-settings.html"
WEEKLY_PLAN_COOKIE = "weekly_plan_session"
WEEKLY_PLAN_PAGE = Path(__file__).parent / "static" / "weekly-plan.html"


class GoalInput(BaseModel):
    id: str | None = None
    goal_type: str = Field(min_length=1, max_length=50)
    target: str = Field(min_length=1, max_length=200)
    target_date: date | None = None
    priority: GoalPriority


class EnvironmentInput(BaseModel):
    id: str | None = None
    display_name: str = Field(min_length=1, max_length=100)
    category: TrainingEnvironmentCategory
    detail: str | None = Field(default=None, max_length=200)


class WorkoutCandidateInput(BaseModel):
    id: str | None = None
    sport: Literal["running", "cycling", "bodyweight"] = "running"
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    minimum_minutes: int = Field(default=30, ge=10, le=240)
    maximum_distance_km: float | None = Field(default=None, gt=0, le=100)
    fastest_pace_seconds_per_km: int | None = Field(default=None, ge=150, le=900)
    maximum_duration_minutes: int | None = Field(default=None, ge=10, le=240)
    example_structure: str = Field(default="", max_length=600)
    required_environment_keywords: list[str] = Field(
        default_factory=list, max_length=20
    )


class ProfileSettingsInput(BaseModel):
    goals: list[GoalInput] = Field(max_length=MAX_ITEMS)
    training_environments: list[EnvironmentInput] = Field(max_length=MAX_ITEMS)
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    target_weight_kg: float | None = None
    enabled_workout_template_ids: list[str] | None = Field(default=None, max_length=30)
    workout_candidates: list[WorkoutCandidateInput] = Field(
        default_factory=list, max_length=20
    )
    reset_workout_candidates: bool = False

    @field_validator("target_weight_kg")
    @classmethod
    def validate_target_weight_kg(cls, value: float | None) -> float | None:
        if value is None:
            return None
        try:
            return parse_kilograms(str(value))
        except InvalidWeightAction as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_profile(self) -> "ProfileSettingsInput":
        if (
            self.goals
            and sum(goal.priority == GoalPriority.PRIMARY for goal in self.goals) != 1
        ):
            raise ValueError("目標を登録する場合、主目標を1件選択してください。")
        if any(goal.goal_type not in GOAL_TYPES for goal in self.goals):
            raise ValueError("未対応の目標種別です。")
        for goal in self.goals:
            goal.target = goal.target.strip()
            if not goal.target:
                raise ValueError("目標の内容を入力してください。")
        keys: set[tuple[str, str]] = set()
        for item in self.training_environments:
            raw_name = item.display_name.strip()
            if not raw_name:
                raise ValueError("運動環境の名前を入力してください。")
            name = ENVIRONMENT_ALIASES.get(raw_name, raw_name)
            expected = (
                TrainingEnvironmentCategory.ACTIVITY_PLACE
                if name in ACTIVITY_PLACES
                else TrainingEnvironmentCategory.EQUIPMENT
                if name in EQUIPMENT
                else TrainingEnvironmentCategory.OTHER
            )
            item.display_name = name
            item.category = expected
            if expected == TrainingEnvironmentCategory.OTHER:
                item.detail = (item.detail or raw_name).strip()
            elif item.detail:
                item.detail = item.detail.strip() or None
            key = (expected.value, name)
            if key in keys:
                raise ValueError("同じ運動環境が重複しています。")
            keys.add(key)
        return self


class AvailabilitySlotInput(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_local_time: time
    end_local_time: time
    max_workout_minutes: int | None = Field(default=None, ge=1, le=1440)
    buffer_before_minutes: int = Field(default=0, ge=0, le=720)
    buffer_after_minutes: int = Field(default=0, ge=0, le=720)
    environment_ids: list[str] = Field(default_factory=list, max_length=20)
    outdoors_allowed: bool = True
    split_allowed: bool = False


class WorkoutPreferenceInput(BaseModel):
    preference_type: str = Field(min_length=1, max_length=80)
    value: dict[str, object]
    strength: PreferenceStrength = PreferenceStrength.SOFT


class PlanningSettingsInput(BaseModel):
    expected_availability_version: int | None = Field(default=None, ge=1)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    slots: list[AvailabilitySlotInput] = Field(default_factory=list, max_length=50)
    preferences: list[WorkoutPreferenceInput] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def unique_slots_and_preferences(self) -> "PlanningSettingsInput":
        keys = [
            (item.weekday, item.start_local_time, item.end_local_time)
            for item in self.slots
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("同じ曜日・時間帯の枠は重複登録できません。")
        types = [item.preference_type for item in self.preferences]
        if len(types) != len(set(types)):
            raise ValueError("同じ種類の希望は1件だけ設定してください。")
        return self


PLANNING_SETTINGS_PREFERENCE_TYPES = {"weekend_intensity"}


class WeeklyPlanGenerationTask(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    line_user_id: str = Field(min_length=1, max_length=128)
    week_start: date
    plan_version: int = Field(ge=1)
    generation_reason: str = Field(min_length=1, max_length=80)
    input_revision: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("requested_at")
    @classmethod
    def requested_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must be timezone-aware")
        return value.astimezone(UTC)


class WeeklyPlanDecisionInput(BaseModel):
    plan_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    decision: Literal["approve", "reject", "repropose"]
    action_token: str = Field(min_length=1, max_length=2048)


class PlanRevisionRequestInput(BaseModel):
    base_plan_id: str = Field(min_length=1, max_length=128)
    scope: RevisionScope
    effective_date: date | None = None
    reason_code: RevisionReason
    requested_adjustment: RequestedAdjustment
    note: str = Field(default="", max_length=500)
    readiness_assessment_id: str | None = Field(default=None, max_length=128)
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class PlanRevisionDecisionInput(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject", "repropose"]
    action_token: str = Field(min_length=1, max_length=2048)


class MissingWorkoutScanTask(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    line_user_id: str = Field(min_length=1, max_length=128)
    local_date: date
    provider_sync_confirmed: bool = False
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class ReadinessEvaluationTask(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    line_user_id: str = Field(min_length=1, max_length=128)
    activity_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


def _settings_signer() -> SettingsTokenSigner:
    return SettingsTokenSigner(get_settings().oauth_state_signing_key)


def _weekly_plan_signer() -> WeeklyPlanWebSigner:
    return WeeklyPlanWebSigner(get_settings().oauth_state_signing_key)


def _plan_approval_service() -> PlanApprovalService:
    return PlanApprovalService(
        runtime.plan_approval_states,
        runtime.planning_history,
        runtime.active_plan_pointers,
        runtime.plan_action_signer,
    )


def _plan_revision_service() -> PlanRevisionService:
    return PlanRevisionService(
        runtime.revision_generator,
        runtime.planning_history,
        runtime.revision_history,
        runtime.revision_approval_states,
        runtime.active_plan_pointers,
        runtime.training_settings_state,
        runtime.revision_action_signer,
    )


def _weekly_plan_session(request: Request) -> tuple[str, str, int]:
    try:
        return _weekly_plan_signer().verify_session(
            request.cookies.get(WEEKLY_PLAN_COOKIE, "")
        )
    except InvalidWeeklyPlanToken as exc:
        raise HTTPException(
            status_code=401, detail="週間計画をLINEから開き直してください。"
        ) from exc


def _check_weekly_plan_origin(request: Request) -> None:
    configured = get_settings().worker_url.rstrip("/")
    origin = request.headers.get("origin", "").rstrip("/")
    if configured and origin != configured:
        raise HTTPException(status_code=403, detail="Invalid origin")


def _settings_user(request: Request) -> str:
    token = request.cookies.get(SETTINGS_COOKIE, "")
    try:
        return _settings_signer().verify_session(token)
    except InvalidSettingsToken as exc:
        raise HTTPException(
            status_code=401, detail="設定ページをLINEから開き直してください。"
        ) from exc


def _check_settings_origin(request: Request) -> None:
    configured = get_settings().worker_url.rstrip("/")
    origin = request.headers.get("origin", "").rstrip("/")
    if configured and origin != configured:
        raise HTTPException(status_code=403, detail="Invalid origin")


async def create_profile_settings_url(line_user_id: str) -> str:
    settings = get_settings()
    base_url = settings.worker_url or (
        "http://testserver" if settings.app_env == "local" else ""
    )
    if not base_url:
        raise ProfileCommandError("設定ページは現在利用できません。")
    token, link = _settings_signer().create_link(line_user_id)
    await runtime.settings_links.create(link)
    return f"{base_url.rstrip('/')}/settings/profile/start?token={quote(token)}"


async def send_profile_settings_link(line_user_id: str) -> None:
    await runtime.messenger.send_settings_link(
        line_user_id, await create_profile_settings_url(line_user_id)
    )


async def create_weekly_plan_url(line_user_id: str) -> str:
    settings = get_settings()
    base_url = settings.worker_url or (
        "http://testserver" if settings.app_env == "local" else ""
    )
    if not base_url:
        raise PlanApprovalError("週間計画画面は現在利用できません。")
    service = _plan_approval_service()
    plan = await service.current_for_line(line_user_id)
    if plan is not None:
        await service.present(plan)
    else:
        plan = await _active_plan_for_line(line_user_id)
        if plan is None:
            plan = await _generate_initial_plan_for_line(line_user_id)
            await service.present(plan)
    if plan is None:
        raise PlanApprovalError("確認できる週間計画はまだありません。")
    token, link = _weekly_plan_signer().create_link(line_user_id, plan.id, plan.version)
    await runtime.weekly_plan_links.create(link)
    return f"{base_url.rstrip('/')}/weekly-plan/start?token={quote(token)}"


def _weekly_plan_generation_service() -> WeeklyPlanGenerationService:
    settings = get_settings()
    return WeeklyPlanGenerationService(
        runtime.weekly_plan_generator,
        runtime.planning_history,
        TrainingSettingsService(
            runtime.training_settings_state,
            runtime.training_settings_history,
        ),
        runtime.goals,
        runtime.training_resources,
        runtime.activities,
        runtime.condition_reports,
        settings.vertex_model,
        _plan_approval_service(),
        runtime.active_plan_pointers,
        runtime.profile_settings,
    )


async def _generate_initial_plan_for_line(line_user_id: str):
    """Create a conservative current-week draft only after a menu request."""
    now = datetime.now(UTC)
    profile = await runtime.training_settings_state.get_profile(line_user_id)
    if profile is None:
        profile = UserTrainingProfile(
            user_id=line_user_id,
            operation_id="initial-plan-default-profile",
            updated_at=now,
        )
    week_start = profile.local_week_start(now)
    latest = await _plan_approval_service().latest_state_for_line(line_user_id)
    plan_version = (
        latest.version + 1
        if latest is not None and latest.week_start == week_start
        else 1
    )
    event_key = (
        f"initial_weekly_plan:{line_user_id}:{week_start.isoformat()}:"
        f"{profile.version}:v{plan_version}"
    )
    if not await runtime.events.reserve("weekly_plan_generation", event_key):
        raise PlanApprovalError(
            "週間計画を作成中です。少し待ってからもう一度開いてください。"
        )
    try:
        result = await _weekly_plan_generation_service().generate_shadow_plan(
            user_id=line_user_id,
            line_user_id=line_user_id,
            week_start=week_start,
            plan_version=plan_version,
            generation_reason=(
                "initial_menu_reproposal"
                if plan_version > 1
                else "initial_menu_request"
            ),
            input_revision=f"profile-{profile.version}-plan-{plan_version}",
            operation_id=(
                f"initial-menu-{week_start.isoformat()}-profile-{profile.version}"
                f"-plan-{plan_version}"
            ),
            now=now,
        )
    except BaseException:
        await runtime.events.release("weekly_plan_generation", event_key)
        raise
    await runtime.events.complete("weekly_plan_generation", event_key)
    plan = await runtime.planning_history.get_plan(result.plan_id)
    if plan is None:
        raise PlanApprovalError("週間計画の作成結果を確認できませんでした。")
    return plan


async def _active_plan_for_line(line_user_id: str):
    profile = await runtime.training_settings_state.get_profile(line_user_id)
    if profile is None:
        profile = UserTrainingProfile(
            user_id=line_user_id, operation_id="weekly-plan-active-default"
        )
    week_start = profile.local_week_start(datetime.now(UTC))
    for candidate_week in (week_start, week_start + timedelta(days=7)):
        plan_id = await runtime.active_plan_pointers.get(line_user_id, candidate_week)
        if plan_id is None:
            continue
        plan = await runtime.planning_history.get_plan(plan_id)
        if plan is not None and plan.line_user_id == line_user_id:
            return plan
    return None


async def _local_today(user_id: str) -> date:
    profile = await runtime.training_settings_state.get_profile(user_id)
    timezone = profile.timezone if profile is not None else "Asia/Tokyo"
    return datetime.now(UTC).astimezone(ZoneInfo(timezone)).date()


async def send_weekly_plan_link(line_user_id: str) -> None:
    await runtime.messenger.send_weekly_plan_link(
        line_user_id, await create_weekly_plan_url(line_user_id)
    )


def _manual_activity_workflow() -> ManualActivityWorkflow:
    settings = get_settings()
    return ManualActivityWorkflow(
        runtime.manual_activity_drafts,
        runtime.activities,
        runtime.activity_contexts,
        runtime.messenger,
        settings=runtime.training_settings_state,
        environments=runtime.training_resources,
        planning_history=runtime.planning_history,
        active_plans=runtime.active_plan_pointers,
        tokens=runtime.tokens,
        strava=StravaOAuthClient(
            settings.strava_client_id, settings.strava_client_secret
        ),
        publications=runtime.manual_strava_publications,
        ingestion_state=runtime.activity_ingestion_state,
        on_saved=_reconcile_activity,
    )


async def start_manual_activity(line_user_id: str) -> None:
    await _manual_activity_workflow().start(line_user_id)


def _weight_workflow() -> WeightWorkflow:
    return WeightWorkflow(
        runtime.weight_drafts,
        runtime.weight_logs,
        runtime.weight_targets,
        runtime.messenger,
        settings=runtime.training_settings_state,
    )


async def start_daily_condition(line_user_id: str) -> None:
    profile = await runtime.training_settings_state.get_profile(line_user_id)
    timezone = profile.timezone if profile is not None else "Asia/Tokyo"
    athlete_id = (
        profile.provider_athlete_id
        if profile is not None and profile.provider_athlete_id
        else line_user_id
    )
    local_date = datetime.now(UTC).astimezone(ZoneInfo(timezone)).date().isoformat()
    workflow = ConditionWorkflow(
        runtime.activity_contexts,
        runtime.condition_drafts,
        runtime.condition_reports,
        runtime.messenger,
    )
    await workflow.start_daily(line_user_id, athlete_id, local_date)


def _reconciliation_service() -> WorkoutReconciliationService:
    return WorkoutReconciliationService(
        runtime.planning_history,
        runtime.active_plan_pointers,
        runtime.training_settings_state,
    )


async def _reconcile_activity(activity, line_user_id: str) -> None:
    if await runtime.activity_ingestion_state.is_completed(
        activity.id, "reconciliation"
    ):
        return
    result = await _reconciliation_service().reconcile(activity)
    reconciliation = result.reconciliation
    if result.candidates:
        choices = [
            (
                _workout_choice_label(item),
                (
                    f"action=reconciliation&activity_id={activity.id}"
                    f"&reconciliation_id={reconciliation.id}"
                    f"&planned_workout_id={item.id}"
                ),
            )
            for item in result.candidates[:3]
        ]
        choices.append(
            (
                "計画外",
                (
                    f"action=reconciliation&activity_id={activity.id}"
                    f"&reconciliation_id={reconciliation.id}"
                    "&planned_workout_id=unplanned"
                ),
            )
        )
        if reconciliation.status in {
            ReconciliationStatus.AMBIGUOUS,
            ReconciliationStatus.UNMATCHED,
            ReconciliationStatus.DUPLICATE_CANDIDATE,
        }:
            message = "実施した運動に対応する予定を選んでください。"
        else:
            message = (
                f"予定「{_workout_choice_label(result.candidates[0])}」に"
                "照合しました。異なる場合は修正できます。"
            )
        await runtime.messenger.send_quick_reply(line_user_id, message, choices)
    elif reconciliation.status == ReconciliationStatus.UNPLANNED:
        await runtime.messenger.send_text(
            line_user_id, "この運動は計画外Activityとして記録しました。"
        )
    await runtime.activity_ingestion_state.complete(activity.id, "reconciliation")


def _workout_choice_label(workout) -> str:
    return f"{workout.scheduled_date.month}/{workout.scheduled_date.day} {workout.workout_type}"[
        :20
    ]


async def _handle_reconciliation_postback(line_user_id: str, data: str) -> bool:
    values = {key: items[0] for key, items in parse_qs(data).items() if items}
    if values.get("action") != "reconciliation":
        return False
    activity_id = values.get("activity_id", "")
    reconciliation_id = values.get("reconciliation_id", "")
    selected = values.get("planned_workout_id", "")
    if not activity_id or not reconciliation_id or not selected:
        raise ReconciliationError("Invalid reconciliation selection")
    context = await runtime.activity_contexts.get(activity_id)
    activity = await runtime.activities.get(activity_id)
    if context is None or context.line_user_id != line_user_id or activity is None:
        raise ReconciliationError("Activity does not belong to this LINE user")
    if activity.user_id is None:
        activity = activity.model_copy(update={"user_id": line_user_id})
    await _reconciliation_service().correct(
        user_id=line_user_id,
        activity=activity,
        expected_reconciliation_id=reconciliation_id,
        planned_workout_id=None if selected == "unplanned" else selected,
    )
    await runtime.messenger.send_text(line_user_id, "実績の対応を更新しました。")
    return True


async def _handle_missing_reconciliation_postback(line_user_id: str, data: str) -> bool:
    values = {key: items[0] for key, items in parse_qs(data).items() if items}
    if values.get("action") != "reconciliation_missing":
        return False
    reconciliation_id = values.get("reconciliation_id", "")
    decision = values.get("decision", "")
    if not reconciliation_id or decision not in {
        "not_performed",
        "sync_pending",
        "schedule_changed",
    }:
        raise ReconciliationError("Invalid missing workout selection")
    await _reconciliation_service().resolve_missing(
        user_id=line_user_id,
        expected_reconciliation_id=reconciliation_id,
        decision=decision,
    )
    messages = {
        "not_performed": "未実施として記録しました。",
        "sync_pending": "同期待ちとして保留しました。",
        "schedule_changed": "予定変更として記録しました。",
    }
    await runtime.messenger.send_text(line_user_id, messages[decision])
    return True


async def create_strava_authorization_url(line_user_id: str) -> str:
    settings = get_settings()
    if not settings.strava_client_id:
        raise HTTPException(status_code=503, detail="Strava OAuth is not configured")
    state_token, state = OAuthStateSigner(settings.oauth_state_signing_key).create()
    await runtime.oauth_sessions.create(state.nonce, line_user_id, state.expires_at)
    return strava_authorization_url(
        settings.strava_client_id, settings.strava_redirect_uri, state_token
    )


@app.get("/health")
@app.get("/healthz", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/settings/profile/start")
async def start_profile_settings(token: str) -> RedirectResponse:
    try:
        nonce = _settings_signer().verify_link(token)
    except InvalidSettingsToken as exc:
        raise HTTPException(
            status_code=400, detail="設定リンクが無効か期限切れです。"
        ) from exc
    line_user_id = await runtime.settings_links.consume(nonce, datetime.now(UTC))
    if line_user_id is None:
        raise HTTPException(status_code=400, detail="設定リンクが無効か使用済みです。")
    response = RedirectResponse("/settings/profile", status_code=303)
    response.set_cookie(
        SETTINGS_COOKIE,
        _settings_signer().create_session(line_user_id),
        max_age=30 * 60,
        httponly=True,
        secure=get_settings().app_env != "local",
        samesite="strict",
        path="/settings",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/settings/profile", response_class=HTMLResponse)
async def profile_settings_page(request: Request) -> HTMLResponse:
    _settings_user(request)
    candidates_section = (
        "<section><h2>練習メニュー候補</h2>"
        '<p class="hint">利用できる場所・器具に対応する候補です。使いたいものだけを選ぶと、AIは体調・目標・活動履歴・利用可能時間に合わせて、例と上限の範囲内で週間計画を組み立てます。例はタップすると確認できます。</p>'
        '<div class="tiles workout-candidate-grid" id="workout-candidates"></div>'
        '<p class="hint" id="workout-candidates-empty">まず利用できる運動環境を保存してください。</p>'
        '<div id="custom-running-candidates"></div>'
        '<button class="add" id="add-workout-candidate" type="button">＋ 練習メニュー候補を追加</button>'
        '<button class="add" id="reset-workout-candidates" type="button">標準候補に戻す</button>'
        "</section>"
    )
    planning_section = (
        '<section class="planning-link"><h2>週間計画の条件</h2>'
        '<p class="hint">曜日ごとの運動時間、朝・夜の枠、その時間に使える環境を設定します。</p>'
        '<a class="add" href="/settings/planning">運動できる時間を設定</a></section>'
    )
    mobile_styles = (
        "<style>.planning-link .add{display:block;text-align:center;text-decoration:none;"
        "margin-top:12px}.workout-candidate-grid{display:block}.candidate-group-title{font-size:16px;"
        "margin:18px 0 8px}.candidate-group-title:first-child{margin-top:0}.workout-candidate-list{display:grid;"
        "grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.workout-candidate{min-width:0;border:1px solid var(--line);"
        "border-radius:12px;overflow:hidden}.workout-candidate .tile span{align-items:flex-start;"
        "justify-content:flex-start;text-align:left;padding:10px}.workout-candidate-grid small{"
        "display:block;color:var(--muted);font-size:12px;font-weight:400;margin-top:5px;"
        "line-height:1.4}.candidate-example{border-top:1px solid var(--line);padding:8px 10px;color:var(--muted);"
        "font-size:12px;line-height:1.45}.candidate-example summary{color:var(--green);font-weight:650;cursor:pointer}"
        ".candidate-example ul{padding-left:18px;margin:8px 0 2px}.candidate-example li{margin:5px 0}"
        ".pace-picker{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.pace-picker .field{grid-column:1/-1;margin:0}"
        ".pace-picker .field-hint{grid-column:1/-1}"
        "textarea{display:block;min-width:0;max-width:100%;width:100%;min-height:84px;font:inherit;font-size:16px;"
        "line-height:1.35;color:var(--ink);background:#fff;border:1px solid #bac6be;border-radius:10px;padding:11px;"
        "margin-top:5px;resize:vertical}.field-hint{display:block;margin-top:5px;color:var(--muted);font-size:12px;"
        "line-height:1.4}@media(max-width:420px){.workout-candidate-list{grid-template-columns:1fr}}</style>"
    )
    page = SETTINGS_PAGE.read_text(encoding="utf-8")
    page = page.replace("</head>", f"{mobile_styles}</head>", 1)
    page = page.replace("</main>", f"{planning_section}{candidates_section}</main>", 1)
    script = PROFILE_SETTINGS_CANDIDATES_SCRIPT.read_text(encoding="utf-8")
    custom_script = PROFILE_SETTINGS_CUSTOM_CANDIDATES_SCRIPT.read_text(
        encoding="utf-8"
    )
    page = page.replace(
        "</body>",
        f"<script>{script}</script><script>{custom_script}</script></body>",
        1,
    )
    response = HTMLResponse(page)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        }
    )
    return response


@app.get("/settings/planning", response_class=FileResponse)
async def planning_settings_page(request: Request) -> FileResponse:
    _settings_user(request)
    response = FileResponse(PLANNING_SETTINGS_PAGE, media_type="text/html")
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        }
    )
    return response


@app.get("/settings/profile/api")
async def get_profile_settings(request: Request) -> dict[str, object]:
    line_user_id = _settings_user(request)
    snapshot = await runtime.profile_settings.get(line_user_id)
    candidates = compatible_templates(
        [{"name": item.display_name} for item in snapshot.training_environments],
        custom_running_candidates=snapshot.custom_running_candidates,
    )
    return {
        "goals": [goal.model_dump(mode="json") for goal in snapshot.goals],
        "training_environments": [
            item.model_dump(mode="json") for item in snapshot.training_environments
        ],
        "revision": snapshot.revision,
        "target_weight_kg": await runtime.weight_targets.get(line_user_id),
        "enabled_workout_template_ids": (
            [
                normalize_template_id(item)
                for item in snapshot.enabled_workout_template_ids
            ]
            if snapshot.enabled_workout_template_ids is not None
            else None
        ),
        "workout_candidates": [item.model_dump(mode="json") for item in candidates],
        "custom_running_candidates": snapshot.custom_running_candidates,
        "options": {
            "goal_types": list(GOAL_TYPES),
            "activity_places": sorted(ACTIVITY_PLACES),
            "equipment": sorted(EQUIPMENT),
        },
    }


@app.put("/settings/profile/api")
async def update_profile_settings(
    request: Request, payload: ProfileSettingsInput
) -> dict[str, object]:
    line_user_id = _settings_user(request)
    _check_settings_origin(request)
    goals = [
        Goal(
            id=item.id
            or profile_settings_item_id(
                line_user_id, payload.operation_id, "goal", index
            ),
            goal_type=item.goal_type,
            target=item.target,
            target_date=item.target_date,
            priority=item.priority,
        )
        for index, item in enumerate(payload.goals)
    ]
    training_environments = [
        TrainingEnvironment(
            id=item.id
            or profile_settings_item_id(
                line_user_id, payload.operation_id, "environment", index
            ),
            display_name=item.display_name,
            category=item.category,
            detail=item.detail,
        )
        for index, item in enumerate(payload.training_environments)
    ]
    current_snapshot = await runtime.profile_settings.get(line_user_id)
    current_custom_ids = {
        str(item["id"]) for item in current_snapshot.custom_running_candidates
    }
    catalog_by_id = {item.id: item for item in CATALOG}
    allowed_candidate_ids = current_custom_ids | set(catalog_by_id)
    if any(
        item.id is not None and item.id not in allowed_candidate_ids
        for item in payload.workout_candidates
    ):
        raise HTTPException(
            status_code=422,
            detail="練習候補が更新されています。ページを開き直してください。",
        )

    def candidate_values(item: WorkoutCandidateInput, index: int) -> dict[str, object]:
        candidate_id = item.id or profile_settings_item_id(
            line_user_id, payload.operation_id, "workout-candidate", index
        )
        base = catalog_by_id.get(candidate_id)
        structure = (
            dict(base.structure or {})
            if base is not None
            else {
                "sport": item.sport,
                "steps": [],
                "adjustment_guidance": "AIは候補の上限と構成メモを守り、体調・利用時間・過去実績に合わせて調整します。",
            }
        )
        if item.maximum_distance_km is not None or item.sport == "running":
            structure["maximum_distance_km"] = item.maximum_distance_km
        if item.fastest_pace_seconds_per_km is not None or item.sport == "running":
            structure["fastest_pace_seconds_per_km"] = item.fastest_pace_seconds_per_km
        if item.maximum_duration_minutes is not None:
            structure["maximum_duration_minutes"] = item.maximum_duration_minutes
        if item.example_structure.strip():
            structure["freeform_example"] = item.example_structure.strip()
        return {
            "id": candidate_id,
            "sport": item.sport,
            "title": item.title.strip(),
            "description": item.description.strip(),
            "intensity": "easy",
            "minimum_minutes": item.minimum_minutes,
            "required_environment_keywords": [
                value.strip()
                for value in item.required_environment_keywords
                if value.strip()
            ],
            "structure": structure,
        }

    custom_running_candidates = (
        []
        if payload.reset_workout_candidates
        else [
            candidate_values(item, index)
            for index, item in enumerate(payload.workout_candidates)
        ]
    )
    if len({item["id"] for item in custom_running_candidates}) != len(
        custom_running_candidates
    ):
        raise HTTPException(
            status_code=422, detail="同じ練習候補を複数回保存できません。"
        )
    enabled_template_ids = payload.enabled_workout_template_ids
    if enabled_template_ids is not None:
        enabled_template_ids = [
            *enabled_template_ids,
            *[
                item["id"]
                for item in custom_running_candidates
                if item["id"] not in catalog_by_id
            ],
        ]
    compatible_ids = {
        item.id
        for item in compatible_templates(
            [{"name": item.display_name} for item in training_environments],
            custom_running_candidates=custom_running_candidates,
        )
    }
    if enabled_template_ids is not None:
        selected_ids = enabled_template_ids
        if len(selected_ids) != len(set(selected_ids)) or not set(
            selected_ids
        ).issubset(compatible_ids):
            raise HTTPException(
                status_code=422,
                detail="利用する運動環境に対応しない練習候補が含まれています。",
            )
    try:
        revision = await runtime.profile_settings.replace(
            line_user_id,
            goals,
            training_environments,
            payload.expected_revision,
            payload.operation_id,
            enabled_template_ids,
            custom_running_candidates,
        )
    except ProfileSettingsConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="設定が更新されています。ページを開き直してください。",
        ) from exc
    if "target_weight_kg" in payload.model_fields_set:
        await runtime.weight_targets.save(line_user_id, payload.target_weight_kg)
        if payload.target_weight_kg is not None:
            logger.info("weight_target_saved user_id=%s source=settings", line_user_id)
    return {"status": "saved", "revision": revision}


def _training_settings_service() -> TrainingSettingsService:
    return TrainingSettingsService(
        runtime.training_settings_state, runtime.training_settings_history
    )


@app.get("/settings/planning/api")
async def get_planning_settings(request: Request) -> dict[str, object]:
    line_user_id = _settings_user(request)
    service = _training_settings_service()
    availability = await service.get_availability(line_user_id)
    preferences = await service.effective_preferences(line_user_id, datetime.now(UTC))
    resources = await runtime.training_resources.list(line_user_id)
    return {
        "availability": availability.model_dump(mode="json") if availability else None,
        "preferences": [item.model_dump(mode="json") for item in preferences],
        "training_environments": [item.model_dump(mode="json") for item in resources],
    }


@app.put("/settings/planning/api")
async def update_planning_settings(
    request: Request, payload: PlanningSettingsInput
) -> dict[str, object]:
    line_user_id = _settings_user(request)
    _check_settings_origin(request)
    service = _training_settings_service()
    now = datetime.now(UTC)
    profile = await service.get_profile(line_user_id)
    profile = profile or UserTrainingProfile(
        user_id=line_user_id, operation_id="planning-settings-default", updated_at=now
    )
    current = await service.get_availability(line_user_id)
    if payload.expected_availability_version != (current.version if current else None):
        raise HTTPException(
            status_code=409,
            detail="稼働可能時間が更新されています。ページを開き直してください。",
        )
    resources = await runtime.training_resources.list(line_user_id)
    resource_ids = {item.id for item in resources}
    if {env for slot in payload.slots for env in slot.environment_ids} - resource_ids:
        raise HTTPException(
            status_code=422, detail="未登録の運動環境は時間枠へ指定できません。"
        )
    version = (current.version if current else 0) + 1
    availability = WeeklyAvailabilityVersion(
        id=stable_planning_id(
            "availability", line_user_id, version, payload.operation_id
        ),
        user_id=line_user_id,
        timezone=profile.timezone,
        version=version,
        slots=[
            AvailabilitySlot(
                id=stable_planning_id(
                    "availability-slot", line_user_id, version, index
                ),
                **slot.model_dump(),
            )
            for index, slot in enumerate(payload.slots)
        ],
        supersedes_version_id=current.id if current else None,
        operation_id=payload.operation_id,
        created_at=now,
    )
    try:
        await service.save_availability(
            availability, payload.expected_availability_version
        )
    except PlanVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="稼働可能時間が更新されています。ページを開き直してください。",
        ) from exc
    existing_preferences = await runtime.training_settings_state.list_preferences(
        line_user_id
    )
    submitted_preferences = {item.preference_type: item for item in payload.preferences}
    unsupported_preferences = (
        submitted_preferences.keys() - PLANNING_SETTINGS_PREFERENCE_TYPES
    )
    if unsupported_preferences:
        raise HTTPException(
            status_code=422, detail="未対応の希望条件が含まれています。"
        )
    for preference_type in PLANNING_SETTINGS_PREFERENCE_TYPES:
        item = submitted_preferences.get(preference_type)
        previous = [
            preference
            for preference in existing_preferences
            if preference.preference_type == preference_type
        ]
        latest_previous = (
            max(previous, key=lambda entry: entry.version) if previous else None
        )
        if item is None and latest_previous is None:
            continue
        preference = WorkoutPreference(
            id=stable_planning_id(
                "preference",
                line_user_id,
                preference_type,
                len(previous) + 1,
                payload.operation_id,
            ),
            user_id=line_user_id,
            version=max((entry.version for entry in previous), default=0) + 1,
            preference_type=preference_type,
            value=item.value if item else latest_previous.value,
            strength=item.strength if item else latest_previous.strength,
            source=PreferenceSource.EXPLICIT,
            confirmation_status=(
                PreferenceConfirmationStatus.NOT_REQUIRED
                if item
                else PreferenceConfirmationStatus.REJECTED
            ),
            supersedes_preference_id=latest_previous.id if latest_previous else None,
            operation_id=payload.operation_id,
            created_at=now,
        )
        await service.save_preference(preference)
    return {
        "status": "saved",
        "availability": availability.model_dump(mode="json"),
        "preferences": [item.model_dump(mode="json") for item in payload.preferences],
    }


@app.get("/weekly-plan/start")
async def start_weekly_plan(token: str) -> RedirectResponse:
    try:
        nonce = _weekly_plan_signer().verify_link(token)
    except InvalidWeeklyPlanToken as exc:
        raise HTTPException(
            status_code=400, detail="週間計画リンクが無効か期限切れです。"
        ) from exc
    link = await runtime.weekly_plan_links.consume(nonce, datetime.now(UTC))
    if link is None:
        raise HTTPException(
            status_code=400, detail="週間計画リンクが無効か使用済みです。"
        )
    plan = await runtime.planning_history.get_plan(link.plan_id)
    if (
        plan is None
        or plan.line_user_id != link.line_user_id
        or plan.version != link.version
    ):
        raise HTTPException(
            status_code=403, detail="週間計画の所有者を確認できません。"
        )
    response = RedirectResponse("/weekly-plan", status_code=303)
    response.set_cookie(
        WEEKLY_PLAN_COOKIE,
        _weekly_plan_signer().create_session(link),
        max_age=30 * 60,
        httponly=True,
        secure=get_settings().app_env != "local",
        samesite="strict",
        path="/weekly-plan",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/weekly-plan", response_class=FileResponse)
async def weekly_plan_page(request: Request) -> FileResponse:
    _weekly_plan_session(request)
    response = FileResponse(WEEKLY_PLAN_PAGE, media_type="text/html")
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'self'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        }
    )
    return response


@app.get("/weekly-plan/api")
async def get_weekly_plan(request: Request, response: Response) -> dict[str, object]:
    line_user_id, plan_id, version = _weekly_plan_session(request)
    plan = await runtime.planning_history.get_plan(plan_id)
    if plan is None or plan.line_user_id != line_user_id or plan.version != version:
        raise HTTPException(
            status_code=403, detail="週間計画の所有者を確認できません。"
        )
    approval = await runtime.plan_approval_states.get_current(
        plan.user_id, line_user_id
    )
    if approval is not None and approval.plan_id != plan.id:
        approval = None
    is_active = (
        await runtime.active_plan_pointers.get(plan.user_id, plan.week_start) == plan.id
    )
    if approval is None and not is_active:
        raise HTTPException(
            status_code=409, detail="この週間計画は現在操作できません。"
        )
    workouts = await runtime.planning_history.list_workouts(plan.id)
    previous_plan = None
    previous_workouts = []
    if plan.supersedes_plan_version_id:
        previous_plan = await runtime.planning_history.get_plan(
            plan.supersedes_plan_version_id
        )
        if previous_plan is not None and previous_plan.user_id == plan.user_id:
            previous_workouts = await runtime.planning_history.list_workouts(
                previous_plan.id
            )
        else:
            previous_plan = None
    response.headers["Cache-Control"] = "no-store"
    result = build_weekly_plan_dto(
        plan=plan,
        workouts=workouts,
        approval=approval,
        action_signer=runtime.plan_action_signer,
        previous_plan=previous_plan,
        previous_workouts=previous_workouts,
        reconciliations=await runtime.planning_history.list_plan_reconciliations(
            plan.id
        ),
    )
    result["revision"] = {"enabled": is_active and approval is None}
    result["dashboard"] = build_training_dashboard_dto(
        workouts=workouts,
        local_today=await _local_today(plan.user_id),
        activities=await runtime.activities.list_recent_for_user(plan.user_id, 20),
    )
    return result


@app.post("/weekly-plan/api/decision")
async def decide_weekly_plan(
    request: Request,
    response: Response,
    payload: WeeklyPlanDecisionInput,
) -> dict[str, str]:
    line_user_id, session_plan_id, session_version = _weekly_plan_session(request)
    _check_weekly_plan_origin(request)
    if payload.plan_id != session_plan_id or payload.version != session_version:
        raise HTTPException(
            status_code=403, detail="週間計画の参照sessionが一致しません。"
        )
    plan = await runtime.planning_history.get_plan(payload.plan_id)
    if (
        plan is None
        or plan.line_user_id != line_user_id
        or plan.version != payload.version
    ):
        raise HTTPException(
            status_code=403, detail="週間計画の所有者を確認できません。"
        )
    try:
        status, _ = await _plan_approval_service().decide(
            plan=plan,
            line_user_id=line_user_id,
            decision=payload.decision,
            action_token=payload.action_token,
        )
    except PlanApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"status": status}


async def _revision_dto(proposal) -> dict[str, object]:
    state = await runtime.revision_approval_states.get(proposal.id)
    if state is None:
        raise PlanRevisionError("Revision approval state not found")
    return {
        "id": proposal.id,
        "request_id": proposal.request_id,
        "revision": proposal.revision,
        "base_plan_id": proposal.base_plan_id,
        "base_plan_version": proposal.base_plan_version,
        "proposed_plan_id": proposal.proposed_plan_id,
        "proposed_plan_version": proposal.proposed_plan_version,
        "scope": proposal.scope.value,
        "effective_date": proposal.effective_date.isoformat(),
        "diff": proposal.diff,
        "safety_flags": proposal.safety_flags,
        "expires_at": state.expires_at.isoformat(),
        "actions": await _plan_revision_service().approval_payload(proposal),
    }


@app.post("/weekly-plan/api/revisions")
async def request_plan_revision(
    request: Request,
    response: Response,
    payload: PlanRevisionRequestInput,
) -> dict[str, object]:
    line_user_id, session_plan_id, session_version = _weekly_plan_session(request)
    _check_weekly_plan_origin(request)
    if payload.base_plan_id != session_plan_id:
        raise HTTPException(
            status_code=403, detail="週間計画の参照sessionが一致しません。"
        )
    plan = await runtime.planning_history.get_plan(session_plan_id)
    if (
        plan is None
        or plan.line_user_id != line_user_id
        or plan.version != session_version
    ):
        raise HTTPException(
            status_code=403, detail="週間計画の所有者を確認できません。"
        )
    try:
        proposal = await _plan_revision_service().request_revision(
            user_id=plan.user_id,
            line_user_id=line_user_id,
            base_plan_id=plan.id,
            scope=payload.scope,
            effective_date=payload.effective_date,
            reason_code=payload.reason_code,
            requested_adjustment=payload.requested_adjustment,
            note=payload.note,
            readiness_assessment_id=payload.readiness_assessment_id,
            operation_id=payload.operation_id,
        )
        result = await _revision_dto(proposal)
    except PlanRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return result


@app.post("/weekly-plan/api/revisions/decision")
async def decide_plan_revision(
    request: Request,
    response: Response,
    payload: PlanRevisionDecisionInput,
) -> dict[str, object]:
    line_user_id, session_plan_id, session_version = _weekly_plan_session(request)
    _check_weekly_plan_origin(request)
    proposal = await runtime.revision_history.get_proposal(payload.proposal_id)
    if (
        proposal is None
        or proposal.base_plan_id != session_plan_id
        or proposal.base_plan_version != session_version
    ):
        raise HTTPException(
            status_code=403, detail="週間計画の参照sessionが一致しません。"
        )
    try:
        status, replacement = await _plan_revision_service().decide(
            proposal_id=payload.proposal_id,
            line_user_id=line_user_id,
            decision=payload.decision,
            action_token=payload.action_token,
        )
        result: dict[str, object] = {"status": status}
        if replacement is not None:
            result["proposal"] = await _revision_dto(replacement)
    except PlanRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/webhooks/strava")
async def verify_strava_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
) -> dict[str, str]:
    settings = get_settings()
    if hub_mode != "subscribe" or hub_verify_token != settings.strava_verify_token:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return {"hub.challenge": hub_challenge}


@app.post("/webhooks/strava")
async def receive_strava_webhook(payload: dict) -> dict[str, str]:
    try:
        event = StravaWebhookEvent.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid Strava event") from exc
    if not await runtime.events.reserve("strava", event.event_key):
        logger.info("strava_event_duplicate event_key=%s", event.event_key)
        return {"status": "duplicate"}
    if not event.is_new_activity:
        logger.info(
            "strava_event_ignored event_key=%s object_type=%s aspect_type=%s",
            event.event_key,
            event.object_type,
            event.aspect_type,
        )
        return {"status": "ignored"}
    try:
        await runtime.tasks.publish(event)
    except Exception:
        await runtime.events.release("strava", event.event_key)
        raise
    logger.info(
        "strava_activity_enqueued event_key=%s activity_id=%s athlete_id=%s",
        event.event_key,
        event.object_id,
        event.owner_id,
    )
    return {"status": "accepted"}


@app.post("/tasks/activities/ingest", status_code=202)
async def ingest_activity_task(
    request: Request, event: StravaWebhookEvent
) -> dict[str, str]:
    await verify_cloud_task_request(request, get_settings())
    if not event.is_new_activity:
        raise HTTPException(
            status_code=422, detail="Only new activity events are supported"
        )
    settings = get_settings()
    service = ActivityIngestionService(
        StravaOAuthClient(settings.strava_client_id, settings.strava_client_secret),
        runtime.tokens,
        runtime.activities,
        None,
        runtime.activity_contexts,
        runtime.activity_laps,
        runtime.activity_streams,
        runtime.activity_metrics,
        runtime.activity_ingestion_state,
        runtime.activity_segments,
        runtime.route_fingerprints,
        runtime.route_comparisons,
        runtime.route_hasher,
    )
    try:
        activity = await service.ingest(str(event.object_id), str(event.owner_id))
    except UnknownAthleteToken as exc:
        logger.warning(
            "strava_activity_ingestion_failed event_key=%s activity_id=%s "
            "athlete_id=%s error_kind=unlinked_athlete",
            event.event_key,
            event.object_id,
            event.owner_id,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StravaApiError as exc:
        logger.warning(
            "strava_activity_ingestion_failed event_key=%s activity_id=%s "
            "athlete_id=%s error_kind=%s strava_status_code=%s",
            event.event_key,
            event.object_id,
            event.owner_id,
            exc.error_kind,
            exc.status_code if exc.status_code is not None else "unavailable",
        )
        raise HTTPException(
            status_code=502, detail="Strava activity ingestion failed"
        ) from exc
    await _reconcile_activity(activity, activity.user_id or "")
    logger.info(
        "strava_activity_ingestion_completed event_key=%s activity_id=%s athlete_id=%s",
        event.event_key,
        event.object_id,
        event.owner_id,
    )
    return {"status": "completed", "activity_id": activity.id}


@app.post("/tasks/proposals/decide")
async def decide_proposal_task(
    request: Request, task: ProposalDecisionTask
) -> dict[str, str]:
    settings = get_settings()
    await verify_cloud_task_request(request, settings)
    service = ApprovalService(
        runtime.proposal_states,
        runtime.proposal_analytics,
        runtime.tokens,
        StravaOAuthClient(settings.strava_client_id, settings.strava_client_secret),
    )
    try:
        result = await service.decide(task)
    except ProposalOwnerMismatch as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProposalExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    messages = {
        "approved": "Stravaへの投稿が完了しました。",
        "recorded": "提案を記録しました。Stravaへは投稿していません。",
        "missing_strava_link": (
            "Strava連携がないため投稿できません。"
            "連携後に最新のメッセージから操作してください。"
        ),
        "rejected": "この提案は投稿しませんでした。",
        "duplicate": "この提案はすでに処理済みです。",
    }
    await runtime.messenger.send_text(
        task.line_user_id, messages.get(result, "処理が完了しました。")
    )
    return {"status": result}


@app.post("/tasks/plans/generate", status_code=202)
async def generate_weekly_plan_task(
    request: Request, task: WeeklyPlanGenerationTask
) -> dict[str, object]:
    settings = get_settings()
    await verify_cloud_task_request(request, settings)
    event_key = (
        f"{task.user_id}:{task.week_start.isoformat()}:"
        f"{task.generation_reason}:{task.input_revision}"
    )
    if not await runtime.events.reserve("weekly_plan_generation", event_key):
        return {"status": "duplicate"}
    service = _weekly_plan_generation_service()
    try:
        result = await service.generate_shadow_plan(
            user_id=task.user_id,
            line_user_id=task.line_user_id,
            week_start=task.week_start,
            plan_version=task.plan_version,
            generation_reason=task.generation_reason,
            input_revision=task.input_revision,
            operation_id=task.operation_id,
            now=task.requested_at,
        )
    except Exception:
        await runtime.events.release("weekly_plan_generation", event_key)
        raise
    await runtime.events.complete("weekly_plan_generation", event_key)
    logger.info(
        "weekly_plan_shadow_generated plan_id=%s user_id=%s week_start=%s "
        "used_fallback=%s",
        result.plan_id,
        task.user_id,
        task.week_start,
        result.used_fallback,
    )
    result_payload = result.model_dump(mode="json")
    result_payload["plan_status"] = result_payload.pop("status")
    return {"status": "completed", **result_payload}


@app.post("/tasks/plans/reconcile-missing", status_code=202)
async def reconcile_missing_workouts_task(
    request: Request, task: MissingWorkoutScanTask
) -> dict[str, object]:
    await verify_cloud_task_request(request, get_settings())
    if task.user_id != task.line_user_id:
        raise HTTPException(status_code=403, detail="Task owner mismatch")
    if not await runtime.events.reserve("missing_workout_scan", task.operation_id):
        return {"status": "duplicate"}
    try:
        candidates = await _reconciliation_service().missing_candidates(
            task.user_id,
            task.local_date,
            provider_sync_confirmed=task.provider_sync_confirmed,
        )
        for candidate in candidates:
            await runtime.messenger.send_quick_reply(
                task.line_user_id,
                "予定した運動の実績が見つかりません。状況を選んでください。",
                [
                    (
                        "未実施",
                        (
                            "action=reconciliation_missing&reconciliation_id="
                            f"{candidate.id}&decision=not_performed"
                        ),
                    ),
                    (
                        "同期待ち",
                        (
                            "action=reconciliation_missing&reconciliation_id="
                            f"{candidate.id}&decision=sync_pending"
                        ),
                    ),
                    (
                        "予定変更",
                        (
                            "action=reconciliation_missing&reconciliation_id="
                            f"{candidate.id}&decision=schedule_changed"
                        ),
                    ),
                ],
            )
    except Exception:
        await runtime.events.release("missing_workout_scan", task.operation_id)
        raise
    await runtime.events.complete("missing_workout_scan", task.operation_id)
    return {"status": "completed", "candidate_count": len(candidates)}


@app.post("/tasks/plans/evaluate-readiness", status_code=202)
async def evaluate_readiness_task(
    request: Request, task: ReadinessEvaluationTask
) -> dict[str, object]:
    await verify_cloud_task_request(request, get_settings())
    if task.user_id != task.line_user_id:
        raise HTTPException(status_code=403, detail="Task owner mismatch")
    event_key = f"{task.activity_id}:{task.operation_id}"
    if not await runtime.events.reserve("readiness_evaluation", event_key):
        return {"status": "duplicate"}
    try:
        activity = await runtime.activities.get(task.activity_id)
        context = await runtime.activity_contexts.get(task.activity_id)
        if (
            activity is None
            or context is None
            or context.line_user_id != task.line_user_id
        ):
            raise HTTPException(status_code=404, detail="Activity context not found")
        if activity.user_id is not None and activity.user_id != task.user_id:
            raise HTTPException(status_code=403, detail="Activity owner mismatch")
        if activity.user_id is None:
            activity = activity.model_copy(update={"user_id": task.user_id})
        reports = await runtime.condition_reports.list_recent(
            context.athlete_id, limit=20
        )
        profile = await runtime.training_settings_state.get_profile(task.user_id)
        timezone = profile.timezone if profile is not None else "Asia/Tokyo"
        daily_activity_id = (
            f"daily:{task.user_id}:"
            f"{activity.started_at.astimezone(ZoneInfo(timezone)).date().isoformat()}"
        )
        condition = next(
            (item for item in reports if item.activity_id == task.activity_id), None
        )
        if condition is None:
            condition = next(
                (item for item in reports if item.activity_id == daily_activity_id),
                None,
            )
        result = await WorkoutFeedbackService(
            runtime.planning_history,
            runtime.active_plan_pointers,
            runtime.training_settings_state,
            runtime.active_readiness_pointers,
            runtime.readiness_generator,
        ).evaluate(activity, condition, task.operation_id)
    except Exception:
        await runtime.events.release("readiness_evaluation", event_key)
        raise
    await runtime.events.complete("readiness_evaluation", event_key)
    return {
        "status": "completed",
        "review_id": result.review.id if result.review else None,
        "assessment_id": result.assessment.id if result.assessment else None,
        "readiness_status": (
            result.assessment.status.value if result.assessment else None
        ),
    }


@app.get("/oauth/strava/start")
async def start_strava_oauth(line_user_id: str) -> RedirectResponse:
    return RedirectResponse(await create_strava_authorization_url(line_user_id))


@app.get("/oauth/strava/callback")
async def strava_oauth_callback(code: str, state: str) -> dict[str, str]:
    settings = get_settings()
    try:
        verified = OAuthStateSigner(settings.oauth_state_signing_key).verify(state)
        service = StravaOAuthService(
            StravaOAuthClient(settings.strava_client_id, settings.strava_client_secret),
            runtime.oauth_sessions,
            runtime.tokens,
        )
        athlete_id = await service.complete(code, verified.nonce)
    except InvalidOAuthState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnknownOAuthSession as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaOAuthError as exc:
        raise HTTPException(
            status_code=502, detail="Strava authorization failed"
        ) from exc
    return {"status": "linked", "athlete_id": athlete_id}


@app.post("/webhooks/line")
async def receive_line_webhook(request: Request) -> Response:
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    if not verify_line_signature(body, signature, get_settings().line_channel_secret):
        raise HTTPException(status_code=401, detail="Invalid LINE signature")
    payload = await request.json()
    for event in payload.get("events", []):
        event_key = event.get("webhookEventId") or event.get("deliveryContext", {}).get(
            "eventId"
        )
        if not event_key:
            import hashlib
            import json

            event_key = hashlib.sha256(
                json.dumps(event, sort_keys=True).encode()
            ).hexdigest()
        if not await runtime.events.reserve("line", event_key):
            continue
        try:
            # Reply tokens are short-lived and can only be used once.  Processing
            # a user-originated event in Cloud Tasks turns its response into a
            # paid Push message, so handle it in this webhook request instead.
            await process_line_event(event)
            await runtime.events.complete("line", event_key)
            logger.info(
                "line_event_completed event_key=%s event_type=%s delivery=reply",
                event_key,
                event.get("type", ""),
            )
        except Exception:
            await runtime.events.release("line", event_key)
            raise
    return Response(status_code=200)


async def create_coaching_proposal(report) -> None:
    activity = await runtime.activities.get(report.activity_id)
    context = await runtime.activity_contexts.get(report.activity_id)
    if activity is None or context is None:
        raise HTTPException(status_code=404, detail="Activity context not found")
    if not await runtime.events.reserve("proposal", report.activity_id):
        await runtime.messenger.send_text(
            context.line_user_id,
            "このアクティビティの提案はすでに作成済みです。",
        )
        return
    try:
        service = CoachingService(
            runtime.coach, runtime.proposals, runtime.proposal_sender
        )
        feedback = await WorkoutFeedbackService(
            runtime.planning_history,
            runtime.active_plan_pointers,
            runtime.training_settings_state,
            runtime.active_readiness_pointers,
            runtime.readiness_generator,
        ).evaluate(activity, report)
        coaching_context = CoachingContext(
            goals=await runtime.goals.list(context.line_user_id),
            training_resources=await runtime.training_resources.list(
                context.line_user_id
            ),
            recent_activities=await runtime.activities.list_recent(
                context.athlete_id, limit=10
            ),
            recent_conditions=await runtime.condition_reports.list_recent(
                context.athlete_id, limit=10
            ),
            current_activity_metrics=await runtime.activity_metrics.get(
                report.activity_id
            ),
            high_load_segments=await runtime.activity_segments.list_high_load(
                report.activity_id, limit=5
            ),
            current_route_comparison=await runtime.route_comparisons.get(
                report.activity_id
            ),
        )
        await service.create_proposal(
            activity,
            report,
            context.line_user_id,
            coaching_context,
            plan_version_id=(
                feedback.next_workout.plan_version_id
                if feedback.next_workout is not None
                else None
            ),
            planned_workout_id=(
                feedback.next_workout.id if feedback.next_workout is not None else None
            ),
            review_id=feedback.review.id if feedback.review is not None else None,
            target_date=(
                feedback.next_workout.scheduled_date
                if feedback.next_workout is not None
                else None
            ),
            force_rest=(
                feedback.next_workout is not None
                and (
                    feedback.next_workout.workout_type.lower() == "rest"
                    or feedback.assessment is not None
                    and feedback.assessment.status == ReadinessStatus.BLOCKED
                )
            ),
        )
    except Exception:
        await runtime.events.release("proposal", report.activity_id)
        raise


async def _notify_line(line_user_id: str, text: str) -> None:
    try:
        await runtime.messenger.send_text(line_user_id, text)
    except LineApiError:
        logger.warning("line_error_notice_failed")


async def process_line_event(event: dict) -> None:
    workflow = ConditionWorkflow(
        runtime.activity_contexts,
        runtime.condition_drafts,
        runtime.condition_reports,
        runtime.messenger,
        on_completed=create_coaching_proposal,
    )
    profile_workflow = ProfileWorkflow(
        runtime.goals,
        runtime.training_resources,
        runtime.profile_drafts,
        runtime.messenger,
        on_settings_requested=send_profile_settings_link,
    )
    menu_actions = MenuActionRouter(
        runtime.messenger,
        on_progress_requested=send_weekly_plan_link,
        on_manual_activity_requested=start_manual_activity,
        on_condition_requested=start_daily_condition,
    )
    manual_workflow = _manual_activity_workflow()
    weight_workflow = _weight_workflow()
    event_type = event.get("type")
    line_user_id = event.get("source", {}).get("userId", "")
    event_key = str(event.get("webhookEventId") or "")
    reply_token = event.get("replyToken")
    # Every LINE webhook response is reply-only.  If LINE did not provide a
    # reply token, suppress the response instead of converting it to Push.
    set_line_reply_token(
        reply_token if isinstance(reply_token, str) else None,
        reply_only=True,
    )
    try:
        if event_type == "postback":
            data = event.get("postback", {}).get("data", "")
            if await _handle_missing_reconciliation_postback(line_user_id, data):
                return
            if await _handle_reconciliation_postback(line_user_id, data):
                return
            if await manual_workflow.handle_postback(line_user_id, data):
                return
            if await weight_workflow.handle_postback(line_user_id, data):
                return
            if await profile_workflow.handle_postback(line_user_id, data):
                return
            if await menu_actions.handle(line_user_id, data):
                return
            values = {key: value[0] for key, value in parse_qs(data).items() if value}
            if values.get("action") == "proposal":
                decision = values.get("decision", "")
                proposal_id = values.get("proposal_id", "")
                if not proposal_id or decision not in {"approve", "reject"}:
                    raise InvalidConditionAction("Invalid proposal decision")
                ApprovalActionSigner(get_settings().oauth_state_signing_key).verify(
                    proposal_id,
                    line_user_id,
                    decision,
                    values.get("expires_at", ""),
                    values.get("signature", ""),
                )
                logger.info(
                    "proposal_decision_received proposal_id=%s decision=%s event_source=line",
                    proposal_id,
                    decision,
                )
                await runtime.proposal_tasks.publish_decision(
                    ProposalDecisionTask(
                        proposal_id=proposal_id,
                        line_user_id=line_user_id,
                        decision=decision,
                    )
                )
            else:
                await workflow.handle_postback(line_user_id, data)
            return
        if event_type != "message" or event.get("message", {}).get("type") != "text":
            return
        text = event["message"].get("text", "")
        if text.strip().lower() in {"strava連携", "strava 連携"}:
            authorization_url = await create_strava_authorization_url(line_user_id)
            await runtime.messenger.send_text(
                line_user_id,
                "次のURLからStrava連携を許可してください。"
                "このURLは10分間有効です。\n"
                f"{authorization_url}",
            )
            return
        if await manual_workflow.handle_text(line_user_id, text):
            return
        if await profile_workflow.handle_text(line_user_id, text):
            return
        condition_result = await workflow.handle_text(line_user_id, text)
        if condition_result != "ignored":
            return
        if await weight_workflow.handle_text(
            line_user_id, text, operation_id=event_key or None
        ):
            return
    except (ApprovalActionError, ProposalExpired):
        await _notify_line(
            line_user_id,
            "この操作は期限切れか無効です。最新のメッセージから操作してください。",
        )
    except (
        InvalidConditionAction,
        InvalidManualActivityAction,
        InvalidWeightAction,
        MenuActionError,
        PlanApprovalError,
        ProfileCommandError,
        ReconciliationError,
    ) as exc:
        await _notify_line(line_user_id, str(exc))
    except LineApiError:
        logger.warning("line_message_failed event_type=%s", event_type)
        raise
    finally:
        set_line_reply_token(None)


@app.post("/tasks/line/events")
async def process_line_event_task(request: Request, payload: dict) -> dict[str, str]:
    await verify_cloud_task_request(request, get_settings())
    event = payload.get("event")
    if not isinstance(event, dict):
        raise HTTPException(status_code=422, detail="Invalid LINE event task")
    try:
        await process_line_event(event)
    except LineApiError:
        # This endpoint is retained only to drain tasks created by older
        # revisions.  Their reply tokens are no longer reliable; never retry
        # them into a paid Push message.
        logger.warning("line_event_task_reply_failed_without_push")
    event_key = str(payload.get("event_key", ""))
    if event_key:
        await runtime.events.complete("line", event_key)
    return {"status": "completed", "event_key": event_key}
