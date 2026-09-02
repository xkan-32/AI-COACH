resource "google_firestore_field" "profile_drafts_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "profile_drafts"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}
