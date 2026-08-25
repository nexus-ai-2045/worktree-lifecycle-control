# 0003: checkout health を scan の前提検査としてラチェット化する

- status: accepted
- date: 2026-08-24

## 脅威モデル

| 項目 | 内容 |
|---|---|
| 誰から | 悪意ではなく運用事故。昇格シェル・別権限プロセスが checkout を作り所有者が管理者になる。Windows の `git worktree remove` が登録解除だけ成功して実体 dir を残す |
| 何を | checkout の測定可能性。git が素で動き、scan と依存する運用保証 script が真実を返すこと |
| どうなると困る | scan 自体が RuntimeError で止まり、依存側は error の山になって真因を教えない。「壊れているのが普通」が常態化し、本物の失敗が埋もれる |
| 守らないもの | remote / CI (正しく動く)、他ユーザーの repo、修復の自動実行 (人間の承認境界の向こう)、ACL の完全修復 |

## 決定

`health` subcommand を追加し、scan の前提を検査する。

1. repo ごとに `git status` を**素で** (safe.directory 等の回避なしで) 実行し、
   失敗を `dubious_ownership` / `not_a_repository` / `timeout` / `other` に分類する。
   回避付きで測ると「回避すれば動く」ことしか分からない。
2. `--root` 直下から、`.git` 通常ファイルの gitdir 先が存在しない残骸 dir を探す。
   名前や日時の推測では広げない (作業中 dir の誤検知を避ける)。
3. baseline との比較で件数の増加だけを fail にする (単調非増加 ratchet)。
   baseline に無い種別は 0 として扱い、新種の違反を初回から止める。
   減少は fail にせず、baseline 更新は `--update-baseline` の明示操作に限る。

exit code 契約: 0 = ok / 1 = ratchet 後退 / 2 = 実行エラー。修復は行わず、
repair_hint の提示に留める。

## 根拠 (2026-08-24 fractal-decision-ecosystem での実測)

- BUILTIN/Administrators 所有の checkout で git が全 command を拒否し、repo 自身の
  closeout が常時 error、pytest 7 failed 中 6 件が単一原因の連鎖だった。
  `safe.directory` を env 注入する対照実験で分離し、`takeown /F <path> /R /D Y`
  (非昇格で成功) で根治した。
- `git worktree remove` が「Directory not empty」で登録解除だけ成功し、残骸 dir が
  2 件残った。local の branch 一覧だけを見て「整理完了」と誤報告し、remote heads
  に merged 済み branch が 6 本残っていた。

局所修復 (takeown・削除) だけでは、次に同じ事故が起きた時にまた 1 セッション溶かす。
検知を機械に置き、後退をラチェットで止める。

## 却下した代替案

- **scan に統合する**: scan は「worktree をどう扱うか」、health は「そもそも測れるか」で
  問いが違う。混ぜると scan の read-only 契約と report schema が肥大する。
- **所有者名を直接測る**: Windows の owner 取得は追加依存 (pywin32 等) が要る。
  不変条件は「git が素で動く」なので、git の応答だけで判定できる。
- **remote heads の merged/closed 滞留検査**: gh とネットワークが要る。read-only
  ローカル検査という本 subcommand の境界を超えるため、別 decision に分ける。
