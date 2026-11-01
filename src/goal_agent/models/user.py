"""Pydantic + SQLAlchemy models for user profile and memory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from goal_agent.db.database import Base

NudgeStyle = Literal["firm", "encouraging", "gentle"]


# ---------------------------------------------------------------------------
# SQLAlchemy rows
# ---------------------------------------------------------------------------
class UserProfileRow(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    daily_hours_available: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    work_days: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    checkin_time: Mapped[str] = mapped_column(String(5), nullable=False, default="09:00")
    nudge_style: Mapped[str] = mapped_column(String(20), nullable=False, default="encouraging")
    slippage_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class MemoryRow(Base):
    __tablename__ = "memories"

    memory_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tag: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class UserProfile(BaseModel):
    user_id: int | None = None
    name: str = ""
    daily_hours_available: float = Field(default=2.0, gt=0, le=24)
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    checkin_time: str = "09:00"
    nudge_style: NudgeStyle = "encouraging"
    slippage_multiplier: float = Field(default=1.0, ge=1.0, le=2.0)

    @field_validator("work_days")
    @classmethod
    def _validate_work_days(cls, v: list[int]) -> list[int]:
        for d in v:
            if d not in (0, 1, 2, 3, 4, 5, 6):
                raise ValueError(f"work_day must be 0-6, got {d}")
        return sorted(set(v))

    @field_validator("checkin_time")
    @classmethod
    def _validate_checkin_time(cls, v: str) -> str:
        import re

        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("checkin_time must be HH:MM")
        hh, mm = int(v[:2]), int(v[3:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("checkin_time must be valid HH:MM")
        return v


class UserProfileCreate(BaseModel):
    name: str = ""
    daily_hours_available: float = Field(default=2.0, gt=0, le=24)
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    checkin_time: str = "09:00"
    nudge_style: NudgeStyle = "encouraging"


class Memory(BaseModel):
    memory_id: int | None = None
    text: str
    tag: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class MemoryCreate(BaseModel):
    text: str
    tag: str | None = None
