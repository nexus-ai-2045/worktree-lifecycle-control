from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .evidence import parse_rfc3339

GH_TIMEOUT_SECONDS = 30
"""gh 呼び出しの上限。応答しない gh を無期限に待つと収集全体が止まる。"""


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
    try:
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
            timeout=GH_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
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


def _valid_actor(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and value != "unknown":
        return value
    return None


def _observed_by(payload: dict[str, Any]) -> str | None:
    """収集を実行した主体。統合を実行した主体 (actor) とは別の事実である。"""
    account = payload.get("account_context")
    if not isinstance(account, dict):
        return None
    checks = account.get("checks")
    if not isinstance(checks, dict):
        return None
    active = checks.get("active_api_login")
    if isinstance(active, dict):
        return _valid_actor(active.get("value"))
    return None


def _resolve_actor(
    pr_state: dict[str, Any], payload: dict[str, Any], explicit: str | None
) -> str:
    """統合を実行した主体を返す。特定できなければ失敗させる。

    v2 までは特定できないと文字列 "post_merge_closeout_report" を埋めていた。
    検証側は「actor が空でも unknown でもないこと」しか見ないため、この既定値は
    必ず検証を通る。つまり「誰が merge したか不明」という事実が、証跡の上では
    「判明している」に化けていた。欠落は埋めずに失敗させる。

    収集を実行した account (`account_context`) を actor に流用しない。
    「収集した人」と「merge した人」は別人でありうる。前者は observed_by に置く。

    2026-08-20 時点の shared/scripts/post_merge_closeout_report.py は
    `--json number,state,mergedAt,mergeCommit,url,headRefName,baseRefName,statusCheckRollup`
    を要求しており `mergedBy` を含まない。上流が返すまでは --actor で明示する。
    """
    for candidate in (
        (pr_state.get("mergedBy") or {}).get("login") if isinstance(pr_state.get("mergedBy"), dict) else None,
        explicit,
        payload.get("actor"),
    ):
        resolved = _valid_actor(candidate)
        if resolved is not None:
            return resolved
    raise CloseoutAdapterError(
        "actor could not be determined: pr_state.mergedBy.login is absent "
        "(the closeout collector does not request it) and no --actor was given"
    )


def evidence_from_closeout_collect(
    payload: dict[str, Any],
    *,
    subject_head_sha: str | None = None,
    allow_gh_enrich: bool = True,
    now: datetime | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Normalize post_merge_closeout_report collect JSON into integration-evidence-v3.

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
    try:
        parse_rfc3339(merged_at, field="pr_state.mergedAt")
    except ValueError as error:
        raise CloseoutAdapterError(str(error)) from error

    collection_time = now
    if collection_time is None:
        raw_collection_time = payload.get("observed_at") or payload.get("collected_at")
        if not isinstance(raw_collection_time, str) or not raw_collection_time.strip():
            raise CloseoutAdapterError(
                "closeout collection timestamp is required (observed_at or collected_at)"
            )
        try:
            collection_time = parse_rfc3339(
                raw_collection_time, field="closeout collection timestamp"
            )
        except ValueError as error:
            raise CloseoutAdapterError(str(error)) from error

    explicit = subject_head_sha
    if explicit is None and isinstance(payload.get("subject_head_sha"), str):
        explicit = payload["subject_head_sha"]
    subject_sha = subject_head_from_pr_state(
        pr_state,
        explicit=explicit,
        allow_gh_enrich=allow_gh_enrich,
    )

    resolved_actor = _resolve_actor(pr_state, payload, actor)
    observed_by = _observed_by(payload)

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
        "actor": resolved_actor,
        # observed_at は「いつ観測したか」。merge 時刻ではない。両者を混ぜると、
        # 鮮度窓 (既定 7 日) を過ぎて merge された PR は、今この瞬間に観測し直しても
        # 永久に stale と判定され、統合済みの worktree が二度と候補にならない。
        "observed_at": collection_time.isoformat().replace("+00:00", "Z"),
        "subject_merged_at": merged_at,
        **({"observed_by": observed_by} if observed_by else {}),
    }
