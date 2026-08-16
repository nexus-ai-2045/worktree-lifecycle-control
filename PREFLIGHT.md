<!-- repo-preflight:review-record -->

# 公開範囲とレビュー状態

このリポジトリは、Git worktree のライフサイクルを read-only で棚卸しし、cleanup readiness を fail-closed に判定するローカル制御ツールです。

## 現在の公開状態

- GitHub visibility: **private**
- 公開化: **未承認 / 未実施**
- 本ファイルは private 運用中の境界宣言であり、公開準備完了を意味しない

## 公開対象になりうるもの

- read-only scan / review-packet CLI
- unit test
- schema 契約、architecture、統合境界ドキュメント
- MIT License とセキュリティ報告方針

## 公開対象外（現状）

- 実運用 registry の owner/task 台帳
- `.local/` 配下の scan report / human review packet
- 特定 Projects path、アカウント名、通知先
- 削除 executor、GitHub write、定期実行配線

## 停止線

CI green や `readiness_scan` の部分 pass は、公開してよいことを保証しません。public にする前に次をすべて満たす必要があります。

1. README / LICENSE / SECURITY / CONTRIBUTING / PREFLIGHT の人間レビュー
2. secret scan と personal path scan の再測定
3. dependency audit と現行 CI 結果の再確認
4. repository visibility 変更の対象指定と現在会話での明示承認
