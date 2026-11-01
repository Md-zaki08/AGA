"""AGA CLI: aga chat | aga checkin | aga plan show | aga plan history | aga goals."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.table import Table

console = Console()


def _bootstrap():
    from goal_agent.config import get_settings, resolve_db_path
    from goal_agent.db.database import create_db_engine, create_session_factory, init_db
    from goal_agent.db.repository import Repository

    settings = get_settings()
    db_path = resolve_db_path(settings.db_path)
    engine = create_db_engine(db_path)
    init_db(engine)
    repo = Repository(create_session_factory(engine))
    return settings, repo


def _ensure_onboarded(repo) -> None:
    profile = repo.get_or_create_profile(1)
    if profile.name:
        return
    console.print("[bold green]Welcome to AGA — let's set you up.[/bold green]")
    try:
        name = console.input("Your name? ").strip() or "there"
        daily = console.input("Daily hours available? [2.0] ").strip() or "2.0"
        work = console.input("Work days 0=Mon..6=Sun, comma-separated? [0,1,2,3,4] ").strip() or "0,1,2,3,4"
        checkin = console.input("Daily check-in time HH:MM? [09:00] ").strip() or "09:00"
        style = console.input("Nudge style firm|encouraging|gentle? [encouraging] ").strip() or "encouraging"
        profile.name = name
        profile.daily_hours_available = float(daily)
        profile.work_days = [int(x.strip()) for x in work.split(",") if x.strip() != ""]
        profile.checkin_time = checkin
        if style in ("firm", "encouraging", "gentle"):
            profile.nudge_style = style  # type: ignore[assignment]
        repo.save_profile(profile)
        console.print(f"[green]All set, {name}. Type 'aga chat' to start.[/green]")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Onboarding skipped — defaults apply.[/dim]")
    except Exception as e:
        console.print(f"[red]Onboarding used defaults ({e})[/red]")


def cmd_chat(_args: argparse.Namespace) -> int:
    _settings, repo = _bootstrap()
    _ensure_onboarded(repo)
    from goal_agent.agent.loop import AgentLoop
    from goal_agent.llm.client import create_client

    try:
        llm = create_client()
    except Exception as e:
        console.print(f"[red]LLM setup failed: {e}[/red]")
        return 1
    AgentLoop(repo, llm).run_repl()
    return 0


def cmd_checkin(args: argparse.Namespace) -> int:
    _settings, repo = _bootstrap()
    _ensure_onboarded(repo)
    from datetime import date

    from goal_agent.agent.loop import AgentLoop
    from goal_agent.llm.client import FakeLLM

    # Non-interactive quick check-in: reuse tool handler with a no-LLM loop.
    loop = AgentLoop(repo, FakeLLM())
    goal_id = int(args.goal)
    completed = [int(x) for x in (args.done or "").split(",") if x.strip()] if args.done else []
    result = loop.handle_tool(
        "log_checkin",
        {"goal_id": goal_id, "completed_task_ids": completed, "hours_spent": float(args.hours or 0), "notes": args.notes or ""},
    )
    console.print(result)
    _ = date.today()
    return 0


def cmd_plan_show(args: argparse.Namespace) -> int:
    _settings, repo = _bootstrap()
    goal_id = int(args.goal) if getattr(args, "goal", None) else None
    if goal_id is None:
        goals = repo.list_active_goals()
        if not goals:
            console.print("[dim]No active goals.[/dim]")
            return 0
        goal_id = goals[0].goal_id  # type: ignore[union-attr]
    goal = repo.get_goal(goal_id)
    plan = repo.get_active_plan(goal_id)
    if goal is None or plan is None:
        console.print(f"[red]No active plan for goal {goal_id}.[/red]")
        return 1
    tasks = repo.list_tasks_for_plan(plan.version_id)  # type: ignore[arg-type]
    table = Table(title=f"Plan v{plan.version_number} — {goal.title}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("version", str(plan.version_number))
    table.add_row("target_finish vs deadline", f"{plan.target_finish} vs {goal.deadline}")
    table.add_row("buffer", f"{plan.buffer_days}d")
    table.add_row("projected_finish", str(plan.projected_finish))
    table.add_row("risk", plan.risk)
    table.add_row("changelog", plan.changelog or "-")
    table.add_row("strategy", plan.strategy or "-")
    console.print(table)
    t2 = Table(title="Tasks")
    t2.add_column("id")
    t2.add_column("desc")
    t2.add_column("est")
    t2.add_column("due")
    t2.add_column("status")
    t2.add_column("core")
    for t in tasks:
        t2.add_row(str(t.task_id), t.desc[:60], str(t.est_hours), str(t.due_date), t.status, "✓" if t.is_core else "·")
    console.print(t2)
    return 0


def cmd_plan_history(args: argparse.Namespace) -> int:
    _settings, repo = _bootstrap()
    goal_id = int(args.goal) if getattr(args, "goal", None) else None
    if goal_id is None:
        goals = repo.list_active_goals() or repo.list_goals()
        if not goals:
            console.print("[dim]No goals.[/dim]")
            return 0
        goal_id = goals[0].goal_id  # type: ignore[union-attr]
    history = repo.list_plan_history(goal_id)
    table = Table(title=f"Plan history — goal {goal_id}")
    table.add_column("v")
    table.add_column("active")
    table.add_column("target")
    table.add_column("projected")
    table.add_column("risk")
    table.add_column("changelog")
    for p in history:
        table.add_row(str(p.version_number), "●" if p.is_active else "", str(p.target_finish), str(p.projected_finish), p.risk, (p.changelog or "")[:80])
    console.print(table)
    return 0


def cmd_goals(_args: argparse.Namespace) -> int:
    _settings, repo = _bootstrap()
    goals = repo.list_goals()
    table = Table(title="Goals")
    table.add_column("id")
    table.add_column("title")
    table.add_column("deadline")
    table.add_column("pri")
    table.add_column("status")
    for g in goals:
        table.add_row(str(g.goal_id), (g.title or "")[:50], str(g.deadline), str(g.priority), g.status)
    console.print(table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aga", description="AGA — Adaptive Goal Agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chat", help="Start the agent REPL")
    c.set_defaults(fn=cmd_chat)
    k = sub.add_parser("checkin", help="Log a quick check-in without the REPL")
    k.add_argument("--goal", required=True, help="Goal ID")
    k.add_argument("--done", default="", help="Comma-separated completed task IDs")
    k.add_argument("--hours", default="0", help="Hours spent")
    k.add_argument("--notes", default="", help="Notes")
    k.set_defaults(fn=cmd_checkin)
    s = sub.add_parser("plan", help="Inspect plans")
    ssub = s.add_subparsers(dest="plan_cmd", required=True)
    sh = ssub.add_parser("show", help="Show active plan")
    sh.add_argument("--goal", default=None, help="Goal ID (default: first active)")
    sh.set_defaults(fn=cmd_plan_show)
    hi = ssub.add_parser("history", help="Show all versions with changelogs")
    hi.add_argument("--goal", default=None, help="Goal ID (default: first active)")
    hi.set_defaults(fn=cmd_plan_history)
    g = sub.add_parser("goals", help="List goals")
    g.set_defaults(fn=cmd_goals)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(args.fn(args))
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
