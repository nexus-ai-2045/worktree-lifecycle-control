from __future__ import annotations

from datetime import datetime, timezone

import pytest

from worktree_lifecycle_control.cli import classify, main, parse_porcelain_z
from worktree_lifecycle_control.evidence import validate_integration_evidence


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
HEAD = "a" * 40


def test_parse_porcelain_z_preserves_lock_reason() -> None:
    raw = (
        b"worktree C:/repo\0HEAD abc123\0branch refs/heads/main\0"
        b"locked owner=codex; task=review\0\0"
    )
    assert parse_porcelain_z(raw) == [
        {
            "path": "C:/repo",
            "head": "abc123",
            "branch": "refs/heads/main",
            "locked": True,
            "prunable": False,
            "lock_reason": "owner=codex; task=review",
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"exists": False}, "orphan_unknown"),
        ({"dirty": True}, "protected_dirty"),
        ({"unpushed": 2}, "protected_unpushed"),
        ({"entry": {}}, "owner_unknown"),
        (
            {"entry": {"owner": "codex", "lifecycle_status": "active"}},
            "active",
        ),
        (
            {"locked": True, "entry": {"owner": "codex"}},
            "protected_locked",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "integration": {
                        "status": "verified",
                        "provider": "github",
                        "source": "github-pr:1",
                        "head_sha": HEAD,
                        "actor": "github-api",
                        "observed_at": "2026-08-06T10:00:00+09:00",
                    },
                    "context_saved": False,
                }
            },
            "integrated_context_pending",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "integration": {
                        "status": "verified",
                        "provider": "github",
                        "source": "github-pr:1",
                        "head_sha": HEAD,
                        "actor": "github-api",
                        "observed_at": "2026-08-06T10:00:00+09:00",
                    },
                    "context_saved": True,
                }
            },
            "cleanup_ready",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "expires_at": "2026-08-05T00:00:00+00:00",
                    "integration": {"status": "unknown"},
                }
            },
            "review_due",
        ),
    ],
)
def test_classification_precedence(kwargs: dict, expected: str) -> None:
    defaults = {
        "exists": True,
        "dirty": False,
        "unpushed": 0,
        "locked": False,
        "head": HEAD,
        "entry": {"owner": "codex"},
        "now": NOW,
    }
    defaults.update(kwargs)
    assert classify(**defaults)[0] == expected


def test_naive_deadline_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        classify(
            exists=True,
            dirty=False,
            unpushed=0,
            locked=False,
            entry={"owner": "codex", "expires_at": "2026-08-05T00:00:00"},
            now=NOW,
            head=HEAD,
        )


def test_squash_or_rebase_evidence_must_match_exact_head() -> None:
    entry = {
        "owner": "codex",
        "integration": {
            "status": "verified",
            "provider": "github",
            "source": "github-pr:123",
            "head_sha": HEAD,
            "actor": "github-api",
            "observed_at": "2026-08-06T10:00:00+09:00",
        },
        "context_saved": True,
    }
    assert classify(
        exists=True,
        dirty=False,
        unpushed=3,
        locked=False,
        head=HEAD,
        entry=entry,
        now=NOW,
    )[0] == "cleanup_ready"
    assert classify(
        exists=True,
        dirty=False,
        unpushed=3,
        locked=False,
        head="different",
        entry=entry,
        now=NOW,
    )[0] == "protected_unpushed"


def test_report_path_writes_machine_readable_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "worktree_lifecycle_control.cli.scan_repo",
        lambda repo, registry, now: [],
    )
    report = tmp_path / "reports" / "scan.json"
    assert main(["scan", "--repo", str(tmp_path), "--report-path", str(report), "--json"]) == 0
    payload = __import__("json").loads(report.read_text(encoding="utf-8"))
    assert payload["action"] == "scan"
    assert payload["changed"] is False
    assert payload["verified"] is True
    assert payload["report_path"] == str(report.resolve())


def test_verified_evidence_requires_exact_head_and_provenance() -> None:
    valid = {
        "status": "verified",
        "provider": "github",
        "source": "github-pr:123",
        "head_sha": HEAD,
        "actor": "github-api",
        "observed_at": "2026-08-06T10:00:00+09:00",
    }
    assert validate_integration_evidence(valid, HEAD).verified is True
    invalid = dict(valid, head_sha="b" * 40, observed_at="2026-08-06T10:00:00")
    result = validate_integration_evidence(invalid, HEAD)
    assert result.verified is False
    assert "head_sha does not match the scanned worktree HEAD" in result.errors
    assert "observed_at must include a timezone" in result.errors


def test_review_packet_never_executes_cleanup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "worktree_lifecycle_control.cli.scan_repo",
        lambda repo, registry, now: [],
    )
    report = tmp_path / "review.json"
    assert main(["review-packet", "--repo", str(tmp_path), "--report-path", str(report)]) == 0
    payload = __import__("json").loads(report.read_text(encoding="utf-8"))
    assert payload["executed"] is False
    assert payload["proposed_operations"] == []
