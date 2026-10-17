# 0002. 保護対象を「統合証跡」から「到達可能性」へ移す

- 状態: 採択
- 日付: 2026-08-20
- 影響: `worktree-lifecycle-report/v2` → `v3`、`worktree-lifecycle-review/v2` → `v3`、`worktree-lifecycle/v1` → `v2`、`integration-evidence/v2` → `v3`

## 背景

v1/v2 は「統合された証跡が台帳にある worktree だけを削除候補にする」設計だった。
証跡が無ければ `cleanup_candidate` へ昇格しない fail-closed な allowlist である。

実運用に当てた結果 (2026-08-16 実測):

| 対象 | worktree 数 | cleanup_candidate | unknown_count |
| --- | --- | --- | --- |
| 複数リポジトリを含む作業場 | 数十 | 0 | 全件 |
| そのうち入れ子の 1 リポジトリ | 十数 | 0 | 全件 |

台帳が空である限り候補は構造的に 0 件になる。このツールが解こうとした問題は
「worktree が無数に増えて片付かない」であり、その問題に対して 1 件も寄与していなかった。
CI は緑、PR は merge 済み、テストも通っていた。**正しく実装された間違ったモデル**だったため、
テストでは検出できなかった。

## 実験

隔離した repo で 3 つの対照実験を行った (2026-08-15)。CI では `tests/test_reachability.py` が同じことを毎回検証する。

| # | 操作 | 結果 |
| --- | --- | --- |
| 1 | 未 push commit を持つ worktree を削除 | branch が残り、commit も内容も復元できた |
| 2 | dirty な worktree を削除 | `git worktree remove` 自身が拒否した |
| 3 | detached HEAD の worktree を削除 | git は無警告で削除し、`git gc` 後に commit が消滅した |

判明したこと:

- 「未 push だから守る」は、存在しない危険から守っていた。
- 「dirty だから守る」は、git が既にやっていることの重複だった。
- git が守らない経路は 3 だけであり、v2 はそれを見ていなかった。

**守る対象が丸ごとズレていた。**

## 決定

削除を止める条件 (blocker) を、次のいずれかに限定する。

1. `head_becomes_unreachable` — worktree を消すと HEAD が unreachable になる (git は守らない)
2. `dirty_worktree` / `worktree_locked` — git 自身が拒否する
3. `primary_worktree` — 削除できない
4. `pinned` — 人が台帳で明示した保護。git から導出できない唯一の条件
5. 測定不能 (`path_missing` / `git_status_unknown` / `head_reachability_unknown`)

統合状態・未 push commit・owner・台帳の記入漏れは blocker から外し、`review_signals` へ降格する。
削除を止めないが、人が見る材料としては必ず表に出す。

台帳は allowlist (登録が無いと消せない) から denylist / opt-out protection
(登録した物だけ守る) へ反転し、全項目を任意にする。git から導出できる事実
(owner / 統合状態 / dirty) は台帳に持たせない。導出できるものを保存すると、
保存した瞬間から drift が始まる。

## Principle

git が守るものを二重に守らない。git が守らないものだけを守る。
それ以外は判断材料として見せるだけにする。

## Invariant

- blocker に載るのは「消すと失われる」「git が拒否する」「人が明示保護した」のいずれかだけである。
- 到達可能性の根に、worktree 削除で一緒に消えるもの (worktree 専用 reflog、他 worktree の detached HEAD) を数えない。
- 台帳から導出できる事実と git から導出できる事実を、同じ項目に載せない。
- 測定できなかったことを「安全」と言わない (`None` は blocker)。
- ignored content は `.pytest_cache` / `__pycache__` / `.venv` の明示 allowlist だけを再生成可能とみなし、それ以外は `unknown_ignored_content` として保護する。

## Detector

- `tests/test_reachability.py` — 実 git に対する対照実験。git 側の仕様変更で落ちる。
- `danger_count` — 到達不能になる worktree 数。scan report の top-level に出す。
- `registry_coverage` — 台帳の網羅率。`measurement_status` とは別に持つ。
  v2 は台帳未登録を測定不能に数えていたため、台帳が空の repo では常に `partial` となり、
  本物の測定失敗を隠していた。
- dogfood: 実 repo に当てて `cleanup_candidate` が 0 件なら、モデルが再び壊れている。

## Repair Path

`head_becomes_unreachable` と判定された worktree は、削除する前に branch か tag を付ける。

```bash
git branch rescue/<name> <head-sha>
```

名前を付けた時点で到達可能になり、次の scan で `cleanup_candidate` へ移る。
`tests/test_reachability.py::test_naming_the_detached_commit_makes_it_reachable` が
この手順が実際に効くことを固定している。

## Evidence

- 2026-08-16 dogfood 実測: 複数リポジトリを含む作業場の数十 worktree と、
  入れ子の 1 リポジトリの十数 worktree で候補 0 件。
- 2026-08-20 本 ADR 実装後の実測: 同じ作業場の数十 worktree で過半が候補、保護は少数、
  到達不能 1 件 (`.claude/worktrees/<name>`、detached、branch なし)。
- 危険 1 件は、v2 の判定と本 ADR の判定を別々の方法で走らせて同じ 1 件に一致した。

## 先行実装と用語

車輪の再発明を避けるため、着手前に先行実装を調査した。

- [larsch/git-remove](https://github.com/larsch/git-remove) — 「安全性を証明できなければ削除しない」を
  remote ref 起点で実装している。本実装はローカル ref 起点である点、削除せず候補提示に留める点が異なる。
- [git-trim](https://github.com/foriequal0/git-trim) /
  [git-delete-merged-branches](https://github.com/hartwork/git-delete-merged-branches) —
  squash merge 検出 (patch equivalence) の先行例。ただし branch 単位であり worktree に接続していない。
- git 本体 — `git worktree remove` の dirty 拒否、`git worktree lock` による opt-out 保護、
  `git worktree prune`。ここは実装せず git に任せる。

用語は既存語に揃え、独自造語を作らない。

| 概念 | 用語 | 出典 |
| --- | --- | --- |
| 他の ref から到達できない object | unreachable / dangling | gitglossary(7) |
| 明示許可のみ通す / 明示拒否のみ止める | allowlist / denylist、fail-closed / fail-open | 一般セキュリティ用語 |
| squash merge された branch の検出 | patch equivalence (patch ID) | git-cherry(1) / git-patch-id(1) |

## 却下した代替案

- **数十件を手で台帳登録する** — 初期コストが高く、owner / dirty / 統合状態は git が正本なので
  写しを人が維持し続けることになる。SSOT に反し、書いた瞬間から drift が始まる。
- **worktree 作成時に登録を強制する** — worktree の親フォルダが複数箇所に分散しており、
  複数の agent が別経路で作成しているため、登録を強制できる関所が存在しない。
- **squash 検出に追加ヒューリスティクスを入れる** (`--effort=3` 相当) — 統合状態は signal であって
  blocker ではないため、取りこぼしは「未統合と表示される」で済む。判定を重くする割に blocker を動かさない。
