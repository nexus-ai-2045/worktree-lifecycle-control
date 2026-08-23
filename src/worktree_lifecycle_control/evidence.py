from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

# RFC 3339 date-time, as used by the JSON schemas in this package.  Python's
# ``datetime.fromisoformat`` is intentionally more permissive (it accepts a
# space in place of ``T`` and offsets containing seconds), so it cannot be the
# contract check by itself.
RFC3339_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)

_RFC3339_WITHOUT_TIMEZONE_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?$"
)

SUPPORTED_EVIDENCE_TYPES = frozenset({"github_pr_merged"})
"""台帳に宣言できる証跡の種類。

`git_reachability` は v2 まで受理していたが、これを生成する経路も検証する実装も
存在せず、宣言するだけで通る空欄だった。到達性は git から毎回導出できる事実なので、
宣言させるのではなく scan の度に導出する (reachability.head_reachability)。
導出できる事実を宣言に持たせると、宣言と実体が離れた瞬間から嘘になる。
"""

INTEGRATION_EVIDENCE_FIELDS = frozenset(
    {
        "status",
        "provider",
        "evidence_type",
        "provider_record_id",
        "subject_head_sha",
        "resulting_base_sha",
        "actor",
        "observed_at",
        "subject_merged_at",
        "observed_by",
    }
)

VERIFIED_INTEGRATION_FIELDS = frozenset(
    {
        "status",
        "provider",
        "evidence_type",
        "provider_record_id",
        "subject_head_sha",
        "resulting_base_sha",
        "actor",
        "observed_at",
        "subject_merged_at",
    }
)


@dataclass(frozen=True)
class EvidenceValidation:
    verified: bool
    errors: tuple[str, ...]


def parse_rfc3339(value: Any, *, field: str = "timestamp") -> datetime:
    """Parse the strict RFC 3339 profile used by the v3 JSON schemas.

    ``fromisoformat`` remains useful for calendar validation after the regular
    expression has enforced the wire format.  Keeping this helper in the core
    evidence module makes adapter, lifecycle, and evidence validation agree on
    the same timestamp contract.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if _RFC3339_WITHOUT_TIMEZONE_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must include a timezone")
    if not RFC3339_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be RFC3339-compatible")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be RFC3339-compatible") from error
    if parsed.tzinfo is None:
        # Defensive: the regex currently makes this unreachable, but keeping
        # the invariant explicit avoids a future parser regression.
        raise ValueError(f"{field} must include a timezone")
    return parsed


def validate_integration_shape(integration: Any) -> tuple[str, ...]:
    """Validate the registry's exact ``unknown``/``verified`` alternatives.

    The registry schema deliberately has two shapes: ``{"status":
    "unknown"}`` or a complete v3 verified evidence object.  Checking only
    field names would let ``status: verifed`` or ``status: unknown`` with
    extra evidence pass through as an absent integration declaration.
    """
    if not isinstance(integration, dict):
        return ("integration must be an object",)

    errors: list[str] = []
    unknown_fields = sorted(set(integration) - INTEGRATION_EVIDENCE_FIELDS)
    errors.extend(f"unknown integration field: {field}" for field in unknown_fields)

    status = integration.get("status")
    if status == "unknown":
        if set(integration) != {"status"}:
            errors.append("unknown integration must contain only status")
        return tuple(errors)
    if status != "verified":
        errors.append("integration status must be exactly 'unknown' or 'verified'")
        return tuple(errors)

    missing = sorted(VERIFIED_INTEGRATION_FIELDS - set(integration))
    errors.extend(f"integration field is required: {field}" for field in missing)
    if missing:
        # Avoid type errors while reporting the complete shape failure.  The
        # caller still runs the full evidence validator when all fields exist.
        return tuple(errors)

    provider = integration.get("provider")
    if not isinstance(provider, str) or not provider.strip() or provider == "unknown":
        errors.append("provider is required")
    if integration.get("evidence_type") not in SUPPORTED_EVIDENCE_TYPES:
        errors.append("evidence_type is unsupported")
    provider_record_id = integration.get("provider_record_id")
    if not isinstance(provider_record_id, str) or not provider_record_id.strip() or provider_record_id == "unknown":
        errors.append("provider_record_id is required")
    for sha_field in ("subject_head_sha", "resulting_base_sha"):
        value = integration.get(sha_field)
        if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
            errors.append(f"{sha_field} must be a 40-character lowercase hex SHA")
    actor = integration.get("actor")
    if not isinstance(actor, str) or not actor.strip() or actor == "unknown":
        errors.append("actor is required")
    for stamp_field in ("observed_at", "subject_merged_at"):
        try:
            parse_rfc3339(integration.get(stamp_field), field=stamp_field)
        except ValueError as error:
            errors.append(str(error))
    if "observed_by" in integration:
        observed_by = integration.get("observed_by")
        if not isinstance(observed_by, str) or not observed_by.strip():
            errors.append("observed_by must be a non-empty string")
    return tuple(errors)


def validate_integration_evidence(
    integration: dict[str, Any],
    expected_head: str | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=7),
) -> EvidenceValidation:
    """外部サービスを呼ばずに、宣言された統合証跡を検証する。

    ここで検証するのは「git から導出できないこと」だけである。GitHub 上で PR が
    merge されたか否かはローカル git には無い事実なので、宣言と鮮度を見る。
    到達性のように git から導出できる事実は、宣言ではなく導出側で扱う。
    """
    if not isinstance(integration, dict):
        return EvidenceValidation(False, ("integration must be an object",))
    if not integration:
        # 宣言が無いことは誤りではない。台帳自体が任意なので、大半の worktree は
        # ここへ来る。空の宣言を「壊れた宣言」と同じ扱いにすると、正常な省略に
        # 対して報告が毎回 integration_evidence_errors を出す。
        # 台帳に `"integration": {}` と明示的に書いた場合は、registry 側の
        # validate_integration_shape が空を拒否するため見落とさない。
        return EvidenceValidation(False, ())
    shape_errors = validate_integration_shape(integration)
    if integration.get("status") != "verified":
        return EvidenceValidation(False, shape_errors)

    errors: list[str] = list(shape_errors)
    provider = integration.get("provider")
    evidence_type = integration.get("evidence_type")
    provider_record_id = integration.get("provider_record_id")
    subject_head_sha = integration.get("subject_head_sha")
    resulting_base_sha = integration.get("resulting_base_sha")
    actor = integration.get("actor")
    observed_at = integration.get("observed_at")
    subject_merged_at = integration.get("subject_merged_at")

    if not isinstance(provider, str) or not provider.strip() or provider == "unknown":
        errors.append("provider is required")
    if evidence_type not in SUPPORTED_EVIDENCE_TYPES:
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
    try:
        parsed = parse_rfc3339(observed_at, field="observed_at")
    except ValueError as error:
        errors.append(str(error))
    else:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            errors.append("validation reference time must include a timezone")
        else:
            if reference - parsed.astimezone(reference.tzinfo) > max_age:
                errors.append("observed_at is stale")
            if parsed.astimezone(reference.tzinfo) - reference > timedelta(minutes=5):
                errors.append("observed_at is in the future")
    try:
        parse_rfc3339(subject_merged_at, field="subject_merged_at")
    except ValueError as error:
        errors.append(str(error))
    return EvidenceValidation(not errors, tuple(errors))
