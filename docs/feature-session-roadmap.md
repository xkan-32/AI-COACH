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
- Strava OAuth、Webhook、Activity取得、重複排除
- LINE Webhook、体調4択、詳細入力、遅延返信用会話state
- Vertex AI構造化提案と安全制約
- 署名・期限付き承認、冪等なDescription更新
- 目標・運動環境のテキストコマンド

未実装・拡張対象:

- リッチメニュー
- Laps/Streamsと詳細指標
- 手動Activity、体重
- 長期・週間・日次計画
- 7日/30日負荷、連続症状分析
- 提案修正、進捗表示、通知設定
- データ保持・削除、監視、DLQ、総合障害試験

## 推奨順序

| 順序 | ID | 機能 | 依存 |
|---:|---|---|---|
| 1 | RM-01〜04 | LINEリッチメニュー | 現行LINE worker |
| 2 | PF-01 | 目標・プロフィールUI | リッチメニュー |
| 3 | AC-01 | Activity/Laps/Streams | Strava取得 |
| 4 | MA-01 | 手動Activity | Activityモデル、メニュー |
| 5 | WT-01 | 体重記録 | メニュー、データ基盤 |
| 6 | PL-01 | 長期・週間・日次計画 | 目標、Activity、体重 |
| 7 | AN-01 | 負荷・回復・症状分析 | 詳細履歴、計画 |
| 8 | AI-01 | AI評価・提案拡張 | 分析、計画 |
| 9 | AP-01 | 修正・承認UX | AI提案、承認基盤 |
| 10 | NT-01 | 通知・リマインド | 設定、Scheduler/Tasks |
| 11 | PR-01 | 進捗・週間レビュー | 計画、分析、体重 |
| 12 | DS-01 | 同意・保持・削除 | 全store |
| 13 | OP-01 | 監視・Alert・DLQ | 全worker |
| 14 | E2E-01 | 総合E2E・障害試験 | 全MVP |

リッチメニューは`docs/rich-menu-plan.md`を参照する。

## 各セッションの要件

### PF-01 目標・プロフィール・運動環境

- LINEから一覧、追加、変更、無効化を行う。
- 主目標1件、副目標複数、期限なし、器具詳細を扱う。
- 入力stateにTTL、cancel、重複排除を設ける。
- 次回AI提案へ最新値が反映されるtestを作る。

### AC-01 Activity/Laps/Streams

- Run、Ride、WeightTraining、Workout、Walk等を共通Activityとして扱う。
- 必要なLaps/Streamsをrate limit内で取得する。
- 生データと派生指標を分離し、再計算可能にする。
- 位置情報の保存要否・精度・保持期間を明示する。
- Webhook再送と部分失敗を冪等に再試行する。

### MA-01 LINE手動Activity

- 種別、日時、時間、強度、内容、commentを会話形式で登録する。
- `planned/completed/replaced/skipped`を扱う。
- 入力途中保存、cancel、TTL、再開を実装する。
- Strava Manual Activity作成は別の明示承認を必須にする。

### WT-01 体重

- 日付と体重の登録・訂正、7日/30日平均、目標差を実装する。
- 単位、妥当範囲、同日訂正ruleを定義する。
- 生値と集計値を分け、健康情報をlogへ出さない。

### PL-01 計画

- 長期、週間、日次の階層とversion、変更理由を保持する。
- 大会日あり、期限なし、複数目標の優先ruleをtestする。
- 再計画でも過去versionを上書きしない。
- AI失敗時は最後の安全な計画を利用する。

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
