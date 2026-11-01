"""Learned velocity + slippage calculations (pure functions)."""

from __future__ import annotations

from statistics import mean


def _ratios_from_tasks(tasks: list[object]) -> list[float]:
    ratios: list[float] = []
    for t in tasks:
        if isinstance(t, dict):
            est = t.get("est_hours")
            act = t.get("actual_hours")
        else:
            est = getattr(t, "est_hours", None)
            act = getattr(t, "actual_hours", None)
        if est is None or act is None:
            continue
        try:
            est_f = float(est)
            act_f = float(act)
        except (TypeError, ValueError):
            continue
        if est_f <= 0:
            continue
        ratios.append(act_f / est_f)
    return ratios


def compute_velocity(tasks: list[object]) -> float:
    """Mean(actual/est) for last 5 tasks with actuals; default 1.0.

    Callers should pass tasks newest-first or oldest-first — we simply take
    the last 5 entries of the provided list that have actuals. Repository
    helper `list_tasks_with_actuals` already returns the 5 most recent.
    """
    ratios = _ratios_from_tasks(tasks)
    if not ratios:
        return 1.0
    window = ratios[-5:] if len(ratios) > 5 else ratios
    avg = mean(window)
    # Guard against degenerate values; keep within a sane band.
    if avg <= 0:
        return 1.0
    return max(0.25, min(4.0, float(avg)))


def compute_slippage_multiplier(num_late_plans: int) -> float:
    """1.0 + 0.15 per late plan, capped at 2.0."""
    late = max(0, int(num_late_plans))
    return min(2.0, 1.0 + 0.15 * late)


def slippage_after_completion(current_slippage: float, finished_after_target: bool) -> float:
    """Update slippage after a goal completes. Starts at 1.0, +0.15 per late finish."""
    cur = max(1.0, min(2.0, float(current_slippage)))
    if finished_after_target:
        cur = min(2.0, cur + 0.15)
    return cur
