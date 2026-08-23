# 公開準備記録

状態: **PRIVATE／公開候補作業中／外部操作は未承認**

このファイルは公開判断の証拠を集めるものであり、公開許可そのものではありません。

## 対象

- GitHub repository: `nexus-ai-2045/worktree-lifecycle-control`
- default branch: `main` (`40857e6` feat: worktree lifecycle control 初回公開 (v0.1.0))
- 公開候補: README、LICENSE、SECURITY.md、CONTRIBUTING.md、PREFLIGHT.md、schemas、src、tests、docs
- 公開除外: 実運用 registry、`.local/` の scan report、特定マシンの絶対パス、個人名 fixture

## ローカル実測

- [x] MIT LICENSE あり（Copyright (c) 2026 nexus_ai）
- [x] README / SECURITY.md / CONTRIBUTING.md / PREFLIGHT.md あり
- [x] README 情報設計ゲート pass（目的 / できること / クイックスタート / 制約）
- [x] 現在 tree の secret scan 0 件
- [x] 作者の実効名義は `nexus_ai` の GitHub noreply。squash の committer `GitHub <noreply@github.com>` は受け入れ
- [ ] 既存履歴 blob の個人ホームを模した test fixture。履歴 rewrite はしない方針
- [ ] remote CI。GitHub Actions は billing / spending limit で未起動
- [ ] GitHub の Private vulnerability reporting（public 化直後。private では API 404）
- [ ] secret scanning / push protection（public 化直後。新規 public の既定は disabled）
- [ ] 公開後の README・リンク・visibility の read-back

## 検査の限界

機械検査は独自形式の秘密、画像・大容量 binary、第三者素材の権利、内容の妥当性、
remote 設定、実際の CI 成功を完全には保証しません。

`repo-preflight` の pass 相当は公開承認ではありません。
履歴 rewrite なしでは、過去 commit の test fixture に個人ホームを模した絶対パスが残ります。

## 公開の停止線

公開前に、対象 repo、実行する visibility 変更コマンド、README、LICENSE、SECURITY.md、
secret scan、personal path scan、このファイルの結果を提示します。
commit 履歴と全ファイルが Web から閲覧可能になることを説明し、
repo 固有の明示承認を得るまで visibility を変更しません。

候補操作:

```text
gh repo edit nexus-ai-2045/worktree-lifecycle-control --visibility public
```
