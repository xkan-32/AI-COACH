output "state_bucket" { value = google_storage_bucket.terraform_state.name }
output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}
output "deploy_service_account" { value = google_service_account.github_deploy.email }
output "plan_service_account" { value = google_service_account.github_plan.email }
