# Worktree Lifecycle Control

Git worktree を「増えたフォルダ」ではなく、所有者・タスク・期限・統合証跡を持つ期限付き作業資産として管理するためのローカル制御ツールです。

現在は nexus_ai 内の private MVP です。Projects 全体への配線、定期実行、worktree・branch の削除、GitHub 操作は行いません。

## 原則

- 年齢は通知条件であり、削除条件にしない。
- dirty、未到達commit、owner不明、統合不明を保護する。
- worktree、branch、task、PRを別の対象として扱う。
- `cleanup_ready` は削除ではなく、人間レビュー用候補を意味する。
- 通常フローでは `--force` を使わない。
- Gitの機械可読形式 `git worktree list --porcelain -z` を使う。

## 状態

| 状態 | 意味 |
| --- | --- |
| `active` | 台帳上で作業中 |
| `protected_dirty` | trackedまたはuntracked差分あり |
| `protected_unpushed` | remoteから到達不能なcommitあり |
| `protected_locked` | Git worktreeがlock済み |
| `owner_unknown` | 台帳にownerがない |
| `review_due` | 期限超過または判断材料不足 |
| `integrated_context_pending` | 統合済みだが引き継ぎ保存未完了 |
| `cleanup_ready` | clean・remote到達済み・統合確認済み・context保存済み |
| `orphan_unknown` | path不在など実体不明 |

## 実行例

```powershell
python -m worktree_lifecycle_control scan `
  --repo "$env:USERPROFILE\Projects\Documents\nexus_ai" `
  --registry registry.example.json `
  --report-path .local\reports\nexus-ai-worktrees.json `
  --json
```

このコマンドはread-onlyです。削除コマンドは実装していません。
`--report-path`を指定した場合だけ、棚卸し結果をUTF-8 JSONとして指定先へ保存します。

人間レビュー用packetも、削除を実行せず生成できます。

```powershell
python -m worktree_lifecycle_control review-packet `
  --repo "$env:USERPROFILE\Projects\Documents\nexus_ai" `
  --registry registry.example.json `
  --report-path .local\reviews\nexus-ai-review.json `
  --json
```

GitHub固有のadapterは、統合済み判定に次をすべて返す必要があります。

- `status: verified`
- `provider`
- 一次証拠を識別する`source`
- scan対象と一致する40桁の`head_sha`
- `actor`
- timezone付き`observed_at`

不足・SHA不一致の場合、`cleanup_ready`へ昇格しません。

## 台帳

`registry.example.json` を参考に、worktreeの絶対pathをキーとして次を記録します。

- `owner`
- `task`
- `created_at`
- `expires_at`
- `return_path`
- `lifecycle_status`
- `integration.status`
- `context_saved`

期限超過だけで `cleanup_ready` にはなりません。
