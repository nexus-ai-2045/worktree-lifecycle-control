"""git から導出できる事実だけを扱う。台帳 (registry) はここに関与しない。

このモジュールが答えるのは 2 つだけである。

1. `head_reachability` — worktree の HEAD commit は、その worktree 自身の HEAD 以外の
   ref からも到達できるか。到達できるなら worktree を消しても commit は残る。
   到達できないなら git は警告なしに消し、gc で完全に失われる。
2. `branch_integration` — その HEAD は統合先 (base) に取り込まれているか。
   merge なら祖先判定で、squash / rebase なら patch-id 等価判定で拾う。

1 は削除を止める条件 (blocker) である。2 は削除を止めない表示用の signal である。
この 2 つを混ぜないことが本モジュールの存在理由で、混ぜていたのが v2 までの欠陥だった。

1 の例外として `unreachable_content_proof` がある。到達不能になる commit の内容が
すべて base にあると git の事実で証明できた時だけ、1 の blocker を外す (ADR 0004)。
2 のような推測ではなく証明であり、証明できない時は 1 のまま保護する。

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


def run_git(
    repo: Path | str, *args: str, timeout: int = GIT_TIMEOUT_SECONDS, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """git をタイムアウト付きで実行する。失敗は例外にせず returncode で返す。

    タイムアウトした場合は returncode=124 の CompletedProcess を合成して返す。
    呼び出し側は「失敗」と「不明」を区別できないと fail-open するため、
    ここで例外を投げずに「不明」を表現できる形へ落とす。
    """
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
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


TREE_MATCH_CANDIDATE_LIMIT = 200
"""tree 一致の候補として調べる base 側 commit の上限。超えたら証明なし (保護側) に倒す。"""

_UNREACHABLE_ROOTS = ("--not", "--branches", "--tags", "--remotes")


def unreachable_content_proof(repo: Path | str, head: str | None, base_ref: str | None) -> dict | None:
    """到達不能になる commit の内容が、すべて base に取り込み済みであることを証明する。

    証明できた時だけ根拠を dict で返し、それ以外 (未取り込み・測定失敗・判定不能) は
    すべて None を返す。None は「危険」と同じ扱いにする。誤って「取り込み済み」と
    言うより、誤って「危険」と言う方を選ぶ。

    squash / rebase merge した PR の branch を消すと、worktree に残った元の commit は
    必ず到達不能になる。到達可能性だけで判定すると、実損ゼロの worktree が危険として
    並ぶ (2026-09-25 実測で danger 2 件がどちらもこの形だった)。

    証明は次の 2 段で行う。

    1. 到達不能な merge commit は、すべて 2 親で、`git merge-tree --write-tree` が
       作る自動 merge の tree と一致すること。merge commit は patch-id を持たず
       `git cherry` に現れないため、手で足した内容 (evil merge) はここでしか捕まえられない。
       一致すれば merge 自身は独自の内容を持たず、その内容は親から再現できる。
    2. 到達不能な非 merge commit について、次のどちらかが成り立つこと。
       - `patch_equivalent`: 空白を剥がさない patch-id (`git patch-id --verbatim`) が等しい
         commit が base にある。rebase merge / cherry-pick の形。`git cherry` は既定の patch-id
         (空白を無視する) を使うので、インデントだけ違う版でも `-` と出る。signal には使えるが、
         唯一の写しを外す証明には使えない。
       - `tree_match`: それらの commit が触ったパスについて、head の内容と完全に一致する
         commit が base の履歴上に 1 つある。複数 commit を 1 つに潰した squash merge の形。
         比較するパスに merge 経由で入ったものを含めないのは、1 で merge が独自の内容を
         持たないと確かめてあるから。含めると、main がその後変えたファイルで必ず不一致になる。

    失われるのは commit メッセージと途中の状態だけで、これは squash merge 自体と同じ扱いである。
    """
    if not head or not base_ref:
        return None
    listing = run_git(repo, "rev-list", "--parents", head, *_UNREACHABLE_ROOTS)
    if listing.returncode != 0:
        return None
    rows = [line.split() for line in listing.stdout.decode("ascii", errors="replace").splitlines() if line.strip()]
    if not rows:
        return None  # 到達可能。証明の対象外。
    merges = [row for row in rows if len(row) > 2]
    plain = [row[0] for row in rows if len(row) <= 2]

    for row in merges:
        if not _is_clean_two_parent_merge(repo, row):
            return None
    clean_merges = [row[0] for row in merges]
    proof: dict = {"base_ref": base_ref, "unreachable_commits": len(rows), "clean_merges": clean_merges}
    if _all_patch_equivalent(repo, head, base_ref, plain):
        return {**proof, "method": "patch_equivalent"}

    paths = _paths_touched_by(repo, plain)
    if not paths:
        return None
    matched = _find_tree_match(repo, head, base_ref, paths)
    if matched is None:
        return None
    return {**proof, "method": "tree_match", "matched_commit": matched, "paths": paths}


def _is_clean_two_parent_merge(repo: Path | str, row: list[str]) -> bool:
    """merge commit の tree が、親 2 つの自動 merge 結果と一致するか。"""
    if len(row) != 3:
        return False  # octopus merge は検証手段を持たないので証明しない
    merge, first, second = row
    merged = run_git(repo, "merge-tree", "--write-tree", first, second)
    if merged.returncode != 0:  # 衝突 (1) も、merge-tree 非対応 (古い git) も証明なし
        return False
    actual = run_git(repo, "rev-parse", f"{merge}^{{tree}}")
    if actual.returncode != 0:
        return False
    expected_tree = merged.stdout.split(maxsplit=1)[0] if merged.stdout.strip() else b""
    return bool(expected_tree) and expected_tree == actual.stdout.strip()


def _all_patch_equivalent(repo: Path | str, head: str, base_ref: str, commits: list[str]) -> bool:
    """到達不能な非 merge commit が、すべて base 側に verbatim patch-id の等しい commit を持つか。

    比べる相手は `git cherry` と同じく「base にあって head に無い commit」。
    `--binary` を付けないと、別々のバイナリ変更が同じ "Binary files differ" に潰れて一致する。
    """
    ours = _verbatim_patch_ids(repo, head, *_UNREACHABLE_ROOTS)
    theirs = _verbatim_patch_ids(repo, f"{head}..{base_ref}")
    if ours is None or theirs is None:
        return False
    base_ids = set(theirs.values())
    # patch が空 (mode だけの変更など) の commit は patch-id が出ないので、ours に無い = 失敗扱い
    return all(ours.get(commit) in base_ids for commit in commits)


def _verbatim_patch_ids(repo: Path | str, *revisions: str) -> dict[str, str] | None:
    """commit → 空白を剥がさない patch-id。測れなければ None。"""
    log = run_git(
        repo, "log", "-p", "--binary", "--full-index", "--no-merges", "--no-renames", "--no-color",
        "--no-ext-diff", "--no-textconv", "--format=commit %H", *revisions,
    )
    if log.returncode != 0:
        return None
    if not log.stdout.strip():
        return {}
    proc = run_git(repo, "patch-id", "--verbatim", stdin=log.stdout)
    if proc.returncode != 0:  # --verbatim の無い古い git も証明なしに倒す
        return None
    ids: dict[str, str] = {}
    for line in proc.stdout.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2:
            ids[parts[1]] = parts[0]
    return ids


def _paths_touched_by(repo: Path | str, commits: list[str]) -> list[str]:
    """到達不能な非 merge commit が触ったパス。rename は旧名・新名の両方を数える。

    `-z` の出力は NUL だけで区切り、前後の改行も含めてパス名として扱う。改行を剥がすと
    `"\nsecret\n"` が別のパス `secret` に化け、無関係なパスを比べて「一致」と誤判定する。
    """
    names: set[str] = set()
    for commit in commits:
        proc = run_git(
            repo, "diff-tree", "-r", "-z", "--root", "--no-commit-id", "--name-only", "--no-renames", commit
        )
        if proc.returncode != 0:
            return []
        names.update(part.decode("utf-8", errors="surrogateescape") for part in proc.stdout.split(b"\0") if part)
    return sorted(names)


def _find_tree_match(repo: Path | str, head: str, base_ref: str, paths: list[str]) -> str | None:
    """paths の内容が head と完全一致する commit を base の履歴から探す。

    paths の状態が変わるのは paths を触った commit だけなので、それらを新しい順に調べれば
    base 上に現れた全状態を網羅できる。上限を超えたら探索を打ち切り、証明なしとする。
    """
    candidates = run_git(
        repo, "--literal-pathspecs", "log", "--format=%H", f"--max-count={TREE_MATCH_CANDIDATE_LIMIT}",
        base_ref, "--", *paths,
    )
    if candidates.returncode != 0:
        return None
    for candidate in candidates.stdout.decode("ascii", errors="replace").split():
        same = run_git(repo, "--literal-pathspecs", "diff", "--quiet", "--no-ext-diff", candidate, head, "--", *paths)
        if same.returncode == 0:
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
