"""Tests for global multi-goal replan + plan immutability."""

from __future__ import annotations

from datetime import date

from goal_agent.planning import replanner as R


def _goal(repo, title, deadline, priority, est=4.0):
    g = repo.create_goal(title, "d", deadline, priority, "mvo")
    plan = repo.create_plan_version(g.goal_id, "initial", deadline, 3, deadline, 1.0, "low", "v1")  # type: ignore[arg-type]
    ms = repo.create_milestone(plan.version_id, "M", deadline)  # type: ignore[arg-type]
    repo.create_task(ms.milestone_id, desc=f"{title}-t", est_hours=est, due_date=deadline, is_core=True)  # type: ignore[arg-type]
    return g


def test_global_replan_allocates_both_and_orders_by_deadline_priority(repo):
    today = date(2025, 1, 6)
    g1 = _goal(repo, "First", date(2025, 2, 10), priority=2)
    g2 = _goal(repo, "Second", date(2025, 2, 5), priority=5)
    out = R.global_replan(repo, today)
    assert set(out["allocations"].keys()) == {g1.goal_id, g2.goal_id}  # type: ignore[union-attr]
    # Order: earlier deadline first regardless of priority; tie -> higher priority first.
    assert out["order"][0] == g2.goal_id  # type: ignore[union-attr]
    # Every active goal gets a new version recording the allocation.
    assert len(repo.list_plan_history(g1.goal_id)) == 2  # type: ignore[arg-type]
    assert len(repo.list_plan_history(g2.goal_id)) == 2  # type: ignore[arg-type]
    assert sum(out["allocations"].values()) <= repo.get_or_create_profile(1).daily_hours_available + 1e-9


def test_global_replan_min_share_and_conflict(repo):
    today = date(2025, 1, 6)
    profile = repo.get_or_create_profile(1)
    profile.daily_hours_available = 2.0
    repo.save_profile(profile)
    # 6 goals x 0.5h = 3h > 2h supply -> conflict presented with priority order.
    goals = [_goal(repo, f"G{i}", date(2025, 3, 1), priority=(i % 5) + 1) for i in range(6)]
    out = R.global_replan(repo, today)
    assert out["conflict"] is not None
    assert "priority order" in out["conflict"].lower() or "exceeds" in out["conflict"].lower()
    assert len(out["allocations"]) == 6
    _ = goals


def test_plan_immutability_insert_only(repo):
    g = repo.create_goal("Imm", "d", date(2025, 3, 31), 3, "mvo")
    p1 = repo.create_plan_version(g.goal_id, "s1", date(2025, 3, 20), 3, date(2025, 3, 18), 1.0, "low", "first")  # type: ignore[arg-type]
    snap = (p1.target_finish, p1.buffer_days, p1.changelog, p1.strategy)
    p2 = repo.create_plan_version(g.goal_id, "s2", date(2025, 3, 21), 4, date(2025, 3, 19), 1.2, "medium", "second")  # type: ignore[arg-type]
    assert p2.version_number == p1.version_number + 1
    hist = repo.list_plan_history(g.goal_id)  # type: ignore[arg-type]
    assert len(hist) == 2
    # Old version content untouched, now inactive; exactly one active.
    old = [p for p in hist if p.version_number == 1][0]
    assert (old.target_finish, old.buffer_days, old.changelog, old.strategy) == snap
    assert old.is_active is False
    assert sum(1 for p in hist if p.is_active) == 1
    assert hist[-1].version_id == p2.version_id and hist[-1].is_active is True
