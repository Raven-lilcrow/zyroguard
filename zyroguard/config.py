"""Process-level configuration, read once from the environment.

Everything that changes per group lives in the database (see settings.py),
not here. This file only holds secrets and machine-level knobs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"


def _int_list(raw: str, name: str = "value") -> list[int]:
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                raise RuntimeError(f"{name} must be numeric ids separated by commas, got '{part}'") from None
    return out


def _int(raw: str, name: str, default: int | None = None) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number, got '{raw}'") from None


EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _effort(raw: str, name: str, default: str) -> str:
    v = (raw or "").strip().lower() or default
    if v not in EFFORTS:
        raise RuntimeError(f"{name} must be one of {', '.join(sorted(EFFORTS))}, got '{v}'")
    return v


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    anthropic_api_key: str | None
    judge_model: str
    judge_effort: str
    assistant_effort: str
    owner_ids: list[int]
    allowed_chat_ids: list[int]
    modlog_chat_id: int | None
    n8n_event_url: str | None
    n8n_event_secret: str | None
    cas_enabled: bool
    similarity_enabled: bool
    contact_email: str | None
    contact_webhook_url: str | None
    contact_webhook_secret: str | None
    owner_ledger_url: str | None
    owner_ledger_secret: str | None
    db_path: Path
    rules_path: Path
    http_host: str
    http_port: int
    log_level: str
    default_mode: str  # "shadow" | "enforce"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def multi_group(self) -> bool:
        """True when any group may add the bot (no allowlist configured)."""
        return not self.allowed_chat_ids

    def chat_allowed(self, chat_id: int) -> bool:
        return self.multi_group or chat_id in self.allowed_chat_ids

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids


def load_config(env: dict[str, str] | None = None) -> Config:
    e = env if env is not None else os.environ
    token = e.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    here = Path(__file__).resolve().parent
    modlog = e.get("MODLOG_CHAT_ID", "").strip()
    mode = e.get("MODE", "shadow").strip().lower()
    if mode not in {"shadow", "enforce"}:
        raise RuntimeError("MODE must be 'shadow' or 'enforce'")
    return Config(
        bot_token=token,
        anthropic_api_key=e.get("ANTHROPIC_API_KEY", "").strip() or None,
        judge_model=e.get("JUDGE_MODEL", "claude-opus-5").strip(),
        judge_effort=_effort(e.get("JUDGE_EFFORT", ""), "JUDGE_EFFORT", "low"),
        assistant_effort=_effort(e.get("ASSISTANT_EFFORT", ""), "ASSISTANT_EFFORT", "medium"),
        owner_ids=_int_list(e.get("OWNER_IDS", ""), "OWNER_IDS"),
        allowed_chat_ids=_int_list(e.get("ALLOWED_CHAT_IDS", ""), "ALLOWED_CHAT_IDS"),
        modlog_chat_id=_int(modlog, "MODLOG_CHAT_ID"),
        n8n_event_url=e.get("N8N_EVENT_URL", "").strip() or None,
        n8n_event_secret=e.get("N8N_EVENT_SECRET", "").strip() or None,
        cas_enabled=_bool(e.get("CAS_ENABLED"), True),
        similarity_enabled=_bool(e.get("SIMILARITY"), True),
        contact_email=e.get("CONTACT_EMAIL", "").strip() or None,
        contact_webhook_url=e.get("CONTACT_WEBHOOK_URL", "").strip() or None,
        contact_webhook_secret=e.get("CONTACT_WEBHOOK_SECRET", "").strip() or None,
        owner_ledger_url=e.get("OWNER_LEDGER_URL", "").strip() or None,
        owner_ledger_secret=e.get("OWNER_LEDGER_SECRET", "").strip() or None,
        db_path=Path(e.get("DB_PATH", "/data/zyroguard.db")),
        rules_path=Path(e.get("RULES_PATH", str(here / "rules.yaml"))),
        http_host=e.get("HTTP_HOST", "0.0.0.0"),
        http_port=_int(e.get("HTTP_PORT", "8080"), "HTTP_PORT", 8080),
        log_level=e.get("LOG_LEVEL", "INFO").upper(),
        default_mode=mode,
    )
