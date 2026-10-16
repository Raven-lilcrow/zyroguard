"""One duration syntax for every command and setting.

Format: ``<number><unit>`` chained freely, e.g. ``30m``, ``4h``, ``3d``, ``2w``,
``1d12h``. ``0``, ``forever``, ``perm`` and ``permanent`` mean no expiry.
Seconds (``s``) are accepted for flood windows.

Telegram treats a restriction shorter than 30 seconds or longer than 366 days
as permanent, so callers that hand a duration to Telegram should go through
``clamp_for_telegram`` and tell the admin when a clamp happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}
_TOKEN = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_FOREVER = {"0", "forever", "perm", "permanent", "inf", "never"}

TELEGRAM_MIN = 30
TELEGRAM_MAX = 366 * 86400

HELP_TEXT = "Times look like 30m, 2h, 7d or 2w. Combine them like 1d12h. 0 or forever means permanent."


class DurationError(ValueError):
    """Raised when a duration string cannot be parsed."""


@dataclass(frozen=True)
class Duration:
    seconds: int | None  # None means permanent

    @property
    def permanent(self) -> bool:
        return self.seconds is None

    def expires_at(self, now: datetime | None = None) -> datetime | None:
        if self.permanent:
            return None
        base = now or datetime.now(timezone.utc)
        return base + timedelta(seconds=self.seconds or 0)

    def __str__(self) -> str:
        return format_duration(self.seconds)


def parse_duration(raw: str | None) -> Duration:
    """Parse ``raw`` into a Duration. Raises DurationError on bad input."""
    if raw is None:
        raise DurationError(HELP_TEXT)
    text = re.sub(r"\s+", "", raw.lower())
    if not text:
        raise DurationError(HELP_TEXT)
    if text in _FOREVER:
        return Duration(None)
    pos = 0
    total = 0
    for match in _TOKEN.finditer(text):
        if match.start() != pos:
            raise DurationError(f"Could not read '{raw}'. {HELP_TEXT}")
        total += int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
        pos = match.end()
    if pos != len(text) or pos == 0:
        raise DurationError(f"Could not read '{raw}'. {HELP_TEXT}")
    if total == 0:
        return Duration(None)
    if total > 10 * 366 * 86400:
        raise DurationError("That is too long. Use up to 10 years, or 0 / forever for permanent.")
    return Duration(total)


def try_parse_duration(raw: str | None) -> Duration | None:
    """Like parse_duration but returns None instead of raising."""
    try:
        return parse_duration(raw)
    except DurationError:
        return None


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "forever"
    if seconds <= 0:
        return "0s"
    parts: list[str] = []
    rem = seconds
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if rem >= size:
            qty, rem = divmod(rem, size)
            parts.append(f"{qty}{unit}")
    return "".join(parts)


def clamp_for_telegram(d: Duration) -> tuple[Duration, str | None]:
    """Return a Duration Telegram will honour plus a note if it was changed."""
    if d.permanent:
        return d, None
    if d.seconds < TELEGRAM_MIN:
        return Duration(None), (
            f"{format_duration(d.seconds)} is under Telegram's 30 s minimum, "
            "so it becomes permanent."
        )
    if d.seconds > TELEGRAM_MAX:
        return Duration(None), (
            f"{format_duration(d.seconds)} is over Telegram's 366 day maximum, "
            "so it becomes permanent."
        )
    return d, None


def until_date(d: Duration, now: datetime | None = None) -> datetime | None:
    """Value for aiogram's ``until_date`` parameter (None means permanent)."""
    clamped, _ = clamp_for_telegram(d)
    return clamped.expires_at(now)


def parse_action(tokens: list[str]) -> tuple[str, Duration | None]:
    """Parse ``["mute", "2h"]``, ``["ban"]`` or ``["kick"]`` into (action, duration).

    Accepted actions: delete, warn, kick, mute, ban, tmute, tban, none.
    ``tmute`` and ``tban`` are Rose aliases and require a duration.
    """
    if not tokens:
        raise DurationError("Expected an action: delete, warn, kick, mute [dur], ban [dur].")
    action = tokens[0].lower()
    alias = {"tmute": "mute", "tban": "ban", "off": "none", "nothing": "none"}
    needs_duration = action in {"tmute", "tban"}
    action = alias.get(action, action)
    if action not in {"delete", "warn", "kick", "mute", "ban", "none"}:
        raise DurationError(
            f"Unknown action '{tokens[0]}'. Use delete, warn, kick, mute [dur], ban [dur]."
        )
    if action in {"mute", "ban"}:
        if len(tokens) > 1:
            return action, parse_duration("".join(tokens[1:]))
        if needs_duration:
            raise DurationError(f"{tokens[0]} needs a duration, e.g. {tokens[0]} 2h.")
        return action, Duration(None)
    if len(tokens) > 1:
        raise DurationError(f"'{action}' does not take a duration.")
    return action, None


def format_action(action: str, duration: Duration | None) -> str:
    if action in {"mute", "ban"}:
        return f"{action} {format_duration(duration.seconds if duration else None)}"
    return action
