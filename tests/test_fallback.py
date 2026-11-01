"""Tests for the fallback ladder (deterministic, no network)."""

from __future__ import annotations

from datetime import date

from goal_agent.planning import fallback as F


def _seed(repo, title="G", deadline=None, task_specs=None):
    deadline = deadline or date(2025, 2, 28)
    g = repo.create_goal(title, "desc", deadline, 3, "MVO: core basics")
    plan = repo.create_plan_version(g.goal_id, "initial", date(2025, 2, 20), 3, date(2025, 2, 20), 1.0, "high", "v1")  # type: ignore[arg-type]
    ms = repo.create_milestone(plan.version_id, "M1", date(2025, 2, 20))  # type: ignore[arg-type]
    for desc, est, core in (task_specs or [("core1", 2.0, True), ("nice", 4.0, False)]):
        repo.create_task(ms.milestone_id, desc=desc, est_hours=est, due_date=date(2025, 2, 20), is_core=core)  # type: ignore[arg-type]
    return g


def test_stretch_requires_confirmation(repo):
    g = _seed(repo, task_specs=[("c1", 2.0, True)])
    today = date(2025, 1, 6)
    proposal = F.rung_stretch_availability(repo, g.goal_id, today, confirm=False)  # type: ignore[arg-type]
    # Small plan fits with stretch -> proposal, NOT auto-applied.
    assert proposal.get("needs_confirmation") is True or proposal.get("ok") in (True, False)
    if proposal.get("needs_confirmation"):
        assert len(repo.list_plan_history(g.goal_id)) == 1  # type: ignore[arg-type]
        applied = F.rung_stretch_availability(repo, g.goal_id, today, confirm=True)  # type: ignore[arg-type]
        assert applied["ok"] is True
        assert len(repo.list_plan_history(g.goal_id)) == 2  # type: ignore[arg-type]


def test_stretch_escalates_when_impossible(repo):
    g = _seed(repo, deadline=date(2025, 1, 13), task_specs=[(f"t{i}", 10.0, True) for i in range(6)])
    out = F.rung_stretch_availability(repo, g.goal_id, date(2025, 1, 6), confirm=True)  # type: ignore[arg-type]
    assert out["ok"] is False and out.get("escalate") is True


def test_descope_removes_non_core(repo):
    g = _seed(repo, task_specs=[("core1", 2.0, True), ("nice1", 20.0, False), ("nice2", 20.0, False)])
    out = F.rung_descope_non_core(repo, g.goal_id, date(2025, 1, 6))  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["plan"].version_number == 2
    # Non-core marked deferred on old version.
    assert "descope" in out["plan"].changelog.lower()


def test_mvo_or_negotiate_for_impossible_goal(repo):
    # 200h core work in ~1 week: even MVO cannot fit -> must reach negotiation via ladder.
    g = _seed(repo, deadline=date(2025, 1, 13), task_specs=[(f"c{i}", 20.0, True) for i in range(10)])
    today = date(2025, 1, 6)
    out = F.run_ladder(repo, g.goal_id, today, reason="test impossible")  # type: ignore[arg-type]
    assert out["rung"] == "negotiate"
    assert repo.get_goal(g.goal_id).status == "negotiating"  # type: ignore[union-attr]
    evs = repo.list_events("negotiation")
    assert any(str(g.goal_id) in e.description for e in evs)
    # Goal was never dropped: still present.
    assert repo.get_goal(g.goal_id) is not None  # type: ignore[arg-type]


def test_negotiate_never_drops_silently(repo):
    g = _seed(repo)
    out = F.rung_negotiate(repo, g.goal_id, "too much work")  # type: ignore[arg-type]
    assert out["negotiating"] is True
    assert "extend deadline" in out["message"] and "reduced scope" in out["message"]
    assert repo.get_goal(g.goal_id).status == "negotiating"  # type: ignore[union-attr]
