from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence, TextIO
from zoneinfo import ZoneInfo
from uuid import uuid4

from .closeout_adapter import CloseoutAdapterError, evidence_from_closeout_collect
from .evidence import validate_integration_evidence, validate_integration_shape
from .reachability import (
    branch_integration,
    head_reachability,
    resolve_base_ref,
    run_git,
)


def configure_stdio() -> None:
    """Prefer UTF-8 console output so Japanese summaries work on Windows CI hosts."""
    for stream_name in ("stdout", "stderr"):
        stream: TextIO | None = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if stream is None or reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def emit(text: str) -> None:
    """Write text without raising UnicodeEncodeError on legacy Windows code pages."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write((text + "\n").encode(encoding, errors="replace"))
            buffer.flush()
            return
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


@dataclass(frozen=True)
class WorktreeRecord:
    repo: str
    path: str
    head: str | None
    branch: str | None
    git_locked: bool
    lock_reason: str | None
    prunable: bool
    exists: bool
    dirty: bool | None
    unpushed_commits: int | None
    owner: str | None
    task: str | None
    created_at: str | None
    days_since_created: int | None
    head_committer_at: str | None
    days_since_head_commit: int | None
    expires_at: str | None
    days_until_review: int | None
    overdue_days: int
    lifecycle_status: str | None
    detached: bool
    head_reachable_elsewhere: bool | None
    integration_state: str
    pinned: bool
    integration_status: str
    integration_evidence_valid: bool
    integration_evidence_errors: tuple[str, ...]
    context_saved: bool
    observations: dict[str, Any]
    blockers: tuple[str, ...]
    review_signals: tuple[str, ...]
    disposition: str


@dataclass(frozen=True)
class LifecycleAssessment:
    observations: dict[str, Any]
    blockers: tuple[str, ...]
    review_signals: tuple[str, ...]
    disposition: str


def decode_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def parse_porcelain_z(raw: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for token in raw.split(b"\0"):
        if not token:
            continue
        if token.startswith(b"worktree "):
            if current is not None:
                records.append(current)
            current = {"path": decode_path(token[9:]), "locked": False, "prunable": False}
            continue
        if current is None:
            continue
        key, separator, value = token.partition(b" ")
        name = key.decode("ascii", errors="replace")
        text = decode_path(value) if separator else None
        if name == "HEAD":
            current["head"] = text
        elif name == "branch":
            current["branch"] = text
        elif name == "detached":
            current["detached"] = True
        elif name == "locked":
            current["locked"] = True
            current["lock_reason"] = text
        elif name == "prunable":
            current["prunable"] = True
            current["prunable_reason"] = text
    if current is not None:
        records.append(current)
    return records


def normalize_path(path: str) -> str:
    """比較用にパスを正規化する。

    大小同一視は `os.path.normcase` に委ねる。Windows では小文字化し、
    大小を区別するファイルシステムでは何もしない。無条件の `casefold()` は
    Linux 上で `/srv/Foo` と `/srv/foo` という別の worktree を同一視し、
    台帳エントリを取り違える。
    """
    return os.path.normcase(str(Path(path).resolve()))


SCAN_SCHEMA_VERSION = "worktree-lifecycle-report/v3"
REVIEW_SCHEMA_VERSION = "worktree-lifecycle-review/v3"

MEASUREMENT_UNKNOWN_BLOCKERS = frozenset(
    {
        "path_missing",
        "git_status_unknown",
        "head_reachability_unknown",
        "ignored_content_measurement_unknown",
    }
)
"""判定に効く事実を測れなかった blocker。台帳の記入漏れはここに含めない。"""


class RegistryError(ValueError):
    """台帳そのものが読めない・契約に合わない。scan を続けない。"""


REGISTRY_SCHEMA_VERSION = "worktree-lifecycle/v2"

REGISTRY_ENTRY_FIELDS = frozenset(
    {"pin", "reason", "expires_at", "return_path", "lifecycle_status", "note",
     "owner", "task", "created_at", "context_saved", "integration"}
)
REGISTRY_FIELDS = frozenset({"schema_version", "soft_budget_per_repo", "entries"})
INTEGRATION_EVIDENCE_FIELDS = frozenset(
    {"status", "provider", "evidence_type", "provider_record_id", "subject_head_sha",
     "resulting_base_sha", "actor", "observed_at", "subject_merged_at", "observed_by"}
)

# 内容ではなく、生成元が明確な名前だけを許可する。広い glob は新しい成果物を
# 誤って再生成可能扱いするため追加しない。
REGENERATABLE_IGNORED_PARTS = frozenset(
    {".pytest_cache", ".pytest-tmp", "__pycache__", ".venv"}
)


def load_registry(path: Path | None) -> dict[str, Any]:
    """台帳を読む。台帳は任意であり、無くても scan は成立する。

    v1 は「登録が無ければ削除候補にしない」許可リストだった。登録されない限り
    何も提案できないため、実運用では 63 worktree に対して候補 0 件になった (実測)。
    v2 では全項目を任意にし、台帳は「これは残す」という人の意思だけを書く保護
    リストにする。git から導出できる事実 (owner / 統合状態 / dirty) は台帳に
    持たせない。導出できるものを保存すると、その瞬間から drift が始まる。
    """
    if path is None:
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"registry could not be read: {error}") from error
    version = payload.get("schema_version")
    if version != REGISTRY_SCHEMA_VERSION:
        raise RegistryError(
            f"unsupported registry schema_version {version!r}; expected {REGISTRY_SCHEMA_VERSION!r}"
        )
    if not isinstance(payload.get("entries"), dict):
        raise RegistryError("registry entries must be an object")
    malformed_entries = [
        str(path)
        for path, entry in payload["entries"].items()
        if not isinstance(entry, dict)
    ]
    if malformed_entries:
        raise RegistryError(
            f"registry entry must be an object: {malformed_entries[0]}"
        )
    unknown_fields = sorted(set(payload) - REGISTRY_FIELDS)
    if unknown_fields:
        raise RegistryError(f"unknown registry field: {unknown_fields[0]}")
    soft_budget = payload.get("soft_budget_per_repo", 3)
    if not isinstance(soft_budget, int) or isinstance(soft_budget, bool) or soft_budget < 1:
        raise RegistryError("soft_budget_per_repo must be an integer greater than zero")
    return payload


def validate_entry(entry: dict[str, Any]) -> list[str]:
    """台帳エントリの型を検査する。全項目任意だが、書いたなら正しくあること。

    `null` は「書いていない」ではなく「書いてあるが空」として扱い、拒否する。
    v1 は `value is not None and ...` という条件だったため、`"task": null` が
    検査を素通りし、必須項目が空のまま候補へ昇格できた。
    """
    errors: list[str] = []
    errors.extend(
        f"unknown registry field: {field}" for field in sorted(set(entry) - REGISTRY_ENTRY_FIELDS)
    )
    for text_field in ("owner", "task", "return_path", "reason", "note"):
        if text_field not in entry:
            continue
        value = entry[text_field]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{text_field} must be a non-empty string")
    for bool_field in ("pin", "context_saved"):
        if bool_field in entry and not isinstance(entry[bool_field], bool):
            errors.append(f"{bool_field} must be a boolean")
    lifecycle = entry.get("lifecycle_status")
    if "lifecycle_status" in entry and (
        not isinstance(lifecycle, str)
        or lifecycle not in {"active", "paused", "complete", "unknown"}
    ):
        errors.append("lifecycle_status is unsupported")
    integration = entry.get("integration")
    if "integration" in entry and not isinstance(integration, dict):
        errors.append("integration must be an object")
    elif isinstance(integration, dict):
        unknown_integration = sorted(set(integration) - INTEGRATION_EVIDENCE_FIELDS)
        errors.extend(
            f"unknown integration field: {field}" for field in unknown_integration
        )
        errors.extend(validate_integration_shape(integration))
    for stamp_field in ("created_at", "expires_at"):
        if stamp_field not in entry:
            continue
        try:
            parse_deadline(entry[stamp_field])
        except (TypeError, ValueError) as error:
            errors.append(str(error).replace("expires_at", stamp_field))
    return errors


def registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        normalize_path(path): entry
        for path, entry in registry.get("entries", {}).items()
        if isinstance(entry, dict)
    }


def registry_entry_for_path(
    registry: dict[str, Any], path_text: str
) -> dict[str, Any]:
    """Resolve a registry entry by filesystem identity, failing safe on uncertainty."""
    entries = registry.get("entries", {})
    indexed = registry_index(registry)
    exact = indexed.get(normalize_path(path_text))
    if exact is not None:
        return exact
    if sys.platform != "darwin":
        return {}
    comparison_failed = False
    for registered_path, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        try:
            if os.path.samefile(path_text, registered_path):
                return entry
        except OSError:
            comparison_failed = True
    return {"_path_identity_unknown": True} if comparison_failed else {}


def count_unpushed(path: Path) -> int | None:
    proc = run_git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def git_common_dir(repo: Path) -> str:
    proc = run_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if proc.returncode != 0 or not proc.stdout.strip():
        return normalize_path(str(repo))
    return normalize_path(proc.stdout.decode("utf-8", errors="replace").strip())


def is_dirty(path: Path) -> bool | None:
    proc = run_git(path, "status", "--porcelain=v1", "--untracked-files=normal")
    return None if proc.returncode != 0 else bool(proc.stdout)


def ignored_paths(path: Path) -> tuple[str, ...] | None:
    proc = run_git(
        path,
        "status",
        "--porcelain=v1",
        "--ignored=matching",
        "--untracked-files=all",
        "-z",
    )
    if proc.returncode != 0:
        return None
    result: list[str] = []
    for token in proc.stdout.split(b"\0"):
        if token.startswith(b"!! "):
            result.append(token[3:].decode("utf-8", errors="surrogateescape"))
    return tuple(result)


def classify_ignored_paths(paths: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed: list[str] = []
    unknown: list[str] = []
    for path in paths:
        path_for_parts = path.replace("\\", "/") if sys.platform == "win32" else path
        parts = tuple(
            part for part in path_for_parts.strip("/").split("/") if part
        )
        regeneratable = any(
            part in REGENERATABLE_IGNORED_PARTS or part.endswith(".egg-info") for part in parts
        )
        target = allowed if regeneratable else unknown
        target.append(path)
    return tuple(allowed), tuple(unknown)


def head_committer_at(path: Path) -> str | None:
    proc = run_git(path, "log", "-1", "--format=%cI")
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip()


def parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed


def calendar_day_delta(earlier: str | None, later: datetime) -> int | None:
    """Return calendar-day difference in the report timezone."""
    if not earlier:
        return None
    parsed = parse_deadline(earlier)
    if parsed is None:
        return None
    return (later.date() - parsed.astimezone(later.tzinfo).date()).days


def safe_calendar_day_delta(earlier: str | None, later: datetime) -> int | None:
    try:
        return calendar_day_delta(earlier, later)
    except (TypeError, ValueError):
        return None


def review_day_counts(expires_at: str | None, now: datetime) -> tuple[int | None, int]:
    days_since_deadline = calendar_day_delta(expires_at, now)
    if days_since_deadline is None:
        return None, 0
    return max(0, -days_since_deadline), max(0, days_since_deadline)


def human_day_summary(record: WorktreeRecord) -> str:
    created = (
        f"{record.days_since_created}日"
        if record.days_since_created is not None
        else "不明（台帳未登録）"
    )
    committed = (
        f"{record.days_since_head_commit}日"
        if record.days_since_head_commit is not None
        else "不明"
    )
    if record.expires_at is None:
        review = "未設定"
    elif record.overdue_days > 0:
        review = f"期限を{record.overdue_days}日超過"
    elif record.days_until_review == 0:
        review = "今日が見直し期限"
    else:
        review = f"あと{record.days_until_review}日"
    return f"作成から: {created} / HEAD commitから: {committed} / 見直し: {review}"


def assess_lifecycle(
    *,
    exists: bool,
    dirty: bool | None,
    unpushed: int | None,
    locked: bool,
    head: str | None,
    entry: dict[str, Any],
    now: datetime,
    primary: bool = False,
    reachable: bool | None = None,
    integration_state: str = "unknown",
    detached: bool = False,
    unknown_ignored_paths: Sequence[str] = (),
    ignored_measurement_failed: bool = False,
) -> LifecycleAssessment:
    """worktree 1 件を評価する。

    blocker は「このフォルダを消すと何かが失われる、または git が消させない」条件だけに
    限定する。それ以外は signal (人が見る情報) に置く。両者を混ぜると、危険でないものが
    危険と同じ重さで並び、本当に危険な 1 件が 62 件の雑音に埋もれる。

    blocker と signal の切り分けは 2026-08-15 の隔離実験に基づく。詳細は
    docs/decisions/0002-protect-what-git-does-not.md を参照。
    """
    integration_value = entry.get("integration")
    integration = integration_value if isinstance(integration_value, dict) else {}
    integration_validation = validate_integration_evidence(integration, head, now=now)
    owner = entry.get("owner")
    lifecycle = entry.get("lifecycle_status")
    pinned = entry.get("pin") is True
    context_saved = entry.get("context_saved") is True
    registry_errors = validate_entry(entry)
    try:
        deadline = parse_deadline(entry.get("expires_at"))
    except (TypeError, ValueError):
        deadline = None

    observations = {
        "path_exists": exists,
        "dirty": dirty,
        "unpushed_commits": unpushed,
        "git_locked": locked,
        "detached_head": detached,
        "head_reachable_elsewhere": reachable,
        "integration_state": integration_state,
        "owner": owner,
        "lifecycle_status": lifecycle,
        "pinned": pinned,
        "integration_status": integration.get("status", "unknown"),
        "integration_evidence_valid": integration_validation.verified,
        "context_saved": context_saved,
        "registry_errors": registry_errors,
        "registered": bool(entry),
        "primary_worktree": primary,
        "unknown_ignored_paths": list(unknown_ignored_paths),
        "ignored_measurement_failed": ignored_measurement_failed,
    }

    blockers: list[str] = []
    signals: list[str] = []

    # --- blocker: 測定できない ---------------------------------------------
    if not exists:
        blockers.append("path_missing")
    if dirty is None:
        blockers.append("git_status_unknown")
    if reachable is None:
        blockers.append("head_reachability_unknown")

    # --- blocker: 消すと失われる / git が消させない -------------------------
    if reachable is False:
        # git は detached HEAD の worktree を無警告で削除し、gc で commit を失う。
        # git が守らない唯一の経路であり、このツールの中核的な存在理由。
        blockers.append("head_becomes_unreachable")
    if dirty:
        # git worktree remove 自身が拒否する。ここでの blocker は重複だが、
        # 「実行しても弾かれる」ことを候補一覧の段階で示すために残す。
        blockers.append("dirty_worktree")
    if locked:
        blockers.append("worktree_locked")
    if primary:
        blockers.append("primary_worktree")
    if pinned:
        # 人が明示した保護。git からは導出できない唯一の blocker。
        blockers.append("pinned")
    if unknown_ignored_paths:
        blockers.append("unknown_ignored_content")
    if ignored_measurement_failed:
        blockers.append("ignored_content_measurement_unknown")

    # --- signal: 判断材料。削除を止めない -----------------------------------
    if detached:
        signals.append("detached_head")
    if unpushed is None:
        signals.append("remote_reachability_unknown")
    elif unpushed > 0:
        # branch は worktree 削除後も残るため、commit は失われない (実測)。
        signals.append("unpushed_commits")
    if integration_state == "not_integrated":
        signals.append("branch_not_integrated")
    elif integration_state == "unknown":
        signals.append("branch_integration_unknown")
    if not owner:
        signals.append("owner_unknown")
    if lifecycle == "active":
        signals.append("lifecycle_active")
    if entry and not context_saved:
        signals.append("context_not_saved")
    if integration.get("status") == "verified" and integration_validation.errors:
        signals.append("integration_evidence_invalid")
    if registry_errors:
        signals.append("registry_invalid")
    if deadline is not None and deadline <= now:
        signals.append("review_deadline_reached")
    if unknown_ignored_paths:
        signals.append("unknown_ignored_content")
    if ignored_measurement_failed:
        signals.append("ignored_content_measurement_unknown")

    if "path_missing" in blockers or "git_status_unknown" in blockers:
        disposition = "orphan_unknown"
    elif lifecycle == "active":
        disposition = "active"
    elif blockers:
        disposition = "protected"
    elif registry_errors:
        # 台帳に宣言があるのに壊れている。宣言が無い場合と区別する。
        disposition = "review_required"
    else:
        disposition = "cleanup_candidate"
    return LifecycleAssessment(observations, tuple(blockers), tuple(signals), disposition)


def scan_repo(repo: Path, registry: dict[str, Any], now: datetime) -> list[WorktreeRecord]:
    proc = run_git(repo, "worktree", "list", "--porcelain", "-z")
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree list failed for {repo}")
    # base ref の解決は repo 単位で 1 回。worktree ごとに引くと 66 回 git を呼ぶ。
    base_ref = resolve_base_ref(repo)
    result: list[WorktreeRecord] = []
    for index, raw in enumerate(parse_porcelain_z(proc.stdout)):
        path_text = raw["path"]
        path = Path(path_text)
        exists = path.exists()
        entry = registry_entry_for_path(registry, path_text)
        dirty = is_dirty(path) if exists else None
        ignored = ignored_paths(path) if exists else None
        ignored_allowed, ignored_unknown = classify_ignored_paths(ignored or ())
        unpushed = count_unpushed(path) if exists else None
        head_sha = raw.get("head")
        detached = bool(raw.get("detached"))
        # 到達性と統合状態は repo 側の ref を見る。worktree が消えていても評価できる。
        reachable = head_reachability(repo, head_sha)
        integration_state = branch_integration(repo, head_sha, base_ref)
        commit_at = head_committer_at(path) if exists else None
        created_at = entry.get("created_at")
        expires_at = entry.get("expires_at")
        days_since_created = safe_calendar_day_delta(created_at, now)
        days_since_head_commit = calendar_day_delta(commit_at, now)
        try:
            days_until_review, overdue_days = review_day_counts(expires_at, now)
        except (TypeError, ValueError):
            days_until_review, overdue_days = None, 0
        assessment = assess_lifecycle(
            exists=exists,
            dirty=dirty,
            unpushed=unpushed,
            locked=bool(raw.get("locked")),
            head=head_sha,
            entry=entry,
            now=now,
            primary=index == 0,
            reachable=reachable,
            integration_state=integration_state,
            detached=detached,
            unknown_ignored_paths=(ignored_unknown if ignored is not None else ("<measurement-failed>",)),
            ignored_measurement_failed=ignored is None and exists,
        )
        assessment.observations["regeneratable_ignored_paths"] = list(ignored_allowed)
        integration_value = entry.get("integration")
        integration = integration_value if isinstance(integration_value, dict) else {}
        integration_validation = validate_integration_evidence(integration, raw.get("head"), now=now)
        result.append(
            WorktreeRecord(
                repo=str(repo.resolve()),
                path=path_text,
                head=raw.get("head"),
                branch=raw.get("branch"),
                git_locked=bool(raw.get("locked")),
                lock_reason=raw.get("lock_reason"),
                prunable=bool(raw.get("prunable")),
                exists=exists,
                dirty=dirty,
                unpushed_commits=unpushed,
                owner=entry.get("owner"),
                task=entry.get("task"),
                created_at=created_at,
                days_since_created=days_since_created,
                head_committer_at=commit_at,
                days_since_head_commit=days_since_head_commit,
                expires_at=expires_at,
                days_until_review=days_until_review,
                overdue_days=overdue_days,
                lifecycle_status=entry.get("lifecycle_status"),
                detached=detached,
                head_reachable_elsewhere=reachable,
                integration_state=integration_state,
                pinned=entry.get("pin") is True,
                integration_status=integration.get("status", "unknown"),
                integration_evidence_valid=integration_validation.verified,
                integration_evidence_errors=integration_validation.errors,
                context_saved=entry.get("context_saved") is True,
                observations=assessment.observations,
                blockers=assessment.blockers,
                review_signals=assessment.review_signals,
                disposition=assessment.disposition,
            )
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Git worktree lifecycle read-only control")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="inventory worktrees without changing them")
    scan.add_argument("--repo", action="append", type=Path, required=True)
    scan.add_argument("--registry", type=Path)
    scan.add_argument("--report-path", type=Path)
    scan.add_argument("--json", action="store_true")
    review = subparsers.add_parser("review-packet", help="generate a human review packet without cleanup")
    review.add_argument("--repo", action="append", type=Path, required=True)
    review.add_argument("--registry", type=Path)
    review.add_argument("--report-path", type=Path, required=True)
    review.add_argument("--json", action="store_true")
    evidence = subparsers.add_parser(
        "evidence-from-closeout",
        help="normalize post_merge_closeout_report collect JSON into integration-evidence-v3",
    )
    evidence.add_argument(
        "--input",
        type=Path,
        help="path to collect JSON; defaults to stdin",
    )
    evidence.add_argument("--output", type=Path, help="optional path to write evidence JSON")
    evidence.add_argument(
        "--subject-head-sha",
        help="override PR head SHA when collect JSON lacks commits",
    )
    evidence.add_argument(
        "--no-gh-enrich",
        action="store_true",
        help="do not call gh to fill missing subject_head_sha",
    )
    evidence.add_argument(
        "--actor",
        help=(
            "統合を実行した主体。closeout collect が mergedBy を返さないため、"
            "上流が返すまではここで明示する"
        ),
    )
    evidence.add_argument("--json", action="store_true", help="print evidence JSON to stdout")
    return parser


def write_json_atomic(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "evidence-from-closeout":
        try:
            if args.input is None:
                raw = sys.stdin.read()
            else:
                raw = args.input.read_text(encoding="utf-8")
            payload = json.loads(raw)
            evidence = evidence_from_closeout_collect(
                payload,
                subject_head_sha=args.subject_head_sha,
                allow_gh_enrich=not args.no_gh_enrich,
                actor=args.actor,
            )
        except (OSError, json.JSONDecodeError, CloseoutAdapterError) as exc:
            emit(f"error: {exc}")
            return 2
        rendered = json.dumps(evidence, ensure_ascii=True, indent=2)
        if args.output is not None:
            write_json_atomic(args.output, rendered)
        if args.json or args.output is None:
            emit(rendered)
        return 0
    try:
        return run_inventory(args)
    except (RegistryError, RuntimeError, OSError, ValueError) as error:
        # 生 traceback を出さない。CLI の失敗は 1 行の理由と exit code で伝える。
        emit(f"error: {error}")
        return 2


def run_inventory(args: argparse.Namespace) -> int:
    unique_repos: list[Path] = []
    seen_repos: set[str] = set()
    for repo in args.repo:
        key = git_common_dir(repo)
        if key not in seen_repos:
            seen_repos.add(key)
            unique_repos.append(repo)
    args.repo = unique_repos
    registry = load_registry(args.registry)
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    records = [record for repo in args.repo for record in scan_repo(repo, registry, now)]
    soft_budget = registry.get("soft_budget_per_repo", 3)
    budget_warnings = []
    for repo in args.repo:
        active_count = sum(
            record.lifecycle_status == "active"
            for record in records
            if record.repo == str(repo.resolve())
        )
        if active_count > soft_budget:
            budget_warnings.append(
                {
                    "repo": str(repo.resolve()),
                    "active_count": active_count,
                    "soft_budget": soft_budget,
                    "action": "review_only",
                }
            )
    # 測定不能 = 判定に効く事実が取れなかったもの。台帳に書いていないことは
    # 測定失敗ではない。v2 では「台帳未登録」を測定不能に数えていたため、台帳が
    # 空の repo では常に measurement_status=partial となり、本物の測定失敗を隠した。
    unknown_count = sum(
        any(blocker in MEASUREMENT_UNKNOWN_BLOCKERS for blocker in record.blockers)
        for record in records
    )
    danger_count = sum("head_becomes_unreachable" in record.blockers for record in records)
    registered_count = sum(bool(record.observations.get("registered")) for record in records)
    scan_payload = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "run_id": str(uuid4()),
        "observed_at": now.isoformat(),
        "action": "scan",
        "target": [str(repo.resolve()) for repo in args.repo],
        "dry_run": True,
        "changed": False,
        "scan_completed": True,
        "measurement_status": "partial" if unknown_count else "complete",
        "unknown_count": unknown_count,
        "danger_count": danger_count,
        "registry_coverage": {
            "registered": registered_count,
            "total": len(records),
        },
        "registry_validation_status": (
            "partial"
            if any("registry_invalid" in record.review_signals for record in records)
            else "valid"
        ),
        "read_only": True,
        "soft_budget": soft_budget,
        "budget_warnings": budget_warnings,
        "records": [asdict(record) for record in records],
        "report_path": str(args.report_path.resolve()) if args.report_path else None,
        "next_action": "review dispositions and blockers; no cleanup is performed",
    }
    if args.command == "review-packet":
        candidates = [record for record in records if record.disposition == "cleanup_candidate"]
        protected = [record for record in records if record.disposition != "cleanup_candidate"]
        payload = {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "run_id": scan_payload["run_id"],
            "recorded_at": now.isoformat(),
            "recorded_by": "worktree-lifecycle-control",
            "valid_until": (now + timedelta(hours=24)).isoformat(),
            "action": "human_review_packet",
            "target": scan_payload["target"],
            "executed": False,
            "candidate_count": len(candidates),
            "protected_or_unknown_count": len(protected),
            "cleanup_candidates": [asdict(record) for record in candidates],
            "protected_or_unknown": [asdict(record) for record in protected],
            "proposed_operations": [
                {
                    "target": record.path,
                    "operation": ["git", "-C", record.repo, "worktree", "remove", record.path],
                    "expected_head": record.head,
                    "preconditions": [
                        "packet is within valid_until",
                        "exact path and expected_head still match",
                        "all blockers are remeasured immediately before execution",
                        "human approval names this exact target and operation",
                    ],
                    "executed": False,
                }
                for record in candidates
            ],
            "separate_approval_boundaries": [
                "worktree removal",
                "local branch deletion",
                "remote branch deletion",
                "GitHub pull request close",
                "Projects-wide scheduling or hook wiring",
            ],
            "recommendation": (
                "review exact cleanup candidates" if candidates else "do not delete; resolve protected or unknown records"
            ),
            "report_path": str(args.report_path.resolve()),
        }
    else:
        payload = scan_payload
    rendered = json.dumps(payload, ensure_ascii=True, indent=2)
    if args.report_path:
        write_json_atomic(args.report_path, rendered)
    if args.json:
        emit(rendered)
    else:
        counts: dict[str, int] = {}
        for record in records:
            counts[record.disposition] = counts.get(record.disposition, 0) + 1
        emit(
            "合計 {total} / 削除候補 {c} / 保護 {p} / 要確認 {r} / 作業中 {a} / 実体不明 {o}".format(
                total=len(records),
                c=counts.get("cleanup_candidate", 0),
                p=counts.get("protected", 0),
                r=counts.get("review_required", 0),
                a=counts.get("active", 0),
                o=counts.get("orphan_unknown", 0),
            )
        )
        if danger_count:
            emit(
                f"警告: {danger_count} 件は HEAD がどの branch/tag/remote からも到達できません。"
                "git は無警告で削除し、gc 後に commit を復元できません。"
            )
        emit("")
        for record in records:
            emit(f"{record.disposition:28} {record.path}")
            emit(f"  {human_day_summary(record)}")
            for blocker in record.blockers:
                emit(f"  - blocker: {blocker}")
            for signal in record.review_signals:
                emit(f"  - review: {signal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
