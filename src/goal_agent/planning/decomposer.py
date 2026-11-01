"""LLM decomposition: goal -> milestones/tasks. Scheduler owns all date math."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from goal_agent.llm.prompts import DECOMPOSER_PROMPT


class DecomposedTask(BaseModel):
    desc: str
    est_hours: float = Field(gt=0)
    is_core: bool = True
    deps: list[int] = Field(default_factory=list)


class DecomposedMilestone(BaseModel):
    name: str
    tasks: list[DecomposedTask]


class Decomposition(BaseModel):
    milestones: list[DecomposedMilestone]


def _coerce_decomposition(raw: Any) -> Decomposition:
    """Validate raw LLM output; raises ValidationError on bad data."""
    if isinstance(raw, Decomposition):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(f"Decomposition must be a dict, got {type(raw).__name__}")
    # Accept {"milestones": [...]} or {"arguments": {...}} wrappers.
    if "arguments" in raw and isinstance(raw["arguments"], dict):
        raw = raw["arguments"]
    milestones_raw = raw.get("milestones", [])
    milestones: list[DecomposedMilestone] = []
    for ms in milestones_raw:
        if not isinstance(ms, dict):
            raise ValueError("Each milestone must be an object")
        tasks: list[DecomposedTask] = []
        for t in ms.get("tasks", []):
            if not isinstance(t, dict):
                raise ValueError("Each task must be an object")
            tasks.append(
                DecomposedTask(
                    desc=str(t.get("desc", "") or t.get("description", "") or "Untitled task"),
                    est_hours=float(t.get("est_hours", t.get("hours", 1.0))),
                    is_core=bool(t.get("is_core", True)),
                    deps=[int(d) for d in (t.get("deps", t.get("dependencies", [])) or [])],
                )
            )
        milestones.append(DecomposedMilestone(name=str(ms.get("name", "Milestone")), tasks=tasks))
    if not milestones:
        raise ValueError("Decomposition must contain at least one milestone")
    if all(len(m.tasks) == 0 for m in milestones):
        raise ValueError("Decomposition must contain at least one task")
    return Decomposition(milestones=milestones)


def decompose_goal(
    goal_title: str,
    goal_description: str,
    minimal_viable_outcome: str,
    llm: Any,
) -> Decomposition:
    """Call the LLM once; retry once on invalid output.

    The LLM only proposes structure + hour estimates — never dates.
    """
    from goal_agent.llm.tools import get_decomposer_tool

    messages = [
        {"role": "system", "content": DECOMPOSER_PROMPT},
        {
            "role": "user",
            "content": (
                f"Goal: {goal_title}\nDescription: {goal_description}\n"
                f"Minimal viable outcome: {minimal_viable_outcome}\n"
                "Break this down via propose_breakdown."
            ),
        },
    ]
    tools = [get_decomposer_tool()]

    resp = llm.complete(messages, tools)
    tool_calls = resp.get("tool_calls") or []
    if not tool_calls:
        # Retry once with an explicit nudge toward the tool.
        resp = llm.complete(
            messages + [{"role": "user", "content": "You must call propose_breakdown with the breakdown."}],
            tools,
        )
        tool_calls = resp.get("tool_calls") or []
    if not tool_calls:
        raise ValueError("LLM did not return a breakdown tool call")
    raw_args = tool_calls[0].get("arguments", {})
    try:
        return _coerce_decomposition(raw_args)
    except Exception as first_err:
        # Retry once: feed the validation error back.
        retry_messages = messages + [
            {"role": "user", "content": f"Your last output was invalid: {first_err}. Return a valid propose_breakdown call."}
        ]
        resp2 = llm.complete(retry_messages, tools)
        tool_calls2 = resp2.get("tool_calls") or []
        if not tool_calls2:
            raise ValueError(f"LLM retry failed: {first_err}") from first_err
        return _coerce_decomposition(tool_calls2[0].get("arguments", {}))


def build_and_save_plan(
    repo: Any,
    goal_id: int,
    llm: Any,
    start_date: date,
    strategy: str = "initial",
    changelog: str = "Initial plan",
    daily_hours_override: float | None = None,
) -> dict[str, Any]:
    """Full plan-generation integration: LLM decompose + deterministic schedule + persist.

    Returns {"plan": PlanVersion, "milestones": [...], "tasks": [...], "scheduled": ScheduledPlan}.
    """
    from goal_agent.planning.scheduler import schedule_tasks
    from goal_agent.planning.velocity import compute_velocity

    goal = repo.get_goal(goal_id)
    if goal is None:
        raise ValueError(f"Goal {goal_id} not found")
    profile = repo.get_or_create_profile(1)
    daily_hours = daily_hours_override or profile.daily_hours_available
    work_days = profile.work_days
    slippage = profile.slippage_multiplier
    blocked = repo.get_blocked_dates()
    recent = repo.list_tasks_with_actuals(5)
    velocity = compute_velocity(recent)

    decomposition = decompose_goal(goal.title, goal.description, goal.minimal_viable_outcome, llm)

    # Flatten with synthetic IDs (1-based) for the scheduler.
    synth_tasks: list[dict[str, Any]] = []
    # Map (milestone_idx, local_idx) -> synth_id; also global index -> synth_id.
    local_to_synth: dict[tuple[int, int], int] = {}
    synth_id = 1
    for mi, ms in enumerate(decomposition.milestones):
        for li, _ in enumerate(ms.tasks):
            local_to_synth[(mi, li)] = synth_id
            synth_id += 1
    # Second pass: translate deps (local indexes preferred, global fallback).
    synth_id = 1
    ms_of_synth: dict[int, int] = {}
    for mi, ms in enumerate(decomposition.milestones):
        for li, t in enumerate(ms.tasks):
            ms_of_synth[synth_id] = mi
            synth_id += 1
    synth_id = 1
    for mi, ms in enumerate(decomposition.milestones):
        for li, t in enumerate(ms.tasks):
            deps: list[int] = []
            for d in t.deps:
                if (mi, d) in local_to_synth:
                    deps.append(local_to_synth[(mi, d)])
                elif 0 <= d < sum(len(m.tasks) for m in decomposition.milestones):
                    # Treat as global 0-based index.
                    flat_order: list[int] = []
                    for mii, mm in enumerate(decomposition.milestones):
                        for lii in range(len(mm.tasks)):
                            flat_order.append(local_to_synth[(mii, lii)])
                    if 0 <= d < len(flat_order):
                        deps.append(flat_order[d])
            # Avoid self-dependency.
            deps = [x for x in deps if x != synth_id]
            synth_tasks.append({"task_id": synth_id, "est_hours": t.est_hours, "dependencies": deps})
            synth_id += 1

    scheduled = schedule_tasks(
        synth_tasks,
        start_date,
        goal.deadline,
        daily_hours,
        blocked,
        velocity,
        work_days,
        slippage,
    )

    # Persist: PlanVersion first (need version_id for milestones).
    plan = repo.create_plan_version(
        goal_id=goal_id,
        strategy=strategy,
        target_finish=scheduled.target_finish,
        buffer_days=scheduled.buffer_days,
        projected_finish=scheduled.projected_finish,
        user_velocity=velocity,
        risk=scheduled.risk,
        changelog=changelog,
    )
    due_by_synth = {a.task_id: a.due_date for a in scheduled.assignments}
    saved_milestones = []
    saved_tasks = []
    synth_to_real: dict[int, int] = {}
    # Persist in decomposition order; deps remapped to real IDs when available.
    synth_id = 1
    for mi, ms in enumerate(decomposition.milestones):
        member_synths = [local_to_synth[(mi, li)] for li in range(len(ms.tasks))]
        member_dues = [due_by_synth.get(s, scheduled.target_finish) for s in member_synths]
        ms_target = max(member_dues) if member_dues else scheduled.target_finish
        saved_ms = repo.create_milestone(plan.version_id, ms.name, ms_target)  # type: ignore[arg-type]
        saved_milestones.append(saved_ms)
        for li, t in enumerate(ms.tasks):
            s_id = local_to_synth[(mi, li)]
            # Find original synth entry deps -> map to real IDs already created.
            orig = next(s for s in synth_tasks if s["task_id"] == s_id)
            real_deps = [synth_to_real[d] for d in orig["dependencies"] if d in synth_to_real]
            saved_t = repo.create_task(
                saved_ms.milestone_id,  # type: ignore[arg-type]
                desc=t.desc,
                est_hours=t.est_hours,
                due_date=due_by_synth.get(s_id, ms_target),
                is_core=t.is_core,
                dependencies=real_deps,
            )
            if saved_t.task_id is not None:
                synth_to_real[s_id] = saved_t.task_id
            saved_tasks.append(saved_t)

    return {"plan": plan, "milestones": saved_milestones, "tasks": saved_tasks, "scheduled": scheduled}
