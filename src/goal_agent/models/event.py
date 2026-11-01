"""Pydantic + SQLAlchemy models for events."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from goal_agent.db.database import Base

EventType = Literal["interruption", "new_goal", "deadline_change", "progress", "negotiation"]


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Event(BaseModel):
    event_id: int | None = None
    type: EventType
    description: str = ""
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class EventCreate(BaseModel):
    type: EventType
    description: str = ""
    start_date: date | None = None
    end_date: date | None = None
