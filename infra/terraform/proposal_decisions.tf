resource "google_bigquery_table" "proposal_decisions" {
  dataset_id          = google_bigquery_dataset.coach.dataset_id
  table_id            = "proposal_decisions"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "decided_at"
  }
  clustering = ["status"]
  schema = jsonencode([
    { name = "proposal_id", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "decided_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}
