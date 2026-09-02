# PF-01 次セッション引き継ぎ

> 2026-09-02実装: 本文の設計に基づくPF-01会話workflow、構造化運動環境、Firestore draft TTL、AI context反映を`feat/pf-01-profile-training-environment`で実装した。デプロイ・本番反映は未実施。

## 目的

LINEリッチメニューの「設定」から、目標・プロフィール・利用可能な運動環境を安全に管理できるようにする。PF-01で扱うのは今後の提案に利用できる場所・種目・器具であり、実施Activityや体重の記録ではない。

## 現在地

- RM-01〜04は本番反映・実機確認まで完了している。
- `action=menu&version=1&target=settings`は現在、既存テキストコマンドを案内する。
- `app/profile.py`に目標と運動環境の登録・確認コマンドがある。
- 運動環境は現在`list[str]`で全置換され、構造化区分、変更・無効化、会話stateは未実装である。
- 最新の目標・運動環境は`CoachingContext`を通じてAI提案へ渡される。

## 決定事項

- PF-01: 利用可能な運動環境を設定する。
- MA-01: 実際に行った運動を記録する。
- WT-01: 体重を記録・訂正・集計する。
- 「ルームバイク」「エアロバイク」「フィットネスバイク」は`インドアバイク`へ正規化する。
- `ローラー台`はインドアバイクの別名にせず、所有する自転車を使う器具として別項目にする。
- 未定義の場所・器具は詳細として保持し、推測で既知区分へ変換しない。
- PF-01ではリッチメニュー画像と6領域の座標を変更しない。
- リッチメニューから直接Stravaを更新しない。

## 次セッション用プロンプト

```text
AI-COACHリポジトリで、PF-01「目標・プロフィール・運動環境UI」を実装してください。

対象リポジトリ:
\wsl.localhost\Ubuntu-24.04\home\kansei\AI\AI-COACH

最初に次のファイルと関連コードを確認してください。

- AGENTS.md
- CODEX.md
- docs/feature-session-roadmap.md
- docs/rich-menu-plan.md
- docs/next-session-pf-01.md
- docs/architecture.md
- app/profile.py
- app/line_menu.py
- app/main.py
- app/runtime.py
- app/condition.py
- app/coaching.py
- app/domain/models.py
- tests/test_profile.py
- tests/test_line_menu.py
- infra/terraform
- .github/workflows

今回の対象:

1. LINEリッチメニューの「設定」から目標・運動環境の一覧・編集を開始できる会話UI
2. 利用可能な場所・種目・器具の構造化model
3. 一覧、追加、変更、無効化
4. 既存テキストコマンドとの後方互換
5. 最新の有効設定をAI coaching contextへ反映

運動環境の初期区分:

- 場所・種目: 屋外ランニング、トレッドミル、屋外サイクリング、インドアバイク、プール、ジム、自宅トレーニング
- 器具: 自重、ダンベル、バーベル、ケトルベル、マシン、チューブ、ローラー台
- ルームバイク、エアロバイク、フィットネスバイクはインドアバイクへ正規化する
- ローラー台は別項目として扱う
- 未定義項目は詳細として保持し、推測で誤分類しない

要件:

- Webhookは署名検証、event重複排除、Cloud Tasksへのenqueue、即時応答という既存経路を維持する
- 会話処理と外部処理は既存LINE workerで行う
- 入力stateはFirestoreに保存し、TTL、cancel、再開、Task再送の冪等性を持たせる
- 主目標は有効なものを1件、副目標は複数、期限なしを許可する
- 運動環境は自由入力文字列だけにせず、安定したID、表示名、区分、有効状態、任意詳細を持たせる
- 健康情報や自由記述をログへ出さない
- リッチメニューから直接Stravaを更新しない
- Strava更新には既存の署名・期限付き明示承認を必須とする
- 未入力、重複、表記揺れ、上限、変更、無効化、TTL切れ、cancel、Webhook/Task再送をテストする
- 最新の有効な目標・運動環境だけが次回AI提案へ渡ることをテストする
- 必要なTerraformと日本語ドキュメントを更新する

対象外:

- 実施した手動Activityの登録（MA-01）
- 体重の登録・訂正・7日/30日平均（WT-01）
- Strava Manual Activity作成
- RM-05の状態別リッチメニュー
- リッチメニュー画像・6領域座標の変更

進め方:

1. mainを最新化する
2. feat/pf-01-profile-training-environmentブランチを作成する
3. 現状model、Firestore互換性、会話state、移行方針の不足を確認する
4. 実装計画とLINE会話フロー案を提示する
5. PF-01を実装する
6. lint、test、compileall、Terraform validateを実行する
7. 変更内容、data model、冪等性、未実装部分、手動確認事項を日本語で報告する
8. PRを作成する

デプロイ、mainへのマージ、本番LINEアカウントへの反映、秘密値の追加・変更は、私が明示承認するまで行わないでください。
```
