# AI週間トレーニング計画・日次改善構想

## 目的

ユーザーの目標、実際のトレーニング結果、体調、対話、運動環境、確保できる時間をAIが継続的に評価し、実行可能な週間トレーニング計画を提示する。

計画を一度作って終わりにせず、日々の結果を受けて次の予定メニューの実施可能性を判断し、必要な変更をユーザーへ提案する。変更はユーザーの意思を確認してから反映し、過去の計画や判断理由は上書きせず履歴として残す。

note等への外部公開は当面の対象としない。週間計画はLINEリッチメニューから署名・期限付きリンクで開く専用Web画面に表示する。

## この機能の位置付け

週間計画は、既存のActivity分析や単発の翌日提案に追加される別機能ではなく、それらを継続的な改善へつなぐアプリの中心機能とする。

```text
目標・生活時間・運動環境・好み
  -> 週間計画案
  -> ユーザー承認
  -> 実施Activity
  -> 体調確認・実績照合・Workout Review
  -> 次の予定メニューのReadiness判定
  -> 必要時のみ承認付き再計画
  -> 次週の週間計画
```

現在の「Activity完了後に翌日提案を1件作る」処理は、最終的にこのloopの`Workout Review -> Readiness判定`へ統合する。移行中は計画に属さないActivityでも既存提案を継続できる後方互換を保つ。

## 決定済みの設計原則

- 計画の所有主体はStrava athleteではなくアプリユーザーとする。Strava未連携や手動Activityのみのユーザーも週間計画を利用でき、`athlete_id`はprovider linkとして任意に扱う。
- 週はユーザーのIANA timezoneにおける月曜日00:00から日曜日23:59:59までとする。保存時刻はUTC、曜日・日付・通知判定はユーザーtimezoneで行う。
- 「翌日」は原則として「次の未実施PlannedWorkout」を意味する。次の日が休養日なら、休養を上書きして運動を提案しない。
- AIが作成した計画案をそのままactiveにしない。ユーザーが明示承認したversionだけをactive pointerへ設定する。
- 計画、実績、評価、変更案、承認は別recordとして保存し、過去recordを更新して意味を変えない。
- AIの変更提案に対する承認と、安全上の実施可否を分離する。安全ゲートが`blocked`の場合、代替案を拒否しても元の高負荷メニューを実施可能へ戻さない。
- 明示的に設定した好みと、実績から推定した好みを分離する。AI推定は根拠と信頼度を持ち、ユーザーが確認、修正、削除できるようにする。
- AIには判断に必要な集計・コード・最新snapshotだけを渡し、全履歴、生stream、位置情報、route hash、不要な自由記述を渡さない。
- 定期実行を有効にする前に、手動生成によるshadow modeで生成、確認、承認、照合、再計画まで検証する。

## 基本フロー

1. 毎週土曜日の午前に、AIが翌週月曜日から日曜日までの計画を作成する。
2. ユーザーはLINEリッチメニューから週間計画画面を開き、内容と理由を確認する。
3. ユーザーは計画を受け入れるか、希望や予定を入力して変更を依頼する。
4. 計画に従ってトレーニングを行う。
5. ランニングとバイクはウォッチからStravaへ連携されたActivityを自動取得する。
6. ウェイト、自宅トレーニング等はLINEリッチメニューから手動Activityとして登録できる。
7. AIは計画と実績、体調回答、ユーザーとの対話を照合して達成状況と未達理由を評価する。
8. AIは次の予定メニューを予定どおり実施できるか判断する。
9. 変更が必要な場合は理由と代替メニューを提示し、ユーザーが承認、拒否、再提案依頼のいずれかを選ぶ。
10. 承認された変更と日々のReviewを、残りの週間計画および次週の計画へ反映する。

## 状態遷移

### 週間計画

```text
generating -> draft -> pending_approval -> active -> superseded
     |                         |
     +-> generation_failed    +-> rejected / expired
```

- `draft`はAI出力と決定論的検証が完了した未提示の案とする。
- `pending_approval`はユーザーへ提示済みの案とする。
- `active`は明示承認済みの計画だけに許可する。
- 同じユーザー・週にactiveなversionは1件だけとし、Firestore transactionでactive pointerを切り替える。
- 却下、期限切れ、生成失敗時は既存の安全なactive planを維持する。初回計画が失敗した場合は、AIを使わない保守的fallback案を提示する。

### 計画変更

```text
PlanChangeRequest
  -> PlanRevisionProposal(pending)
  -> approved / rejected / expired / needs_reproposal
  -> approvedの場合のみ新しいTrainingPlanVersionを作成
```

- 既存versionや既存PlannedWorkoutを編集しない。
- 変更対象が翌日だけでも新versionを作成する。
- 変更前後の対応を追跡するため、日次メニューに論理的な`workout_lineage_id`または`supersedes_planned_workout_id`を持たせる。
- ユーザーが安全な代替案を拒否した場合、変更案は`rejected`になるが、安全ゲートの`blocked`判定は維持する。

### 日次メニューの実施状態

`PlannedWorkout`は不変な処方recordとする。`completed/skipped/replaced`等の実施結果はPlannedWorkout自体を書き換えず、WorkoutExecutionState、WorkoutReconciliation、WorkoutReviewから導出する。

## 土曜日の週間計画生成

土曜日AMの定期処理で、翌週7日分を一括生成する。ユーザーのtimezoneを基準に実行し、具体的な生成時刻は設定可能にする。

AIへの入力には以下を含める。

- 主目標、副目標、期限、優先度
- 曜日ごとの稼働可能時間
- 時間帯ごとに利用できる運動環境と器具
- ユーザーの継続的な好みと日付指定の希望
- 直近のActivity、250m区間負荷、同一ルート比較
- 計画と実績の差分、Workout Review、未達理由
- 体調履歴、痛み、違和感、疲労、回復状況
- 既存の安全制約と負荷調整ルール

AIは単に空いている時間へ運動を詰めるのではなく、目標達成に必要な刺激と回復を組み合わせ、現実に実行可能な計画を作成する。

生成処理はCloud Schedulerから直接ユーザーごとのAI呼び出しを行わない。Schedulerは対象展開workerを1回起動し、ユーザーtimezoneと生成設定から対象者を列挙してCloud Tasksへ分配する。

生成の冪等keyは少なくとも`user_id + week_start + generation_reason + input_revision`を含める。同じTask再送では同じ計画案を返し、異なる入力から同じplan versionを上書きしない。

次の場合も扱う。

- 土曜日の生成後に登録・timezone変更したユーザーは、手動または初回on-demand生成を利用する。
- 月曜日まで未承認なら既存active planを維持し、初回ユーザーには未承認の計画を実施済み扱いしない。
- 週途中の開始では残り日数だけを対象にし、過去日へメニューを生成しない。
- 生成失敗、Vertex AI timeout、不正JSON、安全検証違反時はstageを記録し、再送またはfallbackへ進む。

## 週間稼働可能時間

設定メニューから、曜日ごとの連続した稼働可能時間を事前登録する。

例:

- 月曜日 06:00〜07:00: 屋外ランニング、自宅トレーニング
- 月曜日 20:00〜21:00: 自宅トレーニングのみ
- 土曜日 08:00〜11:00: 屋外ランニング、バイク
- 日曜日: 原則休養

保持する情報:

- 曜日
- 開始時刻と終了時刻
- 最大運動時間
- 利用可能な運動環境と器具
- 屋外運動の可否
- 分割メニューの可否
- 固定休養日
- 一時的な予定変更

朝1時間と夜1時間が確保されていても、2時間の連続メニューは配置しない。朝夕に分割してよいメニューだけを、ユーザー設定に従って分割する。

夜間は危険なため屋外運動を避けたい、平日夜だけジムを利用できる、雨天時はトレッドミルへ変更したい、といった制約も時間帯と運動環境の組み合わせとして扱う。

追加ルール:

- `AvailabilitySlot`は同一曜日に複数登録でき、日付を跨ぐslotは2日に分割して保存する。
- 移動、着替え、準備、シャワー等に必要なbufferを任意設定できるようにし、運動時間と生活上の占有時間を区別する。
- 一時的な予定変更は通常設定を上書きせず、日付指定overrideとして保持する。
- timezone変更時は過去計画の日付を変換せず、新規計画から新timezoneを使用する。
- 天候連携は地域情報の利用へ同意したユーザーだけを対象とする。正確なGPS位置ではなく市区町村等の粗い地域を使い、取得元、取得時刻、予報の不確実性を記録する。
- 天候予報だけで週間計画を確定的に変更せず、代替環境を事前に計画へ含め、実施日に再判定する。

## ユーザーの希望

ユーザーの希望は、継続的な好みと日付指定の希望に分ける。

継続的な好みの例:

- 土曜日は長めに走りたい
- ウェイトを週2回入れたい
- ランニングとバイクを組み合わせたい
- 高負荷メニューは朝に実施したい

日付指定の希望の例:

- 次の土曜日はLSDを行いたい
- 水曜日は友人とバイクに乗る
- 日曜日は大会へ参加する
- 特定の日は予定があるため休みにしたい

週間計画の生成前に登録された希望は初回計画へ反映する。生成後に追加された希望はPlanChangeRequestとして扱い、変更案を提示する。

希望が安全性、体調、必要な回復期間と衝突する場合、AIは無条件に採用しない。実施できない理由と、時期、距離、時間、強度、運動環境を調整した代替案を提示する。

判断の優先順位は以下とする。

1. 痛み、体調、安全制約
2. 連続して確保できる時間と利用可能な運動環境
3. ユーザーの日付指定の希望
4. 目標達成に必要な負荷と回復
5. ユーザーの継続的な好み

### 好みの学習ルール

- `explicit`: ユーザーが設定した好み。AI推定より常に優先する。
- `inferred`: 実施、変更依頼、承認、拒否、未達理由から推定した候補。根拠event、信頼度、作成日時、最終確認日時を持つ。
- 1回の未実施や予定都合だけから「その運動が嫌い」と推定しない。
- 痛み、体調不良、天候、施設都合による変更は嗜好学習へ直接使用しない。
- 推定した好みは一定期間で減衰させ、計画へ強く反映する前にユーザーへ確認できるようにする。
- センシティブな属性や医療状態を嗜好として推定しない。
- ユーザーはWeb設定画面で推定内容と根拠を確認し、採用、修正、削除できる。

### 目標の扱い

現在の自由記述目標は維持しつつ、計画計算に必要な項目は任意の構造化fieldとして追加する。

- 大会種目、距離、開催日
- 目標タイムまたは完走目標
- 現在の基準記録と計測日
- 目標の優先度と対象期間

複数目標が競合する場合は、主目標、安全性、期限、ユーザーの日付指定希望の順で解決し、採用しなかった目標と理由もplan rationaleへ残す。履歴不足時は経験や能力を断定せず、保守的なcold-start計画と追加質問を使用する。

## LINEリッチメニューと週間計画画面

LINEリッチメニューに「週間計画」の入口を追加する。設定画面と同様に、所有者と有効期限を検証できる署名付きワンタイムURLまたは短期URLを発行する。

週間計画画面には以下を表示する。

- 月曜日から日曜日までのカレンダー
- 各メニューの種目、時間、距離、強度
- 使用する運動環境と器具
- ウォームアップ、メイン、クールダウン等の内容
- AIがそのメニューを選んだ理由
- 計画中、実施済み、一部達成、未達、変更済み等の状態
- 次の予定メニューの実施可能性と判断理由
- 変更前後の内容と計画version

画面から以下の操作を行えるようにする。

- このまま実施する
- メニュー変更を依頼する
- 休養へ変更する
- ユーザーの希望を追加する
- 一時的な予定や利用可能時間を変更する
- AIの変更案を承認する
- 変更案を拒否する
- 別案を依頼する

計画画面の参照と変更は権限を分ける。参照用の短期sessionだけではactive planを変更できないようにし、計画承認とrevision承認は対象user、plan ID、version、decision、有効期限を署名へ結び付ける。Web更新はOrigin、session、所有者、expected revisionを検証する。

初期実装では既存リッチメニュー画像や座標を同時変更せず、テスト用導線または既存領域の案内から週間画面を開く。正式な入口の領域割当と画像変更は、週間画面が安定してからRMの別変更として扱う。

## 日々の評価と次回判断

Activity取得または手動登録後に、計画と実績を照合する。

- 実施時間、距離、強度、種目
- pace、心拍、cadence、標高、区間負荷
- 使用した運動環境
- 完了、一部完了、未実施
- 体調、疲労、違和感、痛み
- ユーザーが対話で説明した理由

評価結果は、客観的な実績要因、体調由来の要因、対話由来の要因に分ける。AIはこれらを基に次の予定メニューの実施可能性を評価する。

次の予定メニューについて以下のいずれかを提示する。

- 予定どおり実施可能
- 時間、距離、強度を下げれば実施可能
- 別の運動環境や種目へ変更
- 休養を推奨
- 判断に追加情報が必要

AIが計画変更を必要と判断しても、自動的に確定しない。ユーザーが変更案を受け入れるかをLINEまたは週間計画画面で確認する。

### 評価単位とタイミング

- ActivityごとにWorkoutReconciliationとWorkoutReviewを作る。
- ReadinessはActivity単位ではなく、ユーザーのローカル日付と「次の予定メニュー」単位で最大1つのactive assessmentを持つ。
- 同日に複数Activityがある場合、各Reviewを保存した後、短いgrace periodを置いてReadinessを再計算する。
- 遅れてActivityが到着した場合は過去assessmentを上書きせず、新しいassessment revisionを追加する。
- Activityがない場合も、予定終了時刻とgrace period後に`not_performed`候補を作り、同期漏れ、予定変更、未実施のどれかをユーザーへ確認する。
- 朝夕の分割メニューは同じ`workout_lineage_id`へ複数のPlannedWorkoutまたは複数Activityを関連付け、全体と各部分を分けて評価する。
- 体調回答が未完了の場合は推測で補わず、`needs_information`または安全側の暫定判定にする。

### Readinessの状態

- `as_planned`: 予定どおり実施可能
- `with_adjustment`: 時間、距離、強度、種目、環境等の調整案あり
- `blocked`: 決定論的な安全制約により元メニューを実施可能としない
- `needs_information`: 判断に必要な体調、予定、同期状況等が不足

各assessmentは対象PlannedWorkout、参照Review、ルールversion、AI model、prompt version、根拠code、表示用理由、入力snapshot digestを保持する。

## 柔軟な計画変更

変更理由として少なくとも以下を扱う。

- 雨、暑さ、強風等の天候
- 疲労、違和感、痛み、体調不良
- 急な予定、時間不足、睡眠不足
- 施設、器具、運動環境が利用できない
- メニューが難しすぎる、または軽すぎる
- ユーザーが別の運動を希望する
- その他の自由入力

変更対象は「翌日だけ」と「残りの週全体」を区別する。過去の日付と過去versionは変更せず、新しいTrainingPlanVersionを追加してactive pointerを切り替える。

計画変更承認は、既存のStrava Description更新承認や将来の外部公開承認とは別domainにする。対象計画、version、所有者、変更内容、有効期限を署名へ結び付け、明示的な承認だけを有効とする。

変更案は少なくとも`翌日のみ`、`指定日以降`、`残りの週全体`を区別する。承認済み変更を適用する際は、過去日と開始済みメニューを固定し、未来メニューだけを新versionへ複製・変更する。

承認、拒否、再提案は同じbuttonの二重押下、期限切れ、旧version、所有者不一致、Task再送を冪等に処理する。再提案は既存案の上書きではなく新しいproposal revisionとする。

## Activityの取得元

すべての運動を共通Activityとして分析する一方、取得元を明示する。

- `strava`: ウォッチ等からStravaへ連携されたランニング、バイク等
- `line_manual`: LINEリッチメニューから登録し、確認後にStrava Manual Activityとして作成するウェイト、自宅トレーニング等

手動Activityでは以下を登録できるようにする。

- 実施日時
- 種目
- 実施時間
- 主観的な強度
- セット、回数、重量または自由入力の内容
- 使用した運動環境と器具
- 完了状態
- コメント
- 対応するPlannedWorkout

Strava Activityと手動Activityは同じWorkoutReconciliation、WorkoutReview、次回Readiness評価へ接続する。センサー情報がない手動Activityについて、心拍や消費量を推測で補完しない。

Activityは少なくとも`source_type`、`source_activity_id`、`user_id`、任意の`provider_athlete_id`を持つ共通境界へ拡張する。providerのIDだけをアプリ内の所有者IDとして使用しない。

### 計画と実績の照合規則

照合の優先順位:

1. ユーザーが手動Activity登録時にPlannedWorkoutを指定した明示link
2. 既に確認済みの照合
3. 種目、予定時間帯、実施時刻、距離、時間から作る高信頼度の自動照合
4. 複数候補や低信頼度の場合は未確定候補としてユーザーへ確認

追加要件:

- matcherの閾値と種目mappingにversionを付ける。
- `matched/partial/unmatched`に加え、`ambiguous/unplanned/duplicate_candidate/not_performed`を表現できるようにする。
- 1つのActivityを複数PlannedWorkoutへ自動確定しない。
- splitを許可した計画だけ複数Activityとの対応を許可する。
- Stravaと手動Activityが同じ実績を表す可能性がある場合は重複候補として確認する。
- 遅延同期のgrace period内は未実施を確定しない。
- 自動照合結果はユーザーが修正でき、修正履歴を残す。

## データ構造

既存の計画基盤を利用する。

- `TrainingPlanVersion`: 目標snapshot、週開始日、version、変更理由、AI・prompt version
- `PlannedWorkout`: 日付、種目、時間、距離、強度、環境、安全制約
- `WorkoutReconciliation`: 計画とStrava／手動Activityの照合
- `WorkoutReview`: 達成状態、客観要因、体調・対話要因、次計画へのfeedback

追加するmodel:

- `WeeklyAvailability`: ユーザーのtimezone、週間設定version
- `AvailabilitySlot`: 曜日、開始・終了時刻、環境、屋外可否、分割可否
- `WorkoutPreference`: 継続的な種目・曜日・時間帯の好み
- `DatedWorkoutRequest`: 日付指定の希望、優先度、内容
- `PlanChangeRequest`: 変更理由、対象日、変更範囲、自由入力
- `NextWorkoutReadinessAssessment`: 次の予定メニューの実施可能性、根拠、安全flag、判定version
- `PlanRevisionProposal`: AIが提示した変更案と入力snapshot
- `PlanRevisionDecision`: 承認、拒否、再提案と所有者・期限

### 追加modelの推奨field

`UserTrainingProfile`:

- `user_id`
- `timezone`
- `week_starts_on`
- `weekly_generation_local_time`
- `experience_level`（本人設定のみ）
- `notifications_enabled`、`quiet_hours`
- `version`、`updated_at`

`WeeklyAvailabilityVersion` / `AvailabilitySlot`:

- 不変な設定versionとactive pointer
- 曜日、開始・終了local time、最大運動時間、buffer
- environment IDs、屋外可否、split可否、固定休養
- 日付指定overrideと有効期間

`WorkoutPreference`:

- `preference_type`、構造化value、hard/soft
- `source=explicit/inferred`
- `confidence`、evidence event IDs、確認状態、有効期限

`PlannedWorkout` / `WorkoutPrescription`:

- `workout_lineage_id`、任意の`supersedes_planned_workout_id`
- 開始予定local time、占有時間、必須／任意
- 種目、目的、環境、代替環境、選定理由
- warm-up、main、cool-down等のphase配列
- duration、distance、pace、heart rate、power、RPE等の範囲
- interval、rest、set、rep、load等の種目別詳細
- 達成判定条件と安全制約

`WorkoutReconciliation`:

- candidateと確定結果を分離
- `match_confidence`、matching evidence、matcher version
- 複数Activity／split group、手動訂正、訂正理由

`PlanRevisionProposal` / `PlanRevisionDecision`:

- base plan ID/version、対象範囲、変更前後diff
- readiness ID、change request ID、安全flag
- owner、revision、expires_at、署名対象decision
- approval event ID、決定時刻、冪等key

## 安全性と監査

- 最大心拍等の本人設定がない状態で絶対的な生理負荷を推定しない。
- 痛みや悪化した違和感がある場合は、AIの希望より決定論的な安全ルールを優先する。
- AIの入力snapshot、model、prompt version、ルールversion、変更理由を保存する。
- AIが提示した案と、ユーザーが承認した最終内容を分離する。
- GPS座標、生stream、route hashを週間計画生成やWeb画面へ渡さない。
- 計画、実績、評価、変更、承認を相互に追跡可能にする。

### 決定論的な安全ゲート

AI実行前に制約を作り、AI出力後にも同じ制約で検証する。少なくとも以下をversion付きルールとして扱う。

- 痛み、運動中の悪化、強い違和感
- 連続する高負荷日と最低回復間隔
- 週間時間・距離・負荷の増加上限
- 長時間メニュー、初心者、長期中断後の上限
- 大会前のtaperと大会後の回復
- 利用可能時間、環境、固定休養日との矛盾
- 欠損データを高い能力や回復として扱わない

最大心拍、心拍zone、消費カロリー、回復時間等は本人設定や根拠データがない場合に推測しない。AI出力が不正、timeout、安全制約違反の場合は、最後に承認された安全な計画または決定論的fallbackを使う。

安全上の`blocked`は医療診断ではなく「このアプリでは元メニューを実施可能と判定しない」という意味に限定し、症状の継続・悪化時は専門家への相談を案内する。

### AI入力と個人情報

- 自由記述は命令ではなくuser dataとして明確に区切り、prompt injectionでsystem ruleを変更できないようにする。
- AI input snapshotには生の健康自由記述を無制限に複製せず、必要なコード、短期参照、digest、source IDを優先する。
- BigQueryの不変履歴と削除要求を両立する保持・削除方針をDS-01で定義する。
- LINE user IDやprovider IDを分析用途へ不要に複製しない。
- application logへ目標本文、症状本文、予定、嗜好の自由記述を出さない。

## 障害、再送、運用

- Cloud Schedulerは起動だけ、対象者展開とAI生成はCloud Tasks workerで行う。
- `generation/reconciliation/review/readiness/revision`ごとにstage状態と安定した冪等keyを持つ。
- BigQueryは不変履歴、Firestoreはactive pointer、draft、承認、Task進行状態を保持する。
- 外部APIやAIの失敗後にTaskが再送されても、承認済み計画、LINE通知、Reviewを重複作成しない。
- LINE送信には安定retry keyを設定し、「保存済みだが通知失敗」と「未保存」を区別する。
- AI生成失敗時は既存active planを失効させない。
- DLQ、Task滞留、生成失敗率、承認待ち滞留、照合不能率、安全fallback率を監視対象にする。
- provider障害中はActivityを未実施と断定せず、同期状態不明として扱う。

## 成功指標

速度や距離の改善だけを成功条件にしない。初期段階では以下を観測する。

- 週間計画の提示率、承認率、期限切れ率
- PlannedWorkoutの実施、一部実施、予定変更、未実施の割合
- 自動照合率、曖昧照合率、ユーザー訂正率
- 変更案の承認、拒否、再提案率
- 「難しすぎる」「軽すぎる」のfeedback比率
- 安全ルール発動後に高負荷案をactiveにしなかった割合
- 計画画面の継続利用率
- AI失敗時のfallback成功率と重複通知率

## 段階的な実装順

### PL-01A ドメイン契約と設定基盤

実装済み。実装model、Firestore/BigQuery境界、旧Planning Foundationからのmigration方針は`docs/next-session-pl-01a.md`を参照する。

- user主体、timezone、週境界を定義する。
- plan lifecycle、安全ゲート、照合状態のenumと遷移を実装する。
- WeeklyAvailability、AvailabilitySlot、WorkoutPreference、DatedWorkoutRequestを実装する。
- PlannedWorkoutを不変な処方recordへ整理し、実施状態を分離する。
- Firestore active stateとBigQuery不変履歴の境界、冪等keyを定義する。

### PL-01B 週間計画生成

実装済み。手動shadow worker、AI入出力、安全検証、fallback、監査項目は`docs/next-session-pl-01b.md`を参照する。

- 手動起動・shadow modeで翌週計画案を生成する。
- 構造化AI出力、決定論的な事前制約・事後検証、fallbackを実装する。
- まだ自動Schedulerや本番通知は有効にしない。

### PL-01C 週間計画画面と初回承認

実装済み。署名付きワンタイムWeb導線、承認状態CAS、lifecycle eventとactive pointerの不変性は`docs/next-session-pl-01c.md`を参照する。

- 署名・期限付きWeb画面で7日分、理由、安全制約、version差分を表示する。
- 計画全体の承認、拒否、再提案を実装し、承認時だけactive pointerを切り替える。

### MA-01 共通手動Activity

実装済み。LINE会話登録、Strava Manual Activity作成、途中保存、TTL、冪等保存は`docs/next-session-ma-01.md`を参照する。

- ウェイト、自宅トレーニング等をStrava上のActivityとして登録し、共通Activity境界へも保存する。
- PlannedWorkoutの明示選択、cancel、TTL、Task再送冪等性を持たせる。
- 翌日提案の「投稿」は作成したStrava ActivityのDescriptionへ追記する。

### PL-01D 実績照合

実装済み。共通matcher、候補・確定履歴、LINE訂正、未実施確認、split・重複制御は`docs/next-session-pl-01d.md`を参照する。

- Stravaと手動Activityを共通matcherへ接続する。
- split、計画外、曖昧候補、遅延同期、未実施確認、手動訂正を実装する。

### PL-01E Workout ReviewとReadiness

- Activity単位Reviewと次の予定メニュー単位Readinessを分ける。
- 同日複数Activity、体調未回答、安全block、追加質問を扱う。
- 既存の単発翌日提案をこの経路へ段階的に接続する。

### PL-01F 承認付き再計画

- 翌日のみ、指定日以降、週全体の変更案をversion化する。
- 承認、拒否、再提案、期限切れ、旧version、二重押下を実装する。

### NT-01 定期実行と運用

- 土曜日のtimezone別Scheduler fan-outを有効にする。
- 通知、quiet hours、再通知上限、DLQ、Dashboard、Alertを実装する。

## 必須テスト観点

- timezone、週境界、日付跨ぎ、夏時間、timezone変更
- 初回、履歴不足、週途中開始、未承認、期限切れ
- 大会日あり、期限なし、競合する複数目標
- 空き時間不足、環境不足、固定休養、split可否
- 同日複数Activity、計画外、複数候補、遅延同期、重複、手動訂正
- Activityなし、体調未回答、予定変更、provider障害
- 痛み、悪化、連続高負荷、負荷急増、AI安全違反
- AI timeout、不正JSON、保存途中失敗、Task再送
- 承認所有者不一致、署名改ざん、旧version、二重押下
- 過去version不変、active pointer競合、同一operationの冪等性
- GPS、生stream、route hash、健康自由記述がAI入力・Web・logへ漏れないこと

## 全体の到達目標

- ユーザーの生活時間と運動環境に収まる週間計画を作成できる。
- ユーザーが希望するメニューを、安全性と回復を損なわない範囲で反映できる。
- Strava自動取得とLINE手動登録の両方を計画実績として評価できる。
- 日々の結果から次の予定メニューを再評価し、必要な変更をユーザーの承認後に反映できる。
- 週間計画、実績、未達理由、AI判断、ユーザー決定が次週の計画へ継続的につながる。

## 次セッション用プロンプト（PL-01A）

```text
AI-COACHリポジトリで、PL-01A「週間計画ドメイン契約・稼働可能時間・嗜好基盤」を実装してください。

対象リポジトリ:
\\wsl.localhost\Ubuntu-24.04\home\kansei\AI\AI-COACH

最初に次のファイルと関連コードを確認してください。

- AGENTS.md
- CODEX.md
- docs/weekly-training-plan-vision.md
- docs/feature-session-roadmap.md
- docs/architecture.md
- docs/rich-menu-plan.md
- app/planning.py
- app/runtime.py
- app/main.py
- app/profile.py
- app/condition.py
- app/coaching.py
- app/domain/models.py
- tests/test_planning.py
- infra/terraform
- infra/bigquery/schema.sql
- .github/workflows

目的:
週間計画をAI生成する前段として、user主体、timezone、週間稼働可能時間、明示的／推定嗜好、計画状態遷移、安全ゲート、実施状態のドメイン契約と保存基盤を実装する。

今回の対象:

1. app userを主体とするUserTrainingProfileとIANA timezone
2. version付きWeeklyAvailabilityと複数AvailabilitySlot
3. 日付指定override、固定休養日、buffer、環境、屋外可否、split可否
4. explicit/inferredを区別するWorkoutPreferenceとevidence・confidence・有効期限
5. DatedWorkoutRequest
6. TrainingPlan lifecycle（generating/draft/pending_approval/active/rejected/expired/generation_failed/superseded）
7. PlannedWorkoutを不変な処方recordとし、実施状態を別modelへ分離
8. Readinessのas_planned/with_adjustment/blocked/needs_informationと決定論的SafetyGateResult
9. BigQueryの不変履歴とFirestoreのactive pointer・編集stateの責務分離
10. 安定ID、version競合、Task再送を想定した冪等性
11. 必要なTerraform、BigQuery schema、日本語ドキュメント、単体テスト

決定事項:

- 計画所有者はStrava athleteではなくapp user。athlete_idは任意のprovider linkとする
- 週はユーザーtimezoneの月曜始まり、保存timestampはUTC
- AI案は承認前にactiveにしない
- 安全blockedは変更案を拒否しても解除しない
- PlannedWorkoutは不変。completed/skipped/replacedはexecution/reconciliationから導出する
- explicit preferenceをinferredより優先する
- 自由記述、健康情報、GPS、生stream、route hashをlogへ出さない
- Webhookの署名検証、重複排除、Cloud Tasks enqueue、即時応答経路を変更しない
- Strava更新や外部公開処理を追加しない

対象外:

- Vertex AIによる週間計画生成（PL-01B）
- 週間計画Web画面と承認（PL-01C）
- 手動Activity登録（MA-01）
- Activity自動照合（PL-01D）
- 日次Workout ReviewとReadiness実行（PL-01E）
- 計画変更承認UI（PL-01F）
- Cloud Scheduler、通知、本番有効化（NT-01）
- リッチメニュー画像・座標変更
- デプロイ、秘密値追加、本番データmigration

進め方:

1. mainを最新化する
2. feat/pl-01a-training-profile-foundationブランチを作成する
3. 現行Planning Foundationとの互換性とmigration方針を確認する
4. 実装計画、state transition、data modelを提示する
5. PL-01Aを実装する
6. timezone、週境界、DST、version競合、重複、期限、明示／推定嗜好、安全block、旧model互換をテストする
7. lint、全test、compileall、Terraform fmt/validateを実行する
8. 変更内容、data model、冪等性、migration、未実装部分、手動確認事項を日本語で報告する
9. PRを作成する

デプロイ、mainへのマージ、本番リソース作成、本番データmigration、秘密値の追加・変更は、私が明示承認するまで行わないでください。
```
