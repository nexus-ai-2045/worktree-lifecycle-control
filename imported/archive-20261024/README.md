# 控えから取り込んだ古い版（2026-10-24）

このフォルダには、履歴を作り直す前の非公開の控えにだけ残っていた中身を、消さずに残すために置いています。

- 置き方: `<控えの枝またはタグ>/<元のパス>`。中身は控えのままです（下の「伏せたもの」を除く）。
- ここにあるファイルは実行も読み込みもされません。テストの収集からも外しています。
- 今のコードへ採り入れるかは、ファイルごとに別のPRで決めます。
- 公開側の履歴に同じ中身があるもの、行がすべて公開側にあるもの、ほかの版で行がそろうものは入れていません。
- 意図して外されていたもの（個人の情報を含む、公開側で削除済みなど）は入れていません。

## 入れたファイル

| 元のパス | 控えの枝・タグ | 置き場所 | 備考 |
|---|---|---|---|
| `.github/workflows/ci.yml` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/.github/workflows/ci.yml` |  |
| `CONTRIBUTING.md` | `codex/projects-wide-shadow-rollout` ほか1 | `codex/projects-wide-shadow-rollout/CONTRIBUTING.md` |  |
| `PUBLIC_READY.md` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/PUBLIC_READY.md` |  |
| `README.md` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/README.md` |  |
| `SECURITY.md` | `codex/projects-wide-shadow-rollout` ほか1 | `codex/projects-wide-shadow-rollout/SECURITY.md` |  |
| `docs/architecture.md` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/docs/architecture.md` |  |
| `docs/integration-projects.md` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/docs/integration-projects.md` |  |
| `docs/reviews/2026-08-06-mvp-human-review.fact.json` | `codex/projects-wide-shadow-rollout` ほか1 | `codex/projects-wide-shadow-rollout/docs/reviews/2026-08-06-mvp-human-review.fact.json` |  |
| `docs/reviews/2026-08-06-mvp-human-review.md` | `codex/projects-wide-shadow-rollout` ほか1 | `codex/projects-wide-shadow-rollout/docs/reviews/2026-08-06-mvp-human-review.md` |  |
| `pyproject.toml` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/pyproject.toml` |  |
| `registry.example.json` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/registry.example.json` |  |
| `schemas/integration-evidence-v2.schema.json` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/schemas/integration-evidence-v2.schema.json` |  |
| `src/worktree_lifecycle_control/cli.py` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/src/worktree_lifecycle_control/cli.py` |  |
| `src/worktree_lifecycle_control/closeout_adapter.py` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/src/worktree_lifecycle_control/closeout_adapter.py` |  |
| `src/worktree_lifecycle_control/evidence.py` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/src/worktree_lifecycle_control/evidence.py` |  |
| `tests/test_lifecycle.py` | `codex/projects-wide-shadow-rollout` | `codex/projects-wide-shadow-rollout/tests/test_lifecycle.py` |  |
| `PREFLIGHT.md` | `tags/v0.1.0` | `tags/v0.1.0/PREFLIGHT.md` |  |
| `README.md` | `tags/v0.1.0` | `tags/v0.1.0/README.md` |  |
| `docs/decisions/0002-protect-what-git-does-not.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/decisions/0002-protect-what-git-does-not.md` |  |
| `docs/integration-projects.md` | `tags/v0.1.0` | `tags/v0.1.0/docs/integration-projects.md` |  |
| `src/worktree_lifecycle_control/cli.py` | `tags/v0.1.0` | `tags/v0.1.0/src/worktree_lifecycle_control/cli.py` |  |
| `src/worktree_lifecycle_control/closeout_adapter.py` | `tags/v0.1.0` | `tags/v0.1.0/src/worktree_lifecycle_control/closeout_adapter.py` |  |
| `src/worktree_lifecycle_control/reachability.py` | `tags/v0.1.0` | `tags/v0.1.0/src/worktree_lifecycle_control/reachability.py` |  |

## 伏せたもの

- なし
