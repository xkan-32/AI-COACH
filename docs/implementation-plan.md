# MVP implementation plan

## Confirmed scope

The MVP supports one athlete first while retaining athlete IDs in every record. Activities are generic at the domain layer, with running as the first optimized use case. AI output is always a proposal: only an explicit LINE approval updates Strava. Pain and discomfort rules are deterministic guardrails, not prompt-only instructions.

## Delivery phases

### Phase 0 - Foundation (this initial structure)

- FastAPI application and webhook endpoints
- Domain models, service orchestration, and external-service ports
- LINE signature and Strava subscription verification
- BigQuery schema and infrastructure placeholders
- Terraform foundation for required APIs, BigQuery, secrets, service account, and Cloud Run
- Unit-test baseline and local configuration template

Exit: local tests pass; no secrets are committed.

### Phase 1 - Accounts, identity, and ingestion

- Create Strava OAuth authorization/callback and encrypted refresh-token storage
- Link Strava athlete ID with LINE user ID
- Validate and deduplicate webhook events
- Fetch activity details and insert an immutable activity snapshot into BigQuery
- Send the four-choice condition check in LINE

Exit: a real Strava activity creates exactly one stored activity and one LINE prompt.

### Phase 2 - Condition and coaching

- Parse LINE postbacks for good/fatigued/discomfort/pain
- Ask body-part/severity follow-ups only for discomfort or pain
- Query recent activity, condition, goal, and equipment context
- Call Vertex AI with a JSON response schema
- Apply hard safety constraints before and validate them after generation
- Persist prompt version, model, inputs, output, and safety decision

Exit: every supported response produces a valid, auditable next-day proposal.

### Phase 3 - Approval and Strava update

- Send proposal with approve/reject actions and an expiring signed action token
- Make approval idempotent
- Re-fetch current Strava Description and append an AI-coach block without deleting user text
- Record approval and external update result

Exit: only one approved proposal is appended, retries do not duplicate text.

### Phase 4 - Production readiness

- Move webhook work to Cloud Tasks; webhook handlers acknowledge quickly
- Add retry/dead-letter handling, structured logs, monitoring, and alerts
- Deploy via Terraform and CI/CD
- Add data retention/deletion flow and least-privilege service accounts
- Run sandbox end-to-end and failure-path tests

## Decisions still needed before real-service connection

1. GCP project ID and preferred deployment region (default: asia-northeast1).
2. LINE Official Account and Messaging API credentials.
3. Strava application credentials and callback hostname.
4. Whether the first release is single-user/private or supports multiple invited users. The schema supports multiple athletes, but onboarding and consent differ.
5. Exact Description text policy: append the next-day plan to the completed activity (current assumption) or update a different Strava object/workflow.

## Definition of done

- Webhook authenticity and replay/idempotency are enforced.
- Tokens and secrets live in Secret Manager, never BigQuery or logs.
- AI output conforms to schema and deterministic safety checks.
- Explicit approval is recorded before Strava mutation.
- Existing Strava Description text is preserved.
- A full trace can be reconstructed using correlation ID and proposal ID.
