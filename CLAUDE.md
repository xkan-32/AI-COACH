# CLAUDE.md

Claude Code must follow `AGENTS.md` as the authoritative repository guidance.
The commands and delivery order in `CODEX.md` apply to every coding agent, including Claude Code.

@AGENTS.md
@CODEX.md

## Claude Code notes

- Local GCP and Terraform setup: `docs/local-terraform-operations.md`.
- Claude Code's shell does not load `mise.local.toml` automatically. Run Terraform as `mise exec -- terraform ...`; the gcloud configuration comes from `env` in `.claude/settings.local.json`.
- Production changes ship through a pull request merged to `main`, which GitHub Actions deploys. Do not run a local `terraform apply` or `destroy` unless the user asks for it in the current task, and follow the pre-apply checks in `docs/local-terraform-operations.md`.
