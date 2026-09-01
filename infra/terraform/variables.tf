variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Primary GCP region"
  type        = string
  default     = "asia-northeast1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "ai-training-coach"
}

variable "container_image" {
  description = "Immutable container image URI"
  type        = string
}

variable "bigquery_location" {
  description = "BigQuery dataset location"
  type        = string
  default     = "asia-northeast1"
}

variable "public_base_url" {
  description = "Public HTTPS base URL used by OAuth callbacks and task audience"
  type        = string
}

variable "allow_public_invocation" {
  description = "Allow unauthenticated access for provider webhooks and OAuth endpoints"
  type        = bool
  default     = false
}
