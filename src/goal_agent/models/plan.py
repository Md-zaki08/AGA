"""Pydantic + SQLAlchemy models for plans, milestones, tasks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from goal_agent.db.database import Base

RiskLevel = Literal["low", "medium", "high"]
TaskStatus = Literal["pending", "in_progress", "done", "skipped"]


class PlanVersionRow(Base):
    __tablename__ = "plan_versions"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.goal_id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    strategy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_finish: Mapped[date] = mapped_column(Date, nullable=False)
    buffer_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    projected_finish: Mapped[date] = mapped_column(Date, nullable=False)
    user_velocity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    risk: Mapped[str] = mapped_column(String(10), nullable=False, default="low")
    changelog: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class MilestoneRow(Base):
    __tablename__ = "milestones"

    milestone_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_version_id: Mapped[int] = mapped_column(
        ForeignKey("plan_versions.version_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)


class TaskRow(Base):
    __tablename__ = "tasks"

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("milestones.milestone_id"), nullable=False, index=True
    )
    desc: Mapped[str] = mapped_column(Text, nullable=False)
    est_hours: Mapped[float] = mapped_column(Float, nullable=False)
    actual_hours: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    is_core: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dependencies: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class PlanVersion(BaseModel):
    version_id: int | None = None
    goal_id: int
    version_number: int
    is_active: bool = True
    strategy: str = ""
    target_finish: date
    buffer_days: int = 3
    projected_finish: date
    user_velocity: float = 1.0
    risk: RiskLevel = "low"
    changelog: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Milestone(BaseModel):
    milestone_id: int | None = None
    plan_version_id: int | None = None
    name: str
    target_date: date


class Task(BaseModel):
    task_id: int | None = None
    milestone_id: int | None = None
    desc: str
    est_hours: float = Field(gt=0)
    actual_hours: float | None = None
    due_date: date | None = None
    status: TaskStatus = "pending"
    is_core: bool = True
    dependencies: list[int] = Field(default_factory=list)
