# 機能別Codexセッション ロードマップ

## 目的

AIトレーニングコーチを1機能・1セッション・1PRで構築するための引き継ぎ資料である。各セッションは`AGENTS.md`、`CODEX.md`、本書、関連文書とコードを最初に読む。

## 共通ルール

1. `main`から`feat/<session-id>-<topic>`ブランチを作る。
2. Webhookは認証、正規化、enqueue、即時応答だけを行い、外部処理はCloud Tasks workerで行う。
3. 分析履歴はBigQuery、会話状態・冪等性・OAuth・承認状態はFirestoreへ置く。
4. 明示的で有効な本人承認なしにStravaを変更しない。
5. AIの前後に決定論的な安全ルールを適用する。
6. GCP、IAM、API、設定はTerraformで管理する。
7. Secret、token、`.env`、tfstate、実健康データをcommitしない。
8. 挙動変更にはtestを追加し、CIとTerraform validateを通す。
9. deploy、cloud resource変更、秘密値登録はユーザーの明示承認後に行う。

## 現在の実装

```text
Strava Webhook -> Cloud Tasks -> Activity取得・保存
 -> LINE体調確認 -> LINE worker -> Vertex AI提案
 -> LINE明示承認 -> Cloud Tasks -> Strava Description追記
```

実装済み:

- Terraform、WIF、GitHub Actions、Cloud Run、Cloud Tasks、Firestore、BigQuery、Secret Manager
- Strava OAuth、Webhook、Activity取得、Activity create再送の安定event keyによる重複排除
- Strava tokenのAES-256-GCM暗号化、OAuth session期限検証、一時FirestoreデータのTTL
- Strava Webhookの`200 OK`応答と、Activity取得失敗時の秘密情報を含まない安全ログ
- LINE Webhook、体調4択、詳細入力、遅延返信用会話state
- Vertex AI構造化提案と安全制約
- 署名・期限付き承認、冪等なDescription更新
- 目標・運動環境のテキストコマンド
- LINEリッチメニュー（RM-01〜04: UI、冪等同期、action router、CI/CD同期）
- PF-01（構造化された目標・運動環境、会話UI、署名付きWeb設定ページ、AI context連携）
- AC-01（Activity詳細、GPS非保存のLaps/Streams、versioned派生指標、直近履歴のAI context連携）
- Planning Foundation（週間version、日次ID、実績照合・Review、公開下書き・専用承認domain）
- AC-02（GPS非保存の250m区間負荷、HMAC route fingerprint、同一ルート比較）

未実装・拡張対象:

- 状態別リッチメニュー（RM-05）
- 手動Activity、体重
- 長期・週間・日次計画
- 7日/30日負荷、連続症状分析
- 提案修正、進捗表示、通知設定
- データ保持・削除、監視、DLQ、総合障害試験

## 推奨順序

| 順序 | ID | 機能 | 依存 |
|---:|---|---|---|
| 完了 | RM-01〜04 | LINEリッチメニュー | 現行LINE worker |
| 完了 | PF-01 | 目標・プロフィールUI | リッチメニュー |
| 完了 | AC-01 | Activity/Laps/Streams | Strava取得 |
| 完了 | AC-02 | 区間負荷・同一ルート比較 | AC-01 |
| 1 | MA-01 | 手動Activity | Activityモデル、メニュー |
| 2 | WT-01 | 体重記録 | メニュー、データ基盤 |
| 3 | PL-01 | 長期・週間・日次計画 | 目標、Activity、体重 |
| 4 | AN-01 | 負荷・回復・症状分析 | 詳細履歴、計画 |
| 5 | AI-01 | AI評価・提案拡張 | 分析、計画 |
| 6 | AP-01 | 修正・承認UX | AI提案、承認基盤 |
| 7 | NT-01 | 通知・リマインド | 設定、Scheduler/Tasks |
| 8 | PR-01 | 進捗・週間レビュー | 計画、分析、体重 |
| 9 | DS-01 | 同意・保持・削除 | 全store |
| 10 | OP-01 | 監視・Alert・DLQ | 全worker |
| 11 | E2E-01 | 総合E2E・障害試験 | 全MVP |

リッチメニューは`docs/rich-menu-plan.md`を参照する。

## 各セッションの要件

### PF-01 目標・プロフィール・運動環境（完了）

通常導線はリッチメニューの「設定」から開く署名・期限付きWeb設定ページである。「目標」は有効目標の一覧表示専用とし、旧会話UIとテキストコマンドは後方互換経路として維持する。実装記録は`docs/next-session-pf-01.md`を参照する。

- LINEから一覧、追加、変更、無効化を行う。
- 主目標1件、副目標複数、期限なし、器具詳細を扱う。
- 「利用可能な運動環境」と「実施したActivity」を分離する。PF-01は利用可能な場所・種目・器具の設定だけを扱い、実施記録はMA-01で扱う。
- 運動環境は自由入力文字列だけにせず、少なくとも次の正規化区分と表示名を持つ。
  - 場所・種目: 屋外ランニング、トレッドミル、屋外サイクリング、インドアバイク、プール、ジム、自宅トレーニング
  - 器具: 自重、ダンベル、バーベル、ケトルベル、マシン、チューブ、ローラー台
- `ルームバイク`、`エアロバイク`、`フィットネスバイク`等の表記揺れは`インドアバイク`へ正規化する。`ローラー台`は自転車を使う器具として別項目にする。
- 未定義項目は詳細欄へ保持できるようにし、既知区分へ誤って変換しない。
- リッチメニューの`settings`から一覧・編集を開始できるpostback導線を追加し、未実装案内を置き換えた。
- 入力stateにTTL、cancel、重複排除を設けた。
- 次回AI提案へ最新値が反映されるtestを追加した。

完了条件:

- 既存のテキストコマンドとの後方互換性を保ち、構造化された運動環境を登録・一覧・変更・無効化できる。
- 表記揺れ、重複、空入力、上限、期限切れ、cancel、Webhook/Task再送をtestする。
- リッチメニューから開始した全導線が応答し、入力途中でも安全に中止・再開できる。
- AI coaching contextへ有効な最新設定だけが渡る。

PF-01の対象外:

- 実施した運動の登録、所要時間、強度、完了状態（MA-01）
- 体重の登録・訂正・移動平均（WT-01）
- Strava Manual Activityの作成および承認

### AC-01 Activity/Laps/Streams（完了）

- Run、Ride、WeightTraining、Workout、Walk等を後方互換な共通Activityとして扱う。
- Run、Walk、Ride系はLapsとGPSを除くStreamsを取得し、非対応種目はsummaryで処理する。
- 生streamと`computation_version`付き派生指標を分離し、再計算可能にした。
- GPS座標は要求・保存・ログ・AI送信を行わない。
- Webhook／Task再送時の保存とLINE体調確認を冪等化し、直近Activity・Conditionと派生指標をAI contextへ渡す。

### AC-02 区間負荷・同一ルート比較（完了）

- GPS非依存streamを250m区間へ集約し、相対負荷rankと根拠コードを保存する。
- route識別時だけworkerメモリ内で座標を処理し、HMAC化後は座標とcanonical点列を破棄する。
- 同一routeの過去2件以上を基準にpace、心拍、cadence差分を作成し、安全なsummaryだけをAI contextへ渡す。

### MA-01 LINE手動Activity

- 種別、日時、時間、強度、内容、commentを会話形式で登録する。
- `planned/completed/replaced/skipped`を扱う。
- 入力途中保存、cancel、TTL、再開を実装する。
- Strava Manual Activity作成は別の明示承認を必須にする。

### WT-01 体重

- 日付と体重の登録・訂正、7日/30日平均、目標差を実装する。
- 単位、妥当範囲、同日訂正ruleを定義する。
- 生値と集計値を分け、健康情報をlogへ出さない。

体重は運動環境ではなく健康記録であるため、PF-01へ含めない。リッチメニューへ体重導線を追加する場合は、WT-01で保存・訂正・表示・privacy要件まで一体で実装する。

### PL-01 計画

週間計画、Activity実績、Workout Review、次の予定メニューのReadiness、承認付き再計画をアプリの中心feedback loopとする。詳細な設計原則、状態遷移、data model、実装段階、必須テストは`docs/weekly-training-plan-vision.md`を正本とする。

- 長期、週間、日次の階層とversion、変更理由を保持する。
- 大会日あり、期限なし、複数目標の優先ruleをtestする。
- 週間version、goal snapshot、日次メニュー、active pointerの基盤は実装済み。PL-01ではAI生成と長期計画を接続する。
- 再計画でも過去versionを上書きしない。
- AI失敗時は最後の安全な計画を利用する。
- 実装はPL-01A（ドメイン契約・設定基盤）、PL-01B（週間生成）、PL-01C（計画画面・初回承認）、PL-01D（実績照合）、PL-01E（Review・Readiness）、PL-01F（承認付き再計画）へ分割する。
- 計画の所有主体はapp userとし、Strava未連携・手動Activityのみでも利用可能にする。
- 安全上`blocked`となった元メニューは、ユーザーが代替案を拒否しても実施可能扱いへ戻さない。

### AN-01 負荷・回復・症状分析

- 7日/30日、前週比較、高強度後の休養、同一部位の連続申告を計算する。
- 式、timezone、週境界、欠損値を文書化する。
- 痛み、増悪、負荷急増時の禁止flagをAI前に決定論的に作る。

### AI-01 AI評価・提案拡張

- 今日の評価、目標上の位置付け、翌日提案、代替案、安全注意、Strava記載案をschema化する。
- REST、RECOVERY、AEROBIC、RUN_QUALITY、STRENGTH、MOBILITYから選ぶ。
- prompt version、model、入力snapshot、出力、安全補正を監査可能にする。
- timeout、不正JSON、安全違反時に安全なfallbackを返す。

### AP-01 提案修正・承認UX

- 「投稿」「修正」「投稿しない」を提供する。
- 修正は新proposal versionとして再承認し、旧buttonを無効化する。
- 二重押下、期限切れ、旧version、所有者不一致を安全に処理する。
- 既存Descriptionとユーザー記述を保持する。

### NT-01 通知・リマインド

- 体調未回答、計画確認、週間レビューを希望時刻に通知する。
- on/off、timezone、quiet hoursを管理する。
- Schedulerは起動だけ、対象展開・送信はTasksで行い、重複送信を防ぐ。

### PR-01 進捗・週間レビュー

- 目標までの日数、週間達成、活動量、体重trendをLINEへ表示する。
- 集計根拠を追跡可能にし、データ不足時は断定しない。
- LINE文字数・Flex Message制約を考慮する。

### DS-01 同意・保持・削除

- 同意、利用停止、export、削除要求を扱う。
- Firestore、BigQuery、Secret Managerの対象を追跡する。
- 削除を再実行可能にし、古いTaskによる再生成を防ぐ。

### OP-01 監視・Alert・DLQ

- activity/proposal IDでWebhook、Task、LINE、Vertex、Stravaを追跡する。
- Task滞留、連続失敗、API error率を監視する。
- DashboardとAlert PolicyをTerraform管理する。
- logへsecretや健康情報本文を出さない。

### E2E-01 総合試験

- 実Activityから体調、AI、承認、Description更新まで確認する。
- 重複Webhook、遅延返信、Task再送、外部障害、二重押下、期限切れを試験する。
- 痛み時の安全制約と、未承認時にStrava不変であることを確認する。
- 実データをrepoやCI logへ残さず、rollback手順を記録する。

## 新しいCodexセッション用テンプレート

```text
AI-COACHリポジトリで <Session IDと機能名> を実装してください。

最初に AGENTS.md、CODEX.md、docs/feature-session-roadmap.md、
docs/rich-menu-plan.md（LINE関連の場合）、関連コードとTerraformを読んでください。

対象:
- <今回実装する範囲>

対象外:
- <次セッションへ回す範囲>

要件:
- Webhookは即時応答し、外部処理はCloud Tasksで行う
- BigQuery/Firestoreの責務分離を守る
- 明示承認なしにStravaを変更しない
- AIの前後に決定論的安全ルールを適用する
- GCP変更はTerraform管理する
- testと日本語文書を更新する

作業:
1. 現状と不足を確認
2. 実装計画を提示
3. 機能branchで実装
4. test、lint、Terraform validate
5. 変更、未解決事項、手動作業を日本語で報告

deployや秘密値登録は、私が明示承認するまで行わないでください。
```

## セッション完了時に残す情報

- 実装範囲と対象外
- data model、API、postback、Task payload
- Terraform resourceとIAM
- 冪等性key、TTL、retry
- security・安全rule
- test結果とE2E手順
- 手動設定、既知制約、次sessionへの依存
- rollback方法
