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
