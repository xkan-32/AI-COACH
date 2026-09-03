import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Activity, ActivitySource, Goal, GoalPriority
from app.plan_approval import (
    InMemoryPlanApprovalStateStore,
    PlanActionSigner,
    PlanApprovalError,
    PlanApprovalService,
    PlanApprovalState,
    _firestore_approval_state_payload,
)
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    TrainingPlanStatus,
    create_plan_version,
    create_planned_workout,
)
from app.web_weekly_plan import (
    InMemoryWeeklyPlanLinkStore,
    WeeklyPlanWebSigner,
    build_training_dashboard_dto,
    build_weekly_plan_dto,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)
WEEK = date(2026, 9, 7)


def make_plan(version: int = 1, owner: str = "line-1", supersedes: str | None = None):
    return create_plan_version(
        user_id=f"user-{owner}",
        line_user_id=owner,
        week_start=WEEK,
        version=version,
        goals=[
            Goal(
                id="goal-1",
                goal_type="運動習慣",
                target="継続",
                priority=GoalPriority.PRIMARY,
            )
        ],
        change_reason="initial",
        status=TrainingPlanStatus.DRAFT,
        supersedes_plan_version_id=supersedes,
        plan_rationale="回復日を挟んだ週間構成です。",
        input_snapshot={
            "health_free_text": "画面へ返してはいけない",
            "gps": [[35.0, 139.0]],
            "route_hash": "secret-route",
            "stream": [1, 2, 3],
        },
        created_at=NOW,
    )


def test_firestore_approval_state_payload_serializes_date_values() -> None:
    payload = _firestore_approval_state_payload(
        PlanApprovalState(
            plan_id="plan-1",
            version=1,
            week_start=WEEK,
            user_id="user-1",
            line_user_id="line-1",
            expires_at=NOW + timedelta(hours=24),
        )
    )

    assert payload["week_start"] == "2026-09-07"
    assert payload["expires_at"] == "2026-09-03T00:00:00Z"


async def setup_service(plan=None):
    plan = plan or make_plan()
    history = InMemoryPlanningHistoryStore()
    pointers = InMemoryActivePlanPointerStore()
    states = InMemoryPlanApprovalStateStore()
    signer = PlanActionSigner("secret", clock=lambda: NOW)
    workout = create_planned_workout(
        plan,
        WEEK,
        0,
        "easy_run",
        "easy",
        target_duration_minutes=30,
        rationale="安全に継続するためです。",
        safety_constraints=["pain_stop"],
        created_at=NOW,
    )
    await history.save_plan(plan)
    await history.save_workouts([workout])
    service = PlanApprovalService(states, history, pointers, signer, clock=lambda: NOW)
    await service.register_draft(plan)
    approval = await service.present(plan)
    return service, states, history, pointers, signer, plan, workout, approval


def test_plan_action_token_rejects_tampering_expiry_and_wrong_decision() -> None:
    signer = PlanActionSigner("secret", clock=lambda: NOW)
    token = signer.create("plan-1", 2, "line-1", "approve", NOW + timedelta(minutes=5))
    assert "line-1" not in token
    signer.verify(
        token,
        plan_id="plan-1",
        version=2,
        line_user_id="line-1",
        decision="approve",
    )
    with pytest.raises(PlanApprovalError):
        signer.verify(
            token + "x",
            plan_id="plan-1",
            version=2,
            line_user_id="line-1",
            decision="approve",
        )
    with pytest.raises(PlanApprovalError, match="target"):
        signer.verify(
            token,
            plan_id="plan-1",
            version=2,
            line_user_id="line-1",
            decision="reject",
        )
    expired = PlanActionSigner("secret", clock=lambda: NOW + timedelta(minutes=6))
    with pytest.raises(PlanApprovalError, match="expired"):
        expired.verify(
            token,
            plan_id="plan-1",
            version=2,
            line_user_id="line-1",
            decision="approve",
        )


async def test_weekly_plan_link_is_one_time_and_session_is_target_bound() -> None:
    signer = WeeklyPlanWebSigner("secret", clock=lambda: NOW)
    store = InMemoryWeeklyPlanLinkStore()
    token, link = signer.create_link("line-sensitive", "plan-1", 1)
    assert "line-sensitive" not in token
    await store.create(link)
    nonce = signer.verify_link(token)
    consumed = await store.consume(nonce, NOW)
    assert consumed is not None
    assert await store.consume(nonce, NOW) is None
    assert signer.verify_session(signer.create_session(consumed)) == (
        "line-sensitive",
        "plan-1",
        1,
    )


async def test_only_approve_moves_pointer_and_keeps_draft_row_immutable() -> None:
    (
        service,
        states,
        history,
        pointers,
        signer,
        plan,
        _,
        approval,
    ) = await setup_service()
    token = signer.create(
        plan.id, plan.version, "line-1", "approve", approval.expires_at
    )
    status, event = await service.decide(
        plan=plan,
        line_user_id="line-1",
        decision="approve",
        action_token=token,
    )
    assert status == "active"
    assert event is not None
    assert await pointers.get(plan.user_id, WEEK) == plan.id
    assert (await history.get_plan(plan.id)).status == TrainingPlanStatus.DRAFT
    assert len(history.plans) == 1
    assert len(history.workouts) == 1
    duplicate, duplicate_event = await service.decide(
        plan=plan,
        line_user_id="line-1",
        decision="approve",
        action_token=token,
    )
    assert duplicate == "duplicate"
    assert duplicate_event == event
    assert states.items[plan.id].status.value == "approved"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("reject", "rejected"), ("repropose", "reproposal_requested")],
)
async def test_non_approval_decisions_leave_pointer_unchanged(
    decision: str, expected: str
) -> None:
    service, _, _, pointers, signer, plan, _, approval = await setup_service()
    token = signer.create(
        plan.id, plan.version, "line-1", decision, approval.expires_at
    )
    status, _ = await service.decide(
        plan=plan,
        line_user_id="line-1",
        decision=decision,
        action_token=token,
    )
    assert status == expected
    assert await pointers.get(plan.user_id, WEEK) is None


async def test_owner_and_stale_version_are_rejected() -> None:
    service, states, history, _, signer, first, _, approval = await setup_service()
    owner_token = signer.create(
        first.id, first.version, "line-1", "approve", approval.expires_at
    )
    with pytest.raises(PlanApprovalError):
        await service.decide(
            plan=first,
            line_user_id="line-2",
            decision="approve",
            action_token=owner_token,
        )
    second = make_plan(version=2, supersedes=first.id)
    await history.save_plan(second)
    await states.register_draft(
        approval.model_copy(
            update={
                "plan_id": second.id,
                "version": second.version,
                "status": "draft",
            }
        )
    )
    with pytest.raises(PlanApprovalError, match="newer"):
        await service.decide(
            plan=first,
            line_user_id="line-1",
            decision="approve",
            action_token=owner_token,
        )


async def test_older_week_cannot_replace_current_plan_pointer() -> None:
    states = InMemoryPlanApprovalStateStore()
    await states.register_draft(
        PlanApprovalState(
            plan_id="next-week",
            version=1,
            week_start=date(2026, 9, 14),
            user_id="user-1",
            line_user_id="line-1",
            expires_at=NOW + timedelta(days=1),
        )
    )

    with pytest.raises(PlanApprovalError, match="newer"):
        await states.register_draft(
            PlanApprovalState(
                plan_id="older-week-retry",
                version=1,
                week_start=WEEK,
                user_id="user-1",
                line_user_id="line-1",
                expires_at=NOW + timedelta(days=1),
            )
        )


async def test_expired_decision_records_lifecycle_without_activation() -> None:
    (
        service,
        states,
        history,
        pointers,
        signer,
        plan,
        _,
        approval,
    ) = await setup_service()
    states.items[plan.id] = approval.model_copy(
        update={"expires_at": NOW - timedelta(seconds=1)}
    )
    token = signer.create(
        plan.id, plan.version, "line-1", "approve", NOW + timedelta(minutes=5)
    )

    with pytest.raises(PlanApprovalError, match="expired"):
        await service.decide(
            plan=plan,
            line_user_id="line-1",
            decision="approve",
            action_token=token,
        )

    assert await pointers.get(plan.user_id, WEEK) is None
    assert any(
        event.to_status == TrainingPlanStatus.EXPIRED
        for event in history.lifecycle_events.values()
    )


async def test_web_dto_has_seven_days_and_excludes_sensitive_snapshot() -> None:
    _, _, _, _, signer, plan, workout, approval = await setup_service()
    payload = build_weekly_plan_dto(
        plan=plan,
        workouts=[workout],
        approval=approval,
        action_signer=signer,
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(payload["plan"]["days"]) == 7
    assert payload["plan"]["version_changes"] == ["初回計画"]
    assert "画面へ返してはいけない" not in encoded
    assert "secret-route" not in encoded
    assert "input_snapshot" not in encoded


async def test_training_dashboard_puts_today_before_safe_activity_history() -> None:
    _, _, _, _, _, plan, workout, _ = await setup_service()
    activity = Activity(
        id="activity-1",
        athlete_id="athlete-1",
        user_id=plan.user_id,
        activity_type="Run",
        started_at=NOW,
        duration_seconds=1850,
        distance_meters=5100,
        description="画面に返してはいけない説明",
        details="健康自由記述を返してはいけない",
        source_type=ActivitySource.LINE_MANUAL,
        perceived_intensity="easy",
        completion_status="completed",
    )

    dashboard = build_training_dashboard_dto(
        workouts=[workout], local_today=WEEK, activities=[activity]
    )
    encoded = json.dumps(dashboard, ensure_ascii=False)

    assert list(dashboard) == ["today", "history"]
    assert dashboard["today"]["date"] == WEEK.isoformat()
    assert dashboard["today"]["workouts"][0]["id"] == workout.id
    assert dashboard["history"]["activities"][0]["duration_minutes"] == 31
    assert "画面に返してはいけない" not in encoded
    assert "健康自由記述" not in encoded


def test_training_menu_opens_one_time_weekly_plan_and_approves() -> None:
    from app.main import app, runtime

    user = "weekly-web-integration-user"
    with TestClient(app) as client:
        generated = client.post(
            "/tasks/plans/generate",
            json={
                "user_id": user,
                "line_user_id": user,
                "week_start": "2026-09-07",
                "plan_version": 1,
                "generation_reason": "manual_shadow",
                "input_revision": "weekly-web-1",
                "operation_id": "weekly-web-op-1",
                "requested_at": "2026-09-05T00:00:00Z",
            },
        )
        assert generated.status_code == 202
        opened = client.post(
            "/tasks/line/events",
            json={
                "event_key": "weekly-web-training-menu-event",
                "event": {
                    "type": "postback",
                    "source": {"userId": user},
                    "postback": {"data": "action=menu&version=1&target=training_menu"},
                },
            },
        )
        assert opened.status_code == 200
        url = runtime.messenger.weekly_plan_links[-1][1]
        assert client.get(url).status_code == 200
        assert client.get(url).status_code == 400
        dto = client.get("/weekly-plan/api")
        assert dto.status_code == 200
        body = dto.json()
        assert len(body["plan"]["days"]) == 7
        approved = client.post(
            "/weekly-plan/api/decision",
            json={
                "plan_id": body["plan"]["id"],
                "version": body["plan"]["version"],
                "decision": "approve",
                "action_token": body["approval"]["actions"]["approve"],
            },
        )
        assert approved.status_code == 200
        assert approved.json() == {"status": "active"}

        reopened = client.post(
            "/tasks/line/events",
            json={
                "event_key": "weekly-web-active-training-menu-event",
                "event": {
                    "type": "postback",
                    "source": {"userId": user},
                    "postback": {"data": "action=menu&version=1&target=training_menu"},
                },
            },
        )
        assert reopened.status_code == 200
        active_url = runtime.messenger.weekly_plan_links[-1][1]
        assert client.get(active_url).status_code == 200
        active_dto = client.get("/weekly-plan/api")
        assert active_dto.status_code == 200
        assert active_dto.json()["revision"]["enabled"] is True
        revision = client.post(
            "/weekly-plan/api/revisions",
            json={
                "base_plan_id": body["plan"]["id"],
                "scope": "next_day",
                "reason_code": "schedule",
                "requested_adjustment": "rest",
                "note": "予定が変わりました",
                "operation_id": "weekly-web-revision-1",
            },
        )
        assert revision.status_code == 200
        revision_body = revision.json()
        assert revision_body["revision"] == 1
        revised = client.post(
            "/weekly-plan/api/revisions/decision",
            json={
                "proposal_id": revision_body["id"],
                "decision": "approve",
                "action_token": revision_body["actions"]["approve"],
            },
        )
        assert revised.status_code == 200
        assert revised.json() == {"status": "active"}


def test_training_menu_generates_an_initial_plan_when_none_exists() -> None:
    from app.main import app, runtime

    user = "weekly-web-initial-plan-user"
    with TestClient(app) as client:
        opened = client.post(
            "/tasks/line/events",
            json={
                "event_key": "weekly-web-initial-plan-event",
                "event": {
                    "type": "postback",
                    "source": {"userId": user},
                    "postback": {"data": "action=menu&version=1&target=training_menu"},
                },
            },
        )

        assert opened.status_code == 200
        url = runtime.messenger.weekly_plan_links[-1][1]
        assert client.get(url).status_code == 200
        dto = client.get("/weekly-plan/api")

    assert dto.status_code == 200
    body = dto.json()
    assert body["plan"]["status"] == "pending"
    assert len(body["plan"]["days"]) == 7
    assert body["dashboard"]["today"]["date"]
