"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _default_db_path() -> str:
    return os.path.expanduser("~/.aga/aga.db")


@dataclass(frozen=True)
class Settings:
    # Generic, provider-agnostic fields (preferred).
    llm_provider: str  # e.g. anthropic|openai|google|ollama|generic|auto
    llm_model: str
    llm_api_key: str | None
    llm_base_url: str | None
    # Legacy / specific provider keys (all accepted, any provider works).
    anthropic_api_key: str | None
    anthropic_model: str
    checkin_time: str
    db_path: str


def _detect_provider(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4")) or "openai" in m:
        return "openai"
    if "gemini" in m or "google" in m or "gemma" in m:
        return "google"
    if "ollama" in m or m.startswith(("llama", "mistral", "mixtral", "phi", "qwen")):
        return "ollama"
    return "auto"


def _first_present(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def get_settings() -> Settings:
    """Load settings from environment with sensible defaults.

    Accepts ANY provider/model: generic LLM_* vars take precedence, with
    fallback to every known provider key. Auto-detects provider from model
    name when LLM_PROVIDER is unset.
    """
    model = (
        os.getenv("LLM_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("GOOGLE_MODEL")
        or os.getenv("GEMINI_MODEL")
        or "claude-sonnet-4-5"
    )
    api_key = _first_present(
        "LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
    )
    provider = os.getenv("LLM_PROVIDER") or os.getenv("PROVIDER") or _detect_provider(model)
    return Settings(
        llm_provider=provider,
        llm_model=model,
        llm_api_key=api_key,
        llm_base_url=os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", model),
        checkin_time=os.getenv("CHECKIN_TIME", "09:00"),
        db_path=os.getenv("DB_PATH", _default_db_path()),
    )


def resolve_db_path(raw: str) -> Path:
    """Expand and resolve the SQLite path, creating parent dirs on demand."""
    p = Path(os.path.expanduser(raw)).resolve()
    return p
