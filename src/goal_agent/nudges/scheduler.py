"""APScheduler daily check-in nudges (alongside the REPL)."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable


def _style_prefix(style: str) -> str:
    return {"firm": "No excuses", "encouraging": "You've got this", "gentle": "Whenever you're ready"}.get(style, "Quick check-in")


def compose_nudge(profile: Any, pending_count: int) -> str:
    """Compose a short nudge in the user's nudge_style."""
    prefix = _style_prefix(getattr(profile, "nudge_style", "encouraging"))
    name = getattr(profile, "name", "") or "there"
    return f"{prefix}, {name}: {pending_count} task(s) pending today — what's the one next step?"


def start_nudger(repo: Any) -> Callable[[], None]:
    """Start APScheduler thread firing at checkin_time. Returns stop function."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        def _noop() -> None:
            return None

        return _noop

    from goal_agent.config import get_settings

    settings = get_settings()
    try:
        profile = repo.get_or_create_profile(1)
        hh, mm = (profile.checkin_time or settings.checkin_time or "09:00").split(":")
        hour, minute = int(hh), int(mm)
    except Exception:
        hour, minute = 9, 0

    sched = BackgroundScheduler()

    def _job() -> None:
        try:
            from rich.console import Console

            today = date.today()
            nudged = 0
            for g in repo.list_active_goals():
                if repo.has_checkin_on(g.goal_id, today):  # type: ignore[arg-type]
                    continue
                plan = repo.get_active_plan(g.goal_id)  # type: ignore[arg-type]
                if plan is None:
                    continue
                tasks = repo.list_tasks_for_plan(plan.version_id)
                pending = [t for t in tasks if t.status in ("pending", "in_progress")]
                if pending:
                    profile_now = repo.get_or_create_profile(1)
                    Console().print(f"\n[bold yellow]nudge>[/bold yellow] {compose_nudge(profile_now, len(pending))} [dim]({g.title})[/dim]")
                    nudged += 1
        except Exception:
            pass

    try:
        sched.add_job(_job, "cron", hour=hour, minute=minute, id="aga-checkin")
        sched.start()
    except Exception:
        pass

    def _stop() -> None:
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass

    return _stop
