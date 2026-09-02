# WT-01 体重記録 実装記録

## 実装範囲

- LINEテキスト「体重」「体重記録」、または「体重 70.2」のワンショットから会話を開始する。リッチメニュー画像・JSONは変更しない。
- 日付（今日／昨日／入力）、kg、確認のあとBigQueryへ不変ログを保存する。同日再記録は訂正として新しい行を追加し、最新`recorded_at`を正本にする。
- 単位はkg。範囲は25.0〜250.0、小数第1位。未来日と1年以上前は拒否する。
- 保存後に最新値、7日平均、30日平均、任意の目標差を返す。平均は欠損日を埋めず、記録がある日だけを使う。
- 「目標体重 68」または会話内の目標体重入力でFirestoreに目標を保存する。
- 途中保存、cancel、24時間TTL、operation IDによる冪等保存。

## 保存境界

- BigQuery `weight_logs`へ生ログをappend-onlyで保存する。集計は読み取り時に計算し、正本にしない。
- Firestore `weight_drafts/{line_user_id}`に会話途中状態と`expires_at`を置き、Terraform TTLを設定する。
- Firestore `weight_targets/{user_id}`に目標体重を置く。TTLは付けない。
- 日付はユーザーtimezoneのローカル日付。保存timestampはUTC。

## privacy

- 体重の数値はアプリケーションログへ出さない。出すのは`user_id`、`measured_on`、訂正有無だけ。
- LINE確認メッセージには本人向けに数値を表示する。

## 対象外

- リッチメニューへの「体重」領域追加（RM画像変更）
- 進捗画面へのtrend表示（PR-01）
- AI coaching contextへの体重入力（AN-01 / AI-01）
- 実績照合（PL-01D）
