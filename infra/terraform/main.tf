locals {
  services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudtasks.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  secret_ids = toset([
    "line-channel-access-token",
    "line-channel-secret",
    "strava-client-id",
    "strava-client-secret",
    "strava-verify-token",
    "oauth-state-signing-key",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.services
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "coach" {
  dataset_id = "training_coach"
  location   = var.bigquery_location

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "google_firestore_database" "state" {
  name        = "ai-coach"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.required]
}

resource "google_cloud_tasks_queue" "events" {
  name       = "ai-coach-events"
  location   = var.region
  depends_on = [google_project_service.required]
  retry_config {
    max_attempts       = 8
    max_retry_duration = "3600s"
    min_backoff        = "5s"
    max_backoff        = "300s"
  }
}

resource "google_service_account" "api" {
  account_id   = "training-coach-api"
  display_name = "AI Training Coach API"
}

resource "google_secret_manager_secret" "provider" {
  for_each  = local.secret_ids
  secret_id = each.value
  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service" "api" {
  name                = var.service_name
  location            = var.region
  deletion_protection = false

  lifecycle {
    # The API returns a computed service-level scaling block even when manual
    # scaling is not configured. Autoscaling remains managed in the template.
    ignore_changes = [scaling]
  }

  template {
    labels = {
      config-generation = "1"
    }
    service_account = google_service_account.api.email
    containers {
      image = var.container_image
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = google_firestore_database.state.name
      }
      env {
        name  = "CLOUD_TASKS_QUEUE_PATH"
        value = google_cloud_tasks_queue.events.id
      }
      env {
        name  = "WORKER_URL"
        value = var.public_base_url
      }
      env {
        name  = "TASK_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.api.email
      }
      env {
        name  = "STRAVA_REDIRECT_URI"
        value = "${var.public_base_url}/oauth/strava/callback"
      }
      dynamic "env" {
        for_each = {
          STRAVA_CLIENT_ID          = "strava-client-id"
          STRAVA_CLIENT_SECRET      = "strava-client-secret"
          STRAVA_VERIFY_TOKEN       = "strava-verify-token"
          LINE_CHANNEL_SECRET       = "line-channel-secret"
          LINE_CHANNEL_ACCESS_TOKEN = "line-channel-access-token"
          OAUTH_STATE_SIGNING_KEY   = "oauth-state-signing-key"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.provider[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_service.required]
}

# Public invocation is intentionally not granted here. Add it only after webhook
# authentication, secret bindings, and the chosen ingress policy are reviewed.
