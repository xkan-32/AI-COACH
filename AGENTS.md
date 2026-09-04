# AGENTS.md

## Project goal

Build a safe, auditable AI training coach integrating Strava, LINE Messaging API, and Vertex AI on GCP.

## Required workflow

1. Read this file, `CODEX.md`, and relevant files before editing.
2. Preserve user changes and keep changes focused.
3. Add or update tests for behavior changes.
4. Run tests and Terraform validation for relevant changes.
5. Never commit credentials, provider tokens, `.env`, tfstate, or health-data exports.

## Architecture rules

- Keep domain policy independent from HTTP and provider SDKs.
- Webhook endpoints authenticate, normalize, enqueue, and return quickly.
- Run external calls and retries in Cloud Tasks workers.
- Use BigQuery for immutable analytical history.
- Use Firestore for OAuth metadata, idempotency, conversation state, and approval transitions.
- Strava may be updated automatically only for an activity evaluation when the user has enabled automatic evaluation publishing. Keep every update idempotent, limited to the app-managed Description block, auditable, and user-disableable. Other Strava mutations still require explicit, valid LINE approval.
- Preserve existing Strava Description text and make updates idempotent.
- Apply deterministic safety rules before and after AI generation.

## Infrastructure rules

- Manage GCP resources, IAM, APIs, and runtime configuration with Terraform.
- Do not place secret values in Terraform variables or state.
- Use least-privilege service accounts and immutable container references.
- Keep dev/staging/prod differences in explicit tfvars or separate state.
