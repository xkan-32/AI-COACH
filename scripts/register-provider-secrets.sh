#!/usr/bin/env bash
set -euo pipefail

project_id="${GCP_PROJECT_ID:-ai-coach-507307}"

register_secret() {
  local secret_id="$1"
  local label="$2"
  local value

  printf '%s を入力してください（画面には表示されません）: ' "$label"
  IFS= read -r -s value
  printf '\n'
  if [[ -z "$value" ]]; then
    printf 'エラー: %s が空です。\n' "$label" >&2
    exit 1
  fi

  printf '%s' "$value" | gcloud secrets versions add "$secret_id" \
    --project "$project_id" \
    --data-file=- \
    --quiet >/dev/null
  unset value
  printf '%s をSecret Managerへ登録しました。\n' "$label"
}

register_generated_secret_once() {
  local secret_id="$1"
  local label="$2"
  local current_version
  local value

  current_version="$(
    gcloud secrets versions list "$secret_id" \
      --project "$project_id" \
      --filter='state=ENABLED' \
      --limit=1 \
      --format='value(name)'
  )"
  if [[ -n "$current_version" ]]; then
    printf '%s は登録済みのため変更しません。\n' "$label"
    return
  fi

  value="$(openssl rand -base64 32)"
  printf '%s' "$value" | gcloud secrets versions add "$secret_id" \
    --project "$project_id" \
    --data-file=- \
    --quiet >/dev/null
  unset value
  printf '%s を生成してSecret Managerへ登録しました。\n' "$label"
}

gcloud auth application-default print-access-token >/dev/null

register_secret "strava-client-id" "Strava Client ID"
register_secret "strava-client-secret" "Strava Client Secret"
register_secret "line-channel-secret" "LINE Channel Secret"
register_secret "line-channel-access-token" "LINE Channel Access Token"
register_generated_secret_once \
  "strava-token-encryption-key" \
  "Strava Token Encryption Key"

printf '外部サービスの秘密値とトークン暗号化鍵を登録しました。\n'
