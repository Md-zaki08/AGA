"""Persistent memory: store/recall facts, inject into planning + conversation."""

from __future__ import annotations

from typing import Any

from goal_agent.models.user import Memory


class MemoryStore:
    """Thin wrapper over Repository memory methods with prompt helpers."""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def remember(self, text: str, tag: str | None = None) -> Memory:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("memory text is required")
        return self._repo.create_memory(cleaned, tag)

    def recall(self, query: str, limit: int = 10) -> list[Memory]:
        return self._repo.search_memories(query or "")[:limit]

    def recent(self, limit: int = 10) -> list[Memory]:
        return self._repo.list_memories()[-limit:]

    def by_tag(self, tag: str) -> list[Memory]:
        return [m for m in self._repo.list_memories() if (m.tag or "") == tag]

    def inject_context(self, query: str | None = None, limit: int = 8) -> str:
        """Render memory facts for system-prompt / planning injection."""
        mems = self.recall(query, limit) if query else self.recent(limit)
        if not mems:
            return ""
        return "User facts: " + "; ".join(m.text for m in mems)

    def auto_remember_checkin(self, checkin_date: str, goal_id: int, completed: list[int], hours: float, notes: str) -> Memory:
        line = f"{checkin_date}: goal {goal_id} done {completed}, {hours}h, {(notes or '')[:100]}".strip()
        return self.remember(line, tag="checkin")
