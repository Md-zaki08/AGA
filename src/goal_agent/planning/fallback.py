"""Fallback ladder — each rung returns a plan OR escalates. Never drops silently."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

RungName = Literal["stretch", "descope", "mvo", "negotiate"]


def _sched_tasks_for_goal(repo: Any, goal_id: int) -> list[dict[str, Any]]:
    plan = repo.get_active_plan(goal_id)
    if plan is None:
        return []
    tasks = repo.list_tasks_for_plan(plan.version_id)
    return [
        {"task_id": t.task_id, "est_hours": t.est_hours, "dependencies": list(t.dependencies or []), "is_core": t.is_core, "status": t.status}
        for t in tasks
        if t.status != "done"
    ]


def rung_stretch_availability(
    repo: Any, goal_id: int, today: date, confirm: bool = False
) -> dict[str, Any]:
    """Rung 1: +20% hours/day. Requires explicit user confirmation to APPLY.

    Returns {"ok": bool, "scheduled": ScheduledPlan|None, "message": str,
             "needs_confirmation": True}. Pure proposal unless confirm=True,
    in which case a new plan version is persisted.
    """
    from goal_agent.planning.scheduler import schedule_tasks
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    profile = repo.get_or_create_profile(1)
    if goal is None:
        return {"ok": False, "escalate": True, "message": f"Goal {goal_id} not found"}
    tasks = _sched_tasks_for_goal(repo, goal_id)
    sched_input = [{"task_id": t["task_id"], "est_hours": t["est_hours"], "dependencies": t["dependencies"]} for t in tasks]
    velocity = compute_velocity(repo.list_tasks_with_actuals(5))
    stretched = round(profile.daily_hours_available * 1.2, 2)
    scheduled = schedule_tasks(sched_input, today, goal.deadline, stretched, repo.get_blocked_dates(), velocity, profile.work_days, profile.slippage_multiplier)
    if scheduled.risk != "high" and not confirm:
        return {
            "ok": False,
            "escalate": False,
            "needs_confirmation": True,
            "scheduled": scheduled,
            "message": (
                f"Stretch to {stretched}h/day would fit (projected {scheduled.projected_finish}, "
                f"risk {scheduled.risk}). Confirm to apply — I won't change your hours without your OK."
            ),
        }
    if scheduled.risk != "high" and confirm:
        plan = repo.create_plan_version(
            goal_id, "stretch(+20%)", scheduled.target_finish, scheduled.buffer_days,
            scheduled.projected_finish, velocity, scheduled.risk,
            "Rung 1: stretched availability +20% with user confirmation.",
        )
        _repersist_task_dues(repo, plan.version_id, goal_id, scheduled)  # type: ignore[arg-type]
        return {"ok": True, "plan": plan, "scheduled": scheduled, "message": f"Applied stretch: {stretched}h/day."}
    return {"ok": False, "escalate": True, "scheduled": scheduled, "message": "Stretch alone does not fit — escalating to descope."}


def rung_descope_non_core(repo: Any, goal_id: int, today: date) -> dict[str, Any]:
    """Rung 2: descope all non-core tasks, recompute. Persists new version on success."""
    from goal_agent.planning.scheduler import schedule_tasks
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    profile = repo.get_or_create_profile(1)
    if goal is None:
        return {"ok": False, "escalate": True, "message": "Goal not found"}
    tasks = _sched_tasks_for_goal(repo, goal_id)
    core = [t for t in tasks if t["is_core"]]
    if not core:
        return {"ok": False, "escalate": True, "message": "No core tasks to keep — escalating to MVO/negotiation."}
    # Mark non-core pending tasks deferred (skipped) on the old version for honesty.
    for t in tasks:
        if not t["is_core"] and t["status"] in ("pending", "in_progress"):
            try:
                repo.update_task(t["task_id"], status="skipped")
            except Exception:
                pass
    velocity = compute_velocity(repo.list_tasks_with_actuals(5))
    sched_input = [{"task_id": t["task_id"], "est_hours": t["est_hours"], "dependencies": [d for d in t["dependencies"] if any(c["task_id"] == d for c in core)]} for t in core]
    scheduled = schedule_tasks(sched_input, today, goal.deadline, profile.daily_hours_available, repo.get_blocked_dates(), velocity, profile.work_days, profile.slippage_multiplier)
    if scheduled.risk != "high":
        plan = repo.create_plan_version(
            goal_id, "descope-non-core", scheduled.target_finish, scheduled.buffer_days,
            scheduled.projected_finish, velocity, scheduled.risk,
            "Rung 2: descoped non-core tasks to deferred; recomputed core-only plan.",
        )
        _repersist_task_dues(repo, plan.version_id, goal_id, scheduled, keep_only_core=True)  # type: ignore[arg-type]
        return {"ok": True, "plan": plan, "scheduled": scheduled, "message": f"Descoped to {len(core)} core tasks; projected {scheduled.projected_finish}."}
    return {"ok": False, "escalate": True, "scheduled": scheduled, "message": "Core-only plan still overflows — escalating to MVO reduction."}


def rung_minimal_viable(repo: Any, goal_id: int, today: date) -> dict[str, Any]:
    """Rung 3: reduce to minimal_viable_outcome only (core tasks, buffer may be eaten).

    Schedules core work against the DEADLINE (not target) as a last honest fit
    attempt before negotiation.
    """
    from datetime import timedelta

    from goal_agent.planning.scheduler import schedule_tasks
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    profile = repo.get_or_create_profile(1)
    if goal is None:
        return {"ok": False, "escalate": True, "message": "Goal not found"}
    tasks = _sched_tasks_for_goal(repo, goal_id)
    core = [t for t in tasks if t["is_core"]]
    if not core:
        return {"ok": False, "escalate": True, "message": "No MVO tasks remain — escalating to negotiation."}
    velocity = compute_velocity(repo.list_tasks_with_actuals(5))
    sched_input = [{"task_id": t["task_id"], "est_hours": t["est_hours"], "dependencies": []} for t in core]
    # Temporarily schedule with slippage=1 and then allow finishing by deadline:
    scheduled = schedule_tasks(sched_input, today, goal.deadline, profile.daily_hours_available, repo.get_blocked_dates(), velocity, profile.work_days, slippage=1.0)
    # Accept if it fits by DEADLINE even when target is missed (eat buffer honestly).
    if scheduled.projected_finish <= goal.deadline:
        plan = repo.create_plan_version(
            goal_id, "mvo-only", scheduled.target_finish, scheduled.buffer_days,
            scheduled.projected_finish, velocity, "high" if scheduled.projected_finish > scheduled.target_finish else scheduled.risk,
            f"Rung 3: reduced to minimal viable outcome only ({goal.minimal_viable_outcome[:120]}). Buffer eaten to meet deadline.",
        )
        _repersist_task_dues(repo, plan.version_id, goal_id, scheduled, keep_only_core=True)  # type: ignore[arg-type]
        return {"ok": True, "plan": plan, "scheduled": scheduled, "message": f"MVO-only plan fits by deadline {goal.deadline} (target was {scheduled.target_finish})."}
    return {"ok": False, "escalate": True, "scheduled": scheduled, "message": "Even MVO-only work misses the deadline — escalating to negotiation."}


def rung_negotiate(repo: Any, goal_id: int, reason: str) -> dict[str, Any]:
    """Rung 4: explicit negotiated outcome. NEVER drops silently."""
    goal = repo.get_goal(goal_id)
    if goal is None:
        return {"ok": False, "message": "Goal not found"}
    repo.update_goal_status(goal_id, "negotiating")
    repo.create_event("negotiation", f"Goal {goal_id} needs negotiation: {reason}. Awaiting user choice.")
    return {
        "ok": True,
        "negotiating": True,
        "message": (
            f"'{goal.title}' cannot fit even as MVO-only ({reason}). "
            "Choose explicitly: (a) extend deadline:<YYYY-MM-DD> or "
            "(b) accept reduced scope:<what stays>. Recorded via negotiate_goal. "
            "Nothing was dropped — status is 'negotiating' until you decide."
        ),
    }


def run_ladder(repo: Any, goal_id: int, today: date, reason: str, auto_confirm_stretch: bool = False) -> dict[str, Any]:
    """Run rungs in order; return first successful plan or negotiation outcome."""
    r1 = rung_stretch_availability(repo, goal_id, today, confirm=auto_confirm_stretch)
    if r1.get("ok"):
        return {"rung": "stretch", **r1}
    if r1.get("needs_confirmation") and not auto_confirm_stretch:
        return {"rung": "stretch-confirm", **r1}
    r2 = rung_descope_non_core(repo, goal_id, today)
    if r2.get("ok"):
        return {"rung": "descope", **r2}
    r3 = rung_minimal_viable(repo, goal_id, today)
    if r3.get("ok"):
        return {"rung": "mvo", **r3}
    r4 = rung_negotiate(repo, goal_id, reason)
    return {"rung": "negotiate", **r4}


def _repersist_task_dues(repo: Any, new_version_id: int, goal_id: int, scheduled: Any, keep_only_core: bool = False) -> None:
    """Copy task shells from the previous active plan into the new version with new dues."""
    history = repo.list_plan_history(goal_id)
    prev_id = None
    for p in history:
        if p.version_id != new_version_id:
            prev_id = p.version_id
    # prev_id = latest old version (list is asc, so last non-new). Find max old:
    olds = [p for p in history if p.version_id != new_version_id]
    if not olds:
        return
    prev = max(olds, key=lambda p: p.version_number)
    prev_tasks = repo.list_tasks_for_plan(prev.version_id)
    due_by_old: dict[int, Any] = {}
    # Map scheduled dues by old task id where possible.
    for a in scheduled.assignments:
        due_by_old[int(a.task_id)] = a.due_date
    for t in prev_tasks:
        if keep_only_core and not t.is_core:
            continue
        if t.status == "done":
            continue
        ms = repo.create_milestone(new_version_id, f"carry: {t.desc[:40]}", due_by_old.get(t.task_id, scheduled.target_finish))  # type: ignore[attr-defined]
        repo.create_task(ms.milestone_id, desc=t.desc, est_hours=t.est_hours, due_date=due_by_old.get(t.task_id, scheduled.target_finish), is_core=t.is_core, dependencies=[])  # type: ignore[arg-type]
