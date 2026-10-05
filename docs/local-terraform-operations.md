# Local Terraform operations

How to run `gcloud` and Terraform for the application stack from a developer machine.
The normal production path is unchanged: merge to `main` and let `.github/workflows/deploy.yml` build the image and apply.
Use local runs for investigation and `plan`; reserve local `apply` for cases the owner explicitly requests.

## One-time setup

```bash
gcloud auth login
gcloud auth application-default login
gcloud config configurations create personal-ai-coach --no-activate
gcloud config set account <owner-account> --configuration=personal-ai-coach
gcloud config set project ai-coach-507307 --configuration=personal-ai-coach
```

Create these untracked files. They hold names and IDs only, never secrets.

`mise.local.toml` (pins the same Terraform version as CI):

```toml
[tools]
terraform = "1.9.8"

[env]
CLOUDSDK_ACTIVE_CONFIG_NAME = "personal-ai-coach"
GOOGLE_CLOUD_QUOTA_PROJECT = "ai-coach-507307"
```

`.claude/settings.local.json` (Claude Code only; its shell does not load mise env):

```json
{
  "env": {
    "CLOUDSDK_ACTIVE_CONFIG_NAME": "personal-ai-coach",
    "GOOGLE_CLOUD_QUOTA_PROJECT": "ai-coach-507307"
  }
}
```

## Variables

CI passes variables as `TF_VAR_*` from GitHub repository variables. Locally, copy the live values into `infra/terraform/terraform.tfvars` (ignored by Git):

```bash
gcloud run services describe ai-training-coach --region=asia-northeast1 \
  --format='value(spec.template.spec.containers[0].image,status.url)'
```

| Variable | Source |
|---|---|
| `project_id` | `ai-coach-507307` |
| `region` | `asia-northeast1` |
| `public_base_url` | The Cloud Run URL used by `PUBLIC_BASE_URL` |
| `allow_public_invocation` | `true` (same as CI) |
| `container_image` | The image digest currently serving traffic |

## Plan

```bash
mise exec -- terraform -chdir=infra/terraform init -input=false \
  -backend-config="bucket=ai-coach-507307-tfstate" \
  -backend-config="prefix=application"
mise exec -- terraform -chdir=infra/terraform plan -input=false -lock=false
```

`-lock=false` matches the read-only CI plan. A plan with no code changes should report `No changes`; anything else means drift or stale variables.

## Before a local apply

1. Confirm `container_image` equals the digest currently serving traffic. A stale digest rolls Cloud Run back to an older image. Every `main` deploy makes the local value stale.
2. Make sure no deploy workflow is running, then apply with locking (omit `-lock=false`).
3. Review the plan for replacements or deletions and get the owner's confirmation before applying.
4. Commit the matching code change and merge it to `main`; otherwise the next deploy reverts the local change.

## Cost notes

- Cloud Run uses request-based billing (`cpu_idle = true`). Declaring a `resources` block turns the provider default off, so keep `cpu_idle` explicit.
- The weekly plan dispatcher runs every five minutes on Saturday-Monday UTC only. Each call wakes Cloud Run, so avoid adding frequent schedules without checking billable instance time.
- Check billable instance time with the Cloud Monitoring metric `run.googleapis.com/container/billable_instance_time`.
