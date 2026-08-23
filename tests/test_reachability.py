"""実 git に対する到達性・統合判定の挙動を固定する。

このツールの中核は「worktree を消したら commit が失われるか」の一点であり、
その答えは git の実挙動でしか確かめられない。bool を注入した単体テストだけでは、
git 側の仕様が変わっても気付けない。2026-08-15 に隔離 repo で手動実行した対照実験を、
CI が毎回実行する形へ移した。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktree_lifecycle_control.reachability import (
    branch_integration,
    head_reachability,
    resolve_base_ref,
    run_git,
)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, text=True
    )
    return proc.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """commit を 1 つ持つ最小 repo。CI の既定 identity に依存しない。"""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-m", "first")
    return root


def commit_file(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"add {name}")
    return git(repo, "rev-parse", "HEAD")


def test_commit_on_a_branch_stays_reachable(repo: Path) -> None:
    """branch が指す commit は worktree を消しても残る。

    実験 1 の再現。未 push commit を「守るべきもの」に数えていた根拠が、
    そもそも存在しないことを示す。
    """
    git(repo, "checkout", "-b", "feature")
    head = commit_file(repo, "b.txt", "b\n")
    git(repo, "checkout", "main")
    assert head_reachability(repo, head) is True


def test_detached_commit_is_unreachable(repo: Path) -> None:
    """どの branch/tag/remote からも指されない commit は到達不能と判定する。

    実験 3 の再現。git はこの状態の worktree を無警告で削除し、gc 後に commit を失う。
    ツールが名指しすべき唯一の危険。
    """
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", base)
    orphan = commit_file(repo, "c.txt", "c\n")
    git(repo, "checkout", "main")
    assert head_reachability(repo, orphan) is False


def test_naming_the_detached_commit_makes_it_reachable(repo: Path) -> None:
    """branch を付ければ到達可能に変わる。修復手順が実際に効くことを固定する。"""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", base)
    orphan = commit_file(repo, "c.txt", "c\n")
    git(repo, "checkout", "main")
    assert head_reachability(repo, orphan) is False
    git(repo, "branch", "rescue", orphan)
    assert head_reachability(repo, orphan) is True


def test_tag_alone_keeps_a_commit_reachable(repo: Path) -> None:
    """tag も根に数える。branch だけを見ると tag 保護された commit を危険と誤報する。"""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", base)
    orphan = commit_file(repo, "c.txt", "c\n")
    git(repo, "tag", "keep", orphan)
    git(repo, "checkout", "main")
    assert head_reachability(repo, orphan) is True


def test_unknown_head_is_reported_as_unknown(repo: Path) -> None:
    """測定できないことを True (安全) と言わない。fail-closed を保つ。"""
    assert head_reachability(repo, None) is None
    assert head_reachability(repo, "0" * 40) is None


def test_merged_branch_is_integrated(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature")
    head = commit_file(repo, "b.txt", "b\n")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "-m", "merge feature", "feature")
    assert branch_integration(repo, head, "main") == "integrated"


def test_squash_merged_branch_is_integrated_via_patch_equivalence(repo: Path) -> None:
    """squash merge は祖先関係を作らない。patch 等価判定でしか拾えない。

    `git merge-base --is-ancestor` だけで判定していると、squash merge した branch が
    永久に「未統合」と表示される。
    """
    git(repo, "checkout", "-b", "feature")
    head = commit_file(repo, "b.txt", "b\n")
    git(repo, "checkout", "main")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature")
    # 祖先ではない (squash は履歴を引き継がない)
    ancestry = run_git(repo, "merge-base", "--is-ancestor", head, "main")
    assert ancestry.returncode != 0
    # patch 等価では取り込み済みと判定できる
    assert branch_integration(repo, head, "main") == "integrated"


def test_divergent_branch_is_not_integrated(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature")
    head = commit_file(repo, "b.txt", "b\n")
    git(repo, "checkout", "main")
    assert branch_integration(repo, head, "main") == "not_integrated"


def test_integration_is_unknown_without_a_base(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert branch_integration(repo, head, None) == "unknown"
    assert branch_integration(repo, None, "main") == "unknown"


def test_base_ref_falls_back_to_local_main(repo: Path) -> None:
    """remote が無い repo でもローカル main を base として解決できる。"""
    assert resolve_base_ref(repo) == "main"


def test_run_git_reports_failure_without_raising(tmp_path: Path) -> None:
    """git の失敗を例外にしない。呼び出し側が「不明」を表現できなくなるため。"""
    proc = run_git(tmp_path / "not-a-repo", "rev-parse", "HEAD")
    assert proc.returncode != 0
