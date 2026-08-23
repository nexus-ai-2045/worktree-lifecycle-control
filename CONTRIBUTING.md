# コントリビューション

人間が手順を踏む必要はありません。AI に次を貼ってください。

```text
このリポジトリを CONTRIBUTING のルールで直して。
https://github.com/nexus-ai-2045/worktree-lifecycle-control
```

```text
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/README.md
```

```text
https://raw.githubusercontent.com/nexus-ai-2045/worktree-lifecycle-control/main/CONTRIBUTING.md
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
