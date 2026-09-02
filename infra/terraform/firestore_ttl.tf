resource "google_firestore_field" "profile_drafts_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "profile_drafts"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "profile_settings_links_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "profile_settings_links"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "oauth_sessions_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "oauth_sessions"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "condition_drafts_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "condition_drafts"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "activity_contexts_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "activity_contexts"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "publication_approval_states_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "publication_approval_states"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "workout_preferences_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "workout_preferences"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "dated_workout_requests_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "dated_workout_requests"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_project_service.required]
}
