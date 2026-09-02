# PL-01D 実績照合 実装記録

## 実装範囲

Strava自動取得とLINE手動登録のActivityを、承認済み週間計画の`PlannedWorkout`へ接続する共通matcherを実装した。

- 手動Activity登録時の明示`planned_workout_id`を最優先する。
- 明示linkがない場合は、ユーザーtimezoneの実施日、種目mapping、予定開始時刻、時間、距離から候補scoreを計算する。
- 一意な高信頼度候補は自動確定し、低信頼度・複数候補はLINE Quick Replyで本人確認する。
- `matched`、`partial`、`unmatched`、`ambiguous`、`unplanned`、`duplicate_candidate`、`not_performed`を扱う。
- splitを許可していない予定へ2件目のActivityを自動確定せず、重複候補として確認する。
- 自動結果は「計画外」を含むLINE選択で訂正でき、元rowを更新せず訂正rowを追加する。
- provider同期確認済みかつ予定終了から2時間経過した場合だけ、未実施候補を作る。本人は未実施、同期待ち、予定変更を選択できる。

## matcherと保存境界

matcher versionは`workout-matcher-v1`である。scoreと閾値、種目mapping、matching evidenceをversionとともに保存する。Description、手動Activity詳細、健康自由記述、GPS、生stream、route hashは照合rowへ複製しない。

BigQuery `workout_reconciliations`は候補と確定結果をappend-onlyで保持する。`candidate_planned_workout_ids`、`match_confidence`、`matching_evidence`、`confirmed`、訂正元ID、operation IDを追加した。確定結果から`WorkoutExecutionState`を別rowとして作り、`PlannedWorkout`は変更しない。

`PlannedWorkout.split_allowed`は生成時のAvailabilitySlotから固定する。旧rowのNULLはfalseとして読み取る。旧reconciliation rowは読み取り時にlegacy operation IDと確定状態を補い、履歴自体は更新しない。

## 冪等性と障害復旧

- Activity IDとmatcher version、operation IDから安定したreconciliation IDを作る。
- Firestore activity ingestion stage `reconciliation`により、Task再送時のLINE通知を重複させない。
- reconciliation保存後にexecution保存が失敗した場合も、再送で同じreconciliationからexecutionを復旧する。
- LINE訂正は対象user、Activity context、reconciliation ID、PlannedWorkout所有者、最新revisionを検証する。古い選択と所有者不一致を拒否する。
- 未実施scanはOIDC保護された`POST /tasks/plans/reconcile-missing`から手動起動できる。定期SchedulerはNT-01まで有効化しない。

## 対象外

- Conditionを含むActivity単位Workout Review（PL-01E）
- 次の予定メニューのReadiness判定（PL-01E）
- AIによる変更案と承認付き再計画（PL-01F）
- 未実施scanの自動Scheduler、quiet hours、再通知（NT-01）
- 本セッションでのデプロイ、本番schema migration

