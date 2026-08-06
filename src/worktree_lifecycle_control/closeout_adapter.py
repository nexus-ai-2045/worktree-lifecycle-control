from __future__ import annotations

import subprocess
from typing import Any
from urllib.parse import urlparse


class CloseoutAdapterError(ValueError):
    """Raised when closeout evidence cannot be normalized fail-closed."""


def _require_full_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise CloseoutAdapterError(f"{field} must be a 40-character lowercase hex SHA")
    return value


def _repo_and_number_from_pr_state(pr_state: dict[str, Any]) -> tuple[str | None, int | None]:
    number = pr_state.get("number")
    if not isinstance(number, int):
        number = None
    url = pr_state.get("url")
    if not isinstance(url, str) or not url.strip():
        return None, number
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    # /owner/repo/pull/N
    if len(parts) >= 4 and parts[2] == "pull":
        return f"{parts[0]}/{parts[1]}", number if number is not None else int(parts[3])
    return None, number


def enrich_subject_head_via_gh(pr_state: dict[str, Any]) -> str | None:
    """Fill missing PR head using gh (no reimplementation of closeout collect)."""
    repo, number = _repo_and_number_from_pr_state(pr_state)
    if not repo or number is None:
        return None
    completed = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "commits",
            "--jq",
            ".commits[-1].oid",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    oid = completed.stdout.strip().lower()
    if len(oid) == 40 and all(ch in "0123456789abcdef" for ch in oid):
        return oid
    return None


def subject_head_from_pr_state(
    pr_state: dict[str, Any],
    *,
    explicit: str | None = None,
    allow_gh_enrich: bool = True,
) -> str:
    """Prefer explicit SHA, then commits, then optional gh enrichment."""
    if isinstance(explicit, str) and explicit.strip():
        return _require_full_sha(explicit.lower(), "subject_head_sha")
    if isinstance(pr_state.get("subject_head_sha"), str):
        return _require_full_sha(pr_state["subject_head_sha"].lower(), "subject_head_sha")
    commits = pr_state.get("commits")
    if isinstance(commits, list) and commits:
        last = commits[-1]
        if isinstance(last, dict):
            oid = last.get("oid") or last.get("sha")
            if isinstance(oid, str):
                return _require_full_sha(oid.lower(), "pr_state.commits[-1].oid")
    if allow_gh_enrich:
        enriched = enrich_subject_head_via_gh(pr_state)
        if enriched is not None:
            return enriched
    raise CloseoutAdapterError("subject_head_sha is missing from closeout/pr_state")


def evidence_from_closeout_collect(
    payload: dict[str, Any],
    *,
    subject_head_sha: str | None = None,
    allow_gh_enrich: bool = True,
) -> dict[str, Any]:
    """Normalize post_merge_closeout_report collect JSON into integration-evidence-v2.

    This adapter does not reimplement closeout collection. Collection remains
    shared/scripts/post_merge_closeout_report.py collect.
    """
    if not isinstance(payload, dict):
        raise CloseoutAdapterError("closeout payload must be an object")

    pr_state = payload.get("pr_state")
    if not isinstance(pr_state, dict):
        raise CloseoutAdapterError("pr_state is required")

    state = str(pr_state.get("state") or "").upper()
    if state != "MERGED":
        raise CloseoutAdapterError("pr_state.state must be MERGED")

    number = pr_state.get("number")
    if not isinstance(number, int) or number < 1:
        raise CloseoutAdapterError("pr_state.number must be a positive integer")

    merge_commit = pr_state.get("mergeCommit")
    if not isinstance(merge_commit, dict):
        raise CloseoutAdapterError("pr_state.mergeCommit is required")
    resulting = merge_commit.get("oid")
    resulting_sha = _require_full_sha(str(resulting).lower() if resulting is not None else "", "mergeCommit.oid")

    merged_at = pr_state.get("mergedAt")
    if not isinstance(merged_at, str) or not merged_at.strip():
        raise CloseoutAdapterError("pr_state.mergedAt is required")

    explicit = subject_head_sha
    if explicit is None and isinstance(payload.get("subject_head_sha"), str):
        explicit = payload["subject_head_sha"]
    subject_sha = subject_head_from_pr_state(
        pr_state,
        explicit=explicit,
        allow_gh_enrich=allow_gh_enrich,
    )

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip() or actor == "unknown":
        account = payload.get("account_context")
        if isinstance(account, dict):
            checks = account.get("checks")
            if isinstance(checks, dict):
                active = checks.get("active_api_login")
                if isinstance(active, dict) and isinstance(active.get("value"), str):
                    actor = active["value"]
        if not isinstance(actor, str) or not actor.strip() or actor == "unknown":
            actor = "post_merge_closeout_report"

    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider.strip() or provider == "unknown":
        provider = "github"

    return {
        "status": "verified",
        "provider": provider,
        "evidence_type": "github_pr_merged",
        "provider_record_id": f"github-pr:{number}",
        "subject_head_sha": subject_sha,
        "resulting_base_sha": resulting_sha,
        "actor": actor,
        "observed_at": merged_at,
    }
