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
  athlete_id STRING NOT NULL,
  line_user_id STRING NOT NULL,
  week_start DATE NOT NULL,
  version INT64 NOT NULL,
  goal_snapshot JSON NOT NULL,
  change_reason STRING NOT NULL,
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
  athlete_id STRING NOT NULL,
  scheduled_date DATE NOT NULL,
  sequence INT64 NOT NULL,
  workout_type STRING NOT NULL,
  target_duration_minutes INT64,
  target_distance_meters FLOAT64,
  target_intensity STRING NOT NULL,
  environment_ids ARRAY<STRING>,
  safety_constraints ARRAY<STRING>,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY scheduled_date
CLUSTER BY athlete_id, plan_version_id;

CREATE TABLE IF NOT EXISTS `training_coach.workout_reconciliations` (
  id STRING NOT NULL,
  plan_version_id STRING NOT NULL,
  planned_workout_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
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
  athlete_id STRING NOT NULL,
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
