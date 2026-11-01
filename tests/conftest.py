"""Shared pytest fixtures: in-memory SQLite + repository + FakeLLM."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src layout importable when running without install.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def repo():
    from goal_agent.db.database import Base, create_db_engine, create_session_factory, init_db
    from goal_agent.db.repository import Repository
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = create_session_factory(engine)
    return Repository(factory)


class FakeLLM:
    """Deterministic stand-in for the Anthropic client. Zero network."""

    def __init__(self, plan_payload: dict | None = None) -> None:
        self.plan_payload = plan_payload or {
            "milestones": [
                {
                    "name": "M1",
                    "tasks": [
                        {"desc": "t1", "est_hours": 2.0, "is_core": True, "deps": []},
                        {"desc": "t2", "est_hours": 3.0, "is_core": True, "deps": [0]},
                    ],
                }
            ]
        }
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, **kwargs):
        import json

        self.calls.append({"messages": messages, "tools": tools})
        # Tool-call shaped response for decomposition, text otherwise.
        last = messages[-1].get("content", "") if messages else ""
        if tools and any(t.get("name") == "propose_breakdown" for t in (tools or [])):
            return {
                "tool_calls": [
                    {"name": "propose_breakdown", "arguments": dict(self.plan_payload)}
                ],
                "text": "",
            }
        return {"tool_calls": [], "text": "fake reply"}


@pytest.fixture()
def fake_llm():
    return FakeLLM()
