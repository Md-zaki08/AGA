"""Pure deterministic scheduling functions.

CRITICAL: The LLM never does date or duration math. All scheduling,
projections, drift, buffer math, and hour allocation live here.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]


class TaskAssignment(BaseModel):
    task_id: int
    due_date: date
    est_hours: float
    scaled_hours: float


class DailyLoad(BaseModel):
    day: date
    hours_used: float


class ScheduledPlan(BaseModel):
    target_finish: date
    buffer_days: int
    projected_finish: date
    risk: RiskLevel = "low"
    note: str = ""
    assignments: list[TaskAssignment] = Field(default_factory=list)
    daily_load: list[DailyLoad] = Field(default_factory=list)
    total_scaled_hours: float = 0.0


def compute_buffer(total_days: int, slippage: float = 1.0) -> int:
    """Buffer = clamp(3, 14, ceil(0.15 * total_days)) * slippage, min 3.

    Args:
        total_days: (deadline - start_date).days, negative treated as 0.
        slippage: user's slippage_multiplier (>=1.0).

    Returns:
        Integer buffer days, minimum 3.
    """
    total = max(0, int(total_days))
    slip = max(1.0, float(slippage))
    base = math.ceil(0.15 * total)
    base = max(3, min(14, base))
    buffered = math.ceil(base * slip)
    return max(3, buffered)


def _working_days_between(
    start: date,
    end_inclusive: date,
    work_days: list[int],
    blocked_dates: set[date],
) -> list[date]:
    """List working dates from start to end inclusive, skipping non-work and blocked."""
    if end_inclusive < start:
        return []
    work_set = set(work_days)
    out: list[date] = []
    cur = start
    while cur <= end_inclusive:
        if cur.weekday() in work_set and cur not in blocked_dates:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _extend_working_days(
    start_after: date,
    work_days: list[int],
    blocked_dates: set[date],
    needed: int,
    limit_days: int = 730,
) -> list[date]:
    """Generate further working days after a date (exclusive of start_after)."""
    work_set = set(work_days)
    out: list[date] = []
    cur = start_after + timedelta(days=1)
    scanned = 0
    while len(out) < needed and scanned < limit_days:
        if cur.weekday() in work_set and cur not in blocked_dates:
            out.append(cur)
        cur += timedelta(days=1)
        scanned += 1
    return out


def _normalize_task(t: object) -> tuple[int, float, list[int]]:
    """Extract (task_id, est_hours, dependencies) from dict, Pydantic, or ORM-like."""
    if isinstance(t, dict):
        tid = int(t.get("task_id", 0))
        est = float(t.get("est_hours", 0.0))
        deps = list(t.get("dependencies", []) or [])
        return tid, est, [int(d) for d in deps]
    tid = int(getattr(t, "task_id", 0) or 0)
    est = float(getattr(t, "est_hours", 0.0) or 0.0)
    raw_deps = getattr(t, "dependencies", []) or []
    return tid, est, [int(d) for d in list(raw_deps)]


def _dependency_depths(task_infos: dict[int, tuple[float, list[int]]]) -> dict[int, int]:
    """Depth 0 = no deps; else 1 + max(dep depths). Missing deps count as depth 0.

    Cycles are broken by treating in-progress nodes as depth 0.
    """
    memo: dict[int, int] = {}
    visiting: set[int] = set()

    def dfs(tid: int) -> int:
        if tid in memo:
            return memo[tid]
        if tid in visiting:
            return 0
        visiting.add(tid)
        _, deps = task_infos.get(tid, (0.0, []))
        if not deps:
            memo[tid] = 0
        else:
            best = -1
            for d in deps:
                if d in task_infos:
                    best = max(best, dfs(d))
                else:
                    best = max(best, -1)  # external dep: treat as no constraint
            memo[tid] = best + 1
        visiting.discard(tid)
        return memo[tid]

    for tid in task_infos:
        dfs(tid)
    return memo


def schedule_tasks(
    tasks: list[object],
    start_date: date,
    deadline: date,
    daily_hours: float,
    blocked_dates: set[date] | list[date] | None = None,
    velocity: float = 1.0,
    work_days: list[int] | None = None,
    slippage: float = 1.0,
) -> ScheduledPlan:
    """Schedule scaled task hours onto working days ending by target_finish.

    - Scales est_hours by velocity.
    - Respects daily_hours, work_days, and blocked_dates.
    - Orders by dependency depth ascending, then scaled hours descending.
    - target_finish = deadline - buffer_days; work is placed to END by
      target_finish, not deadline.
    - If work does not fit: risk="high", projected_finish=honest computed end.
    - Front-load check: ~80% of hours in first ~70% of working days, else
      risk="medium" with a note.
    """
    if work_days is None:
        work_days = [0, 1, 2, 3, 4]
    blocked: set[date] = set(blocked_dates or set())
    vel = max(0.25, float(velocity)) if velocity else 1.0
    cap = max(0.5, float(daily_hours))

    total_days = (deadline - start_date).days
    buffer_days = compute_buffer(total_days, slippage)
    target_finish = deadline - timedelta(days=buffer_days)

    working = _working_days_between(start_date, target_finish, work_days, blocked)

    # Normalize + scale
    infos: dict[int, tuple[float, list[int]]] = {}
    est_map: dict[int, float] = {}
    for t in tasks:
        tid, est, deps = _normalize_task(t)
        # task_id 0 (unsaved) tasks: assign synthetic negative ids to keep unique
        if tid == 0:
            tid = -(len(infos) + 1)
        scaled = max(0.0, float(est) * vel)
        infos[tid] = (scaled, deps)
        est_map[tid] = float(est)

    total_scaled = sum(v[0] for v in infos.values())

    if not infos:
        proj = start_date if not working else working[0]
        # No work: projected is start, risk low
        return ScheduledPlan(
            target_finish=target_finish,
            buffer_days=buffer_days,
            projected_finish=proj,
            risk="low",
            note="No tasks to schedule.",
            assignments=[],
            daily_load=[],
            total_scaled_hours=0.0,
        )

    depths = _dependency_depths(infos)
    ordered = sorted(infos.items(), key=lambda kv: (depths.get(kv[0], 0), -kv[1][0]))

    if not working:
        # No capacity at all before target: everything overflows honestly.
        extended = _extend_working_days(
            target_finish, work_days, blocked, needed=365
        )
        # Allocate across extended days to compute honest finish.
        remaining_table = dict(working) if False else None  # placeholder
        cap_left: dict[date, float] = {d: cap for d in extended}
        due_idx: dict[int, int] = {}
        assignments: list[TaskAssignment] = []
        daily_used: dict[date, float] = {d: 0.0 for d in extended}
        for tid, (scaled, deps) in ordered:
            start_idx = 0
            for d in deps:
                if d in due_idx:
                    start_idx = max(start_idx, due_idx[d])
            need = scaled
            last_idx = start_idx
            idx = start_idx
            while need > 1e-9 and idx < len(extended):
                avail = cap_left[extended[idx]]
                if avail > 1e-9:
                    take = min(avail, need)
                    cap_left[extended[idx]] -= take
                    daily_used[extended[idx]] += take
                    need -= take
                    last_idx = idx
                idx += 1
            due_idx[tid] = last_idx
            due = extended[last_idx] if extended else deadline
            assignments.append(
                TaskAssignment(
                    task_id=tid if tid > 0 else 0,
                    due_date=due,
                    est_hours=est_map[tid],
                    scaled_hours=scaled,
                )
            )
        proj = max((a.due_date for a in assignments), default=deadline)
        daily_load = [
            DailyLoad(day=d, hours_used=round(h, 2)) for d, h in daily_used.items() if h > 1e-9
        ]
        return ScheduledPlan(
            target_finish=target_finish,
            buffer_days=buffer_days,
            projected_finish=proj,
            risk="high",
            note=(
                "No working days available before target_finish "
                "(all blocked or non-work days). Projected finish computed honestly beyond target."
            ),
            assignments=assignments,
            daily_load=daily_load,
            total_scaled_hours=round(total_scaled, 2),
        )

    cap_left_main: dict[date, float] = {d: cap for d in working}
    daily_used_main: dict[date, float] = {d: 0.0 for d in working}
    day_index: dict[date, int] = {d: i for i, d in enumerate(working)}
    due_idx: dict[int, int] = {}
    assignments_main: list[TaskAssignment] = []
    overflow = False

    for tid, (scaled, deps) in ordered:
        start_idx = 0
        for d in deps:
            if d in due_idx:
                start_idx = max(start_idx, due_idx[d])
        need = scaled
        last_idx = start_idx
        idx = start_idx
        while need > 1e-9 and idx < len(working):
            avail = cap_left_main[working[idx]]
            if avail > 1e-9:
                take = min(avail, need)
                cap_left_main[working[idx]] -= take
                daily_used_main[working[idx]] += take
                need -= take
                last_idx = idx
            # Move forward only when current day is full or we took from it?
            # To front-load, keep filling earliest day first: only advance when full.
            if cap_left_main[working[idx]] <= 1e-9:
                idx += 1
            elif need <= 1e-9:
                break
            else:
                # Current day still has room but we... actually take already consumed
                # min(avail, need), so if need remains then avail was fully used.
                idx += 1
        if need > 1e-9:
            overflow = True
            # Record partial placement; remainder handled in extension phase.
            due_idx[tid] = len(working) - 1
            assignments_main.append(
                TaskAssignment(
                    task_id=tid if tid > 0 else 0,
                    due_date=working[-1],
                    est_hours=est_map[tid],
                    scaled_hours=scaled,
                )
            )
        else:
            due_idx[tid] = last_idx
            assignments_main.append(
                TaskAssignment(
                    task_id=tid if tid > 0 else 0,
                    due_date=working[last_idx],
                    est_hours=est_map[tid],
                    scaled_hours=scaled,
                )
            )

    if overflow:
        # Honestly extend beyond target_finish to fit remaining work.
        # Re-run allocation across working + extended days for a truthful projection.
        # Compute leftover hours that did not fit:
        fitted_hours = sum(daily_used_main.values())
        leftover = total_scaled - fitted_hours
        # Generate extension days until leftover fits.
        # Estimate days needed then add margin.
        est_days_needed = max(1, math.ceil(leftover / cap) + 2) if leftover > 0 else 1
        extended = _extend_working_days(
            working[-1], work_days, blocked, needed=est_days_needed + 60
        )
        full_days = working + extended
        cap_left: dict[date, float] = {d: cap for d in full_days}
        daily_used: dict[date, float] = {d: 0.0 for d in full_days}
        due_idx2: dict[int, int] = {}
        assignments: list[TaskAssignment] = []
        for tid, (scaled, deps) in ordered:
            s_idx = 0
            for d in deps:
                if d in due_idx2:
                    s_idx = max(s_idx, due_idx2[d])
            need = scaled
            last = s_idx
            i = s_idx
            while need > 1e-9 and i < len(full_days):
                avail = cap_left[full_days[i]]
                if avail > 1e-9:
                    take = min(avail, need)
                    cap_left[full_days[i]] -= take
                    daily_used[full_days[i]] += take
                    need -= take
                    last = i
                i += 1
            due_idx2[tid] = last
            assignments.append(
                TaskAssignment(
                    task_id=tid if tid > 0 else 0,
                    due_date=full_days[last],
                    est_hours=est_map[tid],
                    scaled_hours=scaled,
                )
            )
        proj = max((a.due_date for a in assignments), default=target_finish)
        daily_load = [
            DailyLoad(day=d, hours_used=round(h, 2)) for d, h in daily_used.items() if h > 1e-9
        ]
        return ScheduledPlan(
            target_finish=target_finish,
            buffer_days=buffer_days,
            projected_finish=proj,
            risk="high",
            note=(
                f"Work does not fit before target_finish {target_finish.isoformat()}. "
                f"Needs {round(total_scaled,1)}h but only "
                f"{round(sum(1 for _ in working) * cap,1)}h available. "
                f"Honest projected finish {proj.isoformat()}."
            ),
            assignments=assignments,
            daily_load=daily_load,
            total_scaled_hours=round(total_scaled, 2),
        )

    # Fits within target: front-load verification.
    n = len(working)
    cutoff = max(1, math.ceil(0.7 * n))
    first_days = set(working[:cutoff])
    hours_first = sum(h for d, h in daily_used_main.items() if d in first_days)
    ratio = (hours_first / total_scaled) if total_scaled > 1e-9 else 1.0
    risk: RiskLevel = "low"
    note = ""
    if ratio < 0.8:
        risk = "medium"
        note = (
            f"Only {ratio*100:.0f}% of hours land in the first 70% of working days "
            f"({hours_first:.1f}/{total_scaled:.1f}h); dependencies or load prevent "
            "front-loading the ideal 80/70 split."
        )
    proj = max((a.due_date for a in assignments_main), default=target_finish)
    daily_load = [
        DailyLoad(day=d, hours_used=round(h, 2)) for d, h in daily_used_main.items() if h > 1e-9
    ]
    return ScheduledPlan(
        target_finish=target_finish,
        buffer_days=buffer_days,
        projected_finish=proj,
        risk=risk,
        note=note,
        assignments=assignments_main,
        daily_load=daily_load,
        total_scaled_hours=round(total_scaled, 2),
    )


def project_finish(
    plan: object,
    completed_hours: float,
    velocity: float,
    today: date,
    *,
    daily_hours: float = 2.0,
    work_days: list[int] | None = None,
    blocked_dates: set[date] | list[date] | None = None,
    total_hours: float | None = None,
) -> date:
    """Recompute end date from remaining scaled hours and remaining availability.

    Args:
        plan: ScheduledPlan | dict with total_scaled_hours/total_hours/tasks
            | float total hours. Flexible to satisfy both spec call shapes.
        completed_hours: hours already done (same units as plan total).
        velocity: current user velocity multiplier.
        today: date to project from (inclusive).
        daily_hours: capacity per working day.
        work_days: weekday ints; defaults to Mon-Fri.
        blocked_dates: dates to skip.
        total_hours: explicit override for plan total (est hours).

    Returns:
        Projected finish date (a working day, or today if nothing remains).
    """
    if work_days is None:
        work_days = [0, 1, 2, 3, 4]
    blocked: set[date] = set(blocked_dates or set())
    vel = max(0.25, float(velocity)) if velocity else 1.0
    cap = max(0.5, float(daily_hours))

    total: float | None = total_hours
    if total is None:
        if isinstance(plan, (int, float)):
            total = float(plan)
        elif isinstance(plan, ScheduledPlan):
            total = float(plan.total_scaled_hours / vel) if vel else float(plan.total_scaled_hours)
            # total_scaled_hours is already scaled; convert back to est then re-scale
            # to keep projection consistent: remaining_est * vel.
            # Simpler: remaining_scaled = total_scaled - completed*vel?
            # We handle below via scaled-total path.
            total = None  # mark scaled path
            total_scaled = float(plan.total_scaled_hours)
            completed_scaled = float(completed_hours) * vel
            remaining_scaled = max(0.0, total_scaled - completed_scaled)
            if remaining_scaled <= 1e-9:
                return today
            return _walk_from(today, remaining_scaled, cap, work_days, blocked)
        elif isinstance(plan, dict):
            for key in ("total_scaled_hours", "total_hours", "total_est_hours"):
                if key in plan:
                    total = float(plan[key])
                    if key == "total_scaled_hours":
                        # already scaled
                        remaining_scaled = max(0.0, total - float(completed_hours) * vel)
                        if remaining_scaled <= 1e-9:
                            return today
                        return _walk_from(today, remaining_scaled, cap, work_days, blocked)
                    break
            if total is None and "tasks" in plan:
                raw_tasks = plan["tasks"]  # type: ignore[index]
                s = 0.0
                for t in raw_tasks:  # type: ignore[union-attr]
                    if isinstance(t, dict):
                        s += float(t.get("est_hours", 0.0))
                    else:
                        s += float(getattr(t, "est_hours", 0.0) or 0.0)
                total = s
        else:
            # Generic object with attributes
            for attr in ("total_scaled_hours", "total_hours"):
                if hasattr(plan, attr):
                    v = float(getattr(plan, attr))
                    if attr == "total_scaled_hours":
                        remaining_scaled = max(0.0, v - float(completed_hours) * vel)
                        if remaining_scaled <= 1e-9:
                            return today
                        return _walk_from(today, remaining_scaled, cap, work_days, blocked)
                    total = v
                    break
            if total is None and hasattr(plan, "assignments"):
                total = sum(float(a.scaled_hours) / vel for a in getattr(plan, "assignments"))  # type: ignore[attr-defined]

    if total is None:
        total = 0.0
    remaining_est = max(0.0, float(total) - float(completed_hours))
    if remaining_est <= 1e-9:
        return today
    remaining_scaled = remaining_est * vel
    return _walk_from(today, remaining_scaled, cap, work_days, blocked)


def _walk_from(
    today: date, remaining_scaled: float, cap: float, work_days: list[int], blocked: set[date]
) -> date:
    work_set = set(work_days)
    cur = today
    need = remaining_scaled
    scanned = 0
    last_work = today
    # If today itself is non-working/blocked, advance to next working day first.
    while scanned < 730:
        is_work = cur.weekday() in work_set and cur not in blocked
        if is_work:
            last_work = cur
            need -= cap
            if need <= 1e-9:
                return cur
        cur += timedelta(days=1)
        scanned += 1
    return last_work
