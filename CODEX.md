# CODEX.md

Codex must follow `AGENTS.md` as the authoritative repository guidance.

## Commands

```bash
python3 -m pytest
python3 -m compileall -q app tests
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
```

## Delivery order

1. Foundation and IaC validation.
2. Strava OAuth, webhook validation, ingestion, and idempotency.
3. LINE linking, condition check, and follow-up state machine.
4. Vertex AI structured proposal with deterministic safety validation.
5. Signed approval and idempotent Strava Description append.
6. End-to-end validation, monitoring, retention, and deletion flows.

Do not deploy, create cloud resources, or insert secrets without explicit user authorization.
