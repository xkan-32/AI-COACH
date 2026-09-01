from app.domain.models import ApprovalStatus, ConditionReport
from app.ports import CoachPort, LinePort, RepositoryPort, StravaPort


class TrainingCoachService:
    def __init__(
        self,
        strava: StravaPort,
        line: LinePort,
        coach: CoachPort,
        repository: RepositoryPort,
    ) -> None:
        self.strava = strava
        self.line = line
        self.coach = coach
        self.repository = repository

    async def on_activity_created(self, activity_id: str, athlete_id: str) -> None:
        activity = await self.strava.get_activity(activity_id, athlete_id)
        await self.repository.save_activity(activity)
        await self.line.request_condition(athlete_id, activity)

    async def on_condition_reported(self, report: ConditionReport) -> str:
        await self.repository.save_condition(report)
        activity = await self.strava.get_activity(report.activity_id, report.athlete_id)
        proposal = await self.coach.propose(activity, report)
        await self.repository.save_proposal(proposal)
        await self.line.send_proposal(report.athlete_id, proposal)
        return proposal.id

    async def approve(self, proposal_id: str) -> None:
        proposal = await self.repository.get_proposal(proposal_id)
        if proposal.status != ApprovalStatus.PENDING:
            return
        proposal.status = ApprovalStatus.APPROVED
        await self.repository.save_proposal(proposal)
        summary = (
            f"AI Coach - Next workout ({proposal.target_date.isoformat()}): "
            f"{proposal.title}, {proposal.duration_minutes} min, {proposal.intensity}."
        )
        await self.strava.append_description(proposal.source_activity_id, summary)
