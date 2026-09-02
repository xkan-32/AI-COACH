# MVP implementation plan and status

> 2026-09-02更新。ここでいう「実装済み」はコードと自動テスト上の状態を示す。実サービスを使うsandbox E2Eと本番運用確認はPhase 4に含める。

## Confirmed scope

The MVP supports one athlete first while retaining athlete IDs in every record. Activities are generic at the domain layer, with running as the first optimized use case. AI output is always a proposal: only an explicit LINE approval updates Strava. Pain and discomfort rules are deterministic guardrails, not prompt-only instructions.

## Delivery phases

### Phase 0 - Foundation（実装済み）

- FastAPI application and webhook endpoints
- Domain models, service orchestration, and external-service ports
- LINE signature and Strava subscription verification
- BigQuery schema and infrastructure placeholders
- Terraform foundation for required APIs, BigQuery, secrets, service account, and Cloud Run
- Unit-test baseline and local configuration template

Exit: local tests pass; no secrets are committed.

### Phase 1 - Accounts, identity, and ingestion（実装済み）

- Create Strava OAuth authorization/callback and encrypted Firestore token storage
- Refresh expired Strava access tokens and persist rotated token values
- Validate OAuth session expiry on consumption and expire transient state with Firestore TTL
- Link Strava athlete ID with LINE user ID
- Validate and deduplicate webhook events
- Fetch activity details and insert an immutable activity snapshot into BigQuery
- Fetch GPS-free laps/streams and persist versioned elevation, pace, heart-rate, cadence, and grade metrics
- Send the four-choice condition check in LINE

Exit: a real Strava activity creates exactly one stored activity and one LINE prompt.

### Phase 2 - Condition and coaching（コア実装済み）

- Parse LINE postbacks for good/fatigued/discomfort/pain
- Ask body-part/severity follow-ups only for discomfort or pain
- Query active goal and equipment context
- Query recent Activity and Condition history and include safe derived metrics in coaching context
- Include GPS-free 250m high-load evidence and same-route deltas while excluding route hashes and raw coordinates
- Call Vertex AI with a JSON response schema
- Apply hard safety constraints before and validate them after generation
- Persist prompt version, model, proposal output, and approval state

Exit: every supported response produces a valid, auditable next-day proposal.

残課題:

- AI入力snapshot、安全補正、処理結果を追跡できる監査証跡を完成させる

### Phase 2.5 - Profile and training environment（PF-01実装済み）

- LINEリッチメニューの「目標」から有効目標を一覧表示
- 「設定」から署名・期限付きワンタイムURLを発行
- 同じCloud Run上のWeb設定ページで目標・運動環境を一括編集
- 主目標・副目標、構造化された運動環境、無効化、表記揺れの正規化
- 旧会話workflowとテキストコマンドを後方互換経路として維持
- 有効な最新設定をAI coaching contextへ反映

Exit: LINEから目標・運動環境を安全かつ冪等に管理でき、次回提案へ反映される。

### Phase 2.6 - Planning and publication foundation（基盤実装済み）

- 目標snapshotを含む週間計画versionと決定的な日次メニューID
- Strava／manual activityに対応できる実績照合と、客観・体調・対話要因を分離したReview
- BigQueryのappend-only履歴とFirestoreのactive plan pointer
- provider非依存の公開draft／decision履歴と、所有者・provider・revision・期限を拘束する専用署名domain
- 既存の日次`WorkoutProposal`へnullableな計画・Review参照を追加

Exit: 週間AI生成、自動照合、未達理由対話、note adapterを後方互換で接続でき、公開には常に別のLINE明示承認を要求できる。

### Phase 3 - Approval and Strava update（実装済み）

- Send proposal with approve/reject actions and an expiring signed action token
- Make approval idempotent
- Re-fetch current Strava Description and append an AI-coach block without deleting user text
- Record approval and external update result

Exit: only one approved proposal is appended, retries do not duplicate text.

### Phase 4 - Production readiness（一部実装済み）

実装済み:

- Webhook処理をCloud Tasksへ分離し、handlerは認証・正規化・enqueue後に即時応答
- Strava Webhookへ`200 OK`を返し、Activity create再送を安定キーで重複排除
- Cloud Run最小インスタンス0を維持し、startup CPU boostでコールドスタートを短縮
- Strava Activity取得失敗時に安全な`error_kind`とHTTP status codeを記録し、tokenや上流response本文はログへ出さない
- Cloud Tasksのretry設定とOIDC付きworker呼び出し
- TerraformとGitHub ActionsによるCI/CD
- LINEリッチメニューの冪等同期
- OAuth session、体調draft、Activity context、プロフィール一時データのFirestore TTL

残課題:

- dead-letter運用、全処理を横断する構造化ログ、監視dashboard、alert
- correlation ID、activity ID、proposal IDによる横断追跡
- データ同意、保持、export、削除flow
- sandbox E2EとWebhook再送・外部障害・二重承認などのfailure-path試験
- APIとworkerのleast-privilege service account分離

## Decisions still needed before real-service connection

1. GCP project ID and preferred deployment region (default: asia-northeast1).
2. LINE Official Account and Messaging API credentials.
3. Strava application credentials and callback hostname.
4. Whether the first release is single-user/private or supports multiple invited users. The schema supports multiple athletes, but onboarding and consent differ.
5. Exact Description text policy: append the next-day plan to the completed activity (current assumption) or update a different Strava object/workflow.

## Definition of done

- Webhook authenticity and replay/idempotency are enforced.
- Provider secrets and token-encryption material live in Secret Manager; encrypted Strava OAuth tokens live in Firestore, never BigQuery or logs.
- AI output conforms to schema and deterministic safety checks.
- Explicit approval is recorded before Strava mutation.
- Existing Strava Description text is preserved.
- A full trace can be reconstructed using correlation ID and proposal ID.
