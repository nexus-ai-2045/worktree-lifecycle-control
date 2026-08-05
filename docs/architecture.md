# アーキテクチャ

## 責務分離

`worktree-lifecycle-control`はローカルGit worktreeのライフサイクルを管理する独立coreです。GitHub API、credential、account切替、PR操作は所有しません。

```text
local Git / registry
  -> worktree-lifecycle-control
     -> scan report
     -> human review packet

GitHub API / gh
  -> github-ops-skills adapter
     -> integration evidence
        -> worktree-lifecycle-control registry
```

## Coreが所有するもの

- `git worktree list --porcelain -z`の解析
- dirty、未到達commit、lock、path不在の観測
- owner、task、期限、return pathの台帳
- cleanup readinessのfail-closed判定
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
  "source": "github-pr:123",
  "head_sha": "0123456789abcdef0123456789abcdef01234567",
  "actor": "github-api",
  "observed_at": "2026-08-06T10:00:00+09:00"
}
```

scan対象のHEADと`head_sha`が一致しない場合、統合済みへ昇格しません。squash/rebaseで元commitがbaseから到達不能でも、exact headに結び付いた一次証拠がある場合だけcleanup判定へ進めます。

## 削除境界

次は別々の承認境界です。

1. worktree削除
2. local branch削除
3. remote branch削除
4. PR close
5. 定期実行・hook・Projects全体配線

このMVPは、いずれも実行しません。

