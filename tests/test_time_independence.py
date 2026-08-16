"""テストスイートが実時刻に依存していないことを固定する。

2026-08-13 以降、test_lifecycle.py の 1 件が「必ず失敗する」状態になっていた。
`validate_integration_evidence` に `now=` を渡さず、固定の `observed_at`
(2026-08-06) を現在時刻と比較していたため、7 日の鮮度窓を過ぎた時点で
無条件に stale になる時限爆弾だった。

CI は push / pull_request でしか起動しないため、リポジトリが静かな間は
この腐敗が緑のまま隠れる。時間で起動する経路 (schedule) と、この検査を
セットで置くことで、同じ形の再発を機械的に捕まえる。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# now= を受け取り、渡さないと実時刻にフォールバックする関数
TIME_DEPENDENT_CALLS = frozenset({"validate_integration_evidence", "assess_lifecycle"})

TESTS_DIR = Path(__file__).parent


def _calls_missing_now(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in TIME_DEPENDENT_CALLS:
            continue
        # kw.arg is None は **kwargs 展開。中身は静的に見えないため判定を保留する。
        # 見逃す方を選ぶ (誤検知でテストを止める方が害が大きい)。
        if any(kw.arg == "now" for kw in node.keywords):
            continue
        if any(kw.arg is None for kw in node.keywords):
            continue
        offenders.append(f"{path.name}:{node.lineno} {name}()")
    return offenders


@pytest.mark.parametrize("path", sorted(TESTS_DIR.glob("test_*.py")), ids=lambda p: p.name)
def test_time_dependent_calls_pin_now(path: Path) -> None:
    offenders = _calls_missing_now(path)
    assert not offenders, (
        "実時刻に依存する呼び出しがある。now= を明示すること "
        f"(時限爆弾の再発防止): {offenders}"
    )
