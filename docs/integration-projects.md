# Projects連携契約

## データフロー

1. Git nativeのread-only scanでworktreeを観測する。
2. 既存の `shared/scripts/post_merge_closeout_report.py collect` で PR merge 証跡を取得する（本repoは取得を再実装しない）。
3. `python -m worktree_lifecycle_control evidence-from-closeout` が観測時刻付き collect JSON を `integration-evidence-v3` へ正規化する。既存の v2 schema は互換性維持のため変更しない。
4. coreがexact `subject_head_sha`、`resulting_base_sha`、provider record、actor、観測時刻を検証する。
5. report v3とreview packet v3が、各recordの`observations`、`blockers`、`review_signals`、`disposition`を提示する。
6. 人間が対象と操作を承認した後だけ、既存の `shared/scripts/post_merge_cleanup.py` を executor として呼ぶ（削除ロジックは再実装しない）。

## 責務境界

| 層 | 役割 | 実行しないこと |
|---|---|---|
| Core | Git観測、分類、review packet | GitHub照会、削除 |
| Closeout provider adapter | PR/merge証跡の取得と正規化 | cleanup実行 |
| Cleanup executor adapter | 承認済み対象のpreflightと削除 | 承認の推定 |
| Worktrunk adapter候補 | 将来の操作UX比較 | SSOT化、自動導入 |

provider入力が欠落、stale、またはHEAD不一致なら`verified`へ昇格せず、blockerと次の確認方法をreview packet側へ返す。executorは候補一覧全体ではなく、承認されたexact pathを受け取る。

## 移行方針

`merge_worktree.sh`はretire候補だが、現時点では変更しない。呼び出し元、Windows経路、PR作成などcleanup以外の副作用を棚卸しし、closeout providerとcleanup executorで代替できない責務を明示してから移行PRを分離する。
