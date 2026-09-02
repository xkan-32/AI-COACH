# AI Training Coach MVP

Stravaのアクティビティ取得を起点に、LINEで体調を確認し、Vertex AIで翌日のメニューを提案、ユーザー承認後にStrava Descriptionへ反映するMVPです。

## MVPフロー

1. Strava webhookがアクティビティ作成を通知する
2. APIがStravaから詳細を取得し、BigQueryへ保存する
3. LINEで体調確認を送る
4. LINE回答を受け、対象Activity、体調、目標、運動環境と安全ルールをVertex AIへ渡す
5. 翌日メニュー案をLINEへ送る
6. ユーザーが承認または却下する
7. 承認時のみ、提案要約を対象アクティビティのStrava Descriptionへ追記する

詳細は [docs/implementation-plan.md](docs/implementation-plan.md) と [docs/architecture.md](docs/architecture.md) を参照してください。 初回GCP/WIF/GitHub Actions構築は [docs/bootstrap-and-cicd.md](docs/bootstrap-and-cicd.md) に手順があります。

LINEリッチメニューの現在地と実装計画は [docs/rich-menu-plan.md](docs/rich-menu-plan.md)、機能別Codexセッションの実装順・完了条件・引き継ぎテンプレートは [docs/feature-session-roadmap.md](docs/feature-session-roadmap.md) を参照してください。
完了したPF-01の設計判断と実装記録は [docs/next-session-pf-01.md](docs/next-session-pf-01.md) にまとめています。AC-01、週間計画・実績評価・公開承認の基盤、PL-01Bの週間shadow生成、PL-01Cの週間計画画面と初回承認、MA-01のLINE手動Activityまでコード上実装済みです。

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

## 実装順

MVPのコアフロー、Cloud Tasks、Terraform/CI/CD、LINEリッチメニュー、PF-01、Strava token暗号化、OAuth session期限検証・TTL、Webhook再送の重複排除、GPS非保存の詳細Activity解析、versioned週間計画・実績評価・公開承認の基盤、手動shadow週間AI生成、署名付き週間画面と初回承認、LINE手動Activityはコード上実装済みです。次はPL-01D、WT-01、監視・DLQ、sandbox E2Eを進めます。週間計画の自動Scheduler・通知、別案の自動再生成、note API連携、実サービスの設定・deployはまだ行いません。

## LINEテキストコマンド

リッチメニュー導入前でも、以下のコマンドで目標と運動環境を登録・確認できます。

```text
目標登録 主目標 marathon 完走 2027-03-14
目標登録 副目標 habit 週3回 なし
目標確認

運動環境登録 屋外ランニング、ルームバイク、自重筋トレ、ダンベル
運動環境確認
```

新しい主目標を登録すると、以前の主目標は履歴を保ったまま副目標へ変更されます。
登録内容は次回のVertex AI提案から入力コンテキストとして使用されます。
