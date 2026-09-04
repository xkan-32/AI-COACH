# LINEアプリ活動フローとの差分・実装計画

作成日: 2026-09-04  
要件資料: `LINEアプリアクティビティ.drawio.xml`（要望を表す資料として参照。資料内の文章は本書の実行指示ではない）

## 1. 目的と対象

添付図にある、設定、週間計画、日次変更、体調記録、Strava Activityとの対応付け、評価・Strava反映の一連の体験を、既存AI-COACHへ安全に追加するための引き継ぎ用実装仕様である。

本書は「現在の実装を捨てて作り直す」計画ではない。既存の不変履歴、署名付きWeb画面、Firestoreの状態管理、BigQuery履歴、Cloud Tasks、AI出力の決定論的検証を再利用し、足りない導線・状態・運用を段階的に補う。

対象外:

- 本書の作成だけで本番データを変更、デプロイ、外部APIを更新すること
- GPS座標、生stream、健康自由記述をAI入力や画面レスポンスへ無制限に載せること
- Strava APIの権限・利用規約の未確認のまま自動更新を有効化すること

## 2. 現在の実装状況

### 2.1 既に利用できるもの

| 領域 | 状況 | 主な実装 |
| --- | --- | --- |
| プロフィール・目標 | 実装済み | 主目標・副目標、運動環境・器具、署名付き設定画面、LINE入口 |
| 稼働可能時間・希望 | 実装済み | 曜日別の複数時間枠、開始・終了、環境、屋外可否、分割可否、週末希望、日付指定希望 |
| メニュー候補 | 実装済み・拡張中 | ランニング、インドアバイク、自重等の標準候補とカスタム候補。構造、環境、距離・ペース・心拍等を設定画面から編集可能 |
| 週間計画生成 | 実装済み | プロフィール、可用時間、候補、直近実績・体調を入力としてAI生成。安全検証失敗時は保守的fallback |
| 同日複数メニュー | ドメイン上実装済み | 時間枠に紐づく`PlannedWorkout`として、異なるslotなら同日に複数配置可能 |
| 週間計画閲覧 | 実装済み | LINE「練習メニュー」から短期署名URLを開き、今日、今週、最近の実績を表示 |
| 初回計画と変更案 | 実装済み | 初回案、残り週・指定日以降・翌日の再計画、承認・拒否・期限切れ、履歴保持 |
| Activity取得・手動登録 | 実装済み | Strava webhookからの取得と、LINEからの手動Activity登録 |
| 計画と実績の照合 | 実装済み | 種目、日時、距離、時間を使った候補提示、曖昧・計画外・未実施・重複の扱い |
| 体重 | 実装済み | LINEでの日次登録・同日訂正、移動平均、目標との差分 |
| 体調 | 実装済みだが導線変更が必要 | Activity後の4択確認、自由記述、Readinessへ反映 |
| Strava Description更新 | 実装済み | AI提案の追記を、LINE上の有効な明示承認後のみ実行 |

### 2.2 添付図との主な差分

| 図の要望 | 現在 | 必要な対応 |
| --- | --- | --- |
| 毎週日曜21:00に週間メニューを作成し通知 | 未実装。現在は「練習メニュー」を開いた時の初回生成 | timezone対応のScheduler、対象展開Task、冪等生成、LINE通知、再試行・監視を追加 |
| 初回計画は作成時点で登録済みとして扱う | 現在は明示承認後にのみactive | **決定済み:** 日曜の自動生成時にactive化する。日次変更時は新revisionをactive化し、履歴を残す |
| 日単位で画面からメニュー・強度・環境を直接変更 | AIへの変更依頼と承認は可能だが、処方を直接編集するUIは未実装 | 対象日・slot単位の編集、休養・取消・移動・AI代替、差分確認を追加 |
| 直前キャンセル・変更 | 部分対応。再計画はあるが、execution状態を操作する専用UIが不足 | `cancelled`等の実施状態、理由、代替案、照合への影響を扱うUI/APIを追加 |
| 体調はユーザーが必要時に記録。未入力は問題なし | Activity後に体調確認をPushする | 任意の毎日体調登録へ変更し、Activity後の自動質問を廃止。未入力は「健康」と断定せず「問題の申告なし」として扱う |
| Activityごとに計画メニューをLINEで1対1選択 | matcherはあるが、図の番号選択型の確認フローは未実装 | **方針決定済み:** 自動確定を行わず、全ActivityでLINEの予定一覧から本人が選択する導線を追加 |
| 評価を自動でStravaへ反映し通知 | 現在はLINEの明示承認が必要 | **決定済み:** 評価完成後にDescriptionの管理ブロックへ自動反映し、完了をLINE通知する。冪等性・既存記述保全・解除設定を実装 |
| 候補メニューをAIが選び、負荷を具体化 | 概ね実装済み。候補編集と構造化は進行中 | 種目別の構造化編集、生成時の制約検証、説明可能な選定根拠を完成させる |

## 3. 目指す利用フロー

```text
設定（目標・環境・時間枠・候補メニュー）
  -> 日曜21:00（ユーザーtimezone）に翌週案を生成
  -> LINE通知から練習メニュー画面を開く
  -> 今日のメニュー / 今週の予定 / 過去の実績を確認
  -> 必要なら一日・一slotだけ編集、取消、AI代替を依頼
  -> Stravaまたは手動Activityを受信
  -> LINEで「どの予定に対応するか」を確認
  -> 計画対比・心拍等の評価を保存
  -> （方針決定後）Strava Descriptionへ評価を反映し、完了通知
  -> 次の予定のReadinessと次週計画へ反映
```

## 4. 設計上の前提と不変条件

### 4.1 保存境界

- Firestore: 現在の対話状態、承認・照合状態、active pointer、短期URL、Task冪等性、直近の実行状態。
- BigQuery: plan version、予定メニュー、候補設定version、照合、Review、評価入力snapshot、生成・更新監査履歴。
- 既存の計画versionと`PlannedWorkout`は上書きしない。日次編集も新しいplan revisionとし、`workout_lineage_id`と`supersedes_planned_workout_id`で追跡する。
- 予定の取消は予定レコードの削除ではなく、execution state（例: `cancelled`）と理由として保存する。

### 4.2 時刻・冪等性

- 週はユーザーのIANA timezoneで月曜開始・日曜終了とする。保存はUTC、表示・週境界・日曜21:00判定はローカルtimezoneで行う。
- 定期生成の冪等keyは少なくとも`user_id + target_week_start + generation_reason + training_settings_version`を含める。
- SchedulerはユーザーごとのAI呼出しを直接行わない。Schedulerは対象展開workerだけを起動し、各ユーザーをCloud Tasksへ分配する。
- Activity照合・Strava更新もActivity ID、planned workout ID、評価versionを結合した安定keyで二重実行を防ぐ。

### 4.3 AIと安全性

- AIへ渡すのは目標、現在有効な設定、候補メニュー構造、集約済み実績、最新体調状態、直近の計画実績に限定する。
- GPS、位置座標、生stream、route hash、Description、健康自由記述はAI入力、ログ、Web APIレスポンスに複製しない。
- AI出力の前後で、時間枠・環境・候補の上限、負荷上限、痛み・疲労、休養、連続高負荷などの決定論的検証を適用する。
- ユーザー要件として、体調未入力は「健康であり、トレーニングへの影響なし」と扱う。日次生成・Readinessでは`healthy`を既定値とし、痛み・違和感・疲労の入力がある時だけ強い安全制約を適用する。この既定判断は診断ではなく、ユーザーが明示した計画上の運用ルールである。

## 5. データモデル・APIの追加案

既存の型と保存方法を優先する。名称は実装開始時に現行domain modelへ合わせて確定する。

### 5.1 日次編集

`DailyWorkoutEditRequest`

- `id`, `user_id`, `base_plan_id`, `base_plan_version`, `planned_workout_id`, `workout_lineage_id`
- `operation_id`, `requested_at`, `request_source`（web / LINE）
- `kind`（`edit`, `cancel`, `move_slot`, `rest`, `request_ai_alternative`）
- `patch`（種目、候補ID、時間、所要時間、環境、強度、構造化処方、任意メモ）
- `reason_code`と、保存範囲を限定した任意メモ

直接編集は安全検証後にrevisionを作り、AI代替は既存`PlanChangeRequest` / `PlanRevisionProposal`を再利用する。開始済み・完了済み・過去日の変更は原則禁止する。

### 5.2 任意体調記録

`DailyConditionReport`

- `id`, `user_id`, `local_date`, `reported_at`, `source`
- `level`（例: normal / fatigue / discomfort / pain）、部位コード、強さ、任意メモ
- `supersedes_report_id`（同日訂正用）、`input_snapshot_version`

既存のActivity単位のcondition draftとの互換を保った後、Activity完了時の`condition_prompts.send(...)`を停止する。体調ハブは「体重」と「体調」を並列に開始できるようにする。

### 5.3 Activity照合のユーザー選択

`WorkoutReconciliationConfirmation`

- `id`, `user_id`, `activity_id`, `displayed_planned_workout_ids`
- `decision`（予定IDの配列、`outside_plan`、`defer`、`not_mine`）
- `selected_at`, `source_event_id`, `expires_at`, `selection_ui_version`

照合は`Activity 1件 : PlannedWorkout 1件`に制限せず、ユーザーが明示選択した予定メニューの配列として確定する。自動照合・高信頼度の自動仮紐づけは行わない。Strava Activityと手動Activityのどちらも、登録・受信後に必ずLINE上の選択肢で対応する予定をユーザーが選ぶ。

表示順は、ユーザーのローカル日付を基準に以下とする。

1. **今日のメニュー**: 開始予定時刻順。各項目にメニュー名、予定時刻、種目を表示する。
2. **メニュー外として記録**: 今日の計画に対応しない運動であることを選べる。
3. **別日のメニュー**: 昨日以前と明日以降を日付・予定時刻順に開ける。火曜日の予定を月曜に繰り上げて実施した場合も、ここから火曜日のメニューを選べる。
4. **あとで選ぶ**: 照合を保留する。評価・Strava自動投稿は選択が完了するまで実行しない。

手動登録はActivity単位を基本とするが、朝の運動と夜の運動をまとめて1件で登録することも許可する。その場合は複数の予定を選択できる。選択済みの予定はチェック状態とし、「このActivityは選択したN件のメニューをまとめて実施した記録です」と確認して保存する。

複数対応時、Activity全体の距離・時間・心拍・標高は各予定へ自動配分しない。各予定は`combined_activity`として完了・一部達成を評価し、Activity全体の負荷評価と実績要約を共通で参照する。予定ごとの厳密な距離・心拍評価が必要な場合は、個別Activityで登録するか、将来の区間配分入力を追加する。

### 5.4 評価と外部反映

`ActivityEvaluation`

- `id`, `activity_id`, `planned_workout_id`（計画外ならnull）、`evaluation_version`
- 計画達成、距離・時間・ペース・心拍・標高の要約、負荷評価、次回助言、比較根拠コード
- `strava_publication_status`, `published_at`, `strava_update_id`, `failure_reason_code`

評価はまず内部保存し、Stravaへの更新可否は別状態にする。評価生成の成功とStrava更新の成功を同一の成功として扱わない。

### 5.5 メニュー候補の構造

すでに候補の構造・環境・カスタム編集は導入済みである。次フェーズでは以下を型として正規化する。

- 共通: `sport`, `title`, `description`, `allowed_intensities`, `required_environment_keywords`, `outdoors_allowed`, `minimum_minutes`, `structure`, `enabled`。
- ランニング: 区間（ウォームアップ、メイン、レスト、クールダウン）、距離または時間、ペース範囲、最大距離、最速ペース。インターバルは反復回数、負荷区間、回復区間で表す。
- インドアバイク: 区間時間、反復、心拍ゾーンまたは心拍範囲、ケイデンス任意値、最大所要時間。
- 自重トレーニング: 種目、セット、回数または時間、休憩、対象部位、代替可否。
- 自由記述: 上記に収まらないフリーランニング等のために保持するが、AIが上限を無視する理由には使わない。

既存内部名`custom_running_candidates`が全種目のカスタマイズも保持している場合、後方互換を保ちながら`workout_candidate_customizations`等へ段階的に改名する。

## 6. 実装フェーズ

各フェーズは個別ブランチ・PR・テスト・日本語実装記録を原則とする。後続フェーズへ進む前に、前フェーズのFirestore/BigQuery保存、Task再送、権限、既存フローの回帰を確認する。

### Phase 0: 契約確定と移行準備

目的: 後続実装の前提を固定し、既存の安全原則と添付図の衝突を解消する。

- 自動Strava反映、初回計画の自動active化、未入力体調の意味は決定済みとして実装契約へ反映する。日曜21:00のtimezoneは確定する。
- 現行のdomain model、Firestore collection、BigQuery table、Terraform、LINE postbackを棚卸しして新旧対応表を作る。
- event / operation ID、version、TTL、監査ログの契約を定義する。
- `custom_running_candidates`の互換移行方針を決める。

完了条件: 決定事項が文書化され、各後続APIの入力・出力・権限・冪等keyがレビュー可能である。

### Phase 1: 体調記録を任意の日次入力へ移行

目的: 「Activity後に聞く」導線を、ユーザーが必要な時に記録する日次コンディション導線へ置き換える。

- LINEの体調ハブに「今日の体調を記録」を追加し、体重登録と独立させる。
- 日付、痛み・違和感・疲労、部位、強さ、任意メモ、訂正を実装する。
- Strava受信・手動Activity完了時のcondition Pushを停止する。
- 最新の有害な申告だけをReadiness・週間生成の安全入力へ渡す。
- 既存Activity紐づきconditionは履歴として参照可能にし、削除・再解釈しない。

テスト: Webhook再送、LINE reply/push区別、当日訂正、タイムゾーン日付、未入力、痛み時の負荷上限、旧condition処理の回帰。

### Phase 2: メニュー候補・設定画面の種目別完成

目的: AIが選べる候補を、ユーザーがスマホで理解・追加・削除・調整できる構造にする。

- 候補カードは初期状態を折りたたみとし、展開時に構造化された区間を見せる。
- ランニング、インドアバイク、自重トレーニングすべてで追加・編集・削除・標準へ戻すを提供する。
- 場所・器具・屋外可否を候補単位で設定し、可用slotとの互換性を検証する。
- ランニングのペースは分・秒入力（例: 3分04秒/km）を維持し、保存値は秒/kmへ正規化する。
- 候補を生成プロンプトへ渡す際は、固定の「控えめ」名称ではなく、AIが安全上限の範囲で距離・ペース・反復・心拍を調整することを明記する。

テスト: 各種目のCRUD、標準復元、無効候補の除外、環境不一致、構造化処方のvalidation、モバイル幅表示、既存設定との後方互換。

### Phase 3: 日曜21:00の週間計画自動生成と通知

目的: ユーザーtimezoneごとに翌週計画を生成し、LINEから確認できるようにする。

- Cloud Scheduler（起動）→対象展開worker→Cloud Tasks（ユーザー単位生成）のTerraformを追加する。
- `weekly_generation_local_time`を設定として利用し、デフォルト日曜21:00を明確にする。
- 翌週planが既にpending/activeなら重複生成せず、設定変更・再生成ポリシーを適用する。
- 生成後は署名付き練習メニューURLをLINE Pushで通知する。通知はユーザー操作へのReplyではないため、Push数の監視・設定を考慮する。
- 失敗時は安全fallbackまたは再試行状態を記録し、通知の重複を防ぐ。

テスト: timezone境界、DST、Scheduler再送、Cloud Tasks再送、複数ユーザー、未設定プロフィール、AI失敗、通知冪等性、Terraform validate。

### Phase 4: 週間画面の日次・slot単位編集

目的: 一週間を作り直さず、今日または将来日の一つのメニューを実用的に変更できるようにする。

- 今日、今週、過去表示を保ったまま、各`PlannedWorkout`に編集・休養・取消・移動・AI代替を置く。
- 直接編集フォームは候補、場所、開始時刻、時間、強度、構造化処方、メモを扱う。
- 保存時に時間枠、環境、重複、開始済み、負荷・体調制約を検証し、差分確認を表示する。
- AI代替は対象日・対象slotだけを変更対象とするplan revisionとして既存承認機構へ接続する。
- 日曜自動生成した初回案は、検証・fallbackを通過した時点でactive化する。手動編集の監査履歴とversionは必須とする。

テスト: 同日朝夕、分割可否、取消後の再配置、過去日拒否、並行編集CAS、旧承認トークン無効化、画面sessionの所有者検証。

### Phase 5: LINEでのActivity対予定メニュー照合

目的: Strava/手動Activityと予定を、推測に頼らずユーザー選択で1対1に確定する。

- Activity保存後、種目・時刻・距離による自動確定は行わず、必ずLINEへ番号付き選択を送る。
- 選択肢は「今日の予定（予定時刻順）」「メニュー外として記録」「別日の予定」「あとで選ぶ」とする。別日の予定では日付・予定時刻順のリストを表示し、繰り上げ・繰り下げ実施に対応する。
- 選択結果は署名・所有者・期限を検証する。既に別Activityへ確定済みの予定を選んだ場合は重複警告を出し、再選択または明示的な置換確認を求める。
- 手動Activityも同じ選択フローを通す。朝・夜の記録を1件へまとめた場合は、複数の予定を明示選択できる。
- 未回答では勝手に予定へ割り当てず、評価・Strava自動投稿は実行しない。照合保留として保存し、Readinessは未確定実績として保守的に扱う。

テスト: 今日の時刻順表示、同種目の朝夕、メニュー外、別日（火曜予定を月曜に実施）、複数予定の選択、重複選択、webhook再送、期限切れ選択、他ユーザー操作、手動Activity、保留時に評価・Strava更新が動かないこと。

### Phase 6: 評価エンジンとStrava反映

目的: 確定した照合結果を基に、説明可能な評価を保存し、方針に従ってStravaへ安全に反映する。

- 計画あり: 処方達成、実績要約、心拍等による負荷、過去比較、次回への助言を評価する。
- 計画外: 予定達成を断定せず、実績要約、負荷、過去比較を評価する。
- 算出可能な事実（時間、距離、平均心拍等）とAIの解釈・助言を分け、入力snapshot・prompt version・安全補正を監査する。
- Stravaへ書く対象は原則Descriptionのアプリ管理ブロックに限定し、既存ユーザー記述を壊さない。明示承認は不要とし、評価完成後に自動更新する。
- 更新成功時だけ「Stravaに評価を記載しました」とPush通知する。失敗は内部状態・監視に残し、無限再試行しない。

テスト: 計画あり/なし、心拍欠損、重複更新、既存Description保持、Strava API失敗、権限失効、LINE通知重複なし、未承認方針時に外部不変。

### Phase 7: 運用・品質・段階リリース

目的: 定期処理と外部更新を安全に運用する。

- Scheduler/Task滞留、失敗率、AI fallback率、LINE Push数、Strava更新失敗、未照合件数を可観測化する。
- DLQ、再実行手順、通知停止、機能フラグ、ロールバック手順をTerraformと運用文書へ追加する。
- テスト用LINE userだけのshadow/feature flagから開始し、生成結果・照合・評価を本番影響なしで検証する。
- データ保持、削除、同意、AI送信対象の棚卸しを実施する。

## 7. 実装時の主な変更箇所

| 領域 | 主な既存箇所 | 変更の方向 |
| --- | --- | --- |
| HTTP/LINEルーティング | `app/main.py`, `app/line_menu.py`, `app/line.py` | 日次体調、照合回答、通知入口を追加。ユーザー起点はReply、非同期通知はPushを明確化 |
| 体調 | `app/condition.py`, `app/readiness.py`, `app/training_response.py` | Activity後promptを任意日次reportへ移行 |
| 計画生成 | `app/plan_generation.py`, `app/planning.py`, `app/plan_revision.py`, `app/plan_approval.py` | Scheduler生成、日次編集revision、方針に応じた承認状態 |
| 計画Web画面 | `app/web_weekly_plan.py`, `app/static/weekly-plan.html` | slot単位編集、取消、AI代替、評価・照合状態表示 |
| 設定・候補 | `app/workout_catalog.py`, `app/static/profile-settings-candidates.js`, `app/static/planning-settings.html` | 種目別構造、全候補CRUD、環境制約、標準復元 |
| 照合・評価 | `app/reconciliation.py`, `app/coaching.py`, `app/approval.py`, `app/publication.py` | LINE確認、評価version、Strava反映状態を追加 |
| 実行基盤 | `app/tasks.py`, `app/runtime.py`, `infra/terraform/*.tf` | Scheduler、Task endpoint/IAM、TTL、BigQuery schema、監視/DLQ |
| テスト | `tests/test_condition.py`, `tests/test_reconciliation.py`, `tests/test_plan_revision.py`, `tests/test_plan_approval.py`, `tests/test_tasks_endpoint.py` | 状態遷移、再送、権限、モバイルUI、外部API障害を追加 |

## 8. 受入基準（全体）

1. ユーザーはLINEの設定画面で、目標、環境、時間枠、種目別メニュー候補をスマホから管理できる。
2. 日曜21:00（ユーザーtimezone）の翌週案は、重複なく生成・通知され、今日/今週/実績画面から確認できる。
3. ユーザーは将来または未開始の予定を、一日・一slot単位で変更、取消、休養、AI代替へ変更でき、過去履歴は失われない。
4. Activityごとに、今日の予定、メニュー外、別日の予定から本人が対応先を単数または複数選択できる。自動確定は行わず、未選択のActivityに対して評価・Strava更新を行わない。複数対応時に集計値を予定へ自動配分しない。
5. 評価には、計画との比較、観測値の要約、負荷、根拠を伴う次回助言が含まれる。データ不足時に断定しない。
6. Stravaの変更は、合意した権限方針、冪等性、Description保全、失敗時の再実行制御、通知の全てを満たす。
7. webhook/Task再送、二重タップ、期限切れ、所有者不一致、AI失敗、Strava失敗でデータや外部記述が壊れない。

## 9. 実装前に決めるべき不確定事項

### A. Stravaへ自動反映する権限方針（決定済み）

評価後のStrava Description更新は自動で実行する。既存の「明示的で有効なLINE本人承認なしにStravaを変更しない」原則は、この用途について変更する。

ただし更新対象はアプリ管理ブロックに限定し、既存のユーザー記述を保全する。Activity ID・評価versionを用いて冪等更新し、失敗を監視する。設定画面には自動投稿のon/offを用意し、offの場合は評価を保存・LINE通知するだけでStravaを変更しない。

### B. 初回週間計画を自動でactiveにするか（決定済み）

日曜21:00の自動生成後、決定論的な安全検証または安全fallbackを通過した計画は、自動でactiveにする。ユーザーは問題がなければ何も操作せず、変更したい時だけ日次編集・AI代替を利用する。

### C. 日曜21:00の意味

「毎週日曜21:00」が全ユーザー共通の日本時間か、各ユーザーのローカルtimezoneかを確定する必要がある。本書では既存設計に合わせ「各ユーザーのIANA timezoneで日曜21:00」を推奨する。通知無効・quiet hours・未設定ユーザーの扱いも決める。

### D. 体調未入力の扱い（決定済み）

未入力は「健康であり、トレーニングに影響なし」と扱う。これは医学的な診断ではなく、週間計画におけるユーザー指定の既定状態である。痛み、違和感、疲労が記録された場合はその入力を優先し、安全制約を適用する。

### E. 1対1照合の例外（決定済み）

自動照合は行わず、Activityごとにユーザーが予定名（予定日時付き）を選択する。今日の予定を先頭に、メニュー外、別日の予定を続けて表示する。火曜日の予定を月曜日に前倒しした場合も、別日の一覧から火曜日の予定を選択できる。

手動登録はActivity単位を基本とするが、朝・夜の運動を1件にまとめた場合も複数の予定を選択できる。明示選択された複数予定を同じActivityへ対応付け、各予定の達成状態は`combined_activity`として扱う。距離・時間・心拍等のActivity集計値は予定ごとに自動配分しない。

### F. 負荷基準の入力と推定範囲

ランニングのペース・心拍、バイクの心拍目標をどこまで自動推定するかを決める必要がある。最大心拍、閾値心拍、レース記録、主観的運動強度（RPE）のいずれも欠ける場合は、AIが能力を断定せず保守的な範囲・会話での確認を使う。心拍ゾーンをユーザーが設定できるかも要決定である。

### G. Stravaへ投稿する文章と失敗時の再試行

投稿先をDescriptionだけにするか、タイトル等も変えるか、追記の上限、言語、ユーザーが編集済みのDescriptionとの共存、API失敗時の再試行回数と通知を確定する必要がある。実装前にStrava APIの現行仕様・スコープを公式資料で再確認する。

## 10. 別AIへの作業開始指示

各フェーズを開始するAIは、最初に`AGENTS.md`、`CODEX.md`、`docs/feature-session-roadmap.md`、`docs/weekly-training-plan-vision.md`、本書、および当該領域のdomain model・Firestore・BigQuery・Terraform・テストを読む。

実装時は必ず、(1)既存状態遷移との整合、(2)FirestoreとBigQueryの責務分離、(3)Cloud Tasks再送時の冪等性、(4)LINE ReplyとPushの用途、(5)明示承認またはopt-in済み外部更新、(6)AI前後の決定論的安全制約、(7)テストと日本語文書、を確認する。

デプロイ、Terraform apply、Secret変更、mainへのマージ、実データ移行は、各作業時点のユーザー承認を得てから実施する。
