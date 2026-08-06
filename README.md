# Worktree Lifecycle Control

Git worktree を「増えたフォルダ」ではなく、所有者・タスク・期限・統合証跡を持つ期限付き作業資産として管理するためのローカル制御ツールです。

現在は nexus_ai 内の private MVP です。Projects 全体への配線、定期実行、worktree・branch の削除、GitHub 操作は行いません。

## 原則

- 経過日数は通知条件であり、削除条件にしない。
- dirty、未到達commit、owner不明、統合不明を保護する。
- worktree、branch、task、PRを別の対象として扱う。
- `cleanup_candidate` は削除ではなく、人間レビュー用候補を意味する。
- 通常フローでは `--force` を使わない。
- Gitの機械可読形式 `git worktree list --porcelain -z` を使う。

## 判定

危険条件は一つの状態へ潰さず、`blockers[]`へ同時に保持します。その上で、操作可否だけを`disposition`へ集約します。

| disposition | 意味 |
| --- | --- |
| `active` | 台帳上で作業中 |
| `protected` | dirty・未到達commit・lockなど、削除禁止条件あり |
| `review_required` | owner・統合証拠・contextなどの確認が必要 |
| `cleanup_candidate` | blockerなし。人間レビュー候補 |
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

既存の closeout collector から統合証跡を正規化できます（GitHub 取得は collector 側）。

```powershell
python $env:USERPROFILE\Projects\shared\scripts\post_merge_closeout_report.py collect `
  --repo nexus-ai-2045/worktree-lifecycle-control `
  --pr 1 `
  --cwd . `
  --json |
  python -m worktree_lifecycle_control evidence-from-closeout --json
```

GitHub固有のadapterは、統合済み判定に次をすべて返す必要があります。

- `status: verified`
- `provider`
- 証拠の意味を示す`evidence_type`
- 一次証拠を識別する`provider_record_id`
- scan対象と一致する40桁の`subject_head_sha`
- 統合先を示す40桁の`resulting_base_sha`
- `actor`
- timezone付き`observed_at`（scan時点から7日以内）

不足・SHA不一致・7日超過・未来時刻の場合、`cleanup_candidate`へ昇格しません。

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

期限超過だけで `cleanup_candidate` にはなりません。

## 日数の表示

「年齢」という曖昧な表現は使わず、次を別々に表示します。

| JSON field | 日本語での意味 | 用途 |
| --- | --- | --- |
| `days_since_created` | 作成からの経過日数 | 長期化の通知 |
| `days_since_head_commit` | HEAD commitのcommitter dateからの経過日数 | commit時点の古さの確認 |
| `days_until_review` | 見直し期限までの残り日数 | review予定 |
| `overdue_days` | 見直し期限の超過日数 | review優先度 |

いずれもカレンダー日数として計算します。0日は「今日」、1日は「1日前／期限まで1日」です。期限を過ぎた場合は`days_until_review: 0`とし、超過分を`overdue_days`へ表示します。何日経過しても、日数だけではworktreeを削除しません。

通常表示では次の形にまとめます。

```text
作成から: 不明（台帳未登録） / HEAD commitから: 22日 / 見直し: 未設定
```
