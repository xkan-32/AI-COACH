# MA-01 LINE手動Activity 実装記録

## 実装範囲

- 既存リッチメニューの`manual_activity` postbackから会話登録を開始する。画像とJSONは変更しない。
- 種目、実施日時、時間、主観強度、完了状態、任意のPlannedWorkout、運動環境、内容・コメントを途中保存しながら登録する。
- 完了状態は`completed` / `partial` / `replaced` / `skipped`。心拍・距離・消費カロリーは推測しない。
- Activityは共通境界で保存し、`source_type=line_manual`、`user_id`、`source_activity_id`、任意の`planned_workout_id`を持つ。
- `operation_id`から決定的なActivity IDを作り、Task再送でも同じ行を重複作成しない。
- 保存後は既存の体調確認プロンプトを送り、翌日提案経路へ接続する。Strava Manual Activityは作成しない。

## 保存境界

- BigQuery `activities`へ不変スナップショットを追加する。既存Strava行の必須列は維持し、手動用列はnullableで追加する。
- Firestore `manual_activity_drafts/{line_user_id}`に会話途中状態と`expires_at`を置く。アプリ側でも24時間TTLを検証し、TerraformでFirestore TTLを設定する。
- 対応する当日PlannedWorkoutはactive pointerとユーザーtimezoneの当日分だけを候補にする。他ユーザーの計画IDは拒否する。
- 運動環境はPF-01の有効項目だけを候補にする。

## 対象外

- 実績照合（PL-01D）
- Strava Manual Activity作成と、そのための別承認
- リッチメニュー画像・座標・JSONの変更
- 体重記録（WT-01）
