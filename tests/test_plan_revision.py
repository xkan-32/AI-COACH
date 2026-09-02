from datetime import UTC, date, datetime, timedelta

import pytest

from app.plan_revision import (
    InMemoryRevisionApprovalStateStore,
    InMemoryRevisionHistoryStore,
    PlanRevisionActionSigner,
    PlanRevisionError,
    PlanRevisionOutput,
    PlanRevisionService,
    RequestedAdjustment,
    RevisionReason,
    RevisionScope,
    RevisionWorkoutChange,
)
from app.planning import (
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    ReadinessStatus,
    SafetyGateStatus,
    TrainingPlanStatus,
    UserTrainingProfile,
    create_plan_version,
    create_planned_workout,
    create_readiness_assessment,
    create_safety_gate_result,
)

NOW = datetime(2026, 9, 8, 3, tzinfo=UTC)  # 12:00 JST


class CapturingGenerator:
    model_name = "revision-test-model"

    def __init__(self) -> None:
        self.inputs = []

    async def generate(self, revision_input):
        self.inputs.append(revision_input)
        target = revision_input["target_workouts"][0]
        return PlanRevisionOutput(
            rationale="負荷を調整します。",
            changes=[
                RevisionWorkoutChange(
                    planned_workout_id=target["id"],
                    workout_type=target["workout_type"],
                    target_duration_minutes=20,
                    target_distance_meters=2000,
                    target_intensity="easy",
                    outdoors=target["outdoors"],
                    environment_ids=target["environment_ids"],
                    rationale="安全側に短縮します。",
                )
            ],
        )


async def setup_revision(generator=None, states=None, clock=lambda: NOW):
    history = InMemoryPlanningHistoryStore()
    revision_history = InMemoryRevisionHistoryStore()
    states = states or InMemoryRevisionApprovalStateStore()
    pointers = InMemoryActivePlanPointerStore()
    settings = InMemoryTrainingSettingsStore()
    await settings.save_profile(
        UserTrainingProfile(
            user_id="line-1",
            timezone="Asia/Tokyo",
            operation_id="profile-1",
            updated_at=NOW,
        ),
        None,
    )
    base = create_plan_version(
        "line-1",
        "line-1",
        date(2026, 9, 7),
        1,
        [],
        "initial",
        status=TrainingPlanStatus.DRAFT,
        athlete_id="athlete-1",
        created_at=NOW - timedelta(days=2),
    )
    workouts = [
        create_planned_workout(
            base,
            date(2026, 9, 8 + offset),
            offset,
            "run" if offset != 2 else "rest",
            "easy" if offset != 2 else "rest",
            target_duration_minutes=40 if offset != 2 else 0,
            target_distance_meters=5000 if offset != 2 else None,
            created_at=NOW - timedelta(days=2),
        )
        for offset in range(5)
    ]
    await history.save_plan(base)
    await history.save_workouts(workouts)
    await pointers.set("line-1", base.week_start, base.id, None)
    signer = PlanRevisionActionSigner("revision-signing-key", clock=clock)
    service = PlanRevisionService(
        generator or CapturingGenerator(),
        history,
        revision_history,
        states,
        pointers,
        settings,
        signer,
        clock=clock,
    )
    return service, history, revision_history, states, pointers, signer, base, workouts


async def request_next_day(service, base, **values):
    defaults = {
        "user_id": "line-1",
        "line_user_id": "line-1",
        "base_plan_id": base.id,
        "scope": RevisionScope.NEXT_DAY,
        "reason_code": RevisionReason.CONDITION,
        "requested_adjustment": RequestedAdjustment.REDUCE_LOAD,
        "operation_id": "revision-request-1",
    }
    defaults.update(values)
    return await service.request_revision(**defaults)


async def test_next_day_proposal_freezes_other_days_until_explicit_approval() -> None:
    generator = CapturingGenerator()
    (
        service,
        history,
        revisions,
        states,
        pointers,
        _,
        base,
        workouts,
    ) = await setup_revision(generator)

    proposal = await request_next_day(
        service, base, note="private health and schedule free text"
    )
    candidate = await history.get_plan(proposal.proposed_plan_id)
    proposed = await history.list_workouts(candidate.id)

    assert await pointers.get(base.user_id, base.week_start) == base.id
    assert proposal.changed_workout_ids == [workouts[1].id]
    assert proposed[0].scheduled_date == workouts[0].scheduled_date
    assert proposed[0].target_duration_minutes == workouts[0].target_duration_minutes
    assert proposed[1].target_duration_minutes == 20
    assert proposed[1].supersedes_planned_workout_id == workouts[1].id
    assert candidate.goal_snapshot == base.goal_snapshot
    assert "private" not in str(generator.inputs)
    assert len(revisions.requests) == 1
    assert (await states.get(proposal.id)).status.value == "pending"


async def test_approval_activates_new_version_and_duplicate_is_idempotent() -> None:
    service, history, revisions, _, pointers, _, base, _ = await setup_revision()
    proposal = await request_next_day(service, base)
    actions = await service.approval_payload(proposal)

    status, replacement = await service.decide(
        proposal_id=proposal.id,
        line_user_id="line-1",
        decision="approve",
        action_token=actions["approve"],
    )
    duplicate, _ = await service.decide(
        proposal_id=proposal.id,
        line_user_id="line-1",
        decision="approve",
        action_token=actions["approve"],
    )

    assert status == "active"
    assert duplicate == "duplicate"
    assert replacement is None
    assert (
        await pointers.get(base.user_id, base.week_start) == proposal.proposed_plan_id
    )
    assert len(revisions.decisions) == 1
    assert any(
        event.to_status == TrainingPlanStatus.ACTIVE
        for event in history.lifecycle_events.values()
    )


async def test_reject_keeps_base_active_and_does_not_clear_blocked_readiness() -> None:
    service, history, _, _, pointers, _, base, workouts = await setup_revision()
    gate = create_safety_gate_result(
        "line-1",
        "blocked-gate",
        SafetyGateStatus.BLOCKED,
        ["condition_pain"],
        "test-rule",
        {"safe": True},
        workouts[1].id,
        evaluated_at=NOW,
    )
    readiness = create_readiness_assessment(
        "line-1",
        date(2026, 9, 8),
        workouts[1],
        1,
        ReadinessStatus.BLOCKED,
        gate.id,
        "test-rule",
        "blocked-assessment",
        {"safe": True},
        reason_codes=["condition_pain"],
        created_at=NOW,
    )
    await history.save_safety_gate(gate)
    await history.save_readiness(readiness)
    proposal = await request_next_day(
        service, base, readiness_assessment_id=readiness.id
    )
    candidate_workouts = await history.list_workouts(proposal.proposed_plan_id)
    changed = next(item for item in candidate_workouts if item.scheduled_date.day == 9)
    actions = await service.approval_payload(proposal)

    status, _ = await service.decide(
        proposal_id=proposal.id,
        line_user_id="line-1",
        decision="reject",
        action_token=actions["reject"],
    )

    assert changed.workout_type == "rest"
    assert changed.target_duration_minutes == 0
    assert "blocked_workout_forced_to_rest" in proposal.safety_flags
    assert status == "rejected"
    assert await pointers.get(base.user_id, base.week_start) == base.id
    stored = await history.list_readiness_assessments("line-1", workouts[1].id)
    assert stored[-1].status == ReadinessStatus.BLOCKED


async def test_reproposal_appends_revision_and_invalidates_old_approval() -> None:
    service, _, revisions, _, pointers, _, base, _ = await setup_revision()
    first = await request_next_day(service, base)
    first_actions = await service.approval_payload(first)

    status, second = await service.decide(
        proposal_id=first.id,
        line_user_id="line-1",
        decision="repropose",
        action_token=first_actions["repropose"],
    )

    assert status == "reproposal_requested"
    assert second.revision == 2
    assert second.supersedes_proposal_id == first.id
    assert len(await revisions.list_proposals(first.request_id)) == 2
    assert await pointers.get(base.user_id, base.week_start) == base.id
    with pytest.raises(PlanRevisionError, match="newer"):
        await service.decide(
            proposal_id=first.id,
            line_user_id="line-1",
            decision="approve",
            action_token=first_actions["approve"],
        )


async def test_scope_validation_owner_signature_and_expiry() -> None:
    now = [NOW]
    clock = lambda: now[0]
    service, _, _, _, _, _, base, _ = await setup_revision(clock=clock)
    with pytest.raises(PlanRevisionError, match="future day"):
        await service.request_revision(
            user_id="line-1",
            line_user_id="line-1",
            base_plan_id=base.id,
            scope=RevisionScope.FROM_DATE,
            effective_date=date(2026, 9, 8),
            reason_code=RevisionReason.SCHEDULE,
            requested_adjustment=RequestedAdjustment.REST,
            operation_id="past-date",
        )
    proposal = await request_next_day(service, base)
    actions = await service.approval_payload(proposal)
    with pytest.raises(PlanRevisionError, match="owner"):
        await service.decide(
            proposal_id=proposal.id,
            line_user_id="another-user",
            decision="approve",
            action_token=actions["approve"],
        )
    with pytest.raises(PlanRevisionError, match="target"):
        await service.decide(
            proposal_id=proposal.id,
            line_user_id="line-1",
            decision="reject",
            action_token=actions["approve"],
        )
    now[0] = NOW + timedelta(days=2)
    with pytest.raises(PlanRevisionError, match="expired"):
        await service.decide(
            proposal_id=proposal.id,
            line_user_id="line-1",
            decision="approve",
            action_token=actions["approve"],
        )


async def test_generator_failure_uses_deterministic_fallback() -> None:
    class FailingGenerator:
        model_name = "failing-model"

        async def generate(self, revision_input):
            raise TimeoutError("provider unavailable")

    service, history, _, _, _, _, base, _ = await setup_revision(FailingGenerator())

    proposal = await request_next_day(
        service,
        base,
        requested_adjustment=RequestedAdjustment.REST,
        operation_id="provider-fallback",
    )
    workouts = await history.list_workouts(proposal.proposed_plan_id)

    assert "generator_fallback" in proposal.safety_flags
    assert next(
        item for item in workouts if item.scheduled_date.day == 9
    ).workout_type == ("rest")


async def test_retry_recovers_after_state_registration_failure() -> None:
    class FailOnceStates(InMemoryRevisionApprovalStateStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        async def register(self, state):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient Firestore failure")
            await super().register(state)

    states = FailOnceStates()
    service, history, revisions, _, _, _, base, _ = await setup_revision(states=states)

    with pytest.raises(RuntimeError, match="transient Firestore failure"):
        await request_next_day(service, base, operation_id="recover-state")
    recovered = await request_next_day(service, base, operation_id="recover-state")

    assert len(revisions.proposals) == 1
    assert await history.get_plan(recovered.proposed_plan_id) is not None
    assert await states.get(recovered.id) is not None


async def test_ai_cannot_increase_condition_load_or_change_frozen_workout() -> None:
    class UnsafeGenerator:
        model_name = "unsafe-model"

        async def generate(self, revision_input):
            target = revision_input["target_workouts"][0]
            return PlanRevisionOutput(
                rationale="unsafe",
                changes=[
                    RevisionWorkoutChange(
                        planned_workout_id=target["id"],
                        workout_type="hard_run",
                        target_duration_minutes=200,
                        target_distance_meters=30000,
                        target_intensity="moderate",
                        outdoors=True,
                        rationale="unsafe increase",
                    ),
                    RevisionWorkoutChange(
                        planned_workout_id="past-workout-id",
                        workout_type="run",
                        target_duration_minutes=100,
                        target_intensity="moderate",
                        rationale="out of scope",
                    ),
                ],
            )

    service, history, _, _, _, _, base, workouts = await setup_revision(
        UnsafeGenerator()
    )

    proposal = await request_next_day(service, base, operation_id="unsafe-output")
    proposed = await history.list_workouts(proposal.proposed_plan_id)
    target = next(item for item in proposed if item.scheduled_date.day == 9)

    assert target.target_duration_minutes == workouts[1].target_duration_minutes
    assert target.target_distance_meters == workouts[1].target_distance_meters
    assert target.target_intensity == "easy"
    assert proposed[0].target_duration_minutes == workouts[0].target_duration_minutes
    assert "out_of_scope_change_removed" in proposal.safety_flags


async def test_remainder_week_preserves_every_blocked_readiness_result() -> None:
    service, history, _, _, _, _, base, workouts = await setup_revision()
    blocked_targets = [workouts[1], workouts[3]]
    for index, workout in enumerate(blocked_targets, start=1):
        gate = create_safety_gate_result(
            "line-1",
            f"blocked-gate-{index}",
            SafetyGateStatus.BLOCKED,
            ["condition_pain"],
            "test-rule",
            {"safe": True},
            workout.id,
            evaluated_at=NOW,
        )
        readiness = create_readiness_assessment(
            "line-1",
            date(2026, 9, 8),
            workout,
            1,
            ReadinessStatus.BLOCKED,
            gate.id,
            "test-rule",
            f"blocked-assessment-{index}",
            {"safe": True},
            reason_codes=["condition_pain"],
            created_at=NOW,
        )
        await history.save_safety_gate(gate)
        await history.save_readiness(readiness)

    proposal = await service.request_revision(
        user_id="line-1",
        line_user_id="line-1",
        base_plan_id=base.id,
        scope=RevisionScope.REMAINDER_WEEK,
        reason_code=RevisionReason.CONDITION,
        requested_adjustment=RequestedAdjustment.REDUCE_LOAD,
        operation_id="all-blocked-readiness",
    )
    proposed = await history.list_workouts(proposal.proposed_plan_id)
    proposed_by_date = {item.scheduled_date: item for item in proposed}

    assert proposed_by_date[date(2026, 9, 9)].workout_type == "rest"
    assert proposed_by_date[date(2026, 9, 11)].workout_type == "rest"
    assert proposal.safety_flags.count("blocked_workout_forced_to_rest") == 1


async def test_operation_reuse_with_different_request_is_rejected() -> None:
    service, _, _, _, _, _, base, _ = await setup_revision()
    await request_next_day(service, base, operation_id="same-operation")

    with pytest.raises(PlanRevisionError, match="another request"):
        await request_next_day(
            service,
            base,
            operation_id="same-operation",
            requested_adjustment=RequestedAdjustment.REST,
        )


async def test_decision_rejects_stale_base_after_another_plan_became_active() -> None:
    service, _, _, _, pointers, _, base, _ = await setup_revision()
    proposal = await request_next_day(service, base, operation_id="stale-base")
    actions = await service.approval_payload(proposal)
    await pointers.set(base.user_id, base.week_start, "newer-plan", base.id)

    with pytest.raises(PlanRevisionError, match="newer plan"):
        await service.decide(
            proposal_id=proposal.id,
            line_user_id="line-1",
            decision="approve",
            action_token=actions["approve"],
        )


@pytest.mark.parametrize(
    ("scope", "effective_date", "expected_target_count"),
    [
        (RevisionScope.NEXT_DAY, None, 1),
        (RevisionScope.FROM_DATE, date(2026, 9, 10), 3),
        (RevisionScope.REMAINDER_WEEK, None, 4),
    ],
)
async def test_revision_scopes_select_only_future_target_range(
    scope, effective_date, expected_target_count
) -> None:
    generator = CapturingGenerator()
    service, _, _, _, _, _, base, _ = await setup_revision(generator)

    await service.request_revision(
        user_id="line-1",
        line_user_id="line-1",
        base_plan_id=base.id,
        scope=scope,
        effective_date=effective_date,
        reason_code=RevisionReason.SCHEDULE,
        requested_adjustment=RequestedAdjustment.REDUCE_LOAD,
        operation_id=f"scope-{scope.value}",
    )

    assert len(generator.inputs[-1]["target_workouts"]) == expected_target_count
    assert all(
        date.fromisoformat(item["scheduled_date"]) > date(2026, 9, 8)
        for item in generator.inputs[-1]["target_workouts"]
    )
