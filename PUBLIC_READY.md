# 公開準備記録

状態: **PUBLIC／v0.1.0 公開済み／追随の記録**

このファイルは公開判断の証拠を集めるものであり、公開許可そのものではありません。
visibility は既に public です。これ以上の visibility 変更はしません。

## 対象

- GitHub repository: `nexus-ai-2045/worktree-lifecycle-control`
- default branch: `main` (`40857e6` feat: worktree lifecycle control 初回公開 (v0.1.0)、tag `v0.1.0`)
- 公開対象: README、LICENSE、SECURITY.md、CONTRIBUTING.md、PREFLIGHT.md、schemas、src、tests、docs
- 公開除外: 実運用 registry、`.local/` の scan report、特定マシンの絶対パス、個人名 fixture

## ローカル実測

- [x] MIT LICENSE あり（Copyright (c) 2026 nexus_ai）
- [x] README / SECURITY.md / CONTRIBUTING.md / PREFLIGHT.md あり
- [x] README 情報設計ゲート pass（目的 / できること / クイックスタート / 制約）
- [x] 現在 tree の secret scan 0 件
- [x] ADR / 人間レビューの dogfood 実測から非公開 repo 名、正確な本数、`.local/` ファイル名を外した
- [x] 作者の実効名義は `nexus_ai` の GitHub noreply。squash の committer `GitHub <noreply@github.com>` は受け入れ
- [x] visibility read-back: PUBLIC
- [x] secret scanning / push protection: enabled。secret-scanning alerts 0 件
- [x] remote CI: public 化後の PR では ubuntu / windows とも SUCCESS
- [ ] 既存履歴 blob の個人ホームを模した test fixture。履歴 rewrite はしない方針
- [ ] GitHub の Private vulnerability reporting（API 上は enabled=false）

## 検査の限界

機械検査は独自形式の秘密、画像・大容量 binary、第三者素材の権利、内容の妥当性、
remote 設定、実際の CI 成功を完全には保証しません。

`repo-preflight` の pass 相当は公開承認ではありません。
履歴 rewrite なしでは、過去 commit の test fixture に個人ホームを模した絶対パスが残ります。
公開済み default branch の過去 commit には、一般化前の dogfood 数字も残ります。
