resource "google_bigquery_table" "activities" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activities"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
  clustering = ["athlete_id", "activity_type"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_type", type = "STRING", mode = "NULLABLE" },
    { name = "started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "duration_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "description", type = "STRING", mode = "NULLABLE" },
    { name = "elapsed_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "total_elevation_gain_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_speed_mps", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_speed_mps", type = "FLOAT", mode = "NULLABLE" },
    { name = "has_heartrate", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "average_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_cadence_per_minute", type = "FLOAT", mode = "NULLABLE" },
    { name = "suffer_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "calories", type = "FLOAT", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "activity_laps" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_laps"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "activity_started_at"
  }
  clustering = ["athlete_id", "activity_id"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "lap_index", type = "INTEGER", mode = "REQUIRED" },
    { name = "name", type = "STRING", mode = "NULLABLE" },
    { name = "elapsed_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "moving_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "total_elevation_gain_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_speed_mps", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_speed_mps", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_cadence_per_minute", type = "FLOAT", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "activity_stream_points" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_stream_points"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "activity_started_at"
  }
  clustering = ["athlete_id", "activity_id"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "sample_index", type = "INTEGER", mode = "REQUIRED" },
    { name = "time_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "altitude_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "velocity_mps", type = "FLOAT", mode = "NULLABLE" },
    { name = "heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "cadence_rpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "watts", type = "FLOAT", mode = "NULLABLE" },
    { name = "temperature_celsius", type = "FLOAT", mode = "NULLABLE" },
    { name = "moving", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "grade_percent", type = "FLOAT", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "activity_metrics" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_metrics"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "computed_at"
  }
  clustering = ["athlete_id", "metric_quality"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "computation_version", type = "STRING", mode = "REQUIRED" },
    { name = "metric_quality", type = "STRING", mode = "REQUIRED" },
    { name = "quality_reasons", type = "STRING", mode = "REPEATED" },
    { name = "average_pace_seconds_per_km", type = "FLOAT", mode = "NULLABLE" },
    { name = "ascent_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "descent_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "uphill_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "flat_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "downhill_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "uphill_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "flat_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "downhill_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "pace_variability_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "lap_pace_variability_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "heartrate_drift_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_cadence_per_minute", type = "FLOAT", mode = "NULLABLE" },
    { name = "suffer_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "computed_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "condition_reports" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "condition_reports"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "reported_at"
  }
  clustering = ["athlete_id"]
  schema = jsonencode([
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "condition_level", type = "STRING", mode = "REQUIRED" },
    { name = "body_part", type = "STRING", mode = "NULLABLE" },
    { name = "severity", type = "INTEGER", mode = "NULLABLE" },
    { name = "worsened_during_activity", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "comment", type = "STRING", mode = "NULLABLE" },
    { name = "reported_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "proposals" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "proposals"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["athlete_id", "status"]
  schema = jsonencode([
    { name = "proposal_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "source_activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "target_date", type = "DATE", mode = "REQUIRED" },
    { name = "title", type = "STRING", mode = "NULLABLE" },
    { name = "rationale", type = "STRING", mode = "NULLABLE" },
    { name = "duration_minutes", type = "INTEGER", mode = "NULLABLE" },
    { name = "intensity", type = "STRING", mode = "NULLABLE" },
    { name = "safety_notes", type = "STRING", mode = "REPEATED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "model_name", type = "STRING", mode = "NULLABLE" },
    { name = "prompt_version", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "decided_at", type = "TIMESTAMP", mode = "NULLABLE" },
  ])
}
