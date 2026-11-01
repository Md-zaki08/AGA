"""SQLAlchemy engine / session / Base."""

from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session


class Base(DeclarativeBase):
    pass


def create_db_engine(db_path: str | Path):
    """Create a sync SQLite engine, ensuring parent directory exists."""
    p = Path(str(db_path)).expanduser()
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    # SQLite needs absolute path for reliability
    url = f"sqlite:///{p.resolve() if p.is_absolute() else p.absolute()}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    return engine


def create_session_factory(engine):
    """Return a sessionmaker bound to engine."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_db(engine) -> None:
    """Create all tables."""
    # Import models so they register on Base metadata.
    from goal_agent.models import goal as _goal  # noqa: F401
    from goal_agent.models import plan as _plan  # noqa: F401
    from goal_agent.models import checkin as _checkin  # noqa: F401
    from goal_agent.models import event as _event  # noqa: F401
    from goal_agent.models import user as _user  # noqa: F401

    Base.metadata.create_all(engine)
