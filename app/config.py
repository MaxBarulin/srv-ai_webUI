"""Application configuration loaded from environment variables / .env file.

No python-dotenv dependency (not in the allowed-dependency list, §2 of the TZ) —
a minimal KEY=VALUE parser is enough for our small, fixed set of settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_FILE_VALUES = _parse_env_file(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, _FILE_VALUES.get(name, default))


def _get_bool(name: str, default: bool) -> bool:
    return _get(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    raw = _get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: int
    rag_enabled: bool
    rag_base_url: str
    rag_api_key: str
    rag_mode: str
    session_ttl_hours: int
    tools_confirm_destructive: bool
    system_prompt_file: str
    max_upload_mb: int
    vision_max_pages: int
    pii_filter: bool
    pii_whitelist_file: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "srv-ai-ui.db"


def load_settings() -> Settings:
    data_dir = Path(_get("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_host=_get("APP_HOST", "0.0.0.0"),
        app_port=_get_int("APP_PORT", 8080),
        data_dir=data_dir,
        llm_base_url=_get("LLM_BASE_URL", "http://127.0.0.1:8000/v1"),
        llm_api_key=_get("LLM_API_KEY", "").strip(),
        llm_model=_get("LLM_MODEL", "qwen3.6-35b"),
        llm_timeout=_get_int("LLM_TIMEOUT", 600),
        rag_enabled=_get_bool("RAG_ENABLED", True),
        rag_base_url=_get("RAG_BASE_URL", ""),
        rag_api_key=_get("RAG_API_KEY", "").strip(),
        rag_mode=_get("RAG_MODE", "hybrid"),
        session_ttl_hours=_get_int("SESSION_TTL_HOURS", 12),
        tools_confirm_destructive=_get_bool("TOOLS_CONFIRM_DESTRUCTIVE", True),
        system_prompt_file=_get("SYSTEM_PROMPT_FILE", "./system_prompt.txt"),
        max_upload_mb=_get_int("MAX_UPLOAD_MB", 15),
        vision_max_pages=_get_int("VISION_MAX_PAGES", 10),
        pii_filter=_get_bool("PII_FILTER", False),
        pii_whitelist_file=_get("PII_WHITELIST_FILE", ""),
    )


settings = load_settings()
