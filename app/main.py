import logging
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

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
from app.line_menu import MenuActionError, MenuActionRouter
from app.oauth import InvalidOAuthState, OAuthStateSigner, strava_authorization_url
from app.oauth_service import StravaOAuthService, UnknownOAuthSession
from app.profile import (
    ACTIVITY_PLACES,
    ENVIRONMENT_ALIASES,
    EQUIPMENT,
    GOAL_TYPES,
    MAX_ITEMS,
    ProfileCommandError,
    ProfileWorkflow,
)
from app.runtime import build_runtime
from app.security import (
    ApprovalActionError,
    ApprovalActionSigner,
    verify_cloud_task_request,
)
from app.strava import StravaApiError, StravaOAuthClient, StravaOAuthError
from app.web_settings import InvalidSettingsToken, SettingsTokenSigner
from app.webhooks import verify_line_signature

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Training Coach", version="0.3.0")
runtime = build_runtime(get_settings())
SETTINGS_COOKIE = "profile_settings_session"
SETTINGS_PAGE = Path(__file__).parent / "static" / "profile-settings.html"


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


class ProfileSettingsInput(BaseModel):
    goals: list[GoalInput] = Field(max_length=MAX_ITEMS)
    training_environments: list[EnvironmentInput] = Field(max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def validate_profile(self) -> "ProfileSettingsInput":
        if (
            self.goals
            and sum(goal.priority == GoalPriority.PRIMARY for goal in self.goals) != 1
        ):
            raise ValueError("目標を登録する場合、主目標を1件選択してください。")
        if any(goal.goal_type not in GOAL_TYPES for goal in self.goals):
            raise ValueError("未対応の目標種別です。")
        keys: set[tuple[str, str]] = set()
        for item in self.training_environments:
            name = ENVIRONMENT_ALIASES.get(
                item.display_name.strip(), item.display_name.strip()
            )
            expected = (
                TrainingEnvironmentCategory.ACTIVITY_PLACE
                if name in ACTIVITY_PLACES
                else TrainingEnvironmentCategory.EQUIPMENT
                if name in EQUIPMENT
                else TrainingEnvironmentCategory.OTHER
            )
            if item.category != expected:
                raise ValueError("運動環境の区分が表示名と一致しません。")
            key = (item.category.value, name)
            if key in keys:
                raise ValueError("同じ運動環境が重複しています。")
            keys.add(key)
        return self


def _settings_signer() -> SettingsTokenSigner:
    return SettingsTokenSigner(get_settings().oauth_state_signing_key)


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
        path="/settings/profile",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/settings/profile", response_class=FileResponse)
async def profile_settings_page(request: Request) -> FileResponse:
    _settings_user(request)
    response = FileResponse(SETTINGS_PAGE, media_type="text/html")
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
    return {
        "goals": [
            goal.model_dump(mode="json")
            for goal in await runtime.goals.list(line_user_id)
        ],
        "training_environments": [
            item.model_dump(mode="json")
            for item in await runtime.training_resources.list(line_user_id)
        ],
        "options": {
            "goal_types": list(GOAL_TYPES),
            "activity_places": sorted(ACTIVITY_PLACES),
            "equipment": sorted(EQUIPMENT),
        },
    }


@app.put("/settings/profile/api")
async def update_profile_settings(
    request: Request, payload: ProfileSettingsInput
) -> dict[str, str]:
    line_user_id = _settings_user(request)
    _check_settings_origin(request)
    current_goals = {goal.id: goal for goal in await runtime.goals.list(line_user_id)}
    current_environments = {
        item.id: item for item in await runtime.training_resources.list(line_user_id)
    }
    supplied_goal_ids = {item.id for item in payload.goals if item.id}
    supplied_environment_ids = {
        item.id for item in payload.training_environments if item.id
    }
    if (
        not supplied_goal_ids <= current_goals.keys()
        or not supplied_environment_ids <= current_environments.keys()
    ):
        raise HTTPException(
            status_code=409,
            detail="設定が更新されています。ページを開き直してください。",
        )

    for goal_id in current_goals.keys() - supplied_goal_ids:
        await runtime.goals.deactivate(line_user_id, goal_id)
    for item in payload.goals:
        await runtime.goals.save(
            line_user_id,
            Goal(
                id=item.id or str(uuid.uuid4()),
                goal_type=item.goal_type,
                target=item.target.strip(),
                target_date=item.target_date,
                priority=item.priority,
            ),
        )

    for resource_id in current_environments.keys() - supplied_environment_ids:
        await runtime.training_resources.deactivate(line_user_id, resource_id)
    for item in payload.training_environments:
        display_name = ENVIRONMENT_ALIASES.get(
            item.display_name.strip(), item.display_name.strip()
        )
        await runtime.training_resources.save(
            line_user_id,
            TrainingEnvironment(
                id=item.id or str(uuid.uuid4()),
                display_name=display_name,
                category=item.category,
                detail=item.detail.strip() if item.detail else None,
            ),
        )
    return {"status": "saved"}


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
        runtime.condition_prompts,
        runtime.activity_contexts,
        runtime.activity_laps,
        runtime.activity_streams,
        runtime.activity_metrics,
        runtime.activity_ingestion_state,
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
        "rejected": "この提案は投稿しませんでした。",
        "duplicate": "この提案はすでに処理済みです。",
    }
    await runtime.messenger.send_text(
        task.line_user_id, messages.get(result, "処理が完了しました。")
    )
    return {"status": result}


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
            await runtime.line_tasks.publish(event_key, event)
            logger.info(
                "line_event_enqueued event_key=%s event_type=%s",
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
    service = CoachingService(runtime.coach, runtime.proposals, runtime.proposal_sender)
    coaching_context = CoachingContext(
        goals=await runtime.goals.list(context.line_user_id),
        training_resources=await runtime.training_resources.list(context.line_user_id),
        recent_activities=await runtime.activities.list_recent(
            context.athlete_id, limit=10
        ),
        recent_conditions=await runtime.condition_reports.list_recent(
            context.athlete_id, limit=10
        ),
        current_activity_metrics=await runtime.activity_metrics.get(report.activity_id),
    )
    try:
        await service.create_proposal(
            activity, report, context.line_user_id, coaching_context
        )
    except Exception:
        await runtime.events.release("proposal", report.activity_id)
        raise


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
    menu_actions = MenuActionRouter(runtime.messenger)
    event_type = event.get("type")
    line_user_id = event.get("source", {}).get("userId", "")
    try:
        if event_type == "postback":
            data = event.get("postback", {}).get("data", "")
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
        if await profile_workflow.handle_text(line_user_id, text):
            return
        await workflow.handle_text(line_user_id, text)
    except (ApprovalActionError, ProposalExpired):
        await runtime.messenger.send_text(
            line_user_id,
            "この操作は期限切れか無効です。最新のメッセージから操作してください。",
        )
    except (InvalidConditionAction, MenuActionError, ProfileCommandError) as exc:
        await runtime.messenger.send_text(line_user_id, str(exc))


@app.post("/tasks/line/events")
async def process_line_event_task(request: Request, payload: dict) -> dict[str, str]:
    await verify_cloud_task_request(request, get_settings())
    event = payload.get("event")
    if not isinstance(event, dict):
        raise HTTPException(status_code=422, detail="Invalid LINE event task")
    await process_line_event(event)
    event_key = str(payload.get("event_key", ""))
    if event_key:
        await runtime.events.complete("line", event_key)
    return {"status": "completed", "event_key": event_key}
