"""Tool JSON schemas for the agent loop (exact tool list)."""

from __future__ import annotations

from typing import Any


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _int(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


def _num(desc: str) -> dict[str, Any]:
    return {"type": "number", "description": desc}


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return the 12 exact tools the agent can call."""
    return [
        {
            "name": "create_goal",
            "description": "Create a new goal with deadline, priority, and minimal viable outcome.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": _str("Goal title"),
                    "description": _str("Goal description"),
                    "deadline": _str("Deadline YYYY-MM-DD"),
                    "priority": _int("Priority 1-5"),
                    "minimal_viable_outcome": _str("Descope target set at creation"),
                },
                "required": ["title", "deadline", "minimal_viable_outcome"],
            },
        },
        {
            "name": "generate_plan",
            "description": "Generate the first plan version for a goal (LLM decomposition + deterministic scheduling).",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": _int("Goal ID")},
                "required": ["goal_id"],
            },
        },
        {
            "name": "revise_plan",
            "description": "Create a new plan version with a changelog reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": _int("Goal ID"),
                    "reason": _str("Why this revision exists"),
                },
                "required": ["goal_id", "reason"],
            },
        },
        {
            "name": "log_checkin",
            "description": "Log progress: completed tasks, hours spent, notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": _int("Goal ID"),
                    "completed_task_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Completed task IDs",
                    },
                    "hours_spent": _num("Hours spent"),
                    "notes": _str("Free-text notes"),
                },
                "required": ["goal_id", "completed_task_ids", "hours_spent"],
            },
        },
        {
            "name": "add_interruption",
            "description": "Block dates (e.g. busy until Friday) and replan around them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": _str("What the interruption is"),
                    "start_date": _str("Start YYYY-MM-DD"),
                    "end_date": _str("End YYYY-MM-DD"),
                },
                "required": ["description", "start_date", "end_date"],
            },
        },
        {
            "name": "change_deadline",
            "description": "Change a goal deadline and replan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": _int("Goal ID"),
                    "new_deadline": _str("New deadline YYYY-MM-DD"),
                },
                "required": ["goal_id", "new_deadline"],
            },
        },
        {
            "name": "list_goals",
            "description": "List all goals with status.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "get_plan",
            "description": "Show the active plan for a goal.",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": _int("Goal ID")},
                "required": ["goal_id"],
            },
        },
        {
            "name": "remember",
            "description": "Store a fact about the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": _str("Fact to remember"),
                    "tag": _str("Optional tag"),
                },
                "required": ["text"],
            },
        },
        {
            "name": "recall",
            "description": "Recall facts matching a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": _str("Search query")},
                "required": ["query"],
            },
        },
        {
            "name": "complete_goal",
            "description": "Mark a goal completed and update learned velocity/slippage.",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": _int("Goal ID")},
                "required": ["goal_id"],
            },
        },
        {
            "name": "negotiate_goal",
            "description": "Record the user's negotiation decision (extend deadline or accept reduced scope).",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": _int("Goal ID"),
                    "decision": _str("extend deadline:<YYYY-MM-DD> | accept reduced scope:<what>"),
                },
                "required": ["goal_id", "decision"],
            },
        },
    ]


def get_decomposer_tool() -> dict[str, Any]:
    """Single tool used by the decomposer to force structured breakdown output."""
    return {
        "name": "propose_breakdown",
        "description": "Propose milestones and tasks with hour estimates. Never compute dates.",
        "parameters": {
            "type": "object",
            "properties": {
                "milestones": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tasks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "desc": {"type": "string"},
                                        "est_hours": {"type": "number"},
                                        "is_core": {"type": "boolean"},
                                        "deps": {
                                            "type": "array",
                                            "items": {"type": "integer"},
                                            "description": "0-based indexes into this milestone's task list, or global task indexes",
                                        },
                                    },
                                    "required": ["desc", "est_hours"],
                                },
                            },
                        },
                        "required": ["name", "tasks"],
                    },
                }
            },
            "required": ["milestones"],
        },
    }
