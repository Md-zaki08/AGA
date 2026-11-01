"""Tests for replanner triggers (deterministic, no network)."""

from __future__ import annotations

from datetime import date, timedelta

from goal_agent.planning import replanner as R


def _seed_goal_with_plan(repo, title="G", deadline=None, priority=3, target=None, tasks=None):
    deadline = deadline or date(2025, 3, 31)
    g = repo.create_goal(title, "desc", deadline, priority, "MVO")
    target = target or date(2025, 3, 20)
    plan = repo.create_plan_version(g.goal_id, "initial", target, 3, target, 1.0, "low", "v1")  # type: ignore[arg-type]
    ms = repo.create_milestone(plan.version_id, "M1", target)  # type: ignore[arg-type]
    for desc, est in (tasks or [("t1", 2.0), ("t2", 1.0)]):
        repo.create_task(ms.milestone_id, desc=desc, est_hours=est, due_date=target, is_core=True)  # type: ignore[arg-type]
    return g, plan


def test_compute_drift_days():
    assert R.compute_drift_days(date(2025, 1, 10), date(2025, 1, 10)) == 0
    assert R.compute_drift_days(date(2025, 1, 12), date(2025, 1, 10)) == 2
    assert R.compute_drift_days(date(2025, 1, 8), date(2025, 1, 10)) == -2


def test_handle_drift_branches():
    today = date(2025, 1, 6)
    assert "On track" in R.handle_drift(None, 1, 0, today)
    assert "Ahead" in R.handle_drift(None, 1, -3, today)
    assert "auto-replan" in R.handle_drift(None, 1, 2, today)
    assert "At risk" in R.handle_drift(None, 1, 5, today)


def test_check_goal_healthy_when_little_work(repo):
    today = date(2025, 1, 6)
    g, _plan = _seed_goal_with_plan(repo, deadline=date(2025, 3, 31), target=date(2025, 3, 20), tasks=[("t1", 1.0)])
    out = R.check_goal(repo, g.goal_id, today)  # type: ignore[arg-type]
    assert out["drift"] <= 0
    assert out["action"] in ("healthy", "pull_earlier", "healthy+micro_nudge", "pull_earlier+micro_nudge")


def test_check_goal_flags_at_risk_on_big_drift(repo):
    today = date(2025, 1, 6)
    # 100h remaining, 2h/day -> projected far beyond March target.
    g, _plan = _seed_goal_with_plan(
        repo, deadline=date(2025, 1, 31), target=date(2025, 1, 20), tasks=[("big", 100.0)]
    )
    out = R.check_goal(repo, g.goal_id, today)  # type: ignore[arg-type]
    assert out["drift"] > 3
    assert "strategy_options" in out["action"]
    assert repo.get_goal(g.goal_id).status == "at_risk"  # type: ignore[union-attr]


def test_auto_replan_creates_new_version_with_changelog(repo):
    today = date(2025, 1, 6)
    g, p1 = _seed_goal_with_plan(repo)
    out = R.auto_replan(repo, g.goal_id, today, "drift +2d: shift/compress")  # type: ignore[arg-type]
    assert out["plan"].version_number == p1.version_number + 1
    assert "shift" in out["plan"].changelog
    hist = repo.list_plan_history(g.goal_id)  # type: ignore[arg-type]
    assert len(hist) == 2
    assert sum(1 for p in hist if p.is_active) == 1


def test_interruption_blocks_dates_and_replans(repo):
    today = date(2025, 1, 6)
    g, p1 = _seed_goal_with_plan(repo)
    res = R.replan_around_interruption(repo, today, "Family emergency", date(2025, 1, 10), date(2025, 1, 12))
    assert date(2025, 1, 10) in repo.get_blocked_dates()
    assert date(2025, 1, 12) in repo.get_blocked_dates()
    assert res[0]["goal_id"] == g.goal_id  # type: ignore[union-attr]
    hist = repo.list_plan_history(g.goal_id)  # type: ignore[arg-type]
    assert len(hist) == 2
    assert "interruption" in hist[-1].changelog.lower()


def test_staleness_micro_and_scope(repo):
    g, _p = _seed_goal_with_plan(repo, tasks=[("tiny", 0.5), ("huge", 5.0)])
    created = repo.get_goal(g.goal_id).created_at.date()  # type: ignore[union-attr]
    # Simulate: 3 days after creation with no check-in -> micro nudge naming smallest task.
    out2 = R.check_staleness(repo, g.goal_id, created + timedelta(days=3))  # type: ignore[arg-type]
    assert out2["action"] == "micro_nudge"
    assert "tiny" in out2["message"]
    out5 = R.check_staleness(repo, g.goal_id, created + timedelta(days=6))  # type: ignore[arg-type]
    assert out5["action"] == "scope_or_pause"
    # After a fresh check-in, no nudge.
    repo.create_checkin(g.goal_id, created + timedelta(days=6), [], 1.0, "ok")  # type: ignore[arg-type]
    out0 = R.check_staleness(repo, g.goal_id, created + timedelta(days=6))  # type: ignore[arg-type]
    assert out0["action"] == "none"
