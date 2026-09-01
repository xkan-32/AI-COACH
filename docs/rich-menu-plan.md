# LINEリッチメニュー 実装状況と計画

## 現在の実装状況

現時点では、LINE Messaging APIとのメッセージ連携は動作しているが、LINEの常設「リッチメニュー」自体は未実装である。

| 項目 | 状態 | 実装内容 |
|---|---|---|
| LINE Webhook署名検証 | 実装済み | 不正なWebhookを拒否 |
| Webhook非同期・重複排除 | 実装済み | Cloud Tasksとevent IDを使用 |
| Strava連携 | 実装済み | `Strava連携`コマンドからOAuth |
| 体調確認 | 実装済み | Quick Replyで4択、必要時に詳細質問 |
| AI翌日提案 | 実装済み | 提案とButtons Templateを送信 |
| Strava投稿承認 | 実装済み | 署名・期限付きの投稿／投稿しない |
| 目標・運動環境 | 一部実装済み | テキストコマンドで登録・確認 |
| リッチメニュー画像・領域 | 未実装 | アセットと座標定義なし |
| Rich Menu API同期 | 未実装 | 作成、画像設定、既定化、削除なし |
| メニューaction処理 | 未実装 | 専用postback routerなし |
| CI/CD・IaC連携 | 未実装 | デプロイ時の同期処理なし |
| 状態別メニュー | 未実装 | 未連携／連携済みの切替なし |

Quick ReplyとButtons Templateは対話UIだが、画面下部に常設されるリッチメニューとは別機能である。

## MVPメニュー案

| 位置 | 表示 | action | 初期動作 |
|---|---|---|---|
| 上段左 | 今日の提案 | `today_proposal` | 最新の有効な提案を表示 |
| 上段中央 | 体調を記録 | `condition` | 日次体調入力を開始 |
| 上段右 | 運動を記録 | `manual_activity` | 手動Activity入力を開始 |
| 下段左 | 目標 | `goals` | 目標一覧・登録導線 |
| 下段中央 | 記録・進捗 | `progress` | 直近活動・週間進捗への入口 |
| 下段右 | 設定 | `settings` | Strava、運動環境、通知、データ管理 |

未実装機能を押した場合も無反応にせず、「準備中」と利用可能な既存コマンドを案内する。

postback dataは次の形式とし、個人情報や権限情報を埋め込まない。

```text
action=menu&version=1&target=today_proposal
action=menu&version=1&target=condition
action=menu&version=1&target=manual_activity
action=menu&version=1&target=goals
action=menu&version=1&target=progress
action=menu&version=1&target=settings
```

## 管理方針

- 画像、領域座標、action定義をGit管理する。
- Channel Access TokenはSecret Managerから取得し、ログやstateへ出さない。
- Rich Menu APIへの反映は冪等な同期スクリプトで行う。
- 更新時は新規作成、画像設定、既定切替、旧メニュー削除の順に行う。
- GitHub ActionsではCloud RunとTerraform apply成功後に同期する。
- リッチメニューは入口であり、Strava更新の明示承認を代替しない。

推奨構成:

```text
app/line_menu.py
assets/line-rich-menu/rich-menu-v1.png
assets/line-rich-menu/rich-menu-v1.source.*
config/line-rich-menu/rich-menu-v1.json
scripts/sync-line-rich-menu.py
tests/test_line_menu.py
tests/test_rich_menu_sync.py
```

## 機能別セッション

### RM-01 UI仕様と画像

- 6領域の文言、配色、アイコン、座標を確定する。
- LINE仕様に適合するPNG、編集可能な元データ、JSONを作る。
- 実機で可読性とタップ領域を確認する。

完了条件: 画像と領域定義がレビュー可能である。

### RM-02 Rich Menu API同期

- 作成、画像upload、既定設定、一覧、削除を実装する。
- 定義hash等で再実行を冪等化し、dry-runを設ける。
- APIエラー時もtokenをログへ出さない。

完了条件: 同じ同期を複数回実行してもメニューが重複しない。

### RM-03 action router

- LINE workerで`action=menu`を処理する。
- 6 targetすべてに必ず応答する。
- 既存のevent重複排除と遅延返信対応を維持する。

完了条件: 各領域のpostback testがあり、押下時に無反応にならない。

### RM-04 CI/CD統合

- Deploy workflowへ同期を追加する。
- 本番environmentの承認ルールを維持する。
- 同期失敗をデプロイ結果として検知可能にする。

完了条件: mainへのmergeでアプリとメニューが同一releaseとして更新される。

### RM-05 状態別メニュー（MVP後）

- Strava未連携用オンボーディングメニューを用意する。
- 連携済みユーザーへ通常メニューをlinkする。
- 解除・再連携・切替を冪等にする。

## ユーザー側で必要な確認

- デザイン文言、ブランドカラー、アイコン方針の決定。
- Channel Access TokenがSecret Managerに登録済みであること。
- LINE Official Account Managerと実機で表示・タップ領域を確認すること。

管理画面から毎回手作業で作る運用にはせず、初回確認以外は自動同期する。
