"""Tool-calling agent REPL loop."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from rich.console import Console

from goal_agent.llm.prompts import AGENT_SYSTEM_PROMPT
from goal_agent.llm.tools import get_tool_schemas

console = Console()


def _parse_date(s: str) -> date:
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _today() -> date:
    return date.today()


class AgentLoop:
    """REPL: user input -> messages + system + tools -> LLM -> exec tools -> repeat."""

    def __init__(self, repo: Any, llm: Any, today_fn=_today) -> None:
        self._repo = repo
        self._llm = llm
        self._today_fn = today_fn
        self._messages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Message building (memory injection)
    # ------------------------------------------------------------------
    def _system_content(self) -> str:
        parts = [AGENT_SYSTEM_PROMPT]
        try:
            profile = self._repo.get_or_create_profile(1)
            parts.append(
                f"[profile] name={profile.name} daily_hours={profile.daily_hours_available} "
                f"work_days={profile.work_days} checkin={profile.checkin_time} "
                f"style={profile.nudge_style} velocity_slippage={profile.slippage_multiplier}"
            )
        except Exception:
            pass
        try:
            mems = self._repo.list_memories()[-10:]
            if mems:
                facts = "; ".join(m.text for m in mems)
                parts.append(f"[memory] {facts}")
        except Exception:
            pass
        try:
            goals = self._repo.list_active_goals()
            if goals:
                summaries = []
                for g in goals:
                    plan = self._repo.get_active_plan(g.goal_id)  # type: ignore[arg-type]
                    if plan:
                        summaries.append(
                            f"{g.title} (id {g.goal_id}): target {plan.target_finish} "
                            f"vs deadline {g.deadline}, risk {plan.risk}, status {g.status}"
                        )
                    else:
                        summaries.append(f"{g.title} (id {g.goal_id}): no plan yet, deadline {g.deadline}")
                parts.append("[standing] " + " | ".join(summaries))
        except Exception:
            pass
        return "\n".join(parts)

    def _complete_with_retry(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            return self._llm.complete(messages, tools)
        except Exception:
            try:
                return self._llm.complete(messages, tools)
            except Exception as e:
                return {"text": f"Sorry — the model is unreachable ({e}). Your goals are safe; try again in a bit.", "tool_calls": [], "_offline": True}

    # ------------------------------------------------------------------
    # Single turn (testable)
    # ------------------------------------------------------------------
    def run_once(self, user_input: str) -> str:
        """Process one user message, executing tool calls until final text."""
        self._messages.append({"role": "user", "content": user_input})
        tools = get_tool_schemas()
        for _ in range(6):  # cap tool-call rounds
            messages = [{"role": "system", "content": self._system_content()}] + self._messages
            resp = self._complete_with_retry(messages, tools)
            if resp.get("_offline"):
                self._messages.append({"role": "assistant", "content": resp["text"]})
                return resp["text"]
            tool_calls = resp.get("tool_calls") or []
            text = resp.get("text") or ""
            if not tool_calls:
                final = text or "(no reply)"
                self._messages.append({"role": "assistant", "content": final})
                return final
            # Execute tools, feed results back.
            self._messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                try:
                    result = self.handle_tool(name, args)
                except Exception as e:
                    result = f"ERROR: {e}"
                self._messages.append({"role": "tool", "content": str(result), "tool_name": name})
        fallback = "Done — let me know your next step."
        self._messages.append({"role": "assistant", "content": fallback})
        return fallback

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------
    def handle_tool(self, name: str, args: dict[str, Any]) -> str:
        handler = {
            "create_goal": self._t_create_goal,
            "generate_plan": self._t_generate_plan,
            "revise_plan": self._t_revise_plan,
            "log_checkin": self._t_log_checkin,
            "add_interruption": self._t_add_interruption,
            "change_deadline": self._t_change_deadline,
            "list_goals": self._t_list_goals,
            "get_plan": self._t_get_plan,
            "remember": self._t_remember,
            "recall": self._t_recall,
            "complete_goal": self._t_complete_goal,
            "negotiate_goal": self._t_negotiate_goal,
        }.get(name)
        if handler is None:
            return f"ERROR: unknown tool '{name}'"
        try:
            return handler(args or {})
        except (ValueError, KeyError, TypeError) as e:
            return f"ERROR: invalid args for {name}: {e}"

    # -- tools ----------------------------------------------------------
    def _t_create_goal(self, a: dict[str, Any]) -> str:
        title = str(a["title"])
        description = str(a.get("description", ""))
        deadline = _parse_date(a["deadline"])
        priority = int(a.get("priority", 3))
        mvo = str(a.get("minimal_viable_outcome", a.get("minimalViableOutcome", "")))
        if not (1 <= priority <= 5):
            raise ValueError("priority must be 1-5")
        if not mvo:
            raise ValueError("minimal_viable_outcome is required")
        g = self._repo.create_goal(title, description, deadline, priority, mvo)
        self._repo.create_event("new_goal", f"Created goal '{title}' (id {g.goal_id})")
        return json.dumps({"goal_id": g.goal_id, "title": g.title, "deadline": str(g.deadline)})

    def _t_generate_plan(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.decomposer import build_and_save_plan

        goal_id = int(a["goal_id"])
        out = build_and_save_plan(self._repo, goal_id, self._llm, self._today_fn(), changelog="Initial plan")
        plan = out["plan"]
        return json.dumps(
            {
                "version": plan.version_number,
                "target_finish": str(plan.target_finish),
                "deadline": str(self._repo.get_goal(goal_id).deadline),  # type: ignore[union-attr]
                "buffer_days": plan.buffer_days,
                "projected_finish": str(plan.projected_finish),
                "risk": plan.risk,
                "tasks": len(out["tasks"]),
            }
        )

    def _t_revise_plan(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.decomposer import build_and_save_plan

        goal_id = int(a["goal_id"])
        reason = str(a.get("reason", "Manual revision"))
        out = build_and_save_plan(
            self._repo, goal_id, self._llm, self._today_fn(), strategy="revision", changelog=reason
        )
        plan = out["plan"]
        return json.dumps({"version": plan.version_number, "target_finish": str(plan.target_finish), "risk": plan.risk, "changelog": reason})

    def _t_log_checkin(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.scheduler import project_finish

        goal_id = int(a["goal_id"])
        completed = [int(x) for x in (a.get("completed_task_ids", []) or [])]
        hours = float(a.get("hours_spent", 0.0))
        notes = str(a.get("notes", ""))
        today = self._today_fn()
        checkin = self._repo.create_checkin(goal_id, today, completed, hours, notes)
        # Distribute hours across completed tasks for velocity tracking.
        if completed:
            tasks = [self._repo.get_task(t) for t in completed]
            valid = [t for t in tasks if t is not None]
            total_est = sum(t.est_hours for t in valid) or 1.0
            for t in valid:
                share = hours * (t.est_hours / total_est) if hours else t.est_hours
                self._repo.update_task(t.task_id, status="done", actual_hours=round(share, 2))  # type: ignore[arg-type]
        # Auto-remember one line per check-in.
        try:
            line = f"{today.isoformat()}: goal {goal_id} done {completed}, {hours}h, {notes[:80]}"
            self._repo.create_memory(line, tag="checkin")
        except Exception:
            pass
        # Standing vs plan.
        standing = ""
        try:
            plan = self._repo.get_active_plan(goal_id)
            tasks_all = self._repo.list_tasks_for_plan(plan.version_id) if plan else []  # type: ignore[union-attr]
            done_hours = sum(t.est_hours for t in tasks_all if t.status == "done")
            total_hours = sum(t.est_hours for t in tasks_all) or (done_hours + 1.0)
            profile = self._repo.get_or_create_profile(1)
            recent = self._repo.list_tasks_with_actuals(5)
            from goal_agent.planning.velocity import compute_velocity

            vel = compute_velocity(recent)
            proj = project_finish(
                total_hours, done_hours, vel, today,
                daily_hours=profile.daily_hours_available,
                work_days=profile.work_days,
                blocked_dates=self._repo.get_blocked_dates(),
            )
            if plan:
                drift = (proj - plan.target_finish).days
                standing = f" Projected {proj} vs target {plan.target_finish} (drift {drift}d)."
                # Lightweight trigger hook (full replanner in step 5 wires deeper actions).
                if drift > 0:
                    try:
                        from goal_agent.planning import replanner as _rp

                        if hasattr(_rp, "handle_drift"):
                            standing += " " + str(_rp.handle_drift(self._repo, goal_id, drift, today))
                    except Exception:
                        pass
        except Exception:
            pass
        return json.dumps({"checkin_id": checkin.checkin_id, "date": str(checkin.date)}) + standing

    def _t_add_interruption(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.decomposer import build_and_save_plan

        desc = str(a.get("description", "interruption"))
        start = _parse_date(a["start_date"])
        end = _parse_date(a["end_date"])
        if end < start:
            raise ValueError("end_date before start_date")
        self._repo.create_event("interruption", desc, start, end)
        replanned = []
        for g in self._repo.list_active_goals():
            try:
                out = build_and_save_plan(
                    self._repo, g.goal_id, self._llm, self._today_fn(),  # type: ignore[arg-type]
                    strategy="interruption", changelog=f"Replanned around interruption: {desc} {start}..{end}",
                )
                replanned.append({"goal_id": g.goal_id, "target_finish": str(out["plan"].target_finish)})
            except Exception as e:
                replanned.append({"goal_id": g.goal_id, "error": str(e)})
        return json.dumps({"blocked": f"{start}..{end}", "replanned": replanned, "note": "Zero guilt — just replanned around it."})

    def _t_change_deadline(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.decomposer import build_and_save_plan

        goal_id = int(a["goal_id"])
        new_dl = _parse_date(a["new_deadline"])
        g = self._repo.update_goal_deadline(goal_id, new_dl)
        if g is None:
            raise ValueError(f"goal {goal_id} not found")
        self._repo.create_event("deadline_change", f"Goal {goal_id} deadline -> {new_dl}")
        out = build_and_save_plan(self._repo, goal_id, self._llm, self._today_fn(), strategy="deadline_change", changelog=f"Deadline changed to {new_dl}")
        return json.dumps({"goal_id": goal_id, "new_deadline": str(new_dl), "target_finish": str(out["plan"].target_finish)})

    def _t_list_goals(self, a: dict[str, Any]) -> str:
        goals = self._repo.list_goals()
        return json.dumps([{"goal_id": g.goal_id, "title": g.title, "deadline": str(g.deadline), "status": g.status, "priority": g.priority} for g in goals])

    def _t_get_plan(self, a: dict[str, Any]) -> str:
        goal_id = int(a["goal_id"])
        plan = self._repo.get_active_plan(goal_id)
        if plan is None:
            return json.dumps({"goal_id": goal_id, "plan": None})
        tasks = self._repo.list_tasks_for_plan(plan.version_id)  # type: ignore[arg-type]
        goal = self._repo.get_goal(goal_id)
        return json.dumps(
            {
                "version": plan.version_number,
                "target_finish": str(plan.target_finish),
                "deadline": str(goal.deadline) if goal else "?",
                "buffer_days": plan.buffer_days,
                "projected_finish": str(plan.projected_finish),
                "risk": plan.risk,
                "changelog": plan.changelog,
                "tasks": [{"task_id": t.task_id, "desc": t.desc, "est": t.est_hours, "due": str(t.due_date), "status": t.status, "core": t.is_core} for t in tasks],
            }
        )

    def _t_remember(self, a: dict[str, Any]) -> str:
        text = str(a["text"])
        tag = a.get("tag")
        m = self._repo.create_memory(text, str(tag) if tag else None)
        return json.dumps({"memory_id": m.memory_id})

    def _t_recall(self, a: dict[str, Any]) -> str:
        q = str(a.get("query", ""))
        mems = self._repo.search_memories(q)
        return json.dumps([{"text": m.text, "tag": m.tag} for m in mems[:10]])

    def _t_complete_goal(self, a: dict[str, Any]) -> str:
        from goal_agent.planning.velocity import slippage_after_completion

        goal_id = int(a["goal_id"])
        plan = self._repo.get_active_plan(goal_id)
        today = self._today_fn()
        late = bool(plan and today > plan.target_finish)
        g = self._repo.update_goal_status(goal_id, "completed")
        profile = self._repo.get_or_create_profile(1)
        new_slip = slippage_after_completion(profile.slippage_multiplier, late)
        self._repo.update_slippage(1, new_slip)
        self._repo.create_event("progress", f"Goal {goal_id} completed {'late' if late else 'early/on-time'}")
        return json.dumps({"goal_id": goal_id, "status": g.status if g else "completed", "celebrate": True, "next": "Offer next goal or rest."})

    def _t_negotiate_goal(self, a: dict[str, Any]) -> str:
        goal_id = int(a["goal_id"])
        decision = str(a.get("decision", ""))
        self._repo.create_event("negotiation", f"Goal {goal_id}: {decision}")
        low = decision.lower()
        if low.startswith("extend deadline:"):
            try:
                new_dl = _parse_date(decision.split(":", 1)[1].strip())
                self._repo.update_goal_deadline(goal_id, new_dl)
                self._repo.update_goal_status(goal_id, "active")
                from goal_agent.planning.decomposer import build_and_save_plan

                out = build_and_save_plan(self._repo, goal_id, self._llm, self._today_fn(), strategy="negotiated-extension", changelog=f"Negotiated: extend deadline to {new_dl}")
                return json.dumps({"goal_id": goal_id, "decision": "extended", "new_deadline": str(new_dl), "target_finish": str(out["plan"].target_finish)})
            except Exception as e:
                return f"ERROR: bad extend date: {e}"
        if "reduced scope" in low or "accept" in low:
            self._repo.update_goal_status(goal_id, "active")
            return json.dumps({"goal_id": goal_id, "decision": "reduced-scope-accepted"})
        self._repo.update_goal_status(goal_id, "negotiating")
        return json.dumps({"goal_id": goal_id, "decision": "recorded", "status": "negotiating"})

    # ------------------------------------------------------------------
    # Interactive REPL
    # ------------------------------------------------------------------
    def run_repl(self) -> None:
        try:
            from goal_agent.nudges.scheduler import start_nudger
        except Exception:
            start_nudger = None  # type: ignore[assignment]

        stopper = None
        if start_nudger is not None:
            try:
                stopper = start_nudger(self._repo)
            except Exception as e:
                console.print(f"[dim]Nudger unavailable: {e}[/dim]")
        console.print("[bold green]AGA — Adaptive Goal Agent[/bold green] (type /quit to exit)")
        while True:
            try:
                user = console.input("[bold cyan]you>[/bold cyan] ")
            except (EOFError, KeyboardInterrupt):
                break
            if user.strip() in ("/quit", "/exit", "quit", "exit"):
                break
            if not user.strip():
                continue
            reply = self.run_once(user)
            console.print(f"[bold magenta]aga>[/bold magenta] {reply}")
        if stopper is not None:
            try:
                stopper()
            except Exception:
                pass
