# ADR-0001: Git native SSOTと既存Projects資産を採用する

- 状態: 採用
- 記録日: 2026-08-06

## 判断

worktreeの現在状態はGitのネイティブな観測結果を正本（SSOT）とする。台帳はowner、task、期限、return pathなどGitが持たない運用メタデータだけを補完し、scan reportとreview packetは観測時点付きの派生物とする。

report/review v2は、直交する`observations`、`blockers`、`review_signals`を保持し、その測定結果から単一の`disposition`を導く。run単位の完了性は`run_id`、`scan_completed`、`measurement_status`、`unknown_count`、`registry_validation_status`で表し、部分測定を完全測定へ推測昇格しない。

Projects連携では、統合証跡providerとして既存の`shared/scripts/post_merge_closeout_report.py`を再利用し、承認後の削除executorとして既存の`shared/scripts/post_merge_cleanup.py`を再利用する。coreはどちらも直接実行せず、証跡契約と承認境界を維持する。

Worktrunkは将来の任意adapter候補とする。現時点ではインストールせず、SSOTにも必須依存にも昇格しない。導入判断には、Git nativeとの差分、Windows対応、既存executorとの重複、障害時の迂回経路を別途実測する。

`shared/scripts/merge_worktree.sh`はretire候補とする。現在の利用箇所を測定し、互換経路と移行証跡が揃うまでは削除・変更しない。

## 理由

- Git自身の状態と別製品の内部状態が競合する二重SSOTを避けられる。
- 観測、阻害要因、処置を分離し、未確認を安全側に保持できる。
- 検証済みProjects資産を再利用し、削除ロジックの再実装を避けられる。
- optional adapterにより探索空間を限定し、運用の次元を増やさず比較できる。

## 帰結

- reportは時点付き観測であり、現在状態そのものとは扱わない。
- cleanupはscanやreview packet生成から自動連鎖しない。
- provider、executor、Worktrunk評価、legacy retireはそれぞれ独立した変更・承認単位にする。
