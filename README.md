# AI Training Coach

Strava、LINE、Vertex AI、Cloud Runを使い、利用可能時間・場所・器具・目標・実績に応じた週間トレーニング計画を扱うコーチアプリです。計画、実績、変更、評価は不変履歴として保存します。

## 現在の利用フロー

1. LINEの設定画面で目標、環境・器具、運動可能時間、練習メニュー候補を設定する
2. 「練習メニュー」から今日、今週の予定、最近の実績を確認する
3. Strava ActivityまたはLINE手動Activityを記録する
4. LINEで対応する予定を明示選択する（今日の予定、計画外、別日の予定、保留）
5. 必要時は「まとめて実施」として複数予定を同じActivityへ対応付ける
6. 体重・コンディションは必要な時にLINEから記録する。未入力は計画上「健康・影響なし」と扱う

## 実装状況

### 実装済み

- GCP基盤: Cloud Run、Cloud Tasks、Firestore、BigQuery、Terraform、WIF、GitHub Actions、Secret Manager
- Strava: OAuth、webhook、再送重複排除、token暗号化、Activity/Laps/非GPS Streams保存、区間負荷・同一ルート比較
- LINE: rich menu、Reply API中心の応答、設定画面・練習メニューへの署名付きURL、手動Activity、体重、任意の日次コンディション
- 計画基盤: 週間version、日次・slot単位の`PlannedWorkout`、可用時間、環境制約、嗜好、候補メニュー、AI週間生成、安全fallback、Readiness、再計画履歴
- 練習候補: ランニング、インドアバイク、自重トレーニング等の標準候補、カスタム候補の追加・編集・削除・標準復元、ペースの分・秒入力
- 実績照合: 自動確定を行わず、本人がLINEで予定を選択。計画外、別日の予定、保留、同一Activityへの複数予定の「まとめて実施」を扱う
- Activity評価: 確定した予定対応ActivityだけをCloud Tasksで評価し、計画対比・実績・心拍等の負荷・次回助言を不変履歴へ保存する。複数予定は数値配分せず共通実績を参照する
- Strava評価反映: 設定で有効な場合だけDescriptionのAI-COACH管理ブロックを冪等更新し、成功時だけLINE Push通知する。失敗状態はFirestoreに残す
- 取得不能Activity: Strava APIの404は恒久失敗として監視ログへ残し、Cloud Tasksの再試行を停止する。一時的なAPI障害は従来どおり再試行する
- 品質: pytest、ruff、Terraform validateをCIとデプロイ前に実行

### 未実装または未完成

- ユーザーtimezoneの日曜21:00に翌週計画を自動生成・active化し、LINE通知するScheduler/Cloud Tasks導線
- 週間画面からの日次・slot単位の直接編集、休養・取消・移動、AI代替案の保存UI
- 複数予定へ対応付けたActivityの距離・時間・心拍を予定別に配分する評価。現状は`combined_activity`として同じ実績を参照する
- 日次コンディションの同日訂正・履歴表示
- quiet hours、通知設定、DLQ、監視・アラート、データ削除・export、総合E2E

詳細な正本は [docs/line-app-activity-implementation-plan.md](docs/line-app-activity-implementation-plan.md)、機能別ロードマップは [docs/feature-session-roadmap.md](docs/feature-session-roadmap.md) を参照してください。

詳細は [docs/implementation-plan.md](docs/implementation-plan.md) と [docs/architecture.md](docs/architecture.md) を参照してください。 初回GCP/WIF/GitHub Actions構築は [docs/bootstrap-and-cicd.md](docs/bootstrap-and-cicd.md) に手順があります。

LINEリッチメニューの現在地と実装計画は [docs/rich-menu-plan.md](docs/rich-menu-plan.md)、機能別Codexセッションの実装順・完了条件・引き継ぎテンプレートは [docs/feature-session-roadmap.md](docs/feature-session-roadmap.md) を参照してください。
完了したPF-01の設計判断と実装記録は [docs/next-session-pf-01.md](docs/next-session-pf-01.md) にまとめています。AC-01、週間計画・実績評価・公開承認の基盤、PL-01Bの週間shadow生成、PL-01Cの週間計画画面と初回承認、MA-01のLINE手動Activity、WT-01の体重記録、PL-01Dの実績照合、PL-01EのWorkout Review・Readiness、PL-01Fの承認付き再計画、PL-01Gの練習メニューからの初回計画生成までコード上実装済みです。

## ローカル起動

Python 3.12を想定しています。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

```powershell
pytest
```

外部サービスの認証情報がなくても、`APP_ENV=local` ではヘルスチェックとWebhook検証のテストを実行できます。

## エンドポイント

- `GET /health`（Cloud Run外部監視用。`/healthz` はローカル互換）
- `GET /webhooks/strava` - webhook購読検証
- `POST /webhooks/strava` - アクティビティ通知
- `POST /webhooks/line` - 体調回答・承認操作
- `POST /tasks/plans/reconcile-missing` - provider同期確認後の未実施候補scan（Cloud Tasks OIDC）

## 次の実装

最優先は、照合済みActivityの評価とStrava Description自動追記です。続いて日曜21:00の自動週間生成、日次・slot単位の編集、運用監視を実装します。引き継ぎ用の具体的な依頼文は [docs/next-session-prompt.md](docs/next-session-prompt.md) にあります。

## LINEテキストコマンド

リッチメニュー導入前でも、以下のコマンドで目標と運動環境を登録・確認できます。

```text
目標登録 主目標 marathon 完走 2027-03-14
目標登録 副目標 habit 週3回 なし
目標確認

運動環境登録 屋外ランニング、ルームバイク、自重筋トレ、ダンベル
運動環境確認

体重
体重 70.2
目標体重 68
```

新しい主目標を登録すると、以前の主目標は履歴を保ったまま副目標へ変更されます。
登録内容は次回のVertex AI提案から入力コンテキストとして使用されます。
