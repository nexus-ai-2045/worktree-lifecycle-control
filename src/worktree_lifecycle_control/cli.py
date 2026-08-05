from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

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
    expires_at: str | None
    lifecycle_status: str | None
    integration_status: str
    integration_evidence_valid: bool
    integration_evidence_errors: tuple[str, ...]
    context_saved: bool
    classification: str
    reasons: tuple[str, ...]


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


def is_dirty(path: Path) -> bool | None:
    proc = run_git(path, "status", "--porcelain=v1", "--untracked-files=normal")
    return None if proc.returncode != 0 else bool(proc.stdout)


def parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed


def classify(
    *,
    exists: bool,
    dirty: bool | None,
    unpushed: int | None,
    locked: bool,
    head: str | None,
    entry: dict[str, Any],
    now: datetime,
) -> tuple[str, tuple[str, ...]]:
    if not exists:
        return "orphan_unknown", ("worktree path does not exist",)
    if dirty is None:
        return "orphan_unknown", ("git status could not be measured",)
    if dirty:
        return "protected_dirty", ("working tree has tracked or untracked changes",)
    integration = entry.get("integration") or {}
    integration_validation = validate_integration_evidence(integration, head)
    integration_verified = integration_validation.verified
    if unpushed is None:
        return "review_due", ("remote reachability could not be measured",)
    if unpushed > 0 and not integration_verified:
        return "protected_unpushed", (f"{unpushed} commit(s) are not reachable from remotes",)

    owner = entry.get("owner")
    if not owner:
        return "owner_unknown", ("registry owner is missing",)

    lifecycle = entry.get("lifecycle_status")
    if lifecycle == "active":
        return "active", ("registry lifecycle_status is active",)
    if locked:
        return "protected_locked", ("git worktree is locked",)

    context_saved = entry.get("context_saved") is True
    if integration_verified and not context_saved:
        return "integrated_context_pending", ("integration is verified but context is not saved",)
    if integration_verified and context_saved:
        reachability = "remote-reachable" if unpushed == 0 else "integration evidence matches head"
        return "cleanup_ready", (f"clean, {reachability}, integrated, and context saved",)

    deadline = parse_deadline(entry.get("expires_at"))
    if deadline is not None and deadline <= now:
        return "review_due", ("registry deadline has passed; age alone does not permit deletion",)
    return "review_due", ("integration evidence is incomplete",)


def scan_repo(repo: Path, registry: dict[str, Any], now: datetime) -> list[WorktreeRecord]:
    proc = run_git(repo, "worktree", "list", "--porcelain", "-z")
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree list failed for {repo}")
    entries = registry_index(registry)
    result: list[WorktreeRecord] = []
    for raw in parse_porcelain_z(proc.stdout):
        path_text = raw["path"]
        path = Path(path_text)
        exists = path.exists()
        entry = entries.get(normalize_path(path_text), {})
        dirty = is_dirty(path) if exists else None
        unpushed = count_unpushed(path) if exists else None
        classification, reasons = classify(
            exists=exists,
            dirty=dirty,
            unpushed=unpushed,
            locked=bool(raw.get("locked")),
            head=raw.get("head"),
            entry=entry,
            now=now,
        )
        integration = entry.get("integration") or {}
        integration_validation = validate_integration_evidence(integration, raw.get("head"))
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
                expires_at=entry.get("expires_at"),
                lifecycle_status=entry.get("lifecycle_status"),
                integration_status=integration.get("status", "unknown"),
                integration_evidence_valid=integration_validation.verified,
                integration_evidence_errors=integration_validation.errors,
                context_saved=entry.get("context_saved") is True,
                classification=classification,
                reasons=reasons,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    scan_payload = {
        "schema_version": "worktree-lifecycle-report/v1",
        "observed_at": now.isoformat(),
        "action": "scan",
        "target": [str(repo.resolve()) for repo in args.repo],
        "dry_run": True,
        "changed": False,
        "verified": True,
        "read_only": True,
        "soft_budget": soft_budget,
        "budget_warnings": budget_warnings,
        "records": [asdict(record) for record in records],
        "report_path": str(args.report_path.resolve()) if args.report_path else None,
        "next_action": "review classifications; no cleanup is performed",
    }
    if args.command == "review-packet":
        candidates = [record for record in records if record.classification == "cleanup_ready"]
        protected = [record for record in records if record.classification != "cleanup_ready"]
        payload = {
            "schema_version": "worktree-lifecycle-review/v1",
            "recorded_at": now.isoformat(),
            "recorded_by": "worktree-lifecycle-control",
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
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    if args.json:
        print(rendered)
    else:
        for record in records:
            print(f"{record.classification:28} {record.path}")
            for reason in record.reasons:
                print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
