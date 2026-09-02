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
| リッチメニュー画像・領域 | RM-01実装済み | 2500×1686 PNG、編集用SVG、6領域JSON |
| Rich Menu API同期 | RM-02実装済み | content hashによる冪等同期、dry-run、失敗時cleanup |
| メニューaction処理 | RM-03実装済み | Cloud Tasks worker内で6領域を案内へrouting |
| CI/CD・IaC連携 | RM-04実装済み | Terraform apply成功後に本番メニューを冪等同期 |
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

## RM-01〜RM-03 実装仕様

- 画像サイズはLINEのlarge rich menuに合わせて2500×1686pxとする。
- 列境界は`x=833`、`x=1666`、行境界は`y=843`とし、端数は右列へ割り当てる。
- `assets/line-rich-menu/rich-menu-v1.source.svg`を編集用ソース、同名PNGを配布画像とする。
- `config/line-rich-menu/rich-menu-v1.json`を領域・actionの正本とする。
- 同期時の管理名はJSONとPNGのSHA-256から決定する。同じ内容なら既存menuを再利用する。
- 更新時は新menuの作成と画像uploadを完了してから既定化し、既定化後に本実装が管理する旧版だけを削除する。
- 画像upload失敗時は作成途中のmenuを削除する。削除にも失敗した場合はエラーとして再試行対象にする。
- 他用途のrich menuは名前prefixが異なるため削除しない。

同期予定の確認（LINE APIへの読み取りは行うが、変更は行わない）:

```bash
export LINE_CHANNEL_ACCESS_TOKEN="$(安全な秘密ストアから取得)"
python3 scripts/sync-line-rich-menu.py --dry-run
```

実同期は`--dry-run`を外す。本番tokenをコマンドライン引数、設定JSON、ログ、Terraform変数へ渡してはならない。
GitHub Actionsでは本番deploy用WIFでSecret Managerからtokenを実行時に取得し、maskして同期processだけへ渡す。GitHub SecretsやTerraform stateへtokenを複製しない。同期はTerraform apply成功後に実行し、APIエラー時はdeploy workflowを失敗させる。

## 初回反映の手動手順

本番LINEアカウントへの反映自体は、次の準備とmainへのmergeによって行う。token値をIssue、PR、GitHub Actions variable、Terraform tfvars、shell履歴へ記録しないこと。

1. LINE Developers Consoleで対象Messaging API channelのChannel Access Tokenを発行する。
2. ローカル端末からtokenを標準入力でGCP Secret Managerへ追加する。

   ```bash
   gcloud secrets versions add line-channel-access-token --data-file=-
   ```

   コマンド実行後にtokenを貼り付け、Ctrl-Dで確定する。
3. production GitHub Environmentのrequired reviewer、main限定deployment rule、`GCP_PROJECT_ID`、`WIF_PROVIDER`、`WIF_SERVICE_ACCOUNT`等が`docs/bootstrap-and-cicd.md`どおり設定済みか確認する。
4. 本番変更前にローカルから読み取り専用dry-runを実行する。tokenは秘密ストアからshell変数へ一時取得し、コマンド終了後にunsetする。

   ```bash
   read -rsp "LINE Channel Access Token: " LINE_CHANNEL_ACCESS_TOKEN
   export LINE_CHANNEL_ACCESS_TOKEN
   python3 scripts/sync-line-rich-menu.py --dry-run
   unset LINE_CHANNEL_ACCESS_TOKEN
   ```

5. PRをmainへmergeする。`Deploy` workflowのproduction承認後、アプリ・Terraformの反映に成功した場合だけrich menu同期が実行される。
6. Actions logで`create`、`upload-image`、`set-default`を確認する。再実行時は`already-synchronized`となることを確認する。tokenそのものがlogに表示された場合は直ちにtokenを失効・再発行する。
7. LINEスマートフォンアプリでトークを開き直し、6領域の表示・境界・応答を確認する。PC版LINEではrich menuは表示されない。

rollbackする場合は、直前commitをrevertしてmainへmergeする。旧画像の定義が復元されればcontent hashが変わり、同じ同期手順で旧版を新規作成・既定化して現行版を削除する。

### メニュー押下時の動作

| target | RM-03での応答 |
|---|---|
| `today_proposal` | アクティビティ後の体調記録と最新提案メッセージを案内 |
| `condition` | 既存のアクティビティ後体調確認を案内 |
| `manual_activity` | 準備中を案内し、Stravaを直接更新しないことを明示 |
| `goals` | `目標確認`、`目標登録`コマンドを案内 |
| `progress` | 準備中と既存フィードバックを案内 |
| `settings` | `Strava連携`、`運動環境確認`コマンドを案内 |

メニューpostbackはLINE Webhookで署名検証・event重複排除後に既存Cloud Tasksへenqueueされ、workerで処理される。
提案承認postbackの署名、期限、所有者検証経路には入らず、メニュー押下だけでStrava更新Taskを作成しない。

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
