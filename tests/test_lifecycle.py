from __future__ import annotations

from datetime import datetime, timezone

import pytest

from worktree_lifecycle_control.cli import (
    WorktreeRecord,
    calendar_day_delta,
    assess_lifecycle,
    human_day_summary,
    main,
    load_registry,
    parse_porcelain_z,
    review_day_counts,
)
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
        ({"dirty": True}, "protected"),
        ({"unpushed": 2}, "protected"),
        ({"entry": {}}, "review_required"),
        (
            {"entry": {"owner": "codex", "lifecycle_status": "active"}},
            "active",
        ),
        (
            {"locked": True, "entry": {"owner": "codex"}},
            "protected",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "task": "test",
                    "return_path": "task:test",
                    "lifecycle_status": "complete",
                    "integration": {
                        "status": "verified",
                        "provider": "github",
                        "evidence_type": "github_pr_merged",
                        "provider_record_id": "github-pr:1",
                        "subject_head_sha": HEAD,
                        "resulting_base_sha": "b" * 40,
                        "actor": "github-api",
                        "observed_at": "2026-08-06T09:00:00+09:00",
                    },
                    "context_saved": False,
                }
            },
            "review_required",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "task": "test",
                    "return_path": "task:test",
                    "lifecycle_status": "complete",
                    "integration": {
                        "status": "verified",
                        "provider": "github",
                        "evidence_type": "github_pr_merged",
                        "provider_record_id": "github-pr:1",
                        "subject_head_sha": HEAD,
                        "resulting_base_sha": "b" * 40,
                        "actor": "github-api",
                        "observed_at": "2026-08-06T09:00:00+09:00",
                    },
                    "context_saved": True,
                }
            },
            "cleanup_candidate",
        ),
        (
            {
                "entry": {
                    "owner": "codex",
                    "expires_at": "2026-08-05T00:00:00+00:00",
                    "integration": {"status": "unknown"},
                }
            },
            "review_required",
        ),
    ],
)
def test_disposition(kwargs: dict, expected: str) -> None:
    defaults = {
        "exists": True,
        "dirty": False,
        "unpushed": 0,
        "locked": False,
        "head": HEAD,
        "entry": {
            "owner": "codex",
            "task": "test",
            "return_path": "task:test",
            "lifecycle_status": "complete",
            "integration": {"status": "unknown"},
            "context_saved": False,
        },
        "now": NOW,
    }
    defaults.update(kwargs)
    assert assess_lifecycle(**defaults).disposition == expected


def test_assessment_preserves_orthogonal_blockers() -> None:
    result = assess_lifecycle(
        exists=True,
        dirty=True,
        unpushed=2,
        locked=True,
        head=HEAD,
        entry={},
        now=NOW,
    )
    assert set(result.blockers) >= {
        "dirty_worktree",
        "unpushed_commits",
        "owner_unknown",
        "worktree_locked",
        "integration_unverified",
        "context_not_saved",
    }
    assert result.disposition == "protected"


def test_naive_deadline_is_rejected() -> None:
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=0,
        locked=False,
        entry={"owner": "codex", "expires_at": "2026-08-05T00:00:00"},
        now=NOW,
        head=HEAD,
    )
    assert "registry_invalid" in result.blockers
    assert "expires_at must include a timezone" in result.observations["registry_errors"]


def test_calendar_day_delta_uses_report_timezone_dates() -> None:
    report_now = datetime.fromisoformat("2026-08-06T00:05:00+09:00")
    assert calendar_day_delta("2026-08-05T23:55:00+09:00", report_now) == 1
    assert calendar_day_delta("2026-08-06T00:05:00+09:00", report_now) == 0
    assert calendar_day_delta("2026-08-07T00:00:00+09:00", report_now) == -1


def test_review_day_counts_never_uses_negative_remaining_days() -> None:
    report_now = datetime.fromisoformat("2026-08-06T12:00:00+09:00")
    assert review_day_counts("2026-08-08T00:00:00+09:00", report_now) == (2, 0)
    assert review_day_counts("2026-08-06T00:00:00+09:00", report_now) == (0, 0)
    assert review_day_counts("2026-08-03T00:00:00+09:00", report_now) == (0, 3)


def test_human_day_summary_uses_clear_japanese_labels() -> None:
    fields = {name: None for name in WorktreeRecord.__dataclass_fields__}
    fields.update(
        repo="C:/repo",
        path="C:/repo/worktree",
        git_locked=False,
        prunable=False,
        exists=True,
        dirty=False,
        unpushed_commits=0,
        days_since_created=None,
        days_since_head_commit=22,
        expires_at="2026-08-03T00:00:00+09:00",
        days_until_review=0,
        overdue_days=3,
        context_saved=False,
        integration_evidence_valid=False,
        integration_evidence_errors=(),
        observations={},
        blockers=("integration_unverified",),
        review_signals=("review_deadline_reached",),
        disposition="review_required",
    )
    record = WorktreeRecord(**fields)
    assert human_day_summary(record) == (
        "作成から: 不明（台帳未登録） / HEAD commitから: 22日 / 見直し: 期限を3日超過"
    )


def test_squash_or_rebase_evidence_must_match_exact_head() -> None:
    entry = {
        "owner": "codex",
        "task": "test",
        "return_path": "task:test",
        "lifecycle_status": "complete",
        "integration": {
            "status": "verified",
            "provider": "github",
            "evidence_type": "github_pr_merged",
            "provider_record_id": "github-pr:123",
            "subject_head_sha": HEAD,
            "resulting_base_sha": "b" * 40,
            "actor": "github-api",
            "observed_at": "2026-08-06T09:00:00+09:00",
        },
        "context_saved": True,
    }
    assert assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=3,
        locked=False,
        head=HEAD,
        entry=entry,
        now=NOW,
    ).disposition == "cleanup_candidate"
    assert assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=3,
        locked=False,
        head="different",
        entry=entry,
        now=NOW,
    ).disposition == "protected"


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
    assert payload["scan_completed"] is True
    assert payload["measurement_status"] == "complete"
    assert payload["report_path"] == str(report.resolve())


def test_verified_evidence_requires_exact_head_and_provenance() -> None:
    valid = {
        "status": "verified",
        "provider": "github",
        "evidence_type": "github_pr_merged",
        "provider_record_id": "github-pr:123",
        "subject_head_sha": HEAD,
        "resulting_base_sha": "b" * 40,
        "actor": "github-api",
        "observed_at": "2026-08-06T09:00:00+09:00",
    }
    assert validate_integration_evidence(valid, HEAD, now=NOW).verified is True
    invalid = dict(valid, subject_head_sha="b" * 40, observed_at="2026-08-06T10:00:00")
    result = validate_integration_evidence(invalid, HEAD, now=NOW)
    assert result.verified is False
    assert "subject_head_sha does not match the scanned worktree HEAD" in result.errors
    assert "observed_at must include a timezone" in result.errors


def test_stale_or_unknown_actor_evidence_is_not_verified() -> None:
    evidence = {
        "status": "verified",
        "provider": "github",
        "evidence_type": "github_pr_merged",
        "provider_record_id": "github-pr:123",
        "subject_head_sha": HEAD,
        "resulting_base_sha": "b" * 40,
        "actor": "unknown",
        "observed_at": "2026-07-01T10:00:00+00:00",
    }
    result = validate_integration_evidence(evidence, HEAD, now=NOW)
    assert result.verified is False
    assert "actor is required" in result.errors
    assert "observed_at is stale" in result.errors


def test_bad_registry_types_are_isolated_as_blockers() -> None:
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=0,
        locked=False,
        head=HEAD,
        entry={
            "owner": "codex",
            "task": "test",
            "return_path": "task:test",
            "lifecycle_status": "complete",
            "integration": "bad",
            "context_saved": True,
            "created_at": 123,
        },
        now=NOW,
    )
    assert "registry_invalid" in result.blockers
    assert result.disposition == "review_required"


def test_unhashable_lifecycle_is_isolated_as_blocker() -> None:
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=0,
        locked=False,
        head=HEAD,
        entry={
            "owner": "codex",
            "task": "test",
            "return_path": "task:test",
            "lifecycle_status": {},
            "integration": {"status": "unknown"},
            "context_saved": False,
        },
        now=NOW,
    )
    assert "registry_invalid" in result.blockers


def test_invalid_soft_budget_is_rejected(tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        '{"schema_version":"worktree-lifecycle/v1","soft_budget_per_repo":"3","entries":{}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="soft_budget_per_repo"):
        load_registry(path)


def test_primary_worktree_is_never_cleanup_candidate() -> None:
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=0,
        locked=False,
        head=HEAD,
        entry={
            "owner": "codex",
            "task": "test",
            "return_path": "task:test",
            "lifecycle_status": "complete",
            "integration": {
                "status": "verified",
                "provider": "github",
                "evidence_type": "github_pr_merged",
                "provider_record_id": "github-pr:123",
                "subject_head_sha": HEAD,
                "resulting_base_sha": "b" * 40,
                "actor": "github-api",
                "observed_at": "2026-08-06T10:00:00+00:00",
            },
            "context_saved": True,
        },
        now=NOW,
        primary=True,
    )
    assert "primary_worktree" in result.blockers
    assert result.disposition != "cleanup_candidate"


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
    assert payload["valid_until"] > payload["recorded_at"]


def test_repos_are_deduplicated_by_git_common_dir(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "worktree_lifecycle_control.cli.git_common_dir",
        lambda repo: "c:/same/common-dir",
    )
    monkeypatch.setattr(
        "worktree_lifecycle_control.cli.scan_repo",
        lambda repo, registry, now: calls.append(repo) or [],
    )
    assert main(["scan", "--repo", str(tmp_path), "--repo", str(tmp_path / "linked")]) == 0
    assert len(calls) == 1

from worktree_lifecycle_control.closeout_adapter import (
    CloseoutAdapterError,
    evidence_from_closeout_collect,
)
from worktree_lifecycle_control.evidence import validate_integration_evidence


def test_evidence_from_closeout_collect_maps_merged_pr() -> None:
    payload = {
        "decision": "pass",
        "pr_state": {
            "number": 1,
            "state": "MERGED",
            "mergedAt": "2026-08-06T07:39:04Z",
            "mergeCommit": {"oid": "32e999b4e611d2ad99d95442c53d760a196e2571"},
            "commits": [
                {"oid": "f5b0a5fdae56e34bd5117c6487e31ce86ebbfc1c"},
                {"oid": "931ce10e9dcd1e7e44a1980fc279c10db28aae94"},
            ],
        },
        "account_context": {
            "checks": {"active_api_login": {"status": "ok", "value": "nexus-ai-2045"}}
        },
    }
    evidence = evidence_from_closeout_collect(payload)
    assert evidence["status"] == "verified"
    assert evidence["provider"] == "github"
    assert evidence["evidence_type"] == "github_pr_merged"
    assert evidence["provider_record_id"] == "github-pr:1"
    assert evidence["subject_head_sha"] == "931ce10e9dcd1e7e44a1980fc279c10db28aae94"
    assert evidence["resulting_base_sha"] == "32e999b4e611d2ad99d95442c53d760a196e2571"
    assert evidence["actor"] == "nexus-ai-2045"
    assert evidence["observed_at"] == "2026-08-06T07:39:04Z"
    # observed_at (2026-08-06T07:39:04Z) より後の固定時刻。NOW は同日 00:00 なので
    # そのまま渡すと「未来の観測」と判定される。実時刻に依存させないことが目的。
    validation = validate_integration_evidence(
        evidence,
        "931ce10e9dcd1e7e44a1980fc279c10db28aae94",
        now=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
    )
    assert validation.verified is True
    assert validation.errors == ()


@pytest.mark.xfail(
    reason=(
        "closeout_adapter は observed_at に mergedAt を転記する。"
        "observed_at は「いつ観測したか」であって「いつ merge されたか」ではないため、"
        "7 日より前に merge された PR は、今この瞬間に観測し直しても永久に stale と判定される。"
        "収集時刻を入れるよう直したら xpass するので、その時点で xfail を外す。"
    ),
    strict=True,
)
def test_evidence_observed_at_is_collection_time_not_merge_time() -> None:
    """観測時刻と merge 時刻の取り違えを機械可読に固定する (未修正の既知欠陥).

    xpass 可能であることが必須なので、次の 2 点を避けている。

    - subject SHA は `commits` で渡す。adapter は `headRefOid` を読まないため、
      それだけでは CloseoutAdapterError で落ち、欠陥と無関係な理由で永久に
      xfail してしまう。
    - 鮮度の基準時刻は adapter が出した `observed_at` 自身から作る。修正後の
      `observed_at` は収集時刻 (実時刻) になるので、固定の NOW と比べると
      今度は「未来の観測」で落ちる。絶対時刻ではなく「mergedAt を転記して
      いるか」だけを問う形にする。
    """
    merged_long_ago = "2026-01-01T00:00:00Z"
    subject = "9" * 40
    payload = {
        "pr_state": {
            "number": 1,
            "state": "MERGED",
            "mergedAt": merged_long_ago,
            "commits": [{"oid": subject}],
            "mergeCommit": {"oid": "3" * 40},
            "mergedBy": {"login": "nexus-ai-2045"},
        },
        "account_context": {
            "checks": {"active_api_login": {"status": "ok", "value": "nexus-ai-2045"}}
        },
    }
    evidence = evidence_from_closeout_collect(payload)

    # 遠い過去の merge でも、今収集したなら観測時刻は「今」であるべき
    assert evidence["observed_at"] != merged_long_ago

    # 収集時刻を基準に検証する (絶対時刻に依存させない)
    collected = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))
    validation = validate_integration_evidence(evidence, subject, now=collected)
    assert validation.verified is True


def test_evidence_from_closeout_collect_rejects_open_pr() -> None:
    payload = {
        "pr_state": {
            "number": 2,
            "state": "OPEN",
            "mergedAt": "2026-08-06T07:39:04Z",
            "mergeCommit": {"oid": "32e999b4e611d2ad99d95442c53d760a196e2571"},
            "commits": [{"oid": "931ce10e9dcd1e7e44a1980fc279c10db28aae94"}],
        }
    }
    try:
        evidence_from_closeout_collect(payload)
        assert False, "expected CloseoutAdapterError"
    except CloseoutAdapterError as exc:
        assert "MERGED" in str(exc)
