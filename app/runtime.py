from dataclasses import dataclass

from app.approval import (
    CompositeProposalStore,
    FirestoreProposalStateStore,
    InMemoryProposalStateStore,
    ProposalAnalyticsStore,
    ProposalStateStore,
)
from app.coaching import (
    BigQueryProposalStore,
    CoachGenerator,
    InMemoryProposalStore,
    LocalCoachGenerator,
    ProposalSender,
    ProposalStore,
    VertexCoachGenerator,
)
from app.condition import (
    ActivityContextStore,
    BigQueryConditionReportStore,
    ConditionDraftStore,
    ConditionReportStore,
    FirestoreActivityContextStore,
    FirestoreConditionDraftStore,
    FollowUpMessenger,
    InMemoryActivityContextStore,
    InMemoryConditionDraftStore,
    InMemoryConditionReportStore,
)
from app.config import Settings
from app.ingestion import (
    ActivityStore,
    BigQueryActivityStore,
    ConditionPromptSender,
    InMemoryActivityStore,
)
from app.line import InMemoryConditionPromptSender, LineConditionPromptSender
from app.profile import (
    FirestoreGoalStore,
    FirestoreTrainingResourceStore,
    GoalStore,
    InMemoryGoalStore,
    InMemoryTrainingResourceStore,
    TrainingResourceStore,
)
from app.state import (
    EventStore,
    FirestoreEventStore,
    FirestoreOAuthSessionStore,
    FirestoreStravaTokenStore,
    InMemoryEventStore,
    InMemoryOAuthSessionStore,
    InMemoryStravaTokenStore,
    OAuthSessionStore,
    StravaTokenStore,
)
from app.tasks import (
    ActivityTaskPublisher,
    CloudTasksActivityPublisher,
    CloudTasksProposalDecisionPublisher,
    InMemoryActivityTaskPublisher,
    InMemoryProposalDecisionPublisher,
    ProposalDecisionPublisher,
)


@dataclass(frozen=True)
class Runtime:
    events: EventStore
    oauth_sessions: OAuthSessionStore
    tokens: StravaTokenStore
    tasks: ActivityTaskPublisher
    activities: ActivityStore
    activity_contexts: ActivityContextStore
    condition_prompts: ConditionPromptSender
    condition_drafts: ConditionDraftStore
    condition_reports: ConditionReportStore
    messenger: FollowUpMessenger
    coach: CoachGenerator
    proposals: ProposalStore
    proposal_sender: ProposalSender
    proposal_states: ProposalStateStore
    proposal_analytics: ProposalAnalyticsStore
    proposal_tasks: ProposalDecisionPublisher
    goals: GoalStore
    training_resources: TrainingResourceStore


def build_runtime(settings: Settings) -> Runtime:
    if settings.app_env == "local":
        line = InMemoryConditionPromptSender()
        analytics = InMemoryProposalStore()
        proposal_states = InMemoryProposalStateStore()
        return Runtime(
            events=InMemoryEventStore(),
            oauth_sessions=InMemoryOAuthSessionStore(),
            tokens=InMemoryStravaTokenStore(),
            tasks=InMemoryActivityTaskPublisher(),
            activities=InMemoryActivityStore(),
            activity_contexts=InMemoryActivityContextStore(),
            condition_prompts=line,
            condition_drafts=InMemoryConditionDraftStore(),
            condition_reports=InMemoryConditionReportStore(),
            messenger=line,
            coach=LocalCoachGenerator(),
            proposals=CompositeProposalStore(proposal_states, analytics),
            proposal_sender=line,
            proposal_states=proposal_states,
            proposal_analytics=analytics,
            proposal_tasks=InMemoryProposalDecisionPublisher(),
            goals=InMemoryGoalStore(),
            training_resources=InMemoryTrainingResourceStore(),
        )
    required = {
        "gcp_project_id": settings.gcp_project_id,
        "cloud_tasks_queue_path": settings.cloud_tasks_queue_path,
        "worker_url": settings.worker_url,
        "task_service_account_email": settings.task_service_account_email,
        "line_channel_access_token": settings.line_channel_access_token,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing production settings: {', '.join(missing)}")

    from google import genai
    from google.cloud import bigquery, firestore, tasks_v2

    firestore_client = firestore.AsyncClient(
        project=settings.gcp_project_id, database=settings.firestore_database
    )
    bigquery_client = bigquery.Client(project=settings.gcp_project_id)
    genai_client = genai.Client(
        vertexai=True, project=settings.gcp_project_id, location=settings.gcp_region
    )
    table_prefix = f"{settings.gcp_project_id}.{settings.bigquery_dataset}"
    line = LineConditionPromptSender(settings.line_channel_access_token)
    proposal_states = FirestoreProposalStateStore(firestore_client)
    proposal_analytics = BigQueryProposalStore(
        bigquery_client, f"{table_prefix}.proposals", settings.vertex_model
    )
    return Runtime(
        events=FirestoreEventStore(firestore_client),
        oauth_sessions=FirestoreOAuthSessionStore(firestore_client),
        tokens=FirestoreStravaTokenStore(firestore_client),
        tasks=CloudTasksActivityPublisher(
            tasks_v2.CloudTasksAsyncClient,
            settings.cloud_tasks_queue_path,
            settings.worker_url,
            settings.task_service_account_email,
        ),
        activities=BigQueryActivityStore(bigquery_client, f"{table_prefix}.activities"),
        activity_contexts=FirestoreActivityContextStore(firestore_client),
        condition_prompts=line,
        condition_drafts=FirestoreConditionDraftStore(firestore_client),
        condition_reports=BigQueryConditionReportStore(
            bigquery_client, f"{table_prefix}.condition_reports"
        ),
        messenger=line,
        coach=VertexCoachGenerator(genai_client, settings.vertex_model),
        proposals=CompositeProposalStore(proposal_states, proposal_analytics),
        proposal_sender=line,
        proposal_states=proposal_states,
        proposal_analytics=proposal_analytics,
        proposal_tasks=CloudTasksProposalDecisionPublisher(
            tasks_v2.CloudTasksAsyncClient,
            settings.cloud_tasks_queue_path,
            settings.worker_url,
            settings.task_service_account_email,
        ),
        goals=FirestoreGoalStore(firestore_client),
        training_resources=FirestoreTrainingResourceStore(firestore_client),
    )
