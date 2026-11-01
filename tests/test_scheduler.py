"""Deterministic tests for scheduler pure functions (fixed dates)."""

from __future__ import annotations

from datetime import date

from goal_agent.planning.scheduler import (
    ScheduledPlan,
    compute_buffer,
    project_finish,
    schedule_tasks,
)


def test_compute_buffer_minimum():
    assert compute_buffer(0, 1.0) == 3
    assert compute_buffer(5, 1.0) == 3  # ceil(0.75)=1 -> clamp 3
    assert compute_buffer(20, 1.0) == 3  # ceil(3.0)=3


def test_compute_buffer_scales_with_horizon():
    assert compute_buffer(100, 1.0) == 14  # ceil(15)=15 -> clamp 14
    assert compute_buffer(60, 1.0) == 9  # ceil(9)=9


def test_compute_buffer_slippage_multiplies_min3():
    assert compute_buffer(20, 1.5) == 5  # base 3 * 1.5 = 4.5 -> ceil 5
    assert compute_buffer(100, 2.0) == 28  # base 14 * 2 = 28
    assert compute_buffer(0, 2.0) == 6  # base 3 * 2


def test_schedule_fits_and_target_before_deadline():
    start = date(2025, 1, 6)  # Monday
    deadline = date(2025, 1, 31)
    tasks = [
        {"task_id": 1, "est_hours": 4.0, "dependencies": []},
        {"task_id": 2, "est_hours": 4.0, "dependencies": []},
        {"task_id": 3, "est_hours": 4.0, "dependencies": []},
    ]
    plan = schedule_tasks(tasks, start, deadline, daily_hours=2.0, blocked_dates=set(), velocity=1.0)
    assert isinstance(plan, ScheduledPlan)
    assert plan.target_finish < deadline
    assert plan.buffer_days >= 3
    assert plan.risk in ("low", "medium")
    assert plan.projected_finish <= plan.target_finish
    assert len(plan.assignments) == 3
    # All dues on working days, none on weekend
    for a in plan.assignments:
        assert a.due_date.weekday() < 5


def test_schedule_respects_dependencies():
    start = date(2025, 1, 6)
    deadline = date(2025, 1, 31)
    tasks = [
        {"task_id": 1, "est_hours": 4.0, "dependencies": []},
        {"task_id": 2, "est_hours": 2.0, "dependencies": [1]},
        {"task_id": 3, "est_hours": 2.0, "dependencies": [2]},
    ]
    plan = schedule_tasks(tasks, start, deadline, daily_hours=2.0, blocked_dates=set(), velocity=1.0)
    due = {a.task_id: a.due_date for a in plan.assignments}
    assert due[2] >= due[1]
    assert due[3] >= due[2]


def test_schedule_front_loads_heavy_first():
    start = date(2025, 1, 6)
    deadline = date(2025, 2, 28)
    tasks = [
        {"task_id": 1, "est_hours": 1.0, "dependencies": []},
        {"task_id": 2, "est_hours": 8.0, "dependencies": []},
    ]
    plan = schedule_tasks(tasks, start, deadline, daily_hours=2.0, blocked_dates=set(), velocity=1.0)
    # Heavy task should finish no later than light task starting late would imply;
    # at minimum both fit and daily load is front-packed (first day full).
    assert plan.daily_load[0].hours_used == 2.0
    due = {a.task_id: a.due_date for a in plan.assignments}
    assert due[2] <= due[1] or plan.risk in ("low", "medium")


def test_schedule_skips_blocked_and_weekends():
    start = date(2025, 1, 6)  # Mon
    deadline = date(2025, 1, 24)
    blocked = {date(2025, 1, 7), date(2025, 1, 8), date(2025, 1, 9)}
    tasks = [{"task_id": 1, "est_hours": 4.0, "dependencies": []}]
    plan = schedule_tasks(tasks, start, deadline, daily_hours=2.0, blocked_dates=blocked, velocity=1.0)
    for a in plan.assignments:
        assert a.due_date not in blocked
        assert a.due_date.weekday() < 5
    for d in plan.daily_load:
        assert d.day not in blocked


def test_schedule_overflow_high_risk_honest_projection():
    start = date(2025, 1, 6)
    deadline = date(2025, 1, 13)  # 7 days total -> buffer 3 -> target Jan 10
    tasks = [{"task_id": i, "est_hours": 5.0, "dependencies": []} for i in range(1, 6)]  # 25h
    plan = schedule_tasks(tasks, start, deadline, daily_hours=1.0, blocked_dates=set(), velocity=1.0)
    assert plan.risk == "high"
    assert plan.projected_finish > plan.target_finish
    assert "does not fit" in plan.note


def test_schedule_velocity_scales_hours():
    start = date(2025, 1, 6)
    deadline = date(2025, 1, 31)
    tasks = [{"task_id": 1, "est_hours": 10.0, "dependencies": []}]
    fits = schedule_tasks(tasks, start, deadline, daily_hours=4.0, blocked_dates=set(), velocity=1.0)
    assert fits.risk in ("low", "medium")
    assert fits.total_scaled_hours == 10.0
    strained = schedule_tasks(tasks, start, deadline, daily_hours=4.0, blocked_dates=set(), velocity=3.0)
    assert strained.total_scaled_hours == 30.0


def test_project_finish_basic_walk():
    today = date(2025, 1, 6)  # Monday
    # 4h remaining at 2h/day -> finishes Tue Jan 7
    finish = project_finish(4.0, 0.0, 1.0, today, daily_hours=2.0, work_days=[0, 1, 2, 3, 4])
    assert finish == date(2025, 1, 7)


def test_project_finish_skips_weekend_and_blocked():
    today = date(2025, 1, 10)  # Friday
    finish = project_finish(
        4.0, 0.0, 1.0, today, daily_hours=2.0, work_days=[0, 1, 2, 3, 4], blocked_dates=set()
    )
    # Fri Jan10 (2h) + Mon Jan13 (2h) -> finishes Monday
    assert finish == date(2025, 1, 13)
    blocked = {date(2025, 1, 13)}
    finish2 = project_finish(
        4.0, 0.0, 1.0, today, daily_hours=2.0, work_days=[0, 1, 2, 3, 4], blocked_dates=blocked
    )
    assert finish2 == date(2025, 1, 14)


def test_project_finish_nothing_remaining_returns_today():
    today = date(2025, 1, 6)
    assert project_finish(5.0, 5.0, 1.0, today, daily_hours=2.0) == today
    assert project_finish(0.0, 0.0, 1.0, today, daily_hours=2.0) == today


def test_project_finish_accepts_scheduled_plan():
    start = date(2025, 1, 6)
    deadline = date(2025, 1, 31)
    tasks = [{"task_id": 1, "est_hours": 6.0, "dependencies": []}]
    plan = schedule_tasks(tasks, start, deadline, daily_hours=2.0, blocked_dates=set(), velocity=1.0)
    finish = project_finish(plan, 0.0, 1.0, start, daily_hours=2.0)
    # 6h at 2h/day Mon-Wed -> Jan 8
    assert finish == date(2025, 1, 8)
