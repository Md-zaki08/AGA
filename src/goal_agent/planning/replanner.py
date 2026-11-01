"""Trigger detection + strategy dispatch (checked after every check-in and event)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def compute_drift_days(projected_finish: date, target_finish: date) -> int:
    return (projected_finish - target_finish).days


def _remaining_est_for_goal(repo: Any, goal_id: int) -> tuple[float, float]:
    plan = repo.get_active_plan(goal_id)
    if plan is None:
        return 0.0, 0.0
    tasks = repo.list_tasks_for_plan(plan.version_id)
    total = sum(t.est_hours for t in tasks)
    done = sum(t.est_hours for t in tasks if t.status == "done")
    return total, done


def current_projection(repo: Any, goal_id: int, today: date) -> tuple[date, int, Any]:
    """Return (projected_finish, drift_days, plan). Recomputes from remaining work."""
    from goal_agent.planning.scheduler import project_finish
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    plan = repo.get_active_plan(goal_id)
    profile = repo.get_or_create_profile(1)
    if goal is None or plan is None:
        return today, 0, plan
    total, done = _remaining_est_for_goal(repo, goal_id)
    velocity = compute_velocity(repo.list_tasks_with_actuals(5))
    proj = project_finish(
        total, done, velocity, today,
        daily_hours=profile.daily_hours_available,
        work_days=profile.work_days,
        blocked_dates=repo.get_blocked_dates(),
    )
    return proj, compute_drift_days(proj, plan.target_finish), plan


def handle_drift(repo: Any, goal_id: int, drift_days: int, today: date) -> str:
    """Drift dispatch used by the agent loop after check-ins.

    - drift <= -2: offer pulling target earlier (early finish path).
    - drift <= 0: healthy.
    - 0 < drift <= 3: auto-replan note (caller persists new version).
    - drift > 3: present strategy options.
    """
    if drift_days <= -3:
        return f"Ahead by {-drift_days}d — reply 'pull target earlier' and I'll celebrate + tighten the plan."
    if drift_days <= 0:
        return "On track — nice work."
    if drift_days <= 3:
        return f"Slipped {drift_days}d — I'll auto-replan (shift/compress) and hold target if the buffer allows."
    return (
        f"At risk: {drift_days}d past target. Options: (1) recompute with real pace, "
        "(2) resequence/parallelize, (3) descope non-core, (4) fallback ladder. "
        "Tell me which, or say 'ladder' to escalate step by step."
    )


def check_goal(repo: Any, goal_id: int, today: date) -> dict[str, Any]:
    """Full per-goal trigger check: drift + staleness. Returns action dict."""
    proj, drift, plan = current_projection(repo, goal_id, today)
    goal = repo.get_goal(goal_id)
    action = "healthy"
    message = "On track."
    if drift <= -3:
        action = "pull_earlier"
        message = f"Early by {-drift}d (projected {proj} vs target {plan.target_finish}). Offer to pull target earlier." if plan else message
    elif drift <= 0:
        action = "healthy"
        message = f"Healthy (projected {proj} vs target {plan.target_finish})." if plan else message
    elif drift <= 3:
        action = "auto_replan"
        message = f"Drift +{drift}d — auto-replan (shift/compress), informing user plainly."
    else:
        action = "strategy_options"
        message = handle_drift(repo, goal_id, drift, today)
    # Staleness overlays (no check-in for 2/5 days).
    stale = check_staleness(repo, goal_id, today)
    if stale["action"] != "none":
        action = stale["action"] if action == "healthy" else f"{action}+{stale['action']}"
        message += " " + stale["message"]
    # Update at_risk flag honestly.
    if goal and drift > 3 and goal.status == "active":
        try:
            repo.update_goal_status(goal_id, "at_risk")
        except Exception:
            pass
    if goal and drift <= 0 and goal.status == "at_risk":
        try:
            repo.update_goal_status(goal_id, "active")
        except Exception:
            pass
    return {"goal_id": goal_id, "projected": proj, "drift": drift, "action": action, "message": message, "plan": plan}


def auto_replan(repo: Any, goal_id: int, today: date, reason: str) -> dict[str, Any]:
    """Strategy (1)+(2): recompute with current velocity, resequence by dependencies.

    Deterministic — reschedules EXISTING remaining tasks (no LLM), persists a new
    immutable version with changelog. Returns {"plan", "scheduled"}.
    """
    from goal_agent.planning.scheduler import schedule_tasks
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    plan = repo.get_active_plan(goal_id)
    profile = repo.get_or_create_profile(1)
    if goal is None or plan is None:
        raise ValueError(f"No active plan for goal {goal_id}")
    tasks = [t for t in repo.list_tasks_for_plan(plan.version_id) if t.status != "done"]
    sched_input = [{"task_id": t.task_id, "est_hours": t.est_hours, "dependencies": [d for d in (t.dependencies or [])]} for t in tasks]
    velocity = compute_velocity(repo.list_tasks_with_actuals(5))
    scheduled = schedule_tasks(sched_input, today, goal.deadline, profile.daily_hours_available, repo.get_blocked_dates(), velocity, profile.work_days, profile.slippage_multiplier)
    new_plan = repo.create_plan_version(
        goal_id, "auto-replan(recompute+resequence)", scheduled.target_finish,
        scheduled.buffer_days, scheduled.projected_finish, velocity, scheduled.risk, reason,
    )
    # Carry remaining task shells forward with fresh dues.
    due_by = {int(a.task_id): a.due_date for a in scheduled.assignments}
    for t in tasks:
        ms = repo.create_milestone(new_plan.version_id, f"carry: {t.desc[:40]}", due_by.get(t.task_id, scheduled.target_finish))  # type: ignore[attr-defined]
        repo.create_task(ms.milestone_id, desc=t.desc, est_hours=t.est_hours, due_date=due_by.get(t.task_id, scheduled.target_finish), is_core=t.is_core, dependencies=[])  # type: ignore[arg-type]
    return {"plan": new_plan, "scheduled": scheduled}


def check_staleness(repo: Any, goal_id: int, today: date) -> dict[str, Any]:
    """No check-in for 2 days -> micro-task nudge; 5 days -> scope/pause proposal."""
    last = repo.get_last_checkin_date(goal_id)
    goal = repo.get_goal(goal_id)
    if goal is not None and last is None:
        # Never checked in: count from goal creation.
        created = goal.created_at.date() if hasattr(goal.created_at, "date") else today
        gap = (today - created).days
    elif last is None:
        return {"action": "none", "message": ""}
    else:
        gap = (today - last).days
    if gap >= 5:
        return {"action": "scope_or_pause", "message": f"No check-in for {gap}d — propose scope reduction or pause; smallest step still counts.", "gap": gap}
    if gap >= 2:
        smallest = smallest_pending_task(repo, goal_id)
        hint = f" Try '{smallest}' first." if smallest else ""
        return {"action": "micro_nudge", "message": f"No check-in for {gap}d — micro-task nudge:{hint}", "gap": gap}
    return {"action": "none", "message": "", "gap": gap}


def smallest_pending_task(repo: Any, goal_id: int) -> str | None:
    plan = repo.get_active_plan(goal_id)
    if plan is None:
        return None
    pending = [t for t in repo.list_tasks_for_plan(plan.version_id) if t.status in ("pending", "in_progress")]
    if not pending:
        return None
    smallest = min(pending, key=lambda t: t.est_hours)
    return smallest.desc


def replan_around_interruption(repo: Any, today: date, description: str, start: date, end: date, llm: Any = None) -> list[dict[str, Any]]:
    """Block dates via Event, then replan every active goal (eat buffer first).

    Uses deterministic auto_replan (existing tasks); pass llm to force full
    regeneration instead (not used in tests).
    """
    repo.create_event("interruption", description, start, end)
    results = []
    for g in repo.list_active_goals():
        try:
            if llm is not None:
                from goal_agent.planning.decomposer import build_and_save_plan

                out = build_and_save_plan(repo, g.goal_id, llm, today, strategy="interruption", changelog=f"Replanned around interruption: {description} {start}..{end}")  # type: ignore[arg-type]
                results.append({"goal_id": g.goal_id, "version": out["plan"].version_number, "target_finish": str(out["plan"].target_finish)})
            else:
                out = auto_replan(repo, g.goal_id, today, f"Replanned around interruption: {description} {start}..{end}")  # type: ignore[arg-type]
                results.append({"goal_id": g.goal_id, "version": out["plan"].version_number, "target_finish": str(out["plan"].target_finish)})
        except Exception as e:
            results.append({"goal_id": g.goal_id, "error": str(e)})  # type: ignore[attr-defined]
    return results


def global_replan(repo: Any, today: date) -> dict[str, Any]:
    """Multi-goal replan: sort by (deadline asc, priority desc), allocate greedily.

    Every active goal gets >=0.5h/day if feasible; if total demand exceeds
    supply, present the conflict + proposed priority order.
    """
    from goal_agent.planning.scheduler import project_finish
    from goal_agent.planning.velocity import compute_velocity

    profile = repo.get_or_create_profile(1)
    goals = sorted(repo.list_active_goals(), key=lambda g: (g.deadline, -g.priority))
    daily = profile.daily_hours_available
    n = len(goals)
    min_share = 0.5
    allocations: dict[int, float] = {}
    conflict: str | None = None
    if n == 0:
        return {"allocations": {}, "order": [], "conflict": None}
    if n * min_share > daily:
        # Infeasible to give everyone 0.5h: propose priority order, allocate top-down.
        conflict = (
            f"Demand exceeds supply: {n} active goals need >=0.5h/day each "
            f"({n*min_share:.1f}h) but only {daily:.1f}h available. Proposed priority order: "
            + ", ".join(f"{g.title} (P{g.priority}, due {g.deadline})" for g in goals)
        )
        remaining = daily
        for g in goals:
            share = min(min_share, remaining) if remaining > 0 else 0.0
            allocations[g.goal_id] = round(share, 2)  # type: ignore[arg-type]
            remaining -= share
    else:
        # Estimate demand per goal from remaining scaled hours / days left.
        velocity = compute_velocity(repo.list_tasks_with_actuals(5))
        demands: dict[int, float] = {}
        for g in goals:
            total, done = _remaining_est_for_goal(repo, g.goal_id)  # type: ignore[arg-type]
            remaining = max(0.0, (total - done) * velocity)
            days_left = max(1, (g.deadline - today).days)
            demands[g.goal_id] = remaining / days_left  # type: ignore[index]
        total_demand = sum(demands.values()) or 1.0
        if total_demand <= daily:
            # Everyone gets what they need (at least 0.5 if they need anything).
            for g in goals:
                need = demands[g.goal_id]  # type: ignore[index]
                allocations[g.goal_id] = round(max(min_share if need > 0 else 0.0, need), 2)  # type: ignore[arg-type]
        else:
            # Pro-rata with 0.5 floor where feasible, then scale the rest.
            base = {gid: (min_share if demands[gid] > 0 else 0.0) for gid in demands}
            base_total = sum(base.values())
            leftover = daily - base_total
            extra_demand = sum(max(0.0, demands[gid] - base[gid]) for gid in demands)
            for g in goals:
                gid = g.goal_id  # type: ignore[assignment]
                extra = max(0.0, demands[gid] - base[gid])
                share = base[gid] + (leftover * (extra / extra_demand) if extra_demand > 0 else 0)
                allocations[gid] = round(share, 2)
            conflict = (
                f"Total demand {total_demand:.1f}h/day exceeds {daily:.1f}h supply. "
                "Hours reallocated pro-rata by priority order: "
                + ", ".join(f"{g.title}: {allocations[g.goal_id]}h/day" for g in goals)  # type: ignore[index]
            )
    order = [g.goal_id for g in goals]
    # Persist a global-replan version per goal recording its allocation.
    for g in goals:
        try:
            plan = repo.get_active_plan(g.goal_id)  # type: ignore[arg-type]
            if plan is None:
                continue
            share = allocations.get(g.goal_id, min_share)  # type: ignore[arg-type]
            # Honest projection with the allocated share.
            total, done = _remaining_est_for_goal(repo, g.goal_id)  # type: ignore[arg-type]
            velocity = compute_velocity(repo.list_tasks_with_actuals(5))
            proj = project_finish(total, done, velocity, today, daily_hours=max(0.5, share), work_days=profile.work_days, blocked_dates=repo.get_blocked_dates())
            repo.create_plan_version(
                g.goal_id, f"global-replan({share}h/day)", plan.target_finish, plan.buffer_days,  # type: ignore[arg-type]
                proj, velocity, plan.risk,
                f"Global replan: {share}h/day allocated by (deadline, priority) order.",
            )
        except Exception:
            pass
    _ = timedelta(days=0)  # keep import used
    return {"allocations": allocations, "order": order, "conflict": conflict}
