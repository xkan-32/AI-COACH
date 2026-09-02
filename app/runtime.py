import base64
from dataclasses import dataclass

from app.activity_data import (
    ActivityIngestionStateStore,
    ActivityLapStore,
    ActivityMetricsStore,
    ActivitySegmentStore,
    ActivityStreamStore,
    BigQueryActivityLapStore,
    BigQueryActivityMetricsStore,
    BigQueryActivitySegmentStore,
    BigQueryActivityStreamStore,
    BigQueryRouteComparisonStore,
    BigQueryRouteFingerprintStore,
    FirestoreActivityIngestionStateStore,
    InMemoryActivityIngestionStateStore,
    InMemoryActivityLapStore,
    InMemoryActivityMetricsStore,
    InMemoryActivitySegmentStore,
    InMemoryActivityStreamStore,
    InMemoryRouteComparisonStore,
    InMemoryRouteFingerprintStore,
    RouteComparisonStore,
    RouteFingerprintStore,
)
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
from app.planning import (
    ActivePlanPointerStore,
    BigQueryPlanningHistoryStore,
    FirestoreActivePlanPointerStore,
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    PlanningHistoryStore,
)
from app.profile import (
    FirestoreGoalStore,
    FirestoreProfileDraftStore,
    FirestoreTrainingResourceStore,
    GoalStore,
    InMemoryGoalStore,
    InMemoryProfileDraftStore,
    InMemoryTrainingResourceStore,
    ProfileDraftStore,
    TrainingResourceStore,
)
from app.publication import (
    BigQueryPublicationHistoryStore,
    FirestorePublicationApprovalStateStore,
    InMemoryPublicationApprovalStateStore,
    InMemoryPublicationHistoryStore,
    PublicationActionSigner,
    PublicationApprovalStateStore,
    PublicationHistoryStore,
)
from app.segment_analysis import RouteFingerprintHasher
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
    CloudTasksLineEventPublisher,
    CloudTasksProposalDecisionPublisher,
    InMemoryActivityTaskPublisher,
    InMemoryLineEventTaskPublisher,
    InMemoryProposalDecisionPublisher,
    LineEventTaskPublisher,
    ProposalDecisionPublisher,
)
from app.token_crypto import AesGcmTokenCipher
from app.web_settings import (
    FirestoreSettingsLinkStore,
    InMemorySettingsLinkStore,
    SettingsLinkStore,
)


@dataclass(frozen=True)
class Runtime:
    events: EventStore
    oauth_sessions: OAuthSessionStore
    tokens: StravaTokenStore
    tasks: ActivityTaskPublisher
    line_tasks: LineEventTaskPublisher
    activities: ActivityStore
    activity_laps: ActivityLapStore
    activity_streams: ActivityStreamStore
    activity_metrics: ActivityMetricsStore
    activity_segments: ActivitySegmentStore
    route_fingerprints: RouteFingerprintStore
    route_comparisons: RouteComparisonStore
    route_hasher: RouteFingerprintHasher
    activity_ingestion_state: ActivityIngestionStateStore
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
    profile_drafts: ProfileDraftStore
    settings_links: SettingsLinkStore
    planning_history: PlanningHistoryStore
    active_plan_pointers: ActivePlanPointerStore
    publication_history: PublicationHistoryStore
    publication_states: PublicationApprovalStateStore
    publication_signer: PublicationActionSigner


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
            line_tasks=InMemoryLineEventTaskPublisher(),
            activities=InMemoryActivityStore(),
            activity_laps=InMemoryActivityLapStore(),
            activity_streams=InMemoryActivityStreamStore(),
            activity_metrics=InMemoryActivityMetricsStore(),
            activity_segments=InMemoryActivitySegmentStore(),
            route_fingerprints=InMemoryRouteFingerprintStore(),
            route_comparisons=InMemoryRouteComparisonStore(),
            route_hasher=RouteFingerprintHasher(
                base64.b64encode(b"r" * 32).decode("ascii")
            ),
            activity_ingestion_state=InMemoryActivityIngestionStateStore(),
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
            profile_drafts=InMemoryProfileDraftStore(),
            settings_links=InMemorySettingsLinkStore(),
            planning_history=InMemoryPlanningHistoryStore(),
            active_plan_pointers=InMemoryActivePlanPointerStore(),
            publication_history=InMemoryPublicationHistoryStore(),
            publication_states=InMemoryPublicationApprovalStateStore(),
            publication_signer=PublicationActionSigner(
                settings.oauth_state_signing_key
            ),
        )
    required = {
        "gcp_project_id": settings.gcp_project_id,
        "cloud_tasks_queue_path": settings.cloud_tasks_queue_path,
        "worker_url": settings.worker_url,
        "task_service_account_email": settings.task_service_account_email,
        "line_channel_access_token": settings.line_channel_access_token,
        "token_encryption_key": settings.token_encryption_key,
        "route_fingerprint_key": settings.route_fingerprint_key,
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
    line = LineConditionPromptSender(
        settings.line_channel_access_token,
        action_signing_key=settings.oauth_state_signing_key,
    )
    proposal_states = FirestoreProposalStateStore(firestore_client)
    proposal_analytics = BigQueryProposalStore(
        bigquery_client, f"{table_prefix}.proposals", settings.vertex_model
    )
    return Runtime(
        events=FirestoreEventStore(firestore_client),
        oauth_sessions=FirestoreOAuthSessionStore(firestore_client),
        tokens=FirestoreStravaTokenStore(
            firestore_client, AesGcmTokenCipher(settings.token_encryption_key)
        ),
        tasks=CloudTasksActivityPublisher(
            tasks_v2.CloudTasksAsyncClient,
            settings.cloud_tasks_queue_path,
            settings.worker_url,
            settings.task_service_account_email,
        ),
        line_tasks=CloudTasksLineEventPublisher(
            tasks_v2.CloudTasksAsyncClient,
            settings.cloud_tasks_queue_path,
            settings.worker_url,
            settings.task_service_account_email,
        ),
        activities=BigQueryActivityStore(bigquery_client, f"{table_prefix}.activities"),
        activity_laps=BigQueryActivityLapStore(
            bigquery_client, f"{table_prefix}.activity_laps"
        ),
        activity_streams=BigQueryActivityStreamStore(
            bigquery_client, f"{table_prefix}.activity_stream_points"
        ),
        activity_metrics=BigQueryActivityMetricsStore(
            bigquery_client, f"{table_prefix}.activity_metrics"
        ),
        activity_segments=BigQueryActivitySegmentStore(
            bigquery_client, f"{table_prefix}.activity_segment_metrics"
        ),
        route_fingerprints=BigQueryRouteFingerprintStore(
            bigquery_client, f"{table_prefix}.activity_route_fingerprints"
        ),
        route_comparisons=BigQueryRouteComparisonStore(
            bigquery_client, f"{table_prefix}.activity_route_comparisons"
        ),
        route_hasher=RouteFingerprintHasher(settings.route_fingerprint_key),
        activity_ingestion_state=FirestoreActivityIngestionStateStore(firestore_client),
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
        profile_drafts=FirestoreProfileDraftStore(firestore_client),
        settings_links=FirestoreSettingsLinkStore(firestore_client),
        planning_history=BigQueryPlanningHistoryStore(bigquery_client, table_prefix),
        active_plan_pointers=FirestoreActivePlanPointerStore(firestore_client),
        publication_history=BigQueryPublicationHistoryStore(
            bigquery_client, table_prefix
        ),
        publication_states=FirestorePublicationApprovalStateStore(firestore_client),
        publication_signer=PublicationActionSigner(settings.oauth_state_signing_key),
    )
