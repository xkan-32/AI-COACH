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
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
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
