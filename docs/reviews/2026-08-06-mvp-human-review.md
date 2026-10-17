---
title: Worktree Lifecycle Control MVP 人間レビュー
type: review-decision
status: awaiting-human-review
schema_version: fact-provenance/v1
recorded_at: 2026-08-06T03:30:51+09:00
recorded_by: codex
---

# Worktree Lifecycle Control MVP 人間レビュー

## 推奨判断

**Option A: local MVPの設計と実装を採用し、この独立repo内でのlocal commitを許可する。**

理由:

- ローカルworktree制御とGitHub操作を分離している。
- dirty、未到達commit、owner不明、証跡不正を安全側で保護する。
- TTLは通知だけに使い、削除根拠にしていない。
- squash/rebaseはprovider、source、exact head SHA、actor、観測時刻を要求する。
- review packetは操作案を表示するが、削除を実行しない。

## 今回レビューする範囲

- `README.md`
- `docs/architecture.md`
- `src/worktree_lifecycle_control/cli.py`
- `src/worktree_lifecycle_control/evidence.py`
- `tests/test_lifecycle.py`
- `registry.example.json`

## 実測結果

- actor: `codex`
- event_time: `2026-08-06T03:30:51+09:00`
- observed_at: `2026-08-06T03:30:51+09:00`
- scope: `dogfood した 1 リポジトリの Git 登録 worktree 5本`
- source: `ローカルの human-review packet（未公開。この repo には含めない）`
- fact: 削除候補は0本、保護対象は5本だった。
- fact: 内訳は`protected_dirty` 4本、`protected_unpushed` 1本だった。
- fact: review packetの`executed`は`false`だった。

## 検証結果

- `python -m pytest -q`: 15 passed、exit code 0。
- `python -m compileall -q src`: exit code 0。
- `git diff --check`: exit code 0。
- Windowsのpytest終了処理でTemp directoryの`PermissionError`が表示された。テスト本体とは分離して扱う。

## 未レビュー・未実施

- local commit
- GitHub repository作成
- push、PR、CI、人間コードレビュー
- `github-ops-skills` adapter実装
- Projects全体配線、定期実行、hook、automation
- worktree、local branch、remote branchの削除
- 現在保護された5本のowner・task・統合状態の確定

## 選択肢

### Option A: 採用（推奨）

この独立repo内の現在filesだけをlocal commit候補とする。GitHub操作・Projects配線・削除は引き続き停止する。

### Option B: 修正して再レビュー

状態名、台帳schema、adapter契約、review packet形式の変更点を指定する。commitしない。

### Option C: 保留

filesを未commitのlocal MVPとして保持する。Projectsへ適用しない。

## 次の承認文

Option Aの場合:

```text
Option A採用。worktree-lifecycle-controlの現在filesをlocal commitしてよい。
```

この承認からGitHub repo作成、push、PR、Projects配線、削除へは進まない。
