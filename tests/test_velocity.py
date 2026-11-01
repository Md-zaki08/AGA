"""Tests for learned velocity + slippage (pure, deterministic)."""

from __future__ import annotations

from goal_agent.planning.velocity import (
    compute_slippage_multiplier,
    compute_velocity,
    slippage_after_completion,
)


def test_velocity_default_when_no_actuals():
    assert compute_velocity([]) == 1.0
    assert compute_velocity([{"est_hours": 2.0, "actual_hours": None}]) == 1.0


def test_velocity_mean_ratio():
    tasks = [
        {"est_hours": 2.0, "actual_hours": 2.0},  # 1.0
        {"est_hours": 2.0, "actual_hours": 3.0},  # 1.5
        {"est_hours": 4.0, "actual_hours": 6.0},  # 1.5
    ]
    v = compute_velocity(tasks)
    assert abs(v - (1.0 + 1.5 + 1.5) / 3) < 1e-9


def test_velocity_last_five_window():
    tasks = [{"est_hours": 1.0, "actual_hours": 1.0} for _ in range(5)]
    tasks.append({"est_hours": 1.0, "actual_hours": 3.0})
    tasks.append({"est_hours": 1.0, "actual_hours": 3.0})
    # ratios = [1,1,1,1,1,3,3] -> last 5 = [1,1,1,3,3] -> mean 1.8
    assert abs(compute_velocity(tasks) - 1.8) < 1e-9


def test_velocity_ignores_zero_est():
    tasks = [
        {"est_hours": 0.0, "actual_hours": 5.0},
        {"est_hours": 2.0, "actual_hours": 4.0},
    ]
    assert compute_velocity(tasks) == 2.0


def test_slippage_multiplier_counts_late_plans():
    assert compute_slippage_multiplier(0) == 1.0
    assert abs(compute_slippage_multiplier(1) - 1.15) < 1e-9
    assert abs(compute_slippage_multiplier(2) - 1.30) < 1e-9
    assert compute_slippage_multiplier(100) == 2.0  # capped


def test_slippage_after_completion_increments_once():
    assert slippage_after_completion(1.0, False) == 1.0
    assert abs(slippage_after_completion(1.0, True) - 1.15) < 1e-9
    assert slippage_after_completion(2.0, True) == 2.0  # capped
    assert slippage_after_completion(1.9, True) == 2.0
