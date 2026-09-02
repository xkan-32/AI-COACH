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

resource "google_bigquery_table" "training_plan_versions" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "training_plan_versions"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["athlete_id", "week_start"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "NULLABLE" },
    { name = "athlete_id", type = "STRING", mode = "NULLABLE" },
    { name = "line_user_id", type = "STRING", mode = "NULLABLE" },
    { name = "week_start", type = "DATE", mode = "REQUIRED" },
    { name = "version", type = "INTEGER", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "goal_snapshot", type = "JSON", mode = "REQUIRED" },
    { name = "change_reason", type = "STRING", mode = "REQUIRED" },
    { name = "supersedes_plan_version_id", type = "STRING", mode = "NULLABLE" },
    { name = "safety_flags", type = "STRING", mode = "REPEATED" },
    { name = "ai_model", type = "STRING", mode = "NULLABLE" },
    { name = "prompt_version", type = "STRING", mode = "NULLABLE" },
    { name = "input_snapshot", type = "JSON", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "planned_workouts" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "planned_workouts"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "scheduled_date"
  }
  clustering = ["athlete_id", "plan_version_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "NULLABLE" },
    { name = "athlete_id", type = "STRING", mode = "NULLABLE" },
    { name = "scheduled_date", type = "DATE", mode = "REQUIRED" },
    { name = "sequence", type = "INTEGER", mode = "REQUIRED" },
    { name = "workout_type", type = "STRING", mode = "REQUIRED" },
    { name = "target_duration_minutes", type = "INTEGER", mode = "NULLABLE" },
    { name = "target_distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "target_intensity", type = "STRING", mode = "REQUIRED" },
    { name = "environment_ids", type = "STRING", mode = "REPEATED" },
    { name = "safety_constraints", type = "STRING", mode = "REPEATED" },
    { name = "workout_lineage_id", type = "STRING", mode = "NULLABLE" },
    { name = "supersedes_planned_workout_id", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "workout_reconciliations" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "workout_reconciliations"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["athlete_id", "plan_version_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "planned_workout_id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "NULLABLE" },
    { name = "athlete_id", type = "STRING", mode = "NULLABLE" },
    { name = "source_type", type = "STRING", mode = "REQUIRED" },
    { name = "activity_id", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "duration_delta_minutes", type = "FLOAT", mode = "NULLABLE" },
    { name = "distance_delta_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "intensity_delta", type = "STRING", mode = "NULLABLE" },
    { name = "matcher_version", type = "STRING", mode = "REQUIRED" },
    { name = "objective_factors", type = "STRING", mode = "REPEATED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "workout_reviews" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "workout_reviews"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["athlete_id", "plan_version_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "planned_workout_id", type = "STRING", mode = "REQUIRED" },
    { name = "reconciliation_id", type = "STRING", mode = "NULLABLE" },
    { name = "user_id", type = "STRING", mode = "NULLABLE" },
    { name = "athlete_id", type = "STRING", mode = "NULLABLE" },
    { name = "achievement_status", type = "STRING", mode = "REQUIRED" },
    { name = "objective_factors", type = "STRING", mode = "REPEATED" },
    { name = "condition_factors", type = "STRING", mode = "REPEATED" },
    { name = "dialogue_factors", type = "STRING", mode = "REPEATED" },
    { name = "feedback_codes", type = "STRING", mode = "REPEATED" },
    { name = "rule_version", type = "STRING", mode = "REQUIRED" },
    { name = "ai_model", type = "STRING", mode = "NULLABLE" },
    { name = "prompt_version", type = "STRING", mode = "NULLABLE" },
    { name = "input_snapshot", type = "JSON", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "user_training_profile_versions" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "user_training_profile_versions"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "updated_at"
  }
  clustering = ["user_id"]
  schema = jsonencode([
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "timezone", type = "STRING", mode = "REQUIRED" },
    { name = "week_starts_on", type = "INTEGER", mode = "REQUIRED" },
    { name = "weekly_generation_local_time", type = "TIME", mode = "REQUIRED" },
    { name = "provider_athlete_id", type = "STRING", mode = "NULLABLE" },
    { name = "experience_level", type = "STRING", mode = "NULLABLE" },
    { name = "notifications_enabled", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "quiet_hours_start", type = "TIME", mode = "NULLABLE" },
    { name = "quiet_hours_end", type = "TIME", mode = "NULLABLE" },
    { name = "version", type = "INTEGER", mode = "REQUIRED" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "updated_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "weekly_availability_versions" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "weekly_availability_versions"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["user_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "timezone", type = "STRING", mode = "REQUIRED" },
    { name = "version", type = "INTEGER", mode = "REQUIRED" },
    { name = "slots", type = "JSON", mode = "REQUIRED" },
    { name = "overrides", type = "JSON", mode = "REQUIRED" },
    { name = "supersedes_version_id", type = "STRING", mode = "NULLABLE" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "workout_preferences" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "workout_preferences"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["user_id", "source"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "version", type = "INTEGER", mode = "REQUIRED" },
    { name = "preference_type", type = "STRING", mode = "REQUIRED" },
    { name = "value", type = "JSON", mode = "REQUIRED" },
    { name = "strength", type = "STRING", mode = "REQUIRED" },
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "confidence", type = "FLOAT", mode = "NULLABLE" },
    { name = "evidence_event_ids", type = "STRING", mode = "REPEATED" },
    { name = "confirmation_status", type = "STRING", mode = "REQUIRED" },
    { name = "expires_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "supersedes_preference_id", type = "STRING", mode = "NULLABLE" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "dated_workout_requests" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "dated_workout_requests"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "local_date"
  }
  clustering = ["user_id", "status"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "local_date", type = "DATE", mode = "REQUIRED" },
    { name = "request_type", type = "STRING", mode = "REQUIRED" },
    { name = "value", type = "JSON", mode = "REQUIRED" },
    { name = "priority", type = "INTEGER", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "expires_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "training_plan_lifecycle_events" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "training_plan_lifecycle_events"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "occurred_at"
  }
  clustering = ["user_id", "plan_version_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "from_status", type = "STRING", mode = "REQUIRED" },
    { name = "to_status", type = "STRING", mode = "REQUIRED" },
    { name = "reason_code", type = "STRING", mode = "REQUIRED" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "occurred_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "workout_execution_states" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "workout_execution_states"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "recorded_at"
  }
  clustering = ["user_id", "planned_workout_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "planned_workout_id", type = "STRING", mode = "REQUIRED" },
    { name = "revision", type = "INTEGER", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "source_reconciliation_ids", type = "STRING", mode = "REPEATED" },
    { name = "supersedes_execution_state_id", type = "STRING", mode = "NULLABLE" },
    { name = "operation_id", type = "STRING", mode = "REQUIRED" },
    { name = "recorded_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "safety_gate_results" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "safety_gate_results"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "evaluated_at"
  }
  clustering = ["user_id", "status"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "planned_workout_id", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "reason_codes", type = "STRING", mode = "REPEATED" },
    { name = "rule_version", type = "STRING", mode = "REQUIRED" },
    { name = "input_snapshot_digest", type = "STRING", mode = "REQUIRED" },
    { name = "evaluated_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "readiness_assessments" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "readiness_assessments"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["user_id", "planned_workout_id"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "user_id", type = "STRING", mode = "REQUIRED" },
    { name = "local_date", type = "DATE", mode = "REQUIRED" },
    { name = "planned_workout_id", type = "STRING", mode = "REQUIRED" },
    { name = "revision", type = "INTEGER", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "safety_gate_result_id", type = "STRING", mode = "REQUIRED" },
    { name = "reason_codes", type = "STRING", mode = "REPEATED" },
    { name = "referenced_review_ids", type = "STRING", mode = "REPEATED" },
    { name = "supersedes_assessment_id", type = "STRING", mode = "NULLABLE" },
    { name = "rule_version", type = "STRING", mode = "REQUIRED" },
    { name = "input_snapshot_digest", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "publication_drafts" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "publication_drafts"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  clustering = ["athlete_id", "provider"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "line_user_id", type = "STRING", mode = "REQUIRED" },
    { name = "provider", type = "STRING", mode = "REQUIRED" },
    { name = "source_plan_version_id", type = "STRING", mode = "REQUIRED" },
    { name = "source_review_ids", type = "STRING", mode = "REPEATED" },
    { name = "revision", type = "INTEGER", mode = "REQUIRED" },
    { name = "title", type = "STRING", mode = "REQUIRED" },
    { name = "body", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "supersedes_draft_id", type = "STRING", mode = "NULLABLE" },
    { name = "expires_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ai_model", type = "STRING", mode = "NULLABLE" },
    { name = "prompt_version", type = "STRING", mode = "NULLABLE" },
    { name = "input_snapshot", type = "JSON", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "publication_decisions" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "publication_decisions"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "decided_at"
  }
  clustering = ["athlete_id", "provider"]
  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "draft_id", type = "STRING", mode = "REQUIRED" },
    { name = "draft_revision", type = "INTEGER", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "line_user_id", type = "STRING", mode = "REQUIRED" },
    { name = "provider", type = "STRING", mode = "REQUIRED" },
    { name = "decision", type = "STRING", mode = "REQUIRED" },
    { name = "approval_event_id", type = "STRING", mode = "REQUIRED" },
    { name = "decided_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "activity_segment_metrics" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_segment_metrics"
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
    { name = "computation_version", type = "STRING", mode = "REQUIRED" },
    { name = "segment_index", type = "INTEGER", mode = "REQUIRED" },
    { name = "start_distance_meters", type = "FLOAT", mode = "REQUIRED" },
    { name = "end_distance_meters", type = "FLOAT", mode = "REQUIRED" },
    { name = "elapsed_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "pace_seconds_per_km", type = "FLOAT", mode = "NULLABLE" },
    { name = "elevation_gain_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "elevation_loss_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_grade_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "max_heartrate_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "average_cadence_per_minute", type = "FLOAT", mode = "NULLABLE" },
    { name = "relative_load_rank_percentile", type = "FLOAT", mode = "NULLABLE" },
    { name = "high_load_reasons", type = "STRING", mode = "REPEATED" },
    { name = "metric_quality", type = "STRING", mode = "REQUIRED" },
    { name = "quality_reasons", type = "STRING", mode = "REPEATED" },
    { name = "computed_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "activity_route_fingerprints" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_route_fingerprints"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "activity_started_at"
  }
  clustering = ["athlete_id", "route_hash"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "fingerprint_version", type = "STRING", mode = "REQUIRED" },
    { name = "route_hash", type = "STRING", mode = "REQUIRED" },
    { name = "covered_distance_meters", type = "FLOAT", mode = "REQUIRED" },
    { name = "sampled_point_count", type = "INTEGER", mode = "REQUIRED" },
    { name = "trim_start_meters", type = "FLOAT", mode = "REQUIRED" },
    { name = "trim_end_meters", type = "FLOAT", mode = "REQUIRED" },
    { name = "quantization_decimals", type = "INTEGER", mode = "REQUIRED" },
    { name = "computed_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "activity_route_comparisons" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "activity_route_comparisons"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "activity_started_at"
  }
  clustering = ["athlete_id", "route_hash"]
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "athlete_id", type = "STRING", mode = "REQUIRED" },
    { name = "activity_started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "route_hash", type = "STRING", mode = "REQUIRED" },
    { name = "comparison_version", type = "STRING", mode = "REQUIRED" },
    { name = "baseline_activity_count", type = "INTEGER", mode = "REQUIRED" },
    { name = "previous_activity_id", type = "STRING", mode = "NULLABLE" },
    { name = "pace_delta_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "heartrate_delta_bpm", type = "FLOAT", mode = "NULLABLE" },
    { name = "cadence_delta_per_minute", type = "FLOAT", mode = "NULLABLE" },
    { name = "high_load_segment_indexes", type = "INTEGER", mode = "REPEATED" },
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
    { name = "plan_version_id", type = "STRING", mode = "NULLABLE" },
    { name = "planned_workout_id", type = "STRING", mode = "NULLABLE" },
    { name = "review_id", type = "STRING", mode = "NULLABLE" },
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
