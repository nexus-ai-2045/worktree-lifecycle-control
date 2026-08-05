from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class EvidenceValidation:
    verified: bool
    errors: tuple[str, ...]


def validate_integration_evidence(
    integration: dict[str, Any], expected_head: str | None
) -> EvidenceValidation:
    """Validate provider evidence without calling GitHub or another remote service."""
    if integration.get("status") != "verified":
        return EvidenceValidation(False, ())

    errors: list[str] = []
    provider = integration.get("provider")
    source = integration.get("source")
    head_sha = integration.get("head_sha")
    actor = integration.get("actor")
    observed_at = integration.get("observed_at")

    if not isinstance(provider, str) or not provider.strip():
        errors.append("provider is required")
    if not isinstance(source, str) or source in ("", "unknown"):
        errors.append("source is required")
    if not isinstance(head_sha, str) or not SHA_PATTERN.fullmatch(head_sha):
        errors.append("head_sha must be a 40-character lowercase hex SHA")
    elif expected_head is None or head_sha != expected_head:
        errors.append("head_sha does not match the scanned worktree HEAD")
    if not isinstance(actor, str) or not actor.strip():
        errors.append("actor is required")
    if not isinstance(observed_at, str):
        errors.append("observed_at is required")
    else:
        try:
            parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                errors.append("observed_at must include a timezone")
        except ValueError:
            errors.append("observed_at must be RFC3339-compatible")
    return EvidenceValidation(not errors, tuple(errors))

