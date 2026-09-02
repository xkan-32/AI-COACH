# PL-01C 週間計画画面と初回承認 実装記録

## 実装範囲

- 既存リッチメニューの`progress` postbackから週間計画リンクを送信する。画像とJSONは変更しない。
- URLは10分有効の署名付きnonceを一度だけ消費し、計画所有者・ID・versionを束縛した30分のHttpOnly sessionへ交換する。
- 週間計画画面/APIは7日分、週間・日次理由、安全制約、version、前versionとの差分だけを返す。
- 計画全体の承認、却下、別案依頼を実装する。更新には参照sessionに加え、計画ID、version、LINE所有者、decision、有効期限をHMAC署名したaction tokenが必要となる。
- 別案依頼は`reproposal_requested`で停止し、再生成enqueueはPL-01Fへ残す。

## 保存境界と不変性

`TrainingPlanVersion.status=draft`の既存行は更新しない。有効状態はappend-onlyの`TrainingPlanLifecycleEvent`から導出する。

- 生成完了時にFirestore `plan_approval_states`へdraft参照を登録し、user/LINE別pending pointerを設定する。
- 初回画面発行時に`draft -> pending_approval` eventを保存する。
- decisionはFirestore transaction CASで`pending`から終端状態へ一度だけ遷移する。
- 承認時だけ`pending_approval -> active` eventを検証し、既存plan/workoutを再insertせずactive pointerをCAS更新する。
- 却下・別案依頼はlifecycle eventだけを追加し、active pointerを変更しない。
- decision期限切れは`pending_approval -> expired` eventを保存し、active pointerを変更しない。
- `activate_version`はlegacyテスト・既存呼び出し用として維持する。

Firestoreの`weekly_plan_links`と`plan_approval_states`の`expires_at`にはTerraform TTLを設定する。pending pointerは`week_start + version`の順序を保持して古い週の再送による巻き戻しを拒否する。pending planはFirestore pointerからIDを取得してBigQueryを主キー参照し、全表scanは行わない。

## Webセキュリティ

- 署名目的prefixは`weekly-plan:web:*`と`weekly-plan:action:*`に分離し、既存`oauth_state_signing_key`を利用する。
- CookieはHttpOnly、SameSite Strict、production Secure、`/weekly-plan` path限定。
- HTML/APIは`Cache-Control: no-store`。HTMLにCSP、frame拒否、nosniffを設定する。
- decision APIはOrigin、session、LINE所有者、plan ID、version、decision action tokenを検証する。
- DTOは明示的なallow-listで構築し、`input_snapshot`、健康自由記述、GPS、stream、route hashを返さない。

## 移行上の注意

PL-01C導入前に作成済みでFirestore approval stateを持たないdraftは、自動的なBigQuery scanでは発見しない。必要な場合は対象plan IDを指定する運用migrationでapproval state/pointerを登録する。
