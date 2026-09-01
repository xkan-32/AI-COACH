CREATE TABLE IF NOT EXISTS `training_coach.activities` (
  activity_id STRING NOT NULL,
  athlete_id STRING NOT NULL,
  activity_type STRING,
  started_at TIMESTAMP,
  duration_seconds INT64,
  distance_meters FLOAT64,
  description STRING,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(started_at)
CLUSTER BY athlete_id, activity_type;

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
