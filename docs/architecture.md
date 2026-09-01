# Architecture

## Runtime flow

```text
Strava webhook -> Cloud Run API -> Cloud Tasks -> activity worker
                                          |-> Strava API
                                          |-> BigQuery
                                          `-> LINE condition prompt

LINE webhook -> Cloud Run API -> Cloud Tasks -> coaching worker
                                         |-> BigQuery context
                                         |-> safety policy
                                         |-> Vertex AI
                                         `-> LINE proposal

LINE approval -> Cloud Run API -> Cloud Tasks -> approval worker
                                         |-> approval record
                                         `-> Strava Description update
```

Webhook handlers authenticate, normalize, enqueue, and acknowledge. Workers own external calls and retries. The initial code keeps orchestration in one deployable service; it can be split without changing domain models.

## Data model

- `users`: athlete identity, LINE link, timezone, consent status
- `oauth_tokens`: encrypted/token-reference metadata only (prefer a transactional store for token rotation)
- `activities`: immutable Strava activity snapshots
- `condition_reports`: subjective condition and optional symptom detail
- `goals`: primary/secondary goal, target, date, status
- `equipment`: available exercise methods and constraints
- `proposals`: next-day workout, model/prompt version, safety result, approval status
- `webhook_events`: provider event key, received/processed status, retry metadata
- `audit_events`: approval and Strava mutation evidence

BigQuery is appropriate for history and analysis. A small transactional store such as Firestore is recommended for OAuth refresh-token rotation, webhook idempotency locks, and approval state transitions; using BigQuery alone for these mutable workflows is risky.

## Safety boundary

The model chooses within a bounded envelope. A deterministic policy can force rest/low-impact work, forbid load increases, or suppress a workout. Pain reports produce conservative guidance and are not treated as a medical diagnosis.

## Security

- Verify LINE HMAC signatures and Strava verification token/event shape.
- Store provider secrets and token-encryption material in Secret Manager.
- Use separate least-privilege service accounts for API and workers.
- Do not log access tokens, raw authorization headers, or unnecessary health comments.
- Sign approval postbacks, expire them, and bind them to athlete and proposal IDs.
