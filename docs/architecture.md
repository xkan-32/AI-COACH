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

LINE rich menu -> LINE webhook -> Cloud Tasks -> LINE event worker
                                              `-> menu action router -> guidance
```

Webhook handlers authenticate, normalize, enqueue, and acknowledge. Workers own external calls and retries. The initial code keeps orchestration in one deployable service; it can be split without changing domain models.

リッチメニューは既存機能への入口であり、Strava更新権限を持たない。`action=menu`はLINE event worker内のrouterで案内へ変換し、署名・期限付きの`action=proposal`だけが承認workerを起動できる。

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

## PF-01 目標・運動環境

- `goals/{goal_id}` は所有者、主/副、種別、内容、任意期限、`active/paused` を保持する。storeはAI contextへ有効項目だけを返し、主目標の保存時に既存主目標を副目標へ変更する。
- `training_environments/{environment_id}` は安定ID、所有者、表示名、`activity_place/equipment/other`、`active/inactive`、任意詳細を保持する。旧`training_resources/{line_user_id}.resources`は構造化documentがない場合だけ読み取る。
- `profile_drafts/{line_user_id}` は操作ID、action、step、途中値、`expires_at`を保持する。`expires_at`はFirestore TTL対象で、アプリ側でも期限を検証する。
- 会話の最終保存IDにはdraftの操作IDを使う。同じCloud Taskが保存後に再送されても同じdocumentを上書きするため、追加が重複しない。
- 未定義の運動環境は`other`と詳細へそのまま保持し、推測で既知区分へ分類しない。健康情報や入力本文をapplication logへ出さない。
- リッチメニューの`goals`と`settings`は既存LINE worker内でPF-01 workflowを開始する。Webhookの署名検証、event予約、Cloud Tasks enqueue、即時200応答は変更しない。
- PF-01の通常操作は`action=profile` postbackとQuick Replyを利用する。postbackには操作種別、安定ID、既知選択値だけを含め、自由記述や健康情報は含めない。テキストコマンドは後方互換経路として残す。
- 運動環境の複数選択中は`profile_drafts.values.selected`へJSON配列として途中保存する。完了時はdraft operation IDと正規化keyから決定的なdocument IDを生成し、Task再送時も同じ項目を重複作成しない。
