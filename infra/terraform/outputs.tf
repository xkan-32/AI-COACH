output "cloud_run_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "service_account_email" {
  value = google_service_account.api.email
}
