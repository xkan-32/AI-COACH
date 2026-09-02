# PL-01B 週間計画生成（shadow mode）実装記録

## 実装範囲

PL-01Aの計画・設定基盤へ、翌週月曜日から日曜日までの7日分を生成する手動workerを接続した。

- `POST /tasks/plans/generate`をCloud Tasks OIDC認証対象として追加
- Vertex AIのJSON Schema出力を`WeeklyPlanOutput`で検証
- local環境では決定論的な`LocalWeeklyPlanGenerator`を使用
- 生成結果を`draft`として保存し、active plan pointerは更新しない
- Scheduler、LINE通知、Web画面、承認は有効化しない

## 入力境界

AIへ渡す情報は、計画判断に必要な次の集計・snapshotに限定する。

- 目標、登録済み運動環境
- 曜日・日付別の利用可能slot、buffer適用後の最大時間、屋外可否
- 有効な明示嗜好と、確認済みの推定嗜好
- 対象週の有効な日付指定request
- 直近Activityの種別、時刻、時間、距離、獲得標高、平均心拍
- 直近Conditionのlevel、severity、運動中悪化有無

Activity Description、Condition comment・body part、GPS、生stream、route hashは入力へ含めない。自由形式の嗜好値と日付指定requestは、項目数・key長・文字列長を制限する。

## 決定論的安全ルール

AI出力後に次を再検証し、1件でも違反した場合はAI案全体を採用しない。

- 対象週の各日が重複なく1件ずつ存在する
- 非休養メニューが登録済みavailability slot内に収まる
- bufferとslot上限を超えない
- slotで許可された登録済み環境だけを使う
- 屋外不可slotで屋外メニューを作らない
- 週間時間上限、moderate日数上限、moderate連続禁止を守る
- 休養日は時間・距離・環境・屋外指定を持たない

週間時間上限は利用可能時間、600分の絶対上限、直近7日負荷の120%を基準に決める。履歴不足時は180分を上限とし、痛み・違和感がある場合はさらに抑制する。

## fallbackと監査

Vertex障害、不正JSON、安全違反時は、利用可能slot内の20分以下のeasy mobilityと休養だけで構成するfallbackを生成する。痛みがある場合は全日休養とする。

以下をappend-only履歴へ保存する。

- `TrainingPlanVersion(status=draft)`と7件の`PlannedWorkout`
- `SafetyGateResult`
- `generating -> draft`の`TrainingPlanLifecycleEvent`
- model名、prompt version、入力snapshot、fallback理由、安全違反code

`user_id + week_start + generation_reason + input_revision`をworkerの冪等keyとし、同一Task再送では重複生成しない。計画・日次メニューIDもUUIDv5で安定化する。

## BigQuery schema追加

週間画面で理由と実行slotを表示できるよう、既存tableへnullable列を追加した。

- `training_plan_versions.plan_rationale`
- `planned_workouts.scheduled_start_local_time`
- `planned_workouts.availability_slot_id`
- `planned_workouts.outdoors`
- `planned_workouts.rationale`

本番schema migrationとデプロイはこの実装には含まない。適用前にTerraform planで既存列の破壊的変更がないことを確認する。

## 手動shadowリクエスト

```json
{
  "user_id": "line-user-id",
  "line_user_id": "line-user-id",
  "week_start": "2026-09-07",
  "plan_version": 1,
  "generation_reason": "manual_shadow",
  "input_revision": "settings-1",
  "operation_id": "manual-shadow-20260907-v1",
  "requested_at": "2026-09-05T00:00:00Z"
}
```

`week_start`は月曜日、`requested_at`はtimezone付き日時を要求する。成功時も計画は`draft`であり、承認済み計画として利用されない。

## 検証

- 正常な構造化生成とdraft保存
- 稼働可能時間違反からfallbackへの切替
- provider障害時fallback
- 同一入力再送時の冪等性
- 機微な自由記述をAI入力へ含めないこと
- worker endpointの重複排除
- 全Python test、compileall、Ruff、Terraform fmt/validate

次段階はPL-01Cで、署名・期限付き週間計画画面と初回承認を実装し、承認時だけactive pointerを切り替える。
