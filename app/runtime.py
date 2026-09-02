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
from app.manual_activity import (
    FirestoreManualActivityDraftStore,
    FirestoreManualStravaPublicationStore,
    InMemoryManualActivityDraftStore,
    InMemoryManualStravaPublicationStore,
    ManualActivityDraftStore,
    ManualStravaPublicationStore,
)
from app.plan_approval import (
    FirestorePlanApprovalStateStore,
    InMemoryPlanApprovalStateStore,
    PlanActionSigner,
    PlanApprovalStateStore,
)
from app.plan_generation import (
    LocalWeeklyPlanGenerator,
    VertexWeeklyPlanGenerator,
    WeeklyPlanGenerator,
)
from app.plan_revision import (
    BigQueryRevisionHistoryStore,
    FirestoreRevisionApprovalStateStore,
    InMemoryRevisionApprovalStateStore,
    InMemoryRevisionHistoryStore,
    LocalPlanRevisionGenerator,
    PlanRevisionActionSigner,
    PlanRevisionGenerator,
    RevisionApprovalStateStore,
    RevisionHistoryStore,
    VertexPlanRevisionGenerator,
)
from app.planning import (
    ActivePlanPointerStore,
    BigQueryPlanningHistoryStore,
    BigQueryTrainingSettingsHistoryStore,
    FirestoreActivePlanPointerStore,
    FirestoreTrainingSettingsStateStore,
    InMemoryActivePlanPointerStore,
    InMemoryPlanningHistoryStore,
    InMemoryTrainingSettingsStore,
    PlanningHistoryStore,
    TrainingSettingsHistoryStore,
    TrainingSettingsStateStore,
)
from app.profile import (
    FirestoreGoalStore,
    FirestoreProfileDraftStore,
    FirestoreProfileSettingsStore,
    FirestoreTrainingResourceStore,
    GoalStore,
    InMemoryGoalStore,
    InMemoryProfileDraftStore,
    InMemoryProfileSettingsStore,
    InMemoryTrainingResourceStore,
    ProfileDraftStore,
    ProfileSettingsStore,
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
from app.readiness import (
    ActiveReadinessPointerStore,
    FirestoreActiveReadinessPointerStore,
    InMemoryActiveReadinessPointerStore,
    LocalReadinessGenerator,
    ReadinessGenerator,
    VertexReadinessGenerator,
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
from app.web_weekly_plan import (
    FirestoreWeeklyPlanLinkStore,
    InMemoryWeeklyPlanLinkStore,
    WeeklyPlanLinkStore,
)
from app.weight import (
    BigQueryWeightLogStore,
    FirestoreWeightDraftStore,
    FirestoreWeightTargetStore,
    InMemoryWeightDraftStore,
    InMemoryWeightLogStore,
    InMemoryWeightTargetStore,
    WeightDraftStore,
    WeightLogStore,
    WeightTargetStore,
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
    manual_activity_drafts: ManualActivityDraftStore
    manual_strava_publications: ManualStravaPublicationStore
    weight_logs: WeightLogStore
    weight_drafts: WeightDraftStore
    weight_targets: WeightTargetStore
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
    profile_settings: ProfileSettingsStore
    settings_links: SettingsLinkStore
    planning_history: PlanningHistoryStore
    active_plan_pointers: ActivePlanPointerStore
    active_readiness_pointers: ActiveReadinessPointerStore
    training_settings_state: TrainingSettingsStateStore
    training_settings_history: TrainingSettingsHistoryStore
    weekly_plan_generator: WeeklyPlanGenerator
    readiness_generator: ReadinessGenerator
    revision_generator: PlanRevisionGenerator
    revision_history: RevisionHistoryStore
    revision_approval_states: RevisionApprovalStateStore
    revision_action_signer: PlanRevisionActionSigner
    plan_approval_states: PlanApprovalStateStore
    plan_action_signer: PlanActionSigner
    weekly_plan_links: WeeklyPlanLinkStore
    publication_history: PublicationHistoryStore
    publication_states: PublicationApprovalStateStore
    publication_signer: PublicationActionSigner


def build_runtime(settings: Settings) -> Runtime:
    if settings.app_env == "local":
        line = InMemoryConditionPromptSender()
        analytics = InMemoryProposalStore()
        proposal_states = InMemoryProposalStateStore()
        goals = InMemoryGoalStore()
        training_resources = InMemoryTrainingResourceStore()
        training_settings = InMemoryTrainingSettingsStore()
        planning_history = InMemoryPlanningHistoryStore()
        active_plan_pointers = InMemoryActivePlanPointerStore()
        active_readiness_pointers = InMemoryActiveReadinessPointerStore()
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
            manual_activity_drafts=InMemoryManualActivityDraftStore(),
            manual_strava_publications=InMemoryManualStravaPublicationStore(),
            weight_logs=InMemoryWeightLogStore(),
            weight_drafts=InMemoryWeightDraftStore(),
            weight_targets=InMemoryWeightTargetStore(),
            condition_reports=InMemoryConditionReportStore(),
            messenger=line,
            coach=LocalCoachGenerator(),
            proposals=CompositeProposalStore(proposal_states, analytics),
            proposal_sender=line,
            proposal_states=proposal_states,
            proposal_analytics=analytics,
            proposal_tasks=InMemoryProposalDecisionPublisher(),
            goals=goals,
            training_resources=training_resources,
            profile_drafts=InMemoryProfileDraftStore(),
            profile_settings=InMemoryProfileSettingsStore(goals, training_resources),
            settings_links=InMemorySettingsLinkStore(),
            planning_history=planning_history,
            active_plan_pointers=active_plan_pointers,
            active_readiness_pointers=active_readiness_pointers,
            training_settings_state=training_settings,
            training_settings_history=training_settings,
            weekly_plan_generator=LocalWeeklyPlanGenerator(),
            readiness_generator=LocalReadinessGenerator(),
            revision_generator=LocalPlanRevisionGenerator(),
            revision_history=InMemoryRevisionHistoryStore(),
            revision_approval_states=InMemoryRevisionApprovalStateStore(),
            revision_action_signer=PlanRevisionActionSigner(
                settings.oauth_state_signing_key
            ),
            plan_approval_states=InMemoryPlanApprovalStateStore(),
            plan_action_signer=PlanActionSigner(settings.oauth_state_signing_key),
            weekly_plan_links=InMemoryWeeklyPlanLinkStore(),
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
    planning_history = BigQueryPlanningHistoryStore(bigquery_client, table_prefix)
    active_plan_pointers = FirestoreActivePlanPointerStore(firestore_client)
    active_readiness_pointers = FirestoreActiveReadinessPointerStore(firestore_client)
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
        manual_activity_drafts=FirestoreManualActivityDraftStore(firestore_client),
        manual_strava_publications=FirestoreManualStravaPublicationStore(
            firestore_client
        ),
        weight_logs=BigQueryWeightLogStore(
            bigquery_client, f"{table_prefix}.weight_logs"
        ),
        weight_drafts=FirestoreWeightDraftStore(firestore_client),
        weight_targets=FirestoreWeightTargetStore(firestore_client),
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
        profile_settings=FirestoreProfileSettingsStore(firestore_client),
        settings_links=FirestoreSettingsLinkStore(firestore_client),
        planning_history=planning_history,
        active_plan_pointers=active_plan_pointers,
        active_readiness_pointers=active_readiness_pointers,
        training_settings_state=FirestoreTrainingSettingsStateStore(firestore_client),
        training_settings_history=BigQueryTrainingSettingsHistoryStore(
            bigquery_client, table_prefix
        ),
        weekly_plan_generator=VertexWeeklyPlanGenerator(
            genai_client, settings.vertex_model
        ),
        readiness_generator=VertexReadinessGenerator(
            genai_client, settings.vertex_model
        ),
        revision_generator=VertexPlanRevisionGenerator(
            genai_client, settings.vertex_model
        ),
        revision_history=BigQueryRevisionHistoryStore(bigquery_client, table_prefix),
        revision_approval_states=FirestoreRevisionApprovalStateStore(firestore_client),
        revision_action_signer=PlanRevisionActionSigner(
            settings.oauth_state_signing_key
        ),
        plan_approval_states=FirestorePlanApprovalStateStore(firestore_client),
        plan_action_signer=PlanActionSigner(settings.oauth_state_signing_key),
        weekly_plan_links=FirestoreWeeklyPlanLinkStore(firestore_client),
        publication_history=BigQueryPublicationHistoryStore(
            bigquery_client, table_prefix
        ),
        publication_states=FirestorePublicationApprovalStateStore(firestore_client),
        publication_signer=PublicationActionSigner(settings.oauth_state_signing_key),
    )
