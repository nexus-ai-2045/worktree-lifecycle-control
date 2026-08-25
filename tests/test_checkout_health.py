from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from worktree_lifecycle_control.checkout_health import (
    classify_git_failure,
    compare_with_baseline,
    find_leftover_worktree_dirs,
    load_baseline,
    probe_repo,
)
from worktree_lifecycle_control.cli import main


DUBIOUS_STDERR = (
    b"fatal: detected dubious ownership in repository at 'C:/repo'\n"
    b"'C:/repo' is owned by:\n\tBUILTIN/Administrators (S-1-5-32-544)\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-m", "init")
    return path


# --- 失敗分類: stderr だけで判定できる純粋関数 --------------------------------


def test_classify_git_failure_detects_dubious_ownership() -> None:
    assert classify_git_failure(DUBIOUS_STDERR) == "dubious_ownership"


def test_classify_git_failure_detects_not_a_repository() -> None:
    stderr = b"fatal: not a git repository (or any of the parent directories): .git\n"
    assert classify_git_failure(stderr) == "not_a_repository"


def test_classify_git_failure_falls_back_to_other() -> None:
    assert classify_git_failure(b"fatal: something unexpected\n") == "other"


# --- repo probe: 実 repo で git が素で動くことを測る --------------------------


def test_probe_repo_reports_usable_for_healthy_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    health = probe_repo(repo)
    assert health.git_usable is True
    assert health.failure_kind is None
    assert health.prunable_paths == ()


def test_probe_repo_reports_failure_kind_for_non_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    health = probe_repo(plain)
    assert health.git_usable is False
    assert health.failure_kind == "not_a_repository"


def test_probe_repo_reports_prunable_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt))
    # 実体だけ消して metadata を残すと prunable になる (FDE 2026-08-24 と同型)。
    _git(repo, "worktree", "remove", str(wt))
    _git(repo, "worktree", "add", str(wt))
    import shutil

    shutil.rmtree(wt)
    health = probe_repo(repo)
    assert health.git_usable is True
    assert len(health.prunable_paths) == 1


# --- 残骸 dir: 登録解除済みなのに実体が残った worktree ------------------------


def test_find_leftover_detects_dir_with_dead_gitdir_pointer(tmp_path: Path) -> None:
    root = tmp_path / "root"
    leftover = root / "repo-pr14"
    leftover.mkdir(parents=True)
    (leftover / ".git").write_text(
        "gitdir: C:/nonexistent/.git/worktrees/repo-pr14\n", encoding="utf-8"
    )
    found = find_leftover_worktree_dirs(root)
    assert [item.path for item in found] == [str(leftover)]


def test_find_leftover_ignores_live_worktree_and_plain_dirs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    repo = _init_repo(tmp_path / "repo")
    live = root / "live-wt"
    _git(repo, "worktree", "add", str(live))
    (root / "plain").mkdir()
    normal_repo = _init_repo(root / "normal-repo")
    assert normal_repo.exists()
    assert find_leftover_worktree_dirs(root) == ()


def test_find_leftover_of_missing_root_is_empty(tmp_path: Path) -> None:
    assert find_leftover_worktree_dirs(tmp_path / "nope") == ()


# --- ratchet: baseline より悪化したら fail ------------------------------------


def test_compare_flags_regression_when_count_exceeds_baseline() -> None:
    regressions = compare_with_baseline(
        {"dubious_ownership": 1, "leftover_worktree_dirs": 0},
        {"dubious_ownership": 0, "leftover_worktree_dirs": 0},
    )
    assert regressions == ["dubious_ownership: 0 -> 1"]


def test_compare_treats_missing_baseline_category_as_zero() -> None:
    regressions = compare_with_baseline({"new_kind": 2}, {})
    assert regressions == ["new_kind: 0 -> 2"]


def test_compare_accepts_improvement_without_regression() -> None:
    assert compare_with_baseline({"dubious_ownership": 0}, {"dubious_ownership": 3}) == []


def test_load_baseline_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_version": "nope", "counts": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(path)


# --- CLI 統合 -----------------------------------------------------------------


def test_health_command_reports_ok_for_healthy_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _init_repo(tmp_path / "repo")
    exit_code = main(["health", "--repo", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == "checkout-health-report/v1"
    assert payload["counts"]["dubious_ownership"] == 0
    assert payload["read_only"] is True


def test_health_command_fails_on_ratchet_regression(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    leftover = root / "gone-wt"
    leftover.mkdir(parents=True)
    (leftover / ".git").write_text("gitdir: C:/nonexistent/.git/worktrees/x\n", encoding="utf-8")
    repo = _init_repo(tmp_path / "repo")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"schema_version": "checkout-health-baseline/v1", "counts": {}}),
        encoding="utf-8",
    )
    exit_code = main(
        ["health", "--repo", str(repo), "--root", str(root), "--baseline", str(baseline), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["counts"]["leftover_worktree_dirs"] == 1
    assert payload["ratchet"]["regressions"] == ["leftover_worktree_dirs: 0 -> 1"]


def test_health_update_baseline_writes_current_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _init_repo(tmp_path / "repo")
    baseline = tmp_path / "baseline.json"
    exit_code = main(
        ["health", "--repo", str(repo), "--baseline", str(baseline), "--update-baseline", "--json"]
    )
    assert exit_code == 0
    stored = json.loads(baseline.read_text(encoding="utf-8"))
    assert stored["schema_version"] == "checkout-health-baseline/v1"
    assert stored["counts"]["dubious_ownership"] == 0
    capsys.readouterr()
