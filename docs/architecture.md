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
                                              `-> settings -> signed one-time web link
```

Webhook handlers authenticate, normalize, enqueue, and acknowledge. Workers own external calls and retries. The initial code keeps orchestration in one deployable service; it can be split without changing domain models.

StravaのPOST WebhookはCloud Tasksへのenqueue後、2秒以内に`200 OK`を返す。Cloud Runは低頻度利用向けに最小インスタンス0とstartup CPU boostを使用するため、コールドスタート時の2秒以内応答は保証しない。Stravaから同じActivity `create`が再送されても、`subscription_id/object_type/object_id/aspect_type`の安定キーで一度だけ処理する。`update`や`delete`では同じobjectに対する連続イベントを区別するため、このキーに`event_time`を加える。

リッチメニューは既存機能への入口であり、Strava更新権限を持たない。`action=menu`はLINE event worker内のrouterで案内へ変換し、署名・期限付きの`action=proposal`だけが承認workerを起動できる。

## Data model

- `users`: athlete identity, LINE link, timezone, consent status
- `strava_tokens`: AES-256-GCM encrypted access/refresh tokens and rotation metadata in Firestore
- `activities`: immutable Strava activity snapshots
- `activity_laps`: normalized lap summaries keyed by activity and lap index
- `activity_stream_points`: GPS-free time-series points for time, distance, altitude, speed, heart rate, cadence, watts, temperature, movement, and grade
- `activity_metrics`: versioned, reproducible per-activity metrics and data-quality reasons
- `activity_segment_metrics`: GPS-free 250m segment summaries with evidence-backed relative load ranks
- `activity_route_fingerprints`: athlete-scoped HMAC route identifiers; raw coordinates are discarded in worker memory
- `activity_route_comparisons`: pace, heart-rate, and cadence deltas against at least two prior runs on the same route
- `condition_reports`: subjective condition and optional symptom detail
- `goals`: primary/secondary goal, target, date, status
- `equipment`: available exercise methods and constraints
- `proposals`: next-day workout, model/prompt version, safety result, approval status
- `webhook_events`: provider event key, received/processed status, retry metadata
- `audit_events`: approval and Strava mutation evidence
- `oauth_sessions`: OAuth state nonce, LINE user ID, and `expires_at` with Firestore TTL
- `condition_drafts`: in-progress condition response and `expires_at` with Firestore TTL
- `activity_contexts`: activity-to-LINE-user context and `expires_at` with Firestore TTL

BigQuery is used for immutable history and analysis. Firestore is used for OAuth token rotation, webhook idempotency locks, conversation state, and approval state transitions; BigQuery alone is not suitable for these mutable workflows.

## AC-01 詳細Activity・負荷解析

- Run、Walk、Ride系Activityではdetail、laps、streamsをCloud Tasks worker内で取得する。非対応種目はActivity summaryから安全に処理する。
- GPS座標（`latlng`）は要求・永続化・ログ出力・Vertex AI送信を行わない。高度や勾配の解析には`distance`、`altitude`、`grade_smooth`を使用する。
- GPS以外のstream pointをBigQueryへ不変履歴として保存し、派生指標は`computation_version`付きで別テーブルへ保存する。
- 平均ペース、上昇・下降量、勾配帯別時間・距離、ペース／lap変動、心拍drift、cadence等を決定論的に算出する。欠損センサーを推測で補わず、`metric_quality`と理由を保持する。
- Run／Walk系のStrava cadenceは片足周期として返る値を2倍し、1分あたりの歩数へ正規化する。Ride系は回転数のまま保持する。
- Lapsとstream pointsはActivity開始日でpartitionし、安定row IDとFirestoreのstage状態でTask再送時の重複書き込みを抑止する。
- LINE体調確認にはActivity ID由来の安定した`X-Line-Retry-Key`を付け、送信成功後にprompt stageを完了する。
- Vertex AIへ渡すのはActivity summary、派生指標、直近Activity・Condition履歴だけであり、生streamは渡さない。
- 最大心拍等の本人設定がない状態では心拍zoneを推定しない。7日／30日負荷や負荷急増flagはAN-01で実装する。

## AC-02 区間負荷・同一ルート比較

- 保存済みのGPS非依存streamを250m区間へ集約し、pace、標高差、勾配、心拍、cadence、品質理由を保存する。
- 高負荷判定は同一Activity内の相対rankと、心拍上昇、登坂、pace低下、cadence低下の根拠コードで表現する。最大心拍未設定時の絶対的な生理負荷は推定しない。
- route fingerprintの作成時だけ`latlng`を取得し、先頭・末尾500mを除外、250m間隔でsample、約100m粒度へ量子化して、athlete IDを含むHMAC-SHA256へ変換する。座標とcanonical点列はworkerメモリから破棄し、保存・ログ・AI送信を行わない。
- 同一route hashの過去Activityが2件以上ある場合だけ、pace、心拍、cadenceの中央値差分を作成する。逆走と距離差はv1では別routeとして扱う。
- Vertex AIには高負荷区間と比較差分だけを渡し、route hashも送信しない。

## Safety boundary

The model chooses within a bounded envelope. A deterministic policy can force rest/low-impact work, forbid load increases, or suppress a workout. Pain reports produce conservative guidance and are not treated as a medical diagnosis.

## Security

- Verify LINE HMAC signatures and Strava verification token/event shape.
- Store provider secrets and token-encryption material in Secret Manager.
- Bind encrypted Strava tokens to athlete ID, LINE user ID, and token type with authenticated additional data.
- Use separate least-privilege service accounts for API and workers.
- Do not log access tokens, raw authorization headers, or unnecessary health comments.
- Log only safe Strava failure metadata such as `error_kind` and HTTP status code; do not log tokens or upstream response bodies.
- Sign approval postbacks, expire them, and bind them to athlete and proposal IDs.

## PF-01 目標・運動環境

- `goals/{goal_id}` は所有者、主/副、種別、内容、任意期限、`active/paused` を保持する。storeはAI contextへ有効項目だけを返し、主目標の保存時に既存主目標を副目標へ変更する。
- `training_environments/{environment_id}` は安定ID、所有者、表示名、`activity_place/equipment/other`、`active/inactive`、任意詳細を保持する。旧`training_resources/{line_user_id}.resources`は構造化documentがない場合だけ読み取る。
- `profile_drafts/{line_user_id}` は操作ID、action、step、途中値、`expires_at`を保持する。`expires_at`はFirestore TTL対象で、アプリ側でも期限を検証する。
- `profile_settings_links/{nonce}` はLINEユーザーに紐づく10分間有効な設定リンクを保持する。nonceは署名され、Firestore transactionで一度だけ消費し、`expires_at`をTTL対象にする。URLへLINEユーザーIDは含めない。
- 会話の最終保存IDにはdraftの操作IDを使う。同じCloud Taskが保存後に再送されても同じdocumentを上書きするため、追加が重複しない。
- 未定義の運動環境は`other`と詳細へそのまま保持し、推測で既知区分へ分類しない。健康情報や入力本文をapplication logへ出さない。
- リッチメニューの`goals`は有効目標の一覧表示だけを行う。`settings`は既存LINE workerで署名・期限付きワンタイムURLを発行し、同じCloud Run上の設定ページへのURIボタンを送る。LIFF／LINE Loginチャネルは不要である。Webhookの署名検証、event予約、Cloud Tasks enqueue、即時200応答は変更しない。
- 設定ページはワンタイムURLをHttpOnly・Secure・SameSite=Strictの30分セッションcookieへ交換する。保存APIはcookieとOriginを検証し、既存IDの所有者を確認してから目標・運動環境を更新する。
- 旧`action=profile`会話とテキストコマンドは後方互換経路として残す。通常の編集導線は設定Webページとし、自由記述や健康情報をapplication logへ出さない。
- 運動環境の複数選択中は`profile_drafts.values.selected`へJSON配列として途中保存する。完了時はdraft operation IDと正規化keyから決定的なdocument IDを生成し、Task再送時も同じ項目を重複作成しない。
