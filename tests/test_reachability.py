"""実 git に対する到達性・統合判定の挙動を固定する。

このツールの中核は「worktree を消したら commit が失われるか」の一点であり、
その答えは git の実挙動でしか確かめられない。bool を注入した単体テストだけでは、
git 側の仕様が変わっても気付けない。2026-08-15 に隔離 repo で手動実行した対照実験を、
CI が毎回実行する形へ移した。
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from worktree_lifecycle_control.cli import scan_repo
from worktree_lifecycle_control.reachability import (
    branch_integration,
    head_reachability,
    resolve_base_ref,
    run_git,
    unreachable_content_proof,
)

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


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


# --- 到達不能だが内容は取り込み済み -----------------------------------------
#
# 2026-09-25 の実測で、danger 2 件がどちらも実損ゼロの誤検知だった。
# どちらも「作業 branch に main を merge → PR を squash / rebase で取り込み →
# branch 削除 → worktree だけが detached で残る」形をしている。
# 以下の fixture はその 2 件の形を最小の commit 数で再現する。


def branch_off_merge_main_then_detach(repo: Path) -> dict[str, str]:
    """作業 branch に main を 1 回 merge し、さらに 1 commit 進めてから branch を消す。

    返り値の head は worktree に残った detached HEAD に相当し、どの ref からも届かない。
    merge は衝突の無い自動 merge で、merge commit 自身は独自の内容を持たない。
    """
    git(repo, "checkout", "-b", "feature")
    first = commit_file(repo, "b.txt", "b1\n")
    git(repo, "checkout", "main")
    main_before_merge = commit_file(repo, "other.txt", "main-1\n")
    git(repo, "checkout", "feature")
    git(repo, "merge", "--no-ff", "-m", "Merge main into feature", "main")
    merge = git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b2\n", encoding="utf-8")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    git(repo, "add", "b.txt", "c.txt")
    git(repo, "commit", "-m", "second feature commit")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    return {"first": first, "merge": merge, "head": head, "main_before_merge": main_before_merge}


def test_rebase_merged_branch_with_clean_merge_is_proven_integrated(repo: Path) -> None:
    """nexus_ai 6f10d39e の再現。各 commit は patch 同一で main にあり、merge は自動 merge。

    PR を rebase merge すると、非 merge commit は patch-id の等しい別 commit として main に載る。
    branch を消すと元の commit は到達不能になるが、失われる内容は無い。
    main 側はその後さらに同じファイルを変えている (「main の方が新しい」)。
    """
    shas = branch_off_merge_main_then_detach(repo)
    git(repo, "cherry-pick", shas["first"])
    git(repo, "cherry-pick", shas["head"])
    commit_file(repo, "b.txt", "b3 main is newer\n")
    git(repo, "branch", "-D", "feature")

    assert head_reachability(repo, shas["head"]) is False
    proof = unreachable_content_proof(repo, shas["head"], "main")
    assert proof is not None
    assert proof["method"] == "patch_equivalent"
    assert proof["clean_merges"] == [shas["merge"]]


def test_squash_merged_branch_with_clean_merge_is_proven_by_tree_match(repo: Path) -> None:
    """Projects 0bc1ab13 の再現。複数 commit を 1 つに潰したので patch-id は一致しない。

    squash commit と、枝の非 merge commit が触ったパスの内容が完全に一致することで証明する。
    merge が main から持ち込んだファイル (other.txt) は比較に含めない。main はその後
    other.txt を変えているので、含めると一致しなくなる (実測で 0bc1ab13 がこの形だった)。
    """
    shas = branch_off_merge_main_then_detach(repo)
    commit_file(repo, "other.txt", "main-2 after feature merged main\n")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature (#1)")
    squash = git(repo, "rev-parse", "HEAD")
    commit_file(repo, "b.txt", "b3 main is newer\n")
    git(repo, "branch", "-D", "feature")

    assert head_reachability(repo, shas["head"]) is False
    # patch 同一性では拾えないことを前提として固定する
    assert branch_integration(repo, shas["head"], "main") == "not_integrated"
    proof = unreachable_content_proof(repo, shas["head"], "main")
    assert proof is not None
    assert proof["method"] == "tree_match"
    assert proof["matched_commit"] == squash
    assert proof["paths"] == ["b.txt", "c.txt"]


def test_unique_detached_commit_has_no_proof(repo: Path) -> None:
    """どこにも取り込まれていない commit は証明できない。従来どおり保護する。"""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--detach", base)
    orphan = commit_file(repo, "c.txt", "only here\n")
    git(repo, "checkout", "main")
    assert unreachable_content_proof(repo, orphan, "main") is None


def test_partially_integrated_branch_has_no_proof(repo: Path) -> None:
    """一部の commit だけが取り込まれた branch は証明できない。"""
    git(repo, "checkout", "-b", "feature")
    first = commit_file(repo, "b.txt", "b1\n")
    head = commit_file(repo, "c.txt", "not integrated\n")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", first)
    git(repo, "branch", "-D", "feature")
    assert head_reachability(repo, head) is False
    assert unreachable_content_proof(repo, head, "main") is None


def test_evil_merge_content_blocks_the_proof(repo: Path) -> None:
    """merge commit に手で足した内容は patch-id にも非 merge commit のパスにも現れない。

    `git cherry` は merge commit を飛ばすので、非 merge commit が全部取り込み済みでも
    merge だけが持つ内容が消える。merge が自動 merge と一致しない限り証明しない。
    """
    git(repo, "checkout", "-b", "feature")
    first = commit_file(repo, "b.txt", "b1\n")
    git(repo, "checkout", "main")
    commit_file(repo, "other.txt", "main-1\n")
    git(repo, "checkout", "feature")
    git(repo, "merge", "--no-ff", "--no-commit", "main")
    (repo / "evil.txt").write_text("exists only in the merge commit\n", encoding="utf-8")
    git(repo, "add", "evil.txt")
    git(repo, "commit", "-m", "Merge main with a hand edit")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    git(repo, "cherry-pick", first)
    git(repo, "branch", "-D", "feature")

    assert head_reachability(repo, head) is False
    assert unreachable_content_proof(repo, head, "main") is None


def test_proof_is_none_when_it_cannot_be_measured(repo: Path) -> None:
    """測れないことを「取り込み済み」と言わない。"""
    head = git(repo, "rev-parse", "HEAD")
    assert unreachable_content_proof(repo, None, "main") is None
    assert unreachable_content_proof(repo, head, None) is None
    assert unreachable_content_proof(repo, "0" * 40, "main") is None


def test_scan_downgrades_proven_unreachable_head_to_review_signal(repo: Path, tmp_path: Path) -> None:
    """scan 全体を通して、証明できた 1 件は blocker でなく signal になる。"""
    shas = branch_off_merge_main_then_detach(repo)
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature (#1)")
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "--detach", str(worktree), shas["head"])
    git(repo, "branch", "-D", "feature")

    records = {Path(r.path).resolve(): r for r in scan_repo(repo, {"entries": {}}, NOW)}
    record = records[worktree.resolve()]
    assert "head_becomes_unreachable" not in record.blockers
    assert "head_unreachable_content_integrated" in record.review_signals
    assert record.observations["unreachable_content_proof"]["method"] == "tree_match"
    assert record.disposition == "cleanup_candidate"
