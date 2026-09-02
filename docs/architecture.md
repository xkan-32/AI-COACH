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
- `activities`: immutable activity snapshots from Strava or LINE manual registration
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
- `training_plan_versions`: append-only weekly plan versions with goal and AI-input snapshots
- `planned_workouts`: deterministic daily menus tied to immutable plan versions
- `workout_reconciliations`: versioned planned-versus-actual matching results
- `workout_reviews`: objective, condition, dialogue, and next-plan feedback evidence
- `publication_drafts` / `publication_decisions`: provider-neutral drafts and explicit approval history
- `webhook_events`: provider event key, received/processed status, retry metadata
- `audit_events`: approval and Strava mutation evidence
- `oauth_sessions`: OAuth state nonce, LINE user ID, and `expires_at` with Firestore TTL
- `condition_drafts`: in-progress condition response and `expires_at` with Firestore TTL
- `manual_activity_drafts`: in-progress LINE manual activity input and `expires_at` with Firestore TTL
- `manual_strava_publications`: operation key to created Strava activity ID for idempotent retries
- `activity_contexts`: activity-to-LINE-user context and `expires_at` with Firestore TTL

BigQuery is used for immutable history and analysis. Firestore is used for OAuth token rotation, webhook idempotency locks, conversation state, and approval state transitions; BigQuery alone is not suitable for these mutable workflows.

## 計画・実績評価feedback loop基盤

`Goal snapshot → TrainingPlanVersion → PlannedWorkout → WorkoutReconciliation → WorkoutReview → 次のTrainingPlanVersion`

- 週間計画は上書きせず、週・athlete・versionから決定的に識別する。Firestoreには週ごとのactive pointerだけを置き、全versionとAI入力snapshotはBigQueryへappend-onlyで保存する。
- 日次メニューは計画version、日付、sequenceから決定的に識別する。既存の`WorkoutProposal`はnullableな計画FKを持ち、未計画の日次提案も後方互換で扱う。
- 実績照合はStravaと手動Activityを同じ境界で扱い、matcher versionと客観的な差分を保持する。
- Reviewは客観要因、体調、対話由来要因を分離し、次計画向けfeedback codeとAI／rule versionを残す。週間shadow生成は接続済みで、自動照合と未達理由対話は後続機能で接続する。

## 外部公開承認境界

- `PublicationDraft`はnote等に依存しないprovider-neutralな下書きであり、source plan/review、revision、所有者、期限を固定する。
- 公開承認署名は既存のStrava proposal承認とは別domainで、draft ID、provider、revision、LINE所有者、decision、期限を結び付ける。
- BigQueryはdraftとdecisionの不変履歴、Firestoreはpending/approved/rejected/expiredの競合制御だけを保持する。
- Foundationにはprovider adapter呼び出しを含めない。将来の外部公開処理も、有効なLINE明示承認decisionがなければ実行できない。

## PL-01A 週間計画設定・状態基盤

- 計画所有者はapp `user_id`とし、Strava `athlete_id`はnullableなprovider linkとする。旧plan rowは読み取り時だけ`user_id=athlete_id`として互換化し、履歴を更新しない。
- `UserTrainingProfile`はIANA timezoneと月曜週開始を保持する。ローカル日付の判定時だけtimezone変換し、保存timestampはUTCとする。timezone変更で過去計画の日付を変換しない。
- 稼働可能時間は不変な`WeeklyAvailabilityVersion`と複数slotで表現し、Firestore transactionでactive pointerをCAS更新する。日跨ぎslotは拒否して2日に分割し、固定休養日と通常slotの同居を禁止する。
- explicit/inferred preferenceを分離し、inferredにはconfidence、evidence、確認状態を必須とする。同じ種別にexplicitがある場合はinferredを計画入力から除外する。
- BigQueryには設定version、plan lifecycle event、execution state、Safety Gate、Readinessをappend-onlyで保存する。Firestoreにはprofile現在値、availability active pointer、期限付きpreferences/dated requests、承認済みplan pointerを置く。
- `PlannedWorkout`は`planned`固定の不変な処方recordである。実施結果は`WorkoutExecutionState`、照合は`WorkoutReconciliation`、評価は`WorkoutReview`へ分離する。
- 新規active化経路は`pending_approval -> active` eventを必須とする。Safety Gateの`blocked`は変更案の拒否で解除しない。

## PL-01B 週間計画shadow生成

- OIDC認証された手動workerが、目標、稼働可能slot、運動環境、嗜好、日付指定request、直近負荷、Condition codeから7日分の構造化案を生成する。
- Vertex出力はPydantic schemaと決定論的Safety Gateで再検証する。日付、時間枠、buffer、環境、屋外可否、週間負荷、moderate配置に違反した案は採用しない。
- AI障害・不正出力・安全違反時は、easy mobilityと休養だけの決定論的fallbackへ切り替える。
- Activity Description、Condition自由記述、GPS、生stream、route hashはAI入力へ含めない。
- shadow結果は`draft`、日次処方、Safety Gate、lifecycle eventとしてBigQueryへappend-only保存する。active pointer、通知、Schedulerは変更しない。

## PL-01C 週間計画画面・初回承認

- リッチメニューの進捗領域から、10分有効のワンタイムURLを発行し、計画ID・version・LINE所有者を束縛したHttpOnly sessionへ交換する。
- Web DTOは7日分の処方、理由、安全制約、version差分だけをallow-listで返し、AI入力snapshotや健康自由記述を公開しない。
- 承認、却下、別案依頼にはsessionに加え、decisionを束縛した期限付きHMAC tokenを要求する。
- Firestoreの承認stateは週開始日とversionを含むpending pointerをCAS更新し、古い週・旧version・二重操作を拒否する。
- `TrainingPlanVersion`の作成時statusは不変のまま保持し、有効状態はlifecycle eventを正本とする。承認時だけ既存rowを再保存せずactive pointerを切り替える。

## MA-01 LINE手動Activity

- リッチメニューの「運動を記録」から会話を開始し、種目・日時・時間・主観強度・完了状態・任意の計画メニューと運動環境を登録する。
- 「記録する」はStrava Manual Activity作成の明示承認。未連携では開始せず、確認後に`POST /api/v3/activities`へ作成する。
- 保存先は共通`Activity`境界。`source_type=line_manual`、`user_id`、Strava Activity IDを`id` / `source_activity_id`に使い、心拍や消費カロリーは推測しない。
- Firestore draftは24時間TTL。同じoperationの再送はpublication storeのStrava IDへ収束し、内容が変わった場合だけ拒否する。
- 保存後は既存の体調確認へ接続する。翌日提案の「投稿」は同じStrava ActivityのDescriptionへ追記する。実績照合は後続機能へ残す。

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
- `profile_settings_state/{line_user_id}` はWeb設定のrevisionと最後に完了したoperation ID・payload digestを保持する。目標・運動環境・stateを単一Firestore transactionで更新し、同時編集はrevision不一致として拒否する。同一operation・同一payloadの再送だけを完了済みとして扱い、operation IDを流用した異なるpayloadは拒否する。
- 会話の最終保存IDにはdraftの操作IDを使う。同じCloud Taskが保存後に再送されても同じdocumentを上書きするため、追加が重複しない。
- 未定義の運動環境は`other`と詳細へそのまま保持し、推測で既知区分へ分類しない。健康情報や入力本文をapplication logへ出さない。
- リッチメニューの`goals`は有効目標の一覧表示だけを行う。`settings`は既存LINE workerで署名・期限付きワンタイムURLを発行し、同じCloud Run上の設定ページへのURIボタンを送る。LIFF／LINE Loginチャネルは不要である。Webhookの署名検証、event予約、Cloud Tasks enqueue、即時200応答は変更しない。
- 設定ページはワンタイムURLをHttpOnly・Secure・SameSite=Strictの30分セッションcookieへ交換する。保存APIはcookieとOriginを検証し、既存IDの所有者とrevisionを確認してから目標・運動環境を原子的に更新する。新規IDはoperation IDから決定的に生成する。
- 旧`action=profile`会話とテキストコマンドは後方互換経路として残す。通常の編集導線は設定Webページとし、自由記述や健康情報をapplication logへ出さない。
- 運動環境の複数選択中は`profile_drafts.values.selected`へJSON配列として途中保存する。完了時はdraft operation IDと正規化keyから決定的なdocument IDを生成し、Task再送時も同じ項目を重複作成しない。
