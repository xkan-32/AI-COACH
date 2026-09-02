# PF-01 実装記録

> 2026-09-02更新: PF-01は実装済み。ファイル名は過去の参照を壊さないため維持している。

## 目的

LINEリッチメニューの「設定」から、目標・プロフィール・利用可能な運動環境を安全に管理できるようにする。PF-01で扱うのは今後の提案に利用できる場所・種目・器具であり、実施Activityや体重の記録ではない。

## 現在地

- RM-01〜04は本番反映・実機確認まで完了している。
- `action=menu&version=1&target=settings`は、LINE workerで10分間有効なワンタイムURLを発行する。
- 設定ページは同じCloud Runから配信し、30分間のHttpOnly sessionで目標・運動環境を一括編集する。
- 運動環境は安定ID、表示名、区分、有効状態、任意詳細を持つ。
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

## 実装箇所

- `app/profile.py`: 目標・運動環境model、Firestore store、会話workflow、正規化、冪等保存
- `app/web_settings.py`: 署名付きワンタイムリンク、30分session、所有者・期限検証
- `app/static/profile-settings.html`: モバイル対応の一覧・編集画面
- `app/main.py`: LINE worker routing、設定ページ/API、AI context連携
- `infra/terraform/firestore_ttl.tf`: `profile_drafts`、`profile_settings_links`、`oauth_sessions`、`condition_drafts`、`activity_contexts`のTTL
- `tests/test_profile.py`, `tests/test_web_settings.py`: 会話、Web設定、期限、冪等性、AI連携

## 完了した導線

1. リッチメニューの「目標」は有効目標を読み取り専用で表示する。
2. 「設定」は10分間有効なワンタイムURLをLINEへ送る。
3. URLを30分間有効なHttpOnly・Secure・SameSite=Strict cookieへ交換する。
4. 設定APIはcookie、Origin、document所有者を検証してから保存する。
5. 目標・運動環境は次回のAI提案contextへ反映する。
6. 旧会話UIとテキストコマンドは後方互換経路として残す。

## 対象外として残る機能

- 実施した手動Activityの登録（MA-01）
- 体重の登録・訂正・集計（WT-01）
- Strava Manual Activity作成
- 状態別リッチメニュー（RM-05）

後続の推奨順序と完了条件は`docs/feature-session-roadmap.md`を正本とする。deploy、本番反映、秘密値の追加・変更はユーザーの明示承認後に行う。
