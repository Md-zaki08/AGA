"""Pydantic + SQLAlchemy models for check-ins."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import Date, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from goal_agent.db.database import Base


class CheckInRow(Base):
    __tablename__ = "checkins"

    checkin_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.goal_id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    completed_task_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    hours_spent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CheckIn(BaseModel):
    checkin_id: int | None = None
    goal_id: int
    date: date
    completed_task_ids: list[int] = Field(default_factory=list)
    hours_spent: float = 0.0
    notes: str = ""


class CheckInCreate(BaseModel):
    goal_id: int
    date: date
    completed_task_ids: list[int] = Field(default_factory=list)
    hours_spent: float = 0.0
    notes: str = ""
