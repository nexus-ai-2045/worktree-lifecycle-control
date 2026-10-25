# コントリビューション

人間が手順を踏む必要はありません。AI に次を貼ってください。先に危険レビューを出させます。

```text
このリポジトリを読んで直して。先に危険レビューを出せ。
削除・--force・GitHub write・visibility 変更・secret 露出・個人パスを実行していないか。
cleanup_candidate を削除許可と読まない。unknown を安全と読まない。
問題があればコードを書く前に止めろ。CONTRIBUTING を守れ。

https://github.com/nexus-ai-2045/worktree-lifecycle-control
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/README.md
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/CONTRIBUTING.md
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/SECURITY.md
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/PREFLIGHT.md
```

## ルール

- CLI の既定動作は read-only を保つ。
- `unknown` や tool failure を pass や cleanup_candidate へ丸めない。
- secret 本文を test output や report へ残さない。
- Windows / macOS / Linux の path 差を考慮する。
- 判定変更は失敗するテストを先に追加する。
- 削除、push、PR、merge、visibility 変更を本 CLI に自動化しない。

ローカルで確認するときだけ:

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```
