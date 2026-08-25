"""checkout がそもそも測定可能かを見る、scan の前提検査 (checkout health)。

scan / review-packet は「git が素で動く checkout」を前提にしている。その前提が
壊れると、このツールは worktree を守るどころか RuntimeError で止まり、依存する
運用保証 (closeout 等) は error の山になって真因を教えない。

根拠 (2026-08-24 fractal-decision-ecosystem での実測):

- 昇格シェルが作った checkout が BUILTIN/Administrators 所有になり、git が全 command を
  `dubious ownership` で拒否した。repo 自身の closeout は常時 error になり、pytest の
  7 failed のうち 6 件がこの単一原因の連鎖だった。
- Windows の `git worktree remove` は登録解除だけ成功して実体 dir を残すことがある
  (Directory not empty)。残骸は `.git` ファイルが消滅した metadata を指したまま腐る。

このモジュールは検知だけを行う。修復 (takeown / prune / 削除) は人間の承認境界の
向こう側にあり、report の repair_hint として提示するに留める。

ratchet 契約: baseline より件数が増えたら fail (単調非増加)。一度 0 にした種別の
再発を機械が止める。改善 (減少) は fail にせず、baseline 更新は `--update-baseline`
の明示操作だけで行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reachability import run_git

CHECKOUT_HEALTH_SCHEMA_VERSION = "checkout-health-report/v1"
BASELINE_SCHEMA_VERSION = "checkout-health-baseline/v1"

COUNT_CATEGORIES = (
    "dubious_ownership",
    "git_unusable_other",
    "prunable_worktrees",
    "leftover_worktree_dirs",
)

REPAIR_HINTS = {
    "dubious_ownership": (
        "checkout の所有者が現ユーザーではない。Windows は powershell.exe 経由で "
        "`takeown /F <path> /R /D Y` (Git Bash 直は MSYS 変換で /F が壊れる)、"
        "POSIX は chown。`safe.directory` は回避であって修復ではない"
    ),
    "git_unusable_other": "git が動かない理由を stderr で確認する (repo 消失 / timeout など)",
    "prunable_worktrees": "`git worktree prune` で metadata を掃除する",
    "leftover_worktree_dirs": (
        "登録解除済みの残骸 dir。中身が統合済みであることを確認してから削除する"
    ),
}


@dataclass(frozen=True)
class RepoHealth:
    repo: str
    git_usable: bool
    failure_kind: str | None
    failure_detail: str | None
    prunable_paths: tuple[str, ...]


@dataclass(frozen=True)
class LeftoverDir:
    path: str
    gitdir_target: str


def classify_git_failure(stderr: bytes | str) -> str:
    """git の失敗 stderr を種別へ落とす純粋関数。

    分類は「修復方法が違うもの」だけに分ける。dubious ownership は所有権修復、
    not_a_repository は設定 (対象リスト) の修復であり、混ぜると repair_hint が
    嘘になる。
    """
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
    lowered = text.lower()
    if "dubious ownership" in lowered:
        return "dubious_ownership"
    if "not a git repository" in lowered:
        return "not_a_repository"
    return "other"


def probe_repo(repo: Path) -> RepoHealth:
    """repo 1 つの git 可用性と prunable worktree を測る。

    `git status` を素で (safe.directory 等の回避なしで) 実行するのが要点。
    回避付きで測ると「回避すれば動く」ことしか分からず、素の運用 (repo 自身の
    script / CI / 他ツール) が壊れている事実を見逃す。
    """
    status = run_git(repo, "status", "--porcelain")
    if status.returncode != 0:
        kind = "timeout" if status.returncode == 124 else classify_git_failure(status.stderr)
        detail = status.stderr.decode("utf-8", errors="replace").strip().splitlines()
        return RepoHealth(
            repo=str(repo),
            git_usable=False,
            failure_kind=kind,
            failure_detail=detail[0] if detail else None,
            prunable_paths=(),
        )
    listing = run_git(repo, "worktree", "list", "--porcelain", "-z")
    prunable: list[str] = []
    if listing.returncode == 0:
        from .cli import parse_porcelain_z  # 循環 import 回避のため遅延

        prunable = [
            record["path"] for record in parse_porcelain_z(listing.stdout) if record.get("prunable")
        ]
    return RepoHealth(
        repo=str(repo),
        git_usable=True,
        failure_kind=None,
        failure_detail=None,
        prunable_paths=tuple(prunable),
    )


def find_leftover_worktree_dirs(root: Path) -> tuple[LeftoverDir, ...]:
    """root 直下から、登録解除済み worktree の残骸 dir を探す。

    判定は「`.git` が通常ファイルで、その gitdir 先が存在しない」だけに絞る。
    生きた worktree (gitdir 先が存在する) と通常 repo (`.git` が directory) は
    対象外。名前や更新日時の推測で広げると、作業中の dir を誤検知する。
    """
    if not root.is_dir():
        return ()
    result: list[LeftoverDir] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        marker = child / ".git"
        if not marker.is_file():
            continue
        try:
            first_line = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue
        if not first_line.startswith("gitdir:"):
            continue
        target = first_line[len("gitdir:") :].strip()
        if target and not Path(target).exists():
            result.append(LeftoverDir(path=str(child), gitdir_target=target))
    return tuple(result)


def load_baseline(path: Path) -> dict[str, int]:
    """baseline を読む。無い・壊れているは別の事実として扱う。

    存在しない baseline は「全種別 0 を要求」ではなく error にする。初回は
    `--update-baseline` で明示的に作らせ、暗黙の全ゼロ基準で初回から fail する
    体験 (導入即赤) を避ける。
    """
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported baseline schema_version {payload.get('schema_version')!r}; "
            f"expected {BASELINE_SCHEMA_VERSION!r}"
        )
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("baseline counts must be an object")
    result: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"baseline count must be a non-negative integer: {key}")
        result[str(key)] = value
    return result


def compare_with_baseline(
    current: dict[str, int], baseline: dict[str, int]
) -> list[str]:
    """ratchet 比較。baseline に無い種別は 0 として扱う。

    「無い種別 = 0」は新しい違反種別を初回から止めるための選択。既知件数までは
    許し、増加だけを fail にする (単調非増加)。減少は fail にせず、baseline の
    更新は人間の明示操作に残す。
    """
    regressions: list[str] = []
    for key in sorted(current):
        allowed = baseline.get(key, 0)
        if current[key] > allowed:
            regressions.append(f"{key}: {allowed} -> {current[key]}")
    return regressions


def build_counts(
    repos: list[RepoHealth], leftovers: tuple[LeftoverDir, ...]
) -> dict[str, int]:
    counts = dict.fromkeys(COUNT_CATEGORIES, 0)
    for health in repos:
        if not health.git_usable:
            if health.failure_kind == "dubious_ownership":
                counts["dubious_ownership"] += 1
            else:
                counts["git_unusable_other"] += 1
        counts["prunable_worktrees"] += len(health.prunable_paths)
    counts["leftover_worktree_dirs"] = len(leftovers)
    return counts


def repair_hints_for(counts: dict[str, int]) -> dict[str, str]:
    return {key: REPAIR_HINTS[key] for key in COUNT_CATEGORIES if counts.get(key, 0) > 0}


def build_report(
    *,
    repos: list[RepoHealth],
    leftovers: tuple[LeftoverDir, ...],
    targets: list[str],
    roots: list[str],
    observed_at: str,
    run_id: str,
    baseline: dict[str, int] | None,
) -> dict[str, Any]:
    from dataclasses import asdict

    counts = build_counts(repos, leftovers)
    report: dict[str, Any] = {
        "schema_version": CHECKOUT_HEALTH_SCHEMA_VERSION,
        "run_id": run_id,
        "observed_at": observed_at,
        "action": "checkout_health",
        "target": targets,
        "roots": roots,
        "read_only": True,
        "counts": counts,
        "repos": [asdict(health) for health in repos],
        "leftover_worktree_dirs": [asdict(item) for item in leftovers],
        "repair_hints": repair_hints_for(counts),
        "next_action": "review counts and repair hints; no repair is performed",
    }
    if baseline is not None:
        regressions = compare_with_baseline(counts, baseline)
        report["ratchet"] = {
            "baseline_counts": baseline,
            "regressions": regressions,
            "ok": not regressions,
        }
    return report
