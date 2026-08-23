# アーキテクチャ

## 責務分離

`worktree-lifecycle-control`はローカルGit worktreeのライフサイクルを管理する独立coreです。GitHub API、credential、account切替、PR操作は所有しません。

```text
local Git                      <- 判定に必要な事実の正本
  -> worktree-lifecycle-control
     -> scan report            (danger_count / blockers / review_signals)
     -> human review packet

registry (任意)                <- git から導出できない「人の意思」だけ
  -> worktree-lifecycle-control

GitHub API / gh                <- git から導出できない「PR が merge されたか」
  -> github-ops-skills adapter
     -> integration evidence
        -> worktree-lifecycle-control registry (signal 用。削除条件ではない)
```

判定に効く事実は git から毎回導出し、台帳には保存しない。導出できるものを保存すると、
保存した瞬間から drift が始まる。詳細は
[ADR 0002](decisions/0002-protect-what-git-does-not.md)。

## Coreが所有するもの

- `git worktree list --porcelain -z`の解析
- dirty、lock、path不在の観測
- **到達可能性の導出** — worktree を削除すると HEAD が unreachable になるかの判定。
  git が守らない唯一の経路であり、本 core の中核。
- 統合状態の導出 — `git cherry` による patch equivalence 判定 (signal)
- 見直し期限、return path、pin の台帳 (任意。人の意思のみ)
- 作成からの経過日数、最終commitからの経過日数、期限までの残り日数、期限超過日数の表示
- cleanup readinessのfail-closed判定 (測定不能は「安全」と言わない)
- 削除を行わない人間レビューpacket

## github-ops-skills adapterが所有するもの

- repository owner/nameとidentityの確認
- PR番号、merge状態、merge方式、base、head SHAの一次取得
- API観測時刻とactorの記録
- secretを含まない統合証跡の返却

Coreへ渡す`integration`は次を必須とします。

```json
{
  "status": "verified",
  "provider": "github",
  "evidence_type": "github_pr_merged",
  "provider_record_id": "github-pr:123",
  "subject_head_sha": "0123456789abcdef0123456789abcdef01234567",
  "resulting_base_sha": "89abcdef0123456789abcdef0123456789abcdef",
  "actor": "github-api",
  "observed_at": "2026-08-06T10:00:00+09:00"
}
```

scan対象のHEADと`subject_head_sha`が一致しない場合、統合済みへ昇格しません。squash/rebaseで元commitがbaseから到達不能でも、exact headと統合先SHAに結び付いた一次証拠がある場合だけcleanup判定へ進めます。

証拠はscan時点から7日以内だけ有効とし、未来時刻、`unknown` provider/actor、型不正はentry単位のblockerとして隔離します。

## 削除境界

次は別々の承認境界です。

1. worktree削除
2. local branch削除
3. remote branch削除
4. PR close
5. 定期実行・hook・Projects全体配線

このMVPは、いずれも実行しません。
