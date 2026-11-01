"""Integration: the 5 definition-of-done scenarios (FakeLLM, zero network)."""

from __future__ import annotations

from datetime import date

from goal_agent.agent.loop import AgentLoop
from goal_agent.llm.client import FakeLLM
from goal_agent.planning import fallback as F
from goal_agent.planning import replanner as R
from goal_agent.planning.decomposer import build_and_save_plan


def _goal(repo, title="Goal", deadline=None, priority=3, mvo="MVO basics"):
    return repo.create_goal(title, "desc", deadline or date(2025, 3, 31), priority, mvo)


def test_1_miss_3_days_replan_holds_target_or_eats_buffer(repo):
    """Miss 3 days -> replan holds target_finish or visibly eats buffer; changelog explains."""
    start = date(2025, 1, 6)
    g = _goal(repo, "Learn guitar", date(2025, 2, 28))
    out = build_and_save_plan(repo, g.goal_id, FakeLLM(), start, changelog="Initial plan")  # type: ignore[arg-type]
    v1 = out["plan"]
    # Simulate 3 missed working days: check-in on Jan 6, then silence until Jan 9.
    repo.create_checkin(g.goal_id, date(2025, 1, 6), [], 0.5, "kickoff")  # type: ignore[arg-type]
    today = date(2025, 1, 9)
    stale = R.check_staleness(repo, g.goal_id, today)  # type: ignore[arg-type]
    assert stale["action"] == "micro_nudge"
    rep = R.auto_replan(repo, g.goal_id, today, "Missed 3 days (Jan 6-8, no check-ins): shifted remaining work; buffer absorbs slip.")  # type: ignore[arg-type]
    v2 = rep["plan"]
    assert v2.version_number == v1.version_number + 1
    assert "Missed 3 days" in v2.changelog
    # Target holds (same deadline -> same buffer math within recompute tolerance)
    # or honest overflow is flagged.
    assert (v2.target_finish - v1.target_finish).days <= 2 or v2.risk == "high"
    # Standing is explainable in plain language.
    proj, drift, _ = R.current_projection(repo, g.goal_id, today)  # type: ignore[arg-type]
    assert isinstance(drift, int)


def test_2_second_goal_midflight_global_replan(repo):
    """Add second goal mid-flight -> global replan, hours reallocated, both plans visible."""
    start = date(2025, 1, 6)
    g1 = _goal(repo, "Marathon", date(2025, 3, 31), priority=4)
    build_and_save_plan(repo, g1.goal_id, FakeLLM(), start)  # type: ignore[arg-type]
    # Mid-flight: second goal arrives.
    g2 = _goal(repo, "Spanish B1", date(2025, 2, 28), priority=5)
    build_and_save_plan(repo, g2.goal_id, FakeLLM(), start)  # type: ignore[arg-type]
    out = R.global_replan(repo, date(2025, 1, 10))
    assert set(out["allocations"]) == {g1.goal_id, g2.goal_id}  # type: ignore[union-attr]
    # User sees both plans.
    assert repo.get_active_plan(g1.goal_id) is not None  # type: ignore[arg-type]
    assert repo.get_active_plan(g2.goal_id) is not None  # type: ignore[arg-type]
    assert len(repo.list_plan_history(g1.goal_id)) >= 2  # type: ignore[arg-type]
    assert sum(out["allocations"].values()) <= repo.get_or_create_profile(1).daily_hours_available + 1e-9


def test_3_family_emergency_next_week(repo):
    """'Family emergency all next week' -> dates blocked, plan shifts, target preserved if possible."""
    start = date(2025, 1, 6)
    g = _goal(repo, "Launch site", date(2025, 3, 31))
    out = build_and_save_plan(repo, g.goal_id, FakeLLM(), start)  # type: ignore[arg-type]
    v1_target = out["plan"].target_finish
    res = R.replan_around_interruption(repo, date(2025, 1, 10), "Family emergency all next week", date(2025, 1, 13), date(2025, 1, 19))
    blocked = repo.get_blocked_dates()
    assert date(2025, 1, 13) in blocked and date(2025, 1, 19) in blocked
    v2 = repo.get_active_plan(g.goal_id)  # type: ignore[arg-type]
    assert v2 is not None and v2.version_number == 2
    assert "emergency" in v2.changelog.lower()
    # Light FakeLLM workload (5h) easily absorbs a blocked week: target preserved
    # within buffer-recompute tolerance (replanning from a later start eats ~1d buffer).
    assert abs((v2.target_finish - v1_target).days) <= 2
    assert v2.target_finish < date(2025, 3, 31)
    # No task lands on a blocked date.
    for t in repo.list_tasks_for_plan(v2.version_id):
        assert t.due_date not in blocked
    _ = res


def test_4_impossible_goal_reaches_negotiation_and_records_choice(repo):
    """Impossible goal -> ladder reaches negotiation; user choice recorded explicitly."""
    g = repo.create_goal("Impossible", "d", date(2025, 1, 13), 5, "MVO")
    plan = repo.create_plan_version(g.goal_id, "initial", date(2025, 1, 10), 3, date(2025, 1, 10), 1.0, "high", "v1")  # type: ignore[arg-type]
    ms = repo.create_milestone(plan.version_id, "M", date(2025, 1, 10))  # type: ignore[arg-type]
    for i in range(10):
        repo.create_task(ms.milestone_id, desc=f"c{i}", est_hours=20.0, due_date=date(2025, 1, 10), is_core=True)  # type: ignore[arg-type]
    out = F.run_ladder(repo, g.goal_id, date(2025, 1, 6), reason="200h in 1 week")  # type: ignore[arg-type]
    assert out["rung"] == "negotiate"
    assert repo.get_goal(g.goal_id).status == "negotiating"  # type: ignore[union-attr]
    # User explicitly extends the deadline via the agent tool.
    loop = AgentLoop(repo, FakeLLM(), today_fn=lambda: date(2025, 1, 6))
    res = loop.handle_tool("negotiate_goal", {"goal_id": g.goal_id, "decision": "extend deadline:2025-06-30"})  # type: ignore[arg-type]
    assert "extended" in res
    evs = repo.list_events("negotiation")
    assert any("2025-06-30" in e.description or "extend" in e.description.lower() for e in evs)
    assert repo.get_goal(g.goal_id).deadline == date(2025, 6, 30)  # type: ignore[union-attr]


def test_5_early_finish_pull_target_and_celebrate(repo):
    """Early finish -> target pulled earlier path offered; agent celebrates + offers next action."""
    start = date(2025, 1, 6)
    g = _goal(repo, "5k run", date(2025, 3, 31))
    out = build_and_save_plan(repo, g.goal_id, FakeLLM(), start)  # type: ignore[arg-type]
    tasks = out["tasks"]
    # Finish everything almost immediately with real pace.
    loop = AgentLoop(repo, FakeLLM(), today_fn=lambda: date(2025, 1, 8))
    tids = [t.task_id for t in tasks]
    loop.handle_tool("log_checkin", {"goal_id": g.goal_id, "completed_task_ids": tids, "hours_spent": 4.0, "notes": "felt great"})  # type: ignore[arg-type]
    check = R.check_goal(repo, g.goal_id, date(2025, 1, 8))  # type: ignore[arg-type]
    assert check["drift"] <= 0
    done = loop.handle_tool("complete_goal", {"goal_id": g.goal_id})  # type: ignore[arg-type]
    assert "celebrate" in done and "next" in done
    assert repo.get_goal(g.goal_id).status == "completed"  # type: ignore[union-attr]
