"""Provider-agnostic LLM client.

Accepts ANY model/provider via env keys — never tied to one vendor.

Supported:
  - anthropic (claude-*) via `anthropic` SDK, key: ANTHROPIC_API_KEY / LLM_API_KEY
  - openai-compatible (gpt-*, o1, ollama, lmstudio, together, groq,
    openrouter, mistral, ...) via stdlib HTTP, keys: OPENAI_API_KEY / LLM_API_KEY / ...
  - google/gemini via stdlib REST, keys: GOOGLE_API_KEY / GEMINI_API_KEY
  - generic/auto: picks from model prefix + available keys; custom
    endpoints via LLM_BASE_URL (OpenAI-compatible).

Interface (pinned by architecture):
    complete(messages, tools) -> {"text": str, "tool_calls": [{"name":..., "arguments":...}]}
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any


Message = dict[str, Any]
ToolSpec = dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tool_calls": self.tool_calls or []}


class BaseLLM:
    """Provider abstraction. All backends share this interface."""

    provider_name: str = "base"
    model: str = ""

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        raise NotImplementedError


class FakeLLM(BaseLLM):
    """Deterministic in-process fake — zero network. Used in ALL tests."""

    provider_name = "fake"

    def __init__(self, plan_payload: dict[str, Any] | None = None) -> None:
        self.model = "fake"
        self.plan_payload: dict[str, Any] = plan_payload or {
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
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "tools": tools})
        names = [t.get("name", "") for t in (tools or [])]
        if "propose_breakdown" in names:
            return {"text": "", "tool_calls": [{"name": "propose_breakdown", "arguments": dict(self.plan_payload)}]}
        return {"text": "fake reply", "tool_calls": []}


class AnthropicBackend(BaseLLM):
    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str | None) -> None:
        self.model = model
        self.api_key = api_key

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError("anthropic SDK not installed") from e
        if not self.api_key:
            raise RuntimeError("Missing API key for Anthropic (set LLM_API_KEY or ANTHROPIC_API_KEY)")
        client = anthropic.Anthropic(api_key=self.api_key)
        # Split system messages (Anthropic uses top-level system param).
        system_parts: list[str] = []
        convo: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(str(m.get("content", "")))
            else:
                convo.append({"role": m.get("role", "user"), "content": str(m.get("content", ""))})
        system = "\n".join(system_parts) if system_parts else anthropic.NOT_GIVEN  # type: ignore[attr-defined]
        anth_tools: Any = anthropic.NOT_GIVEN  # type: ignore[attr-defined]
        if tools:
            anth_tools = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object"}),
                }
                for t in tools
            ]
        resp = client.messages.create(
            model=self.model, max_tokens=2048, system=system, messages=convo, tools=anth_tools
        )
        text = ""
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                text += getattr(block, "text", "")
            elif getattr(block, "type", "") == "tool_use":
                tool_calls.append({"name": getattr(block, "name", ""), "arguments": dict(getattr(block, "input", {}) or {})})
        return {"text": text, "tool_calls": tool_calls}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 60) -> dict[str, Any]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.loads(fh.read().decode())


class OpenAICompatBackend(BaseLLM):
    """OpenAI-compatible chat-completions backend (OpenAI, Ollama, Groq, Together...)."""

    provider_name = "openai-compat"

    def __init__(self, model: str, api_key: str | None, base_url: str | None = None) -> None:
        self.model = model
        self.api_key = api_key
        base = (base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.base_url = base

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        oai_messages = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages]
        payload: dict[str, Any] = {"model": self.model, "messages": oai_messages}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {"type": "object"})}}
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        raw = _post_json(url, payload, headers)
        choice = (raw.get("choices") or [{}])[0].get("message", {})
        text = choice.get("content") or ""
        tool_calls: list[dict[str, Any]] = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({"name": fn.get("name", ""), "arguments": args})
        return {"text": text, "tool_calls": tool_calls}


class GoogleBackend(BaseLLM):
    """Minimal Gemini backend via REST (generativelanguage.googleapis.com)."""

    provider_name = "google"

    def __init__(self, model: str, api_key: str | None) -> None:
        self.model = model
        self.api_key = api_key

    def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Missing API key for Google (set LLM_API_KEY or GOOGLE_API_KEY)")
        # Flatten messages to a single prompt; tool-calling via structured prompt.
        prompt = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        if tools:
            prompt += "\n\nAvailable tools (reply with JSON tool call if needed): " + json.dumps(tools)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        raw = _post_json(url, payload, {"Content-Type": "application/json"})
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            text = json.dumps(raw)
        # Best-effort: extract {"name":..., "arguments":...} JSON blocks as tool calls.
        tool_calls: list[dict[str, Any]] = []
        try:
            start = text.find("{")
            if start != -1 and '"name"' in text:
                maybe = json.loads(text[start : text.rfind("}") + 1])
                if isinstance(maybe, dict) and "name" in maybe:
                    tool_calls.append({"name": maybe["name"], "arguments": maybe.get("arguments", {})})
        except (json.JSONDecodeError, ValueError):
            pass
        return {"text": text, "tool_calls": tool_calls}


def detect_provider(model: str, explicit: str | None = None) -> str:
    """Return normalized provider name; 'auto' resolved from model prefix."""
    if explicit and explicit not in ("", "auto"):
        return explicit.lower()
    m = (model or "").lower()
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4")) or "openai" in m:
        return "openai"
    if "gemini" in m or "gemma" in m or "google" in m:
        return "google"
    if "groq" in m:
        return "groq-openai-compat"
    return "openai-compat"


def create_client(
    model: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
) -> BaseLLM:
    """Factory accepting ANY provider/model — never rejects unknown vendors.

    Resolution order: explicit args > generic LLM_* env > any known provider
    key > None (backends raise a helpful missing-key error only when called).
    """
    from goal_agent.config import get_settings

    settings = get_settings()
    resolved_model = model or os.getenv("LLM_MODEL") or settings.llm_model
    resolved_provider = (provider or os.getenv("LLM_PROVIDER") or settings.llm_provider or "auto").lower()
    resolved_key = (
        api_key
        or os.getenv("LLM_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GROQ_API_KEY")
        or os.getenv("TOGETHER_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("MISTRAL_API_KEY")
        or os.getenv("COHERE_API_KEY")
        or settings.llm_api_key
    )
    resolved_base = base_url or os.getenv("LLM_BASE_URL") or settings.llm_base_url

    prov = detect_provider(resolved_model, resolved_provider if resolved_provider != "auto" else None)
    if prov == "anthropic":
        return AnthropicBackend(model=resolved_model, api_key=resolved_key)
    if prov == "google":
        return GoogleBackend(model=resolved_model, api_key=resolved_key)
    # Everything else (openai, ollama, groq, together, openrouter, unknown)
    # goes through the OpenAI-compatible HTTP backend — incl. local no-key servers.
    return OpenAICompatBackend(model=resolved_model, api_key=resolved_key, base_url=resolved_base)
