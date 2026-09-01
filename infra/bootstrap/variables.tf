variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "asia-northeast1"
}
variable "state_bucket_name" { type = string }
variable "github_owner" {
  type    = string
  default = "xkan-32"
}
variable "github_repository" {
  type    = string
  default = "AI-COACH"
}
variable "github_owner_id" {
  description = "Immutable numeric GitHub owner ID"
  type        = string
}
variable "github_repository_id" {
  description = "Immutable numeric GitHub repository ID"
  type        = string
}
