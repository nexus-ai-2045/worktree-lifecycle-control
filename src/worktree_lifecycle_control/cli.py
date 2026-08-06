from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo
from uuid import uuid4

from .evidence import validate_integration_evidence


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


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )


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
    return str(Path(path).resolve()).casefold()


def load_registry(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema_version": "worktree-lifecycle/v1", "entries": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "worktree-lifecycle/v1":
        raise ValueError("unsupported registry schema_version")
    if not isinstance(payload.get("entries"), dict):
        raise ValueError("registry entries must be an object")
    soft_budget = payload.get("soft_budget_per_repo", 3)
    if not isinstance(soft_budget, int) or isinstance(soft_budget, bool) or soft_budget < 1:
        raise ValueError("soft_budget_per_repo must be an integer greater than zero")
    return payload


def registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        normalize_path(path): entry
        for path, entry in registry.get("entries", {}).items()
        if isinstance(entry, dict)
    }


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
) -> LifecycleAssessment:
    integration_value = entry.get("integration")
    integration = integration_value if isinstance(integration_value, dict) else {}
    integration_validation = validate_integration_evidence(integration, head, now=now)
    integration_verified = integration_validation.verified
    owner = entry.get("owner")
    lifecycle = entry.get("lifecycle_status")
    context_saved = entry.get("context_saved") is True
    registry_errors: list[str] = []
    for required in ("owner", "task", "return_path", "lifecycle_status", "integration", "context_saved"):
        if required not in entry:
            registry_errors.append(f"{required} is required")
    for text_field in ("owner", "task", "return_path"):
        value = entry.get(text_field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            registry_errors.append(f"{text_field} must be a non-empty string")
    if integration_value is not None and not isinstance(integration_value, dict):
        registry_errors.append("integration must be an object")
    if "context_saved" in entry and not isinstance(entry.get("context_saved"), bool):
        registry_errors.append("context_saved must be a boolean")
    if lifecycle is not None and (
        not isinstance(lifecycle, str)
        or lifecycle not in {"active", "paused", "complete", "unknown"}
    ):
        registry_errors.append("lifecycle_status is unsupported")
    try:
        deadline = parse_deadline(entry.get("expires_at"))
    except (TypeError, ValueError) as error:
        deadline = None
        registry_errors.append(str(error))
    try:
        parse_deadline(entry.get("created_at"))
    except (TypeError, ValueError) as error:
        registry_errors.append(str(error).replace("expires_at", "created_at"))
    observations = {
        "path_exists": exists,
        "dirty": dirty,
        "unpushed_commits": unpushed,
        "git_locked": locked,
        "owner": owner,
        "lifecycle_status": lifecycle,
        "integration_status": integration.get("status", "unknown"),
        "integration_evidence_valid": integration_verified,
        "context_saved": context_saved,
        "registry_errors": registry_errors,
        "primary_worktree": primary,
    }
    blockers: list[str] = []
    signals: list[str] = []
    if not exists:
        blockers.append("path_missing")
    if dirty is None:
        blockers.append("git_status_unknown")
    elif dirty:
        blockers.append("dirty_worktree")
    if unpushed is None:
        blockers.append("remote_reachability_unknown")
    elif unpushed > 0 and not integration_verified:
        blockers.append("unpushed_commits")
    if not owner:
        blockers.append("owner_unknown")
    if registry_errors:
        blockers.append("registry_invalid")
    if locked:
        blockers.append("worktree_locked")
    if primary:
        blockers.append("primary_worktree")
    if integration.get("status") == "verified" and integration_validation.errors:
        blockers.append("integration_evidence_invalid")
    elif not integration_verified:
        blockers.append("integration_unverified")
    if not context_saved:
        blockers.append("context_not_saved")
    if lifecycle == "active":
        signals.append("lifecycle_active")
    elif lifecycle != "complete":
        blockers.append("lifecycle_not_complete")
    if deadline is not None and deadline <= now:
        signals.append("review_deadline_reached")

    if "path_missing" in blockers or "git_status_unknown" in blockers:
        disposition = "orphan_unknown"
    elif lifecycle == "active":
        disposition = "active"
    elif any(item in blockers for item in ("dirty_worktree", "unpushed_commits", "worktree_locked")):
        disposition = "protected"
    elif blockers:
        disposition = "review_required"
    else:
        disposition = "cleanup_candidate"
    return LifecycleAssessment(observations, tuple(blockers), tuple(signals), disposition)


def scan_repo(repo: Path, registry: dict[str, Any], now: datetime) -> list[WorktreeRecord]:
    proc = run_git(repo, "worktree", "list", "--porcelain", "-z")
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree list failed for {repo}")
    entries = registry_index(registry)
    result: list[WorktreeRecord] = []
    for index, raw in enumerate(parse_porcelain_z(proc.stdout)):
        path_text = raw["path"]
        path = Path(path_text)
        exists = path.exists()
        entry = entries.get(normalize_path(path_text), {})
        dirty = is_dirty(path) if exists else None
        unpushed = count_unpushed(path) if exists else None
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
            head=raw.get("head"),
            entry=entry,
            now=now,
            primary=index == 0,
        )
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
    args = build_parser().parse_args(argv)
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
    unknown_count = sum(
        any(
            blocker in {
                "path_missing",
                "git_status_unknown",
                "remote_reachability_unknown",
                "owner_unknown",
                "integration_unverified",
                "registry_invalid",
            }
            for blocker in record.blockers
        )
        for record in records
    )
    scan_payload = {
        "schema_version": "worktree-lifecycle-report/v2",
        "run_id": str(uuid4()),
        "observed_at": now.isoformat(),
        "action": "scan",
        "target": [str(repo.resolve()) for repo in args.repo],
        "dry_run": True,
        "changed": False,
        "scan_completed": True,
        "measurement_status": "partial" if unknown_count else "complete",
        "unknown_count": unknown_count,
        "registry_validation_status": (
            "partial" if any("registry_invalid" in record.blockers for record in records) else "valid"
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
            "schema_version": "worktree-lifecycle-review/v2",
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
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report_path:
        write_json_atomic(args.report_path, rendered)
    if args.json:
        print(rendered)
    else:
        for record in records:
            print(f"{record.disposition:28} {record.path}")
            print(f"  {human_day_summary(record)}")
            for blocker in record.blockers:
                print(f"  - blocker: {blocker}")
            for signal in record.review_signals:
                print(f"  - review: {signal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
