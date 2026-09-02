# PL-01A 実装記録: 週間計画ドメイン契約・設定基盤

## 実装範囲

PL-01Aでは週間計画生成より前の契約と保存境界を実装した。計画所有者は`user_id`であり、`athlete_id`は任意のprovider linkとして扱う。ユーザーtimezoneはIANA名だけを受理し、週開始日はそのtimezoneにおける月曜日とする。timestampはtimezone-awareな値をUTCへ正規化して保存する。

追加した主なmodelは次のとおりである。

- `UserTrainingProfile`: timezone、月曜週開始、週間生成local time、任意のStrava athlete link、本人設定の経験レベル、通知設定、version
- `WeeklyAvailabilityVersion`: 不変な設定version、複数`AvailabilitySlot`、日付指定`DatedAvailabilityOverride`、supersedes ID
- `AvailabilitySlot`: 曜日、local time、最大運動時間、前後buffer、環境、屋外可否、split可否、固定休養日
- `WorkoutPreference`: explicit/inferred、hard/soft、構造化value、confidence、evidence event、確認状態、有効期限
- `DatedWorkoutRequest`: local date、構造化された希望、優先度、状態、有効期限
- `TrainingPlanStatus`と`TrainingPlanLifecycleEvent`: generationから承認・active・終了までの許可遷移
- `WorkoutExecutionState`: completed/partial/skipped/replaced/not_performedを不変な`PlannedWorkout`から分離
- `SafetyGateResult`と`NextWorkoutReadinessAssessment`: 決定論的安全判定と日次Readinessを分離

PL-01B以降のAI生成、PL-01Cの画面と承認、PL-01Dの自動照合、PL-01EのReadiness実行、PL-01Fの再計画UIは含めない。

## 状態遷移

許可する計画遷移は次のとおりである。`draft -> active`のように承認待ちを飛ばす遷移は拒否する。

```text
generating -> draft -> pending_approval -> active -> superseded
     |                         |       
     +-> generation_failed    +-> rejected / expired
```

新規経路は`activate_approved_version`へ`pending_approval -> active`のlifecycle eventを渡した場合だけactive pointerを更新する。従来のPlanning Foundation向け`activate_version`は既存テストと移行期間の互換経路として残すが、`status=active`以外を拒否する。週間生成・承認を接続するPL-01B/Cでは新規経路だけを使用する。

Safety Gateが`blocked`の場合、提案されたReadinessが`as_planned`でも結果は`blocked`となる。変更案の拒否はSafety Gate recordを更新・削除しない。

## FirestoreとBigQueryの責務

Firestoreには現在値と競合制御に必要な次のdocumentを置く。

- `user_training_profiles/{user_id}`: profileの現在versionと最後のoperation ID
- `weekly_availability_versions/{version_id}`: 不変な稼働可能時間version
- `active_weekly_availability/{user_id}`: active version pointer、version、operation ID
- `workout_preferences/{preference_id}`: 嗜好revision。`expires_at`はTTL対象
- `dated_workout_requests/{request_id}`: 日付指定希望。`expires_at`はTTL対象
- `active_training_plans/{user_id}:{week_start}`: 承認済みplanのactive pointer

BigQueryには上記設定の全version、plan lifecycle event、処方、execution state、Safety Gate、Readinessをappend-onlyで置く。Terraformと`infra/bigquery/schema.sql`へ同じschemaを追加した。

## 冪等性と競合制御

- profileとavailabilityは`expected_version`によるcompare-and-setを行い、versionを1ずつ増やす。
- availabilityは`supersedes_version_id`が現在pointerと一致する場合だけ切り替える。
- 同じ`operation_id`と同じpayloadの再送は成功済みとして扱い、同じoperation IDで異なるpayloadは拒否する。
- plan、workout、availability、preference、dated request、lifecycle event、execution state、Safety Gateは所有者・論理version・operation IDからUUIDv5の安定IDを作れる。
- BigQuery insert IDにも同じ安定IDを使い、Task再送時のbest-effort deduplicationを行う。

Firestore更新より先にBigQuery履歴を保存する。履歴保存後にFirestore CASが競合した場合、その履歴は監査可能な非active versionとして残る。同じoperationの再送は同じrow IDを使う。

## 旧modelとの互換性とmigration

- 既存`training_plan_versions`の`athlete_id`はnullableなprovider linkへ緩和し、`user_id`と`status`をadditiveに追加する。
- 旧BigQuery rowに`user_id`または`status`がない場合、読み取り時だけ`user_id=athlete_id`、`status=active`として解釈する。既存rowは書き換えない。
- 既存`create_plan_version(athlete_id, ...)`呼び出しは第1引数をlegacy user IDとして維持し、明示されない限り同じ値をprovider athlete IDにも設定する。新規コードはapp user IDを渡し、未連携ユーザーでは`athlete_id=None`とする。
- `planned_workouts.status`は既存schema互換のため`planned`固定で残す。completed/skipped/replaced等の結果は新しい`workout_execution_states`だけへ保存する。
- 本番schema変更や既存データbackfillはこのPRでは実行しない。Terraform planを確認し、段階的なschema緩和・列追加を承認後に適用する。

## 安全性・privacy

設定modelと履歴storeは自由記述、健康情報、GPS、生stream、route hashをログへ出さない。AI入力はPL-01Bで別途組み立て、ここで追加した`input_snapshot_digest`にはcanonical JSONのSHA-256だけを保存する。Webhook、Cloud Tasks enqueue、Strava更新、外部公開、秘密値の処理は変更していない。

## 手動確認とrollback

本番適用前にTerraform planで既存BigQuery列の`REQUIRED -> NULLABLE`緩和と追加tableだけであることを確認する。Firestore TTL policy追加も同時に確認する。rollbackはアプリ変更をrevertし、新規table/fieldは削除せず未使用のまま保持する。append-only履歴を削除するrollbackは行わない。

