# 次セッション用プロンプト

次の文を新しいCodexセッションへそのまま渡してください。

```text
AI-COACHの次の実装を進めてください。

対象リポジトリ（絶対パス）:
/home/kansei/AI/AI-COACH

最初に次を必ず読んでください。
- /home/kansei/AI/AI-COACH/AGENTS.md
- /home/kansei/AI/AI-COACH/CODEX.md
- /home/kansei/AI/AI-COACH/README.md
- /home/kansei/AI/AI-COACH/docs/line-app-activity-implementation-plan.md
- /home/kansei/AI/AI-COACH/docs/feature-session-roadmap.md
- 関連する app、tests、infra/terraform

現在の実装済み範囲:
- LINE設定、週間計画の閲覧、手動/Strava Activity、任意日次コンディション、体重、練習候補編集
- Activityと予定の本人選択（今日、計画外、別日、保留、複数予定のまとめて実施）
- Cloud Run / Firestore / BigQuery / Cloud Tasks / Terraform / CI/CD

次の最優先実装:
Activity照合後の評価・Strava Description自動追記を実装してください。
- 予定と対応付いたActivityだけを評価対象とし、保留中は評価・投稿しない。
- 単一予定は計画対比、実績要約、心拍等の負荷、次回助言を保存する。
- 複数予定のcombined_activityは数値を予定別に自動配分せず、共通の実績要約・負荷を参照する。
- 自動投稿は設定で有効な場合だけ、Descriptionのアプリ管理ブロックへ冪等に追記する。
- 既存ユーザー記述を壊さず、失敗時は状態・監視へ残す。成功時のみLINE Pushで通知する。
- webhookは即時応答、外部処理はCloud Tasks、AI前後の決定論的安全制約を守る。

作業手順:
1. mainから機能ブランチを作る
2. domain model、Firestore/BigQuery、Terraform、LINE導線、テストを実装する
3. pytest、ruff、compileall、Terraform fmt/validateを実行する
4. PR作成、CI成功確認、mainマージ、Cloud Runデプロイ、デプロイ成功確認まで実施する
5. 変更、既知制約、LINEでの確認手順を日本語で報告する

デプロイ、mainマージ、Terraform applyは許可済みです。秘密値の追加・変更、本番データの破壊的移行は行わないでください。
```
