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
        # --- 実体が掴めない
        ({"exists": False}, "orphan_unknown"),
        ({"dirty": None}, "orphan_unknown"),
        # --- 消すと失われる / git が消させない
        ({"reachable": False}, "protected"),
        ({"reachable": None}, "protected"),
        ({"dirty": True}, "protected"),
        ({"locked": True}, "protected"),
        ({"primary": True}, "protected"),
        ({"entry": {"pin": True}}, "protected"),
        # --- 未 push commit は保護理由にならない。branch が worktree 削除後も残り、
        #     commit も内容も復元できる (2026-08-15 隔離実験)。
        ({"unpushed": 2}, "cleanup_candidate"),
        # --- 台帳が空でも判定できる。v1 は「登録が無いと消せない」ため、
        #     台帳が空の repo では候補が永久に 0 件だった。
        ({"entry": {}}, "cleanup_candidate"),
        # --- 統合されていなくても、フォルダは片付けられる (branch は残る)
        ({"integration_state": "not_integrated"}, "cleanup_candidate"),
        # --- 人が明示した作業中 / 保護
        ({"entry": {"lifecycle_status": "active"}}, "active"),
        # --- 台帳に宣言があるのに壊れている場合だけ、人の確認へ回す
        ({"entry": {"task": None}}, "review_required"),
        ({"entry": {"lifecycle_status": "bogus"}}, "review_required"),
    ],
)
def test_disposition(kwargs: dict, expected: str) -> None:
    defaults = {
        "exists": True,
        "dirty": False,
        "unpushed": 0,
        "locked": False,
        "head": HEAD,
        "entry": {},
        "now": NOW,
        "reachable": True,
        "integration_state": "integrated",
    }
    defaults.update(kwargs)
    assert assess_lifecycle(**defaults).disposition == expected


def test_null_registry_field_does_not_bypass_validation() -> None:
    """`"task": null` は「書いていない」ではなく「書いてあるが空」として扱う。

    v1 の検査は `value is not None and ...` だったため null が素通りし、
    宣言が空のまま削除候補へ昇格できた。
    """
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=0,
        locked=False,
        head=HEAD,
        entry={"owner": "codex", "task": None, "return_path": None},
        now=NOW,
        reachable=True,
    )
    assert "task must be a non-empty string" in result.observations["registry_errors"]
    assert "return_path must be a non-empty string" in result.observations["registry_errors"]
    assert result.disposition == "review_required"


def test_unreachable_head_is_the_only_blocker_git_does_not_enforce() -> None:
    """git が守らない唯一の経路を、名指しの blocker として保持する。

    git は detached HEAD の worktree を無警告で削除し、gc 後に commit を失う。
    dirty は `git worktree remove` 自身が拒否し、未 push commit は branch が残るため
    失われない。守るべきはここだけである。
    """
    result = assess_lifecycle(
        exists=True,
        dirty=False,
        unpushed=2,
        locked=False,
        head=HEAD,
        entry={},
        now=NOW,
        reachable=False,
        detached=True,
    )
    assert "head_becomes_unreachable" in result.blockers
    assert result.disposition == "protected"
    # 未 push は判断材料であって保護理由ではない
    assert "unpushed_commits" in result.review_signals
    assert "unpushed_commits" not in result.blockers


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
        "worktree_locked",
        "head_reachability_unknown",
    }
    # 危険でないものを blocker に混ぜない。混ぜると本物の 1 件が雑音に埋もれる。
    assert set(result.review_signals) >= {"unpushed_commits", "owner_unknown"}
    assert "unpushed_commits" not in result.blockers
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
    assert "registry_invalid" in result.review_signals
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


def test_evidence_head_mismatch_surfaces_as_signal_not_blocker() -> None:
    """宣言された統合証跡の頭合わせ不一致は、削除を止めずに人へ見せる。

    v2 では証跡の不一致が blocker となり、フォルダ削除の可否を左右していた。
    フォルダを消しても branch は残るため、統合状態は削除条件ではない。
    ただし「verified と書いてあるのに検証が通らない」ことは台帳の嘘なので、
    signal として必ず表に出す。
    """
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
    common = dict(exists=True, dirty=False, unpushed=3, locked=False, entry=entry, now=NOW, reachable=True)
    matched = assess_lifecycle(head=HEAD, **common)
    assert matched.disposition == "cleanup_candidate"
    assert "integration_evidence_invalid" not in matched.review_signals

    mismatched = assess_lifecycle(head="different", **common)
    assert "integration_evidence_invalid" in mismatched.review_signals
    assert "integration_evidence_invalid" not in mismatched.blockers


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
        reachable=True,
    )
    assert "registry_invalid" in result.review_signals
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
        reachable=True,
    )
    assert "registry_invalid" in result.review_signals


def test_invalid_soft_budget_is_rejected(tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        '{"schema_version":"worktree-lifecycle/v2","soft_budget_per_repo":"3","entries":{}}',
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
        reachable=True,
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
            "mergedBy": {"login": "say-yas"},
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
    # merge した主体と、収集した主体は別の事実として持つ
    assert evidence["actor"] == "say-yas"
    assert evidence["observed_by"] == "nexus-ai-2045"
    # merge 時刻は鮮度判定に使わない別項目へ
    assert evidence["subject_merged_at"] == "2026-08-06T07:39:04Z"
    assert evidence["observed_at"] != "2026-08-06T07:39:04Z"
    # 鮮度の基準は収集時刻。絶対時刻に依存させない。
    collected = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))
    validation = validate_integration_evidence(
        evidence,
        "931ce10e9dcd1e7e44a1980fc279c10db28aae94",
        now=collected,
    )
    assert validation.verified is True
    assert validation.errors == ()


def test_evidence_observed_at_is_collection_time_not_merge_time() -> None:
    """観測時刻と merge 時刻の取り違えが再発しないことを固定する.

    2026-08-16 まで xfail(strict) で欠陥として固定していた。修正により xpass した
    ため marker を外した。以後この test が落ちたら、それは退行である。

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
