# Initial setup and GitHub Actions CI/CD

This project uses GitHub Actions OIDC and Google Cloud Workload Identity Federation (WIF). No service-account JSON key is created. Terraform owns the WIF pool/provider, service accounts, IAM, state bucket, application infrastructure, and runtime configuration. Secret payloads and external-provider account setup remain manual by design.

Repository: `xkan-32/AI-COACH`

## What you need to prepare

1. A Google Cloud project with billing enabled.
2. A Google account that can initially administer the project. Project Owner is simplest for bootstrap; a custom equivalent must include Service Usage Admin, IAM/Workload Identity administration, project IAM administration, service-account administration, and Storage administration.
3. Locally installed `gcloud`, Terraform 1.8+, Docker, and optionally GitHub CLI (`gh`).
4. A Strava API application:
   - client ID
   - client secret
   - an arbitrary webhook verify token you generate
5. A LINE Official Account with Messaging API enabled:
   - channel secret
   - channel access token
6. GitHub Actions enabled for the repository.

Do not put any provider secret in `terraform.tfvars`, GitHub variables, GitHub secrets, workflow YAML, or Terraform state.

## 1. Authenticate locally

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud billing projects describe YOUR_GCP_PROJECT_ID
```

The final command should show that billing is enabled.

## 2. Get immutable GitHub IDs

Names can be re-registered after deletion, so WIF admission uses immutable numeric IDs.

```bash
curl -fsSL https://api.github.com/users/xkan-32 | jq -r .id
curl -fsSL https://api.github.com/repos/xkan-32/AI-COACH | jq -r .id
```

Record both values.

## 3. Apply the bootstrap stack locally

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars
```

Set:

- `project_id`
- a globally unique `state_bucket_name`
- `github_owner_id`
- `github_repository_id`

Then run:

```bash
terraform init
terraform plan
terraform apply
```

This creates the versioned/private Terraform state bucket, WIF pool/provider, a read-only plan identity, and a main-only deployment identity.

Save the outputs:

```bash
terraform output -raw state_bucket
terraform output -raw workload_identity_provider
terraform output -raw plan_service_account
terraform output -raw deploy_service_account
```

WIF IAM changes can take several minutes to propagate.

## 4. Migrate bootstrap state to GCS

```bash
cp backend.tf.example backend.tf
```

Replace the bucket placeholder, then migrate:

```bash
terraform init -migrate-state
```

Accept the migration prompt. `backend.tf`, local state, and tfvars are ignored by Git. Keep an offline recovery copy of the original bootstrap state until migration has been verified.

## 5. Initialize the application state

```bash
cd ../terraform
cp terraform.tfvars.example terraform.tfvars
terraform init \
  -backend-config="bucket=YOUR_STATE_BUCKET" \
  -backend-config="prefix=application"
```

Initially set `public_base_url` to `https://bootstrap.invalid`. It will be replaced after Cloud Run returns its generated URL.

## 6. Create foundation resources first

Cloud Run needs an existing image and populated secret versions. Create their containers first:

```bash
terraform apply \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.app \
  -target=google_secret_manager_secret.provider \
  -target=google_service_account.api
```

Targeted apply is only for this bootstrap ordering problem. Normal operation always uses a complete plan/apply.

## 7. Add secret payloads manually

For each command, paste the value and finish input with Ctrl-D. This avoids putting values in Terraform state or command arguments.

```bash
gcloud secrets versions add strava-client-id --data-file=-
gcloud secrets versions add strava-client-secret --data-file=-
gcloud secrets versions add strava-verify-token --data-file=-
gcloud secrets versions add line-channel-secret --data-file=-
gcloud secrets versions add line-channel-access-token --data-file=-
gcloud secrets versions add oauth-state-signing-key --data-file=-
gcloud secrets versions add strava-token-encryption-key --data-file=-
```

Generate the OAuth signing key and Strava verify token with a cryptographically secure generator. Generate the AES-256-GCM token-encryption key as exactly 32 random bytes:

```bash
openssl rand -base64 48
openssl rand -base64 32
```

Run the appropriate command separately for each generated value. Secret payloads are the only GCP configuration intentionally not managed by Terraform. Production startup requires `strava-token-encryption-key`; do not rotate it until all existing `strava_tokens` documents have been re-encrypted with the replacement key. `scripts/register-provider-secrets.sh` creates this key only when no enabled version exists.

### Existing environment migration

Before deploying the token-encryption change to an existing environment, create only the Terraform-managed secret container, add its first payload, and then run the normal complete apply:

```bash
terraform apply \
  -target='google_secret_manager_secret.provider["strava-token-encryption-key"]'
openssl rand -base64 32 | gcloud secrets versions add \
  strava-token-encryption-key --data-file=-
terraform plan
terraform apply
```

Existing plaintext `strava_tokens` documents remain readable during rollout and are replaced with encrypted fields on their next read. Do not roll back to an application version that cannot read encrypted documents after migration has started.

## 8. Build and push the first image

```bash
export GCP_PROJECT_ID="YOUR_GCP_PROJECT_ID"
export GCP_REGION="asia-northeast1"
export IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/ai-training-coach/ai-training-coach"
gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev"
docker build -t "${IMAGE}:bootstrap" ../..
docker push "${IMAGE}:bootstrap"
gcloud artifacts docker images describe "${IMAGE}:bootstrap" \
  --format='value(image_summary.digest)'
```

Set `container_image` in `terraform.tfvars` to `IMAGE@sha256:DIGEST`, not a mutable tag.

## 9. First complete apply

```bash
terraform plan
terraform apply
terraform output -raw cloud_run_uri
```

Replace `public_base_url` with the returned HTTPS URI, set `allow_public_invocation = true`, and apply again:

```bash
terraform apply
```

Public invocation is required for Strava/LINE webhooks and OAuth endpoints. Cloud Tasks worker endpoints validate a Google-signed OIDC token, its audience, and the expected task service-account email at the application layer. Public requests without that identity are rejected.

## 10. Configure external providers

In the Strava application, set the authorization callback to:

```text
https://YOUR_CLOUD_RUN_HOST/oauth/strava/callback
```

Create the Strava webhook subscription after deployment:

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=YOUR_CLIENT_ID \
  -F client_secret=YOUR_CLIENT_SECRET \
  -F callback_url=https://YOUR_CLOUD_RUN_HOST/webhooks/strava \
  -F verify_token=YOUR_VERIFY_TOKEN
```

In LINE Developers, set and verify the webhook URL:

```text
https://YOUR_CLOUD_RUN_HOST/webhooks/line
```

Disable LINE's automatic greeting/reply messages if the bot should be the sole responder.

## 11. Configure GitHub variables

Create these repository-level Actions variables:

- `GCP_PROJECT_ID`
- `GCP_REGION` (`asia-northeast1` by default)
- `TF_STATE_BUCKET`
- `WIF_PROVIDER` (full provider output using the project number)
- `WIF_PLAN_SERVICE_ACCOUNT`
- `WIF_SERVICE_ACCOUNT` (deploy service account)
- `PUBLIC_BASE_URL`

They are identifiers, not secrets. Create a GitHub Environment named `production`, restrict deployments to `main`, and optionally require a reviewer. Do not store GCP service-account keys in GitHub.

The workflows behave as follows:

- `ci.yml`: test, lint, format check, Terraform validation.
- `terraform-plan.yml`: PR plan using a read-only WIF identity. Fork PRs do not receive WIF access, and plan does not lock or write state.
- `deploy.yml`: after merge/push to `main`, build and push an immutable image, run a complete Terraform apply, then synchronize the LINE rich menu. WIF impersonation for this identity is restricted to `refs/heads/main`. The workflow reads the Channel Access Token directly from Secret Manager at runtime, masks it, and never stores it in GitHub variables or Terraform state.

## Manual items that remain

- Creating the GCP project and enabling billing.
- Initial local bootstrap apply and state migration.
- Entering secret payloads in Secret Manager.
- Creating/configuring the Strava and LINE applications.
- Creating the first image before Cloud Run exists.
- Setting GitHub variables and Environment protection.
- Running the first two-stage application apply to discover the Cloud Run URI.

After these steps, normal application and IaC changes are deployed by merging to `main`.
