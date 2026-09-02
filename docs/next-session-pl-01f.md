# PL-01F 承認付き再計画 実装記録

## 実装範囲

実施中の週間計画に対し、ユーザーが変更範囲、理由、希望する調整を選び、AI変更案を確認してから明示承認できるfeedback loopを実装した。

- `next_day`、`from_date`、`remainder_week`の変更範囲を扱う。
- 天候、体調、予定、睡眠、環境、難易度、別種目希望、その他の理由codeを扱う。
- 負荷低減、休養、屋内、別種目の希望を構造化してAIへ渡す。
- 週間計画画面は初回承認後も実施中planを表示し、変更要求、差分確認、承認、拒否、別案依頼を行える。
- 別案は既存proposalを上書きせず、新しいproposal revisionと候補planを追加する。

## 保存境界とactive切替

BigQueryへ`PlanChangeRequest`、`PlanRevisionProposal`、`PlanRevisionDecision`をappend-onlyで保存する。候補planと全候補workoutも不変recordとして保存し、proposalには復旧可能なsnapshotと差分、rule/model/prompt version、入力digestを残す。

Firestoreの`plan_revision_approval_states`と`current_plan_revision_proposals`が現在操作可能なrevisionを管理する。承認tokenはproposal ID・revision、base plan ID・version、LINE所有者、decision、有効期限をHMACへ結び付ける。承認時だけ既存の`PlanningService`でactive pointerをCAS更新する。拒否、別案依頼、期限切れではbase planを維持する。

## 安全性と不変条件

- ユーザーtimezoneの翌日より前、および開始済み・過去のworkoutを変更対象にしない。
- 新planはbase plan全体を複製し、各workoutのlineageとsupersedes IDを残す。
- AIが対象外workoutを返した場合は除外し、同じworkoutの重複変更も除外する。
- 体調、睡眠不足、難しすぎることが理由の場合、時間・距離・強度をbaseより増やさない。
- `blocked` Readinessに対応するworkoutはAI出力に関係なく休養へ変更する。proposalを拒否しても元Readinessのblockは解除しない。
- 変更理由の自由記述は監査履歴には保存するがAI入力、Web応答、logへ複製しない。GPS、生stream、route hash、健康自由記述もAI入力に含めない。

## 冪等性と障害復旧

- change request、proposal revision、decision、候補plan、lifecycle eventは安定IDを使用する。
- 同じoperation IDの再送は同じrequestとproposalを返し、異なる入力へのoperation ID再利用は拒否する。
- proposal保存後に候補plan、workout、Firestore stateの保存が失敗しても、proposal snapshotから同じ候補を復旧する。
- 同じbuttonの二重押下は同じdecisionを返し、別decision、期限切れ、旧proposal、所有者不一致、署名改ざんを拒否する。
- base planより新しいplanがactiveになった場合、古いproposalは適用しない。

## 対象外

- Schedulerによる自動変更要求作成、quiet hours、再通知、DLQ、監視（NT-01）
- 外部公開承認やStrava Description更新承認との統合
- リッチメニュー画像・座標の変更
- 本PRの自動マージとデプロイ
