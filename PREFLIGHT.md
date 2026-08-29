<!-- repo-preflight:review-record -->

# 公開範囲とレビュー状態

このリポジトリは、Git worktree のライフサイクルを read-only で棚卸しし、cleanup readiness を fail-closed に判定するローカル制御ツールです。

## 現在の公開状態

- GitHub repository: `nexus-ai-2045/worktree-lifecycle-control`
- GitHub visibility: **public**
- default branch: `main`
- release: `v0.1.0`
- 追随記録: `PUBLIC_READY.md`

このファイルは現在の境界宣言です。visibility を変える許可ではありません。

## 公開対象

- read-only scan / review-packet CLI
- unit test
- schema 契約、architecture、統合境界ドキュメント
- MIT License とセキュリティ報告方針
- GitHub About（説明と topics）

## 公開対象外

- 実運用 registry の owner/task 台帳
- `.local/` 配下の scan report / human review packet
- 特定マシンの絶対パス、個人名 fixture
- 削除 executor、GitHub write、定期実行配線

## 既知の履歴残渣

履歴 rewrite はしない。過去 commit には個人ホームを模した test fixture と、一般化前の dogfood 数字が残る。現在 tree からは外してある。

## 開発保証ゲート（CI）

検査ロジックは本リポジトリへコピーしない。上流を CI から直接呼ぶ。

| 契約 | 上流 | 設定 | CI での扱い |
|---|---|---|---|
| 文書・実装の宣言整合 | `nexus-ai-2045/repo-preflight`（pin SHA） | `.repo-preflight-consistency.json`（`shadow`） | `consistency_gate` + `readiness_scan`。shadow 所見は止めない。`tool_error` は fail-closed |
| tracked ∧ ignored の新規悪化 | `nexus-ai-2045/ai-ratchet-gate` v0.1.1（wheel + SHA-256） | `.ai-ratchet-gate/baseline.txt` | 既存分は grandfather。baseline に無い新規だけ deny |

古い `.repo-preflight.json` は preferences 用途の旧名だったため、整合契約は `.repo-preflight-consistency.json` へ canonicalize し、旧ファイルは残さない。`engineering-brain` は CI に埋め込まない。

`workflow_dispatch` で BASE と HEAD が同一（空 diff）のときは、差分検査を緑にしない（fail-closed）。

## 停止線

CI green や `readiness_scan` の部分 pass は、追加の公開操作を許可しません。visibility 変更、release、告知の前に次が要ります。

1. README / LICENSE / SECURITY / CONTRIBUTING / PREFLIGHT の人間レビュー
2. secret scan と personal path scan の再測定（現在 tree と履歴を分ける）
3. 現行 CI 結果の再確認
4. 対象 repository と正確な操作文面での、現在会話での明示承認
