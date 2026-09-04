# 日次任意コンディション記録 実装記録

## 実装範囲

Activity完了後に自動で体調確認を送る方式を停止し、LINEリッチメニューの「体調を記録」から任意に日次コンディションを登録する方式へ移行した。

- 体調ハブの「コンディション」から、問題なし、疲労、違和感、痛みを選択できる。
- 違和感・痛みでは、部位、程度、運動中の悪化有無を続けて記録する。
- 日付はユーザーのtimezoneで当日を使用する。Strava未連携ユーザーもLINE user IDを分析上の所有者として記録できる。
- 未入力は、ユーザーが指定した計画上の既定値として健康・影響なし（`healthy_default`）と扱う。
- 日次コンディションは週間計画生成とActivity後Readinessへ渡す。痛み、違和感、疲労は既存の決定論的安全制約を継続して適用する。
- Strava Activity取得と手動Activity登録時の体調Pushを停止した。Activity取得・保存・照合は停止しない。

## データと互換性

- 既存の`ConditionReport`とBigQuery `condition_reports`を継続利用する。
- 日次記録は`activity_id = daily:<line_user_id>:<local_date>`で区別する。既存のActivity紐づき履歴を変更・削除しない。
- 日次記録の完了は、旧Activity後フローのAI提案を起動しない。

## テスト

- 日次開始、選択、保存、旧AI follow-upの非起動をunit testした。
- リッチメニューから体調ハブ、日次コンディション選択のLINE worker routingをtestした。
- 未入力時にReadinessが`healthy_default`で通常評価へ進むことをtestした。
- 全281テスト、ruff、compileall、Terraform fmt/validateを確認した。

## 後続

- 日次体調の同日訂正・履歴表示は、必要になった時点でappend-onlyな訂正レコードとして追加する。
- Activityと予定メニューのLINE明示選択（単数・複数）を次の機能単位で実装する。
