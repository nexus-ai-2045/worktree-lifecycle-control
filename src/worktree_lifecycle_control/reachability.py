"""git から導出できる事実だけを扱う。台帳 (registry) はここに関与しない。

このモジュールが答えるのは 2 つだけである。

1. `head_reachability` — worktree の HEAD commit は、その worktree 自身の HEAD 以外の
   ref からも到達できるか。到達できるなら worktree を消しても commit は残る。
   到達できないなら git は警告なしに消し、gc で完全に失われる。
2. `branch_integration` — その HEAD は統合先 (base) に取り込まれているか。
   merge なら祖先判定で、squash / rebase なら patch-id 等価判定で拾う。

1 は削除を止める条件 (blocker) である。2 は削除を止めない表示用の signal である。
この 2 つを混ぜないことが本モジュールの存在理由で、混ぜていたのが v2 までの欠陥だった。

根拠 (2026-08-15 隔離 repo での対照実験):

- 未 push commit を持つ worktree を削除 → branch が残り commit も内容も復元できた。
  よって「未 push だから守る」は存在しない危険から守っていた。
- dirty な worktree を削除 → `git worktree remove` 自身が拒否した。
  よって「dirty だから守る」は git の重複実装だった。
- detached HEAD の worktree を削除 → git は無警告で削除し、`git gc` 後に commit が消滅した。
  git が守らないのはここだけであり、ツールが名指しすべきもここだけである。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


GIT_TIMEOUT_SECONDS = 60
"""git 呼び出しの上限。応答しない git を無期限に待つと scan 全体が止まる。"""

BASE_REF_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")


def run_git(repo: Path | str, *args: str, timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[bytes]:
    """git をタイムアウト付きで実行する。失敗は例外にせず returncode で返す。

    タイムアウトした場合は returncode=124 の CompletedProcess を合成して返す。
    呼び出し側は「失敗」と「不明」を区別できないと fail-open するため、
    ここで例外を投げずに「不明」を表現できる形へ落とす。
    """
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=["git", *args], returncode=124, stdout=b"", stderr=b"timeout")
    except OSError as error:  # git 不在・パス不正など
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout=b"", stderr=str(error).encode())


def head_reachability(repo: Path | str, head: str | None) -> bool | None:
    """HEAD が worktree 自身以外の ref から到達できるかを返す。不明なら None。

    判定には `git rev-list --max-count=1 <head> --not --branches --tags --remotes` を使う。
    これは「head から到達できるが、どの branch / tag / remote-tracking ref からも
    到達できない commit」を 1 件だけ探す。1 件でも出れば、その worktree の HEAD を
    失った瞬間に到達不能になる commit が実在する。

    根に数えないものが 3 つあり、いずれも意図的である。

    - reflog: worktree 専用 reflog は `.git/worktrees/<id>/logs/` にあり、worktree
      削除で一緒に消える。消える予定の根を根に数えると判定が甘くなる。`git rev-list` が
      既定で reflog を根に含めないのは、ここでは仕様の理解であって漏れではない。
    - 他 worktree の detached HEAD: それ自体が耐久性のない参照。数えると
      「互いに参照し合う 2 つの worktree はどちらも安全」という誤りになる。
    - refs/stash など refs/heads・refs/tags・refs/remotes 以外の ref: 数えない分だけ
      判定は保護側へ倒れる。誤って「安全」と言うより、誤って「危険」と言う方を選ぶ。

    用語は git の公式用語に合わせる (gitglossary(7)): 到達できない object は unreachable、
    どの unreachable object からも参照されないものは dangling。先行実装として
    larsch/git-remove が「安全性を証明できなければ削除しない」を remote ref 起点で
    実装している。本実装はローカル ref 起点である点と、削除せず人間レビュー用の
    候補提示に留める点が異なる。
    """
    if not head:
        return None
    proc = run_git(repo, "rev-list", "--max-count=1", head, "--not", "--branches", "--tags", "--remotes")
    if proc.returncode != 0:
        return None
    return not proc.stdout.strip()


def resolve_base_ref(repo: Path | str) -> str | None:
    """統合先の base ref を解決する。見つからなければ None。"""
    for candidate in BASE_REF_CANDIDATES:
        proc = run_git(repo, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if proc.returncode == 0 and proc.stdout.strip():
            return candidate
    return None


def branch_integration(repo: Path | str, head: str | None, base_ref: str | None) -> str:
    """HEAD が base に取り込まれているかを "integrated" / "not_integrated" / "unknown" で返す。

    `git cherry <base> <head>` を使う。git 自身の patch-id 等価判定なので、
    merge (祖先関係) と squash / rebase (内容一致) の両方を 1 つのコマンドで拾える。
    祖先判定 (`merge-base --is-ancestor`) だけでは squash merge を取りこぼす。

    出力仕様: `+ <sha>` = base 側に等価な patch なし / `- <sha>` = 等価な patch あり。
    出力が空 = base より先行する commit なし = 取り込み済み。

    既知の限界: 複数 commit を 1 つに潰す squash merge では、潰した後の patch-id が
    元の各 commit の patch-id と一致せず、取り込み済みを検出できない場合がある。
    ここは signal (表示用) であって削除条件ではないため、取りこぼしは「未統合と
    表示される」で済み、誤って削除候補へ上げる方向には倒れない。追加の
    ヒューリスティクス (git-delete-merged-branches の --effort=3 相当) は判定を
    重くする割に blocker を動かさないので入れない。
    """
    if not head or not base_ref:
        return "unknown"
    proc = run_git(repo, "cherry", base_ref, head)
    if proc.returncode != 0:
        return "unknown"
    lines = [line for line in proc.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return "integrated"
    if all(line.startswith("-") for line in lines):
        return "integrated"
    return "not_integrated"
