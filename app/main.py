import logging
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

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
from app.domain.models import CoachingContext
from app.ingestion import ActivityIngestionService, UnknownAthleteToken
from app.oauth import InvalidOAuthState, OAuthStateSigner, strava_authorization_url
from app.oauth_service import StravaOAuthService, UnknownOAuthSession
from app.profile import ProfileCommandError, ProfileCommandService
from app.runtime import build_runtime
from app.security import (
    ApprovalActionError,
    ApprovalActionSigner,
    verify_cloud_task_request,
)
from app.strava import StravaApiError, StravaOAuthClient, StravaOAuthError
from app.webhooks import verify_line_signature

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Training Coach", version="0.3.0")
runtime = build_runtime(get_settings())


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


@app.post("/webhooks/strava", status_code=202)
async def receive_strava_webhook(payload: dict) -> dict[str, str]:
    try:
        event = StravaWebhookEvent.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid Strava event") from exc
    if not await runtime.events.reserve("strava", event.event_key):
        return {"status": "duplicate"}
    if not event.is_new_activity:
        return {"status": "ignored"}
    try:
        await runtime.tasks.publish(event)
    except Exception:
        await runtime.events.release("strava", event.event_key)
        raise
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
    )
    try:
        activity = await service.ingest(str(event.object_id), str(event.owner_id))
    except UnknownAthleteToken as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StravaApiError as exc:
        raise HTTPException(
            status_code=502, detail="Strava activity ingestion failed"
        ) from exc
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
    profile_commands = ProfileCommandService(
        runtime.goals, runtime.training_resources, runtime.messenger
    )
    event_type = event.get("type")
    line_user_id = event.get("source", {}).get("userId", "")
    try:
        if event_type == "postback":
            data = event.get("postback", {}).get("data", "")
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
        if await profile_commands.handle(line_user_id, text):
            return
        await workflow.handle_text(line_user_id, text)
    except (ApprovalActionError, ProposalExpired):
        await runtime.messenger.send_text(
            line_user_id,
            "この操作は期限切れか無効です。最新のメッセージから操作してください。",
        )
    except (InvalidConditionAction, ProfileCommandError) as exc:
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
