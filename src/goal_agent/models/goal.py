"""Pydantic + SQLAlchemy models for goals."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from goal_agent.db.database import Base

GoalStatus = Literal["active", "paused", "completed", "at_risk", "negotiating"]


class GoalRow(Base):
    __tablename__ = "goals"

    goal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    minimal_viable_outcome: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Goal(BaseModel):
    goal_id: int | None = None
    title: str
    description: str = ""
    deadline: date
    priority: int = Field(default=3, ge=1, le=5)
    minimal_viable_outcome: str = ""
    status: GoalStatus = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class GoalCreate(BaseModel):
    title: str
    description: str = ""
    deadline: date
    priority: int = Field(default=3, ge=1, le=5)
    minimal_viable_outcome: str = ""
