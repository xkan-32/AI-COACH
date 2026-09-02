CREATE TABLE IF NOT EXISTS `training_coach.activities` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_type STRING,
  started_at TIMESTAMP,
  duration_seconds INT64,
  distance_meters FLOAT64,
  description STRING,
  elapsed_seconds INT64,
  total_elevation_gain_meters FLOAT64,
  average_speed_mps FLOAT64,
  max_speed_mps FLOAT64,
  has_heartrate BOOL,
  average_heartrate_bpm FLOAT64,
  max_heartrate_bpm FLOAT64,
  average_cadence_per_minute FLOAT64,
  suffer_score FLOAT64,
  calories FLOAT64,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(started_at)
CLUSTER BY athlete_id, activity_type;

CREATE TABLE IF NOT EXISTS `training_coach.activity_laps` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_started_at TIMESTAMP NOT NULL,
  lap_index INT64 NOT NULL,
  name STRING,
  elapsed_seconds INT64,
  moving_seconds INT64,
  distance_meters FLOAT64,
  total_elevation_gain_meters FLOAT64,
  average_speed_mps FLOAT64,
  max_speed_mps FLOAT64,
  average_heartrate_bpm FLOAT64,
  max_heartrate_bpm FLOAT64,
  average_cadence_per_minute FLOAT64
)
PARTITION BY DATE(activity_started_at)
CLUSTER BY athlete_id, activity_id;

CREATE TABLE IF NOT EXISTS `training_coach.activity_stream_points` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_started_at TIMESTAMP NOT NULL,
  sample_index INT64 NOT NULL,
  time_seconds INT64,
  distance_meters FLOAT64,
  altitude_meters FLOAT64,
  velocity_mps FLOAT64,
  heartrate_bpm FLOAT64,
  cadence_rpm FLOAT64,
  watts FLOAT64,
  temperature_celsius FLOAT64,
  moving BOOL,
  grade_percent FLOAT64
)
PARTITION BY DATE(activity_started_at)
CLUSTER BY athlete_id, activity_id;

CREATE TABLE IF NOT EXISTS `training_coach.activity_metrics` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  computation_version STRING NOT NULL,
  metric_quality STRING NOT NULL,
  quality_reasons ARRAY<STRING>,
  average_pace_seconds_per_km FLOAT64,
  ascent_meters FLOAT64,
  descent_meters FLOAT64,
  uphill_seconds INT64,
  flat_seconds INT64,
  downhill_seconds INT64,
  uphill_meters FLOAT64,
  flat_meters FLOAT64,
  downhill_meters FLOAT64,
  pace_variability_percent FLOAT64,
  lap_pace_variability_percent FLOAT64,
  average_heartrate_bpm FLOAT64,
  max_heartrate_bpm FLOAT64,
  heartrate_drift_percent FLOAT64,
  average_cadence_per_minute FLOAT64,
  suffer_score FLOAT64,
  computed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(computed_at)
CLUSTER BY athlete_id, metric_quality;

CREATE TABLE IF NOT EXISTS `training_coach.training_plan_versions` (
  id STRING NOT NULL,
  user_id STRING,
  athlete_id STRING,
  line_user_id STRING,
  week_start DATE NOT NULL,
  version INT64 NOT NULL,
  status STRING,
  goal_snapshot JSON NOT NULL,
  change_reason STRING NOT NULL,
  plan_rationale STRING,
  supersedes_plan_version_id STRING,
  safety_flags ARRAY<STRING>,
  ai_model STRING,
  prompt_version STRING,
  input_snapshot JSON NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY athlete_id, week_start;

CREATE TABLE IF NOT EXISTS `training_coach.planned_workouts` (
  id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  user_id STRING,
  athlete_id STRING,
  scheduled_date DATE NOT NULL,
  scheduled_start_local_time TIME,
  availability_slot_id STRING,
  sequence INT64 NOT NULL,
  workout_type STRING NOT NULL,
  target_duration_minutes INT64,
  target_distance_meters FLOAT64,
  target_intensity STRING NOT NULL,
  outdoors BOOL,
  environment_ids ARRAY<STRING>,
  safety_constraints ARRAY<STRING>,
  rationale STRING,
  workout_lineage_id STRING,
  supersedes_planned_workout_id STRING,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY scheduled_date
CLUSTER BY athlete_id, plan_version_id;

CREATE TABLE IF NOT EXISTS `training_coach.workout_reconciliations` (
  id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  planned_workout_id STRING NOT NULL,
  user_id STRING,
  athlete_id STRING,
  source_type STRING NOT NULL,
  activity_id STRING,
  status STRING NOT NULL,
  duration_delta_minutes FLOAT64,
  distance_delta_meters FLOAT64,
  intensity_delta STRING,
  matcher_version STRING NOT NULL,
  objective_factors ARRAY<STRING>,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY athlete_id, plan_version_id;

CREATE TABLE IF NOT EXISTS `training_coach.workout_reviews` (
  id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  planned_workout_id STRING NOT NULL,
  reconciliation_id STRING,
  user_id STRING,
  athlete_id STRING,
  achievement_status STRING NOT NULL,
  objective_factors ARRAY<STRING>,
  condition_factors ARRAY<STRING>,
  dialogue_factors ARRAY<STRING>,
  feedback_codes ARRAY<STRING>,
  rule_version STRING NOT NULL,
  ai_model STRING,
  prompt_version STRING,
  input_snapshot JSON NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY athlete_id, plan_version_id;

CREATE TABLE IF NOT EXISTS `training_coach.user_training_profile_versions` (
  user_id STRING NOT NULL,
  timezone STRING NOT NULL,
  week_starts_on INT64 NOT NULL,
  weekly_generation_local_time TIME NOT NULL,
  provider_athlete_id STRING,
  experience_level STRING,
  notifications_enabled BOOL NOT NULL,
  quiet_hours_start TIME,
  quiet_hours_end TIME,
  version INT64 NOT NULL,
  operation_id STRING NOT NULL,
  updated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(updated_at)
CLUSTER BY user_id;

CREATE TABLE IF NOT EXISTS `training_coach.weekly_availability_versions` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  timezone STRING NOT NULL,
  version INT64 NOT NULL,
  slots JSON NOT NULL,
  overrides JSON NOT NULL,
  supersedes_version_id STRING,
  operation_id STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY user_id;

CREATE TABLE IF NOT EXISTS `training_coach.workout_preferences` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  version INT64 NOT NULL,
  preference_type STRING NOT NULL,
  value JSON NOT NULL,
  strength STRING NOT NULL,
  source STRING NOT NULL,
  confidence FLOAT64,
  evidence_event_ids ARRAY<STRING>,
  confirmation_status STRING NOT NULL,
  expires_at TIMESTAMP,
  supersedes_preference_id STRING,
  operation_id STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY user_id, source;

CREATE TABLE IF NOT EXISTS `training_coach.dated_workout_requests` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  local_date DATE NOT NULL,
  request_type STRING NOT NULL,
  value JSON NOT NULL,
  priority INT64 NOT NULL,
  status STRING NOT NULL,
  operation_id STRING NOT NULL,
  expires_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY local_date
CLUSTER BY user_id, status;

CREATE TABLE IF NOT EXISTS `training_coach.training_plan_lifecycle_events` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  from_status STRING NOT NULL,
  to_status STRING NOT NULL,
  reason_code STRING NOT NULL,
  operation_id STRING NOT NULL,
  occurred_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(occurred_at)
CLUSTER BY user_id, plan_version_id;

CREATE TABLE IF NOT EXISTS `training_coach.workout_execution_states` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  planned_workout_id STRING NOT NULL,
  revision INT64 NOT NULL,
  status STRING NOT NULL,
  source_reconciliation_ids ARRAY<STRING>,
  supersedes_execution_state_id STRING,
  operation_id STRING NOT NULL,
  recorded_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(recorded_at)
CLUSTER BY user_id, planned_workout_id;

CREATE TABLE IF NOT EXISTS `training_coach.safety_gate_results` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  planned_workout_id STRING,
  status STRING NOT NULL,
  reason_codes ARRAY<STRING>,
  rule_version STRING NOT NULL,
  input_snapshot_digest STRING NOT NULL,
  evaluated_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(evaluated_at)
CLUSTER BY user_id, status;

CREATE TABLE IF NOT EXISTS `training_coach.readiness_assessments` (
  id STRING NOT NULL,
  user_id STRING NOT NULL,
  local_date DATE NOT NULL,
  planned_workout_id STRING NOT NULL,
  revision INT64 NOT NULL,
  status STRING NOT NULL,
  safety_gate_result_id STRING NOT NULL,
  reason_codes ARRAY<STRING>,
  referenced_review_ids ARRAY<STRING>,
  supersedes_assessment_id STRING,
  rule_version STRING NOT NULL,
  input_snapshot_digest STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY user_id, planned_workout_id;

CREATE TABLE IF NOT EXISTS `training_coach.publication_drafts` (
  id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  line_user_id STRING NOT NULL,
  provider STRING NOT NULL,
  source_plan_version_id STRING NOT NULL,
  source_review_ids ARRAY<STRING>,
  revision INT64 NOT NULL,
  title STRING NOT NULL,
  body STRING NOT NULL,
  status STRING NOT NULL,
  supersedes_draft_id STRING,
  expires_at TIMESTAMP NOT NULL,
  ai_model STRING,
  prompt_version STRING,
  input_snapshot JSON NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY athlete_id, provider;

CREATE TABLE IF NOT EXISTS `training_coach.publication_decisions` (
  id STRING NOT NULL,
  draft_id STRING NOT NULL,
  draft_revision INT64 NOT NULL,
  athlete_id STRING NOT NULL,
  line_user_id STRING NOT NULL,
  provider STRING NOT NULL,
  decision STRING NOT NULL,
  approval_event_id STRING NOT NULL,
  decided_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY athlete_id, provider;

CREATE TABLE IF NOT EXISTS `training_coach.activity_segment_metrics` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_started_at TIMESTAMP NOT NULL,
  computation_version STRING NOT NULL,
  segment_index INT64 NOT NULL,
  start_distance_meters FLOAT64 NOT NULL,
  end_distance_meters FLOAT64 NOT NULL,
  elapsed_seconds INT64,
  pace_seconds_per_km FLOAT64,
  elevation_gain_meters FLOAT64,
  elevation_loss_meters FLOAT64,
  average_grade_percent FLOAT64,
  average_heartrate_bpm FLOAT64,
  max_heartrate_bpm FLOAT64,
  average_cadence_per_minute FLOAT64,
  relative_load_rank_percentile FLOAT64,
  high_load_reasons ARRAY<STRING>,
  metric_quality STRING NOT NULL,
  quality_reasons ARRAY<STRING>,
  computed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(activity_started_at)
CLUSTER BY athlete_id, activity_id;

CREATE TABLE IF NOT EXISTS `training_coach.activity_route_fingerprints` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_started_at TIMESTAMP NOT NULL,
  fingerprint_version STRING NOT NULL,
  route_hash STRING NOT NULL,
  covered_distance_meters FLOAT64 NOT NULL,
  sampled_point_count INT64 NOT NULL,
  trim_start_meters FLOAT64 NOT NULL,
  trim_end_meters FLOAT64 NOT NULL,
  quantization_decimals INT64 NOT NULL,
  computed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(activity_started_at)
CLUSTER BY athlete_id, route_hash;

CREATE TABLE IF NOT EXISTS `training_coach.activity_route_comparisons` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_started_at TIMESTAMP NOT NULL,
  route_hash STRING NOT NULL,
  comparison_version STRING NOT NULL,
  baseline_activity_count INT64 NOT NULL,
  previous_activity_id STRING,
  pace_delta_percent FLOAT64,
  heartrate_delta_bpm FLOAT64,
  cadence_delta_per_minute FLOAT64,
  high_load_segment_indexes ARRAY<INT64>,
  computed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(activity_started_at)
CLUSTER BY athlete_id, route_hash;

CREATE TABLE IF NOT EXISTS `training_coach.condition_reports` (
  athlete_id STRING NOT NULL,
  activity_id STRING NOT NULL,
  condition_level STRING NOT NULL,
  body_part STRING,
  severity INT64,
  worsened_during_activity BOOL,
  comment STRING,
  reported_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(reported_at)
CLUSTER BY athlete_id;

CREATE TABLE IF NOT EXISTS `training_coach.proposals` (
  proposal_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  source_activity_id STRING NOT NULL,
  plan_version_id STRING,
  planned_workout_id STRING,
  review_id STRING,
  target_date DATE NOT NULL,
  title STRING,
  rationale STRING,
  duration_minutes INT64,
  intensity STRING,
  safety_notes ARRAY<STRING>,
  status STRING NOT NULL,
  model_name STRING,
  prompt_version STRING,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  decided_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY athlete_id, status;

CREATE TABLE IF NOT EXISTS `training_coach.webhook_events` (
  provider STRING NOT NULL,
  event_key STRING NOT NULL,
  received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  processed_at TIMESTAMP,
  status STRING,
  error_code STRING
)
PARTITION BY DATE(received_at)
CLUSTER BY provider, status;
