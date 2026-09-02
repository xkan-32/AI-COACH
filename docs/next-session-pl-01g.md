# PL-01G 初回週間計画の生成導線 実装記録

## 実装範囲

リッチメニューの「練習メニュー」を開いたユーザーに確認対象の週間計画がない場合、既存の週間計画生成と初回承認の境界を利用して初回案を作成・表示する導線を実装した。

- pending proposalがある場合は、そのproposalを表示する。
- active planがある場合は、そのplanを表示する。
- いずれもない場合だけ、ユーザーtimezoneの現在週を対象にconservativeなDRAFTを生成し、既存の承認画面へ遷移する。
- profile未設定でも、既存の安全なprofile fallbackを用いて作成できる。

## 安全性と状態遷移

- 初回案の生成理由は`initial_menu_request`として監査入力に残す。
- 生成後は既存の`PlanApprovalService`でproposalを提示する。ユーザーが明示承認するまでactive pointerを変更しない。
- 明示的なメニュー押下だけが生成の契機であり、Scheduler、通知、自動生成を追加しない。
- active planを再表示する際はproposal提示処理を行わず、既存planの状態を変更しない。

## 冪等性と障害復旧

- 生成の冪等keyはLINE user、ローカル週開始日、profile versionから作る。
- 同一週・同一profileでの重複押下は作成中として扱い、二重生成を防ぐ。
- 生成失敗時はreserve済みのeventを解放し、再試行できる。
- 生成済みのplanは不変履歴から再取得し、取得できなければ承認URLを発行しない。

## テスト

- 計画未作成のユーザーが`training_menu`を押すと、7日分のpending planを持つ署名付き画面を開けることを結合テストで確認した。
- 既存のrich menu全targetのworker routingテストを更新し、計画がない場合にも練習メニューURLが返ることを確認した。

## 対象外

- 週途中開始時の過去日を除く再構成、拒否・期限切れ後の新version再提案
- timezone別Scheduler、通知、quiet hours、DLQ、監視（NT-01）
- リッチメニュー画像・座標の変更
- 本PRの自動マージとデプロイ
