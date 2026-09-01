import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.domain.models import (
    Activity,
    ApprovalStatus,
    ConditionLevel,
    ConditionReport,
    WorkoutProposal,
)
from app.domain.safety import hard_safety_constraints


class CoachOutput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=500)
    duration_minutes: int = Field(ge=0, le=180)
    intensity: Literal["rest", "easy", "moderate"]
    safety_notes: list[str] = Field(default_factory=list, max_length=5)


class CoachGenerator(Protocol):
    async def generate(
        self, activity: Activity, report: ConditionReport
    ) -> CoachOutput: ...


class ProposalStore(Protocol):
    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None: ...


class ProposalSender(Protocol):
    async def send_proposal(
        self, line_user_id: str, proposal: WorkoutProposal
    ) -> None: ...


class VertexCoachGenerator:
    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    async def generate(
        self, activity: Activity, report: ConditionReport
    ) -> CoachOutput:
        from google.genai import types

        constraints = hard_safety_constraints(report)
        contents = json.dumps(
            {
                "activity": activity.model_dump(mode="json"),
                "condition": report.model_dump(mode="json"),
                "mandatory_constraints": constraints,
                "task": "Propose one conservative workout for the next day in Japanese.",
            },
            ensure_ascii=False,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a conservative training-planning assistant, not a medical provider. "
                    "Follow every mandatory constraint. Do not diagnose or promise outcomes."
                ),
                response_mime_type="application/json",
                response_schema=CoachOutput,
                temperature=0.2,
            ),
        )
        if response.parsed is not None:
            return CoachOutput.model_validate(response.parsed)
        return CoachOutput.model_validate_json(response.text)


class LocalCoachGenerator:
    async def generate(
        self, activity: Activity, report: ConditionReport
    ) -> CoachOutput:
        return CoachOutput(
            title="回復を優先した軽い運動",
            rationale="直近の運動と体調回答を踏まえ、負荷を抑えます。",
            duration_minutes=30,
            intensity="easy",
        )


def enforce_safety(output: CoachOutput, report: ConditionReport) -> CoachOutput:
    values = output.model_dump()
    notes = list(
        dict.fromkeys([*output.safety_notes, *hard_safety_constraints(report)])
    )
    if report.level == ConditionLevel.PAIN:
        values.update(title="休養", duration_minutes=0, intensity="rest")
    elif report.level == ConditionLevel.DISCOMFORT:
        values["intensity"] = "easy" if output.duration_minutes else "rest"
        values["duration_minutes"] = min(output.duration_minutes, 45)
    elif report.level == ConditionLevel.FATIGUED and output.intensity == "moderate":
        values["intensity"] = "easy"
        values["duration_minutes"] = min(output.duration_minutes, 45)
    values["safety_notes"] = notes[:5]
    return CoachOutput.model_validate(values)


class CoachingService:
    def __init__(
        self,
        generator: CoachGenerator,
        proposals: ProposalStore,
        sender: ProposalSender,
    ):
        self._generator = generator
        self._proposals = proposals
        self._sender = sender

    async def create_proposal(
        self, activity: Activity, report: ConditionReport, line_user_id: str
    ) -> WorkoutProposal:
        generated = enforce_safety(
            await self._generator.generate(activity, report), report
        )
        proposal = WorkoutProposal(
            id=str(uuid.uuid4()),
            athlete_id=activity.athlete_id,
            source_activity_id=activity.id,
            target_date=activity.started_at.date() + timedelta(days=1),
            **generated.model_dump(),
        )
        await self._proposals.save(proposal, line_user_id)
        await self._sender.send_proposal(line_user_id, proposal)
        return proposal


class InMemoryProposalStore:
    def __init__(self) -> None:
        self.items: dict[str, WorkoutProposal] = {}

    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None:
        self.items[proposal.id] = proposal

    async def update_status(self, proposal_id: str, status: ApprovalStatus) -> None:
        self.items[proposal_id].status = status


class BigQueryProposalStore:
    def __init__(
        self, client: object, table: str, model_name: str, prompt_version: str = "v1"
    ):
        self._client = client
        self._table = table
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def save(self, proposal: WorkoutProposal, line_user_id: str) -> None:
        row = {
            "proposal_id": proposal.id,
            "athlete_id": proposal.athlete_id,
            "source_activity_id": proposal.source_activity_id,
            "target_date": proposal.target_date.isoformat(),
            "title": proposal.title,
            "rationale": proposal.rationale,
            "duration_minutes": proposal.duration_minutes,
            "intensity": proposal.intensity,
            "safety_notes": proposal.safety_notes,
            "status": proposal.status.value,
            "model_name": self._model_name,
            "prompt_version": self._prompt_version,
            "created_at": datetime.now(UTC).isoformat(),
        }
        errors = await asyncio.to_thread(
            self._client.insert_rows_json, self._table, [row], row_ids=[proposal.id]
        )
        if errors:
            raise RuntimeError("BigQuery proposal insert failed")

    async def update_status(self, proposal_id: str, status: ApprovalStatus) -> None:
        decision_table = self._table.rsplit(".", 1)[0] + ".proposal_decisions"
        row = {
            "proposal_id": proposal_id,
            "status": status.value,
            "decided_at": datetime.now(UTC).isoformat(),
        }
        row_id = f"{proposal_id}:{status.value}"
        errors = await asyncio.to_thread(
            self._client.insert_rows_json,
            decision_table,
            [row],
            row_ids=[row_id],
        )
        if errors:
            raise RuntimeError("BigQuery proposal decision insert failed")
