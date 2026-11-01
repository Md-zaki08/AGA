"""Plan-version immutability dedicated suite (INSERT-only guarantee)."""

from __future__ import annotations

from datetime import date


def test_only_one_active_version(repo):
    g = repo.create_goal("G", "d", date(2025, 4, 30), 3, "mvo")
    for i in range(3):
        repo.create_plan_version(g.goal_id, f"s{i}", date(2025, 4, 20), 3, date(2025, 4, 18), 1.0, "low", f"v{i}")  # type: ignore[arg-type]
    hist = repo.list_plan_history(g.goal_id)  # type: ignore[arg-type]
    assert [p.version_number for p in hist] == [1, 2, 3]
    assert sum(p.is_active for p in hist) == 1
    assert hist[-1].is_active is True
    active = repo.get_active_plan(g.goal_id)  # type: ignore[arg-type]
    assert active and active.version_number == 3


def test_version_content_never_updated(repo):
    g = repo.create_goal("G", "d", date(2025, 4, 30), 3, "mvo")
    p1 = repo.create_plan_version(g.goal_id, "orig", date(2025, 4, 20), 5, date(2025, 4, 18), 1.0, "low", "orig reason")  # type: ignore[arg-type]
    _p2 = repo.create_plan_version(g.goal_id, "new", date(2025, 4, 21), 6, date(2025, 4, 19), 1.1, "medium", "new reason")  # type: ignore[arg-type]
    fetched = repo.get_plan_version(p1.version_id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.strategy == "orig"
    assert fetched.buffer_days == 5
    assert fetched.changelog == "orig reason"
