from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class EvidenceValidation:
    verified: bool
    errors: tuple[str, ...]


def validate_integration_evidence(
    integration: dict[str, Any],
    expected_head: str | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=7),
) -> EvidenceValidation:
    """Validate provider evidence without calling GitHub or another remote service."""
    if integration.get("status") != "verified":
        return EvidenceValidation(False, ())

    errors: list[str] = []
    provider = integration.get("provider")
    evidence_type = integration.get("evidence_type")
    provider_record_id = integration.get("provider_record_id")
    subject_head_sha = integration.get("subject_head_sha")
    resulting_base_sha = integration.get("resulting_base_sha")
    actor = integration.get("actor")
    observed_at = integration.get("observed_at")

    if not isinstance(provider, str) or not provider.strip() or provider == "unknown":
        errors.append("provider is required")
    if evidence_type not in {"github_pr_merged", "git_reachability"}:
        errors.append("evidence_type is unsupported")
    if not isinstance(provider_record_id, str) or provider_record_id in ("", "unknown"):
        errors.append("provider_record_id is required")
    if not isinstance(subject_head_sha, str) or not SHA_PATTERN.fullmatch(subject_head_sha):
        errors.append("subject_head_sha must be a 40-character lowercase hex SHA")
    elif expected_head is None or subject_head_sha != expected_head:
        errors.append("subject_head_sha does not match the scanned worktree HEAD")
    if not isinstance(resulting_base_sha, str) or not SHA_PATTERN.fullmatch(resulting_base_sha):
        errors.append("resulting_base_sha must be a 40-character lowercase hex SHA")
    if not isinstance(actor, str) or not actor.strip() or actor == "unknown":
        errors.append("actor is required")
    if not isinstance(observed_at, str):
        errors.append("observed_at is required")
    else:
        try:
            parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                errors.append("observed_at must include a timezone")
            else:
                reference = now or datetime.now(timezone.utc)
                if reference - parsed.astimezone(reference.tzinfo) > max_age:
                    errors.append("observed_at is stale")
                if parsed.astimezone(reference.tzinfo) - reference > timedelta(minutes=5):
                    errors.append("observed_at is in the future")
        except ValueError:
            errors.append("observed_at must be RFC3339-compatible")
    return EvidenceValidation(not errors, tuple(errors))
