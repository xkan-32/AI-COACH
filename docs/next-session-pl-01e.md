# PL-01E Workout Review・Readiness 実装記録

## 実装範囲

確定済みの`WorkoutReconciliation`を、Activity単位の不変な`WorkoutReview`と、次の予定メニュー単位の`NextWorkoutReadinessAssessment`へ接続した。

- Reviewは達成状態、客観要因、体調要因、対話由来要因を分離する。
- 次の予定は日付・sequence順に選び、休養日を運動へ置き換えない。
- Readinessは`as_planned`、`with_adjustment`、`blocked`、`needs_information`を扱う。
- 同日に複数Activityがある場合はReviewを個別保存し、同じ次予定に新しいReadiness revisionを追加する。
- 従来の翌日提案にはplan version、planned workout、Review、予定日を関連付ける。PL-01Eでは`PlannedWorkout`を変更しない。

## Safety GateとAI境界

体調未回答は`needs_information`とし、OIDC保護された`POST /tasks/plans/evaluate-readiness`から追加確認を再送できる。疲労・違和感は`adjustment_required`、痛み・運動中悪化は`blocked`とする。Safety GateはAI生成前に適用し、生成後にも状態を強制する。過去のblockは後続評価で自動解除しない。

Vertex AIへ渡すsnapshotは、所有者ID、予定IDと処方、Review IDと列挙code、体調level・severity bucket、Safety Gateだけである。GPS、生stream、route hash、Activity Description／details、体調comment／body partは渡さない。

## 保存と冪等性

- BigQueryの`workout_reviews`、`safety_gate_results`、`readiness_assessments`へappend-onlyで保存する。
- ReviewとReadinessはrule versionとoperation IDから安定IDを作る。
- Readinessはrevisionと`supersedes_assessment_id`を持ち、Firestoreの`active_readiness_assessments`をCAS更新する。
- 履歴保存後にpointer更新が失敗した再送は同じassessmentを再利用してpointerを復旧する。遅延した旧operationは新しいactive pointerを巻き戻さない。
- BigQuery追加列は既存rowと共存できるNULLABLE列とし、読取時にlegacy operation IDと空配列を補う。

## 対象外

- Readinessを根拠にした計画versionの自動変更
- 翌日のみ／指定日以降／週全体の変更案とユーザー承認（PL-01F）
- grace periodの自動Scheduler、quiet hours、再通知上限（NT-01）
- PL-01E実装PRの自動マージとデプロイ
