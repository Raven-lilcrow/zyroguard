"""Plain-language wording shared by every reply the bot sends.

One place that turns "mute 24h" into "mute for 24h" or "Muted for 24h", so commands, the
log group, /diag and /help all read the same way.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .durations import format_duration

_PAST = {
    "mute": "Muted",
    "ban": "Banned",
    "kick": "Kicked out",
    "delete": "Deleted the message",
    "warn": "Warned",
    "none": "Did nothing",
    "flag": "Flagged for a human to check",
}

_PLAIN = {
    "kick": "kick out",
    "delete": "delete the message",
    "warn": "warn",
    "none": "do nothing",
    "flag": "flag for a human to check",
}

SOURCE_WORDS = {
    "rule": "spam rule",
    "judge": "AI judge",
    "admin": "admin command",
    "captcha": "join check",
    "cas": "known spammer list",
    "flood": "flooding",
    "bio": "spam bio",
    "name": "spam name",
    "join": "join check",
    "safety": "safety check",
    "forward": "forward check",
}


def duration_words(seconds: int | None) -> str:
    """None becomes 'for good', 86400 becomes 'for 1 day'."""
    return "for good" if seconds is None else f"for {long_duration(format_duration(seconds))}"


def long_duration(raw: str) -> str:
    """Compact '2m', '24h' or '1d12h' spelled out: '2 minutes', '24 hours', '1 day 12 hours'."""
    from .durations import try_parse_duration
    d = try_parse_duration(raw)
    if d is None:
        return raw
    if d.permanent:
        return "forever"
    parts = []
    rem = d.seconds or 0
    for name, size in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        if rem >= size:
            qty, rem = divmod(rem, size)
            parts.append(f"{qty} {name}" + ("s" if qty != 1 else ""))
    return " ".join(parts) or "0 seconds"


def action_words(raw: str, long: bool = False) -> str:
    """Setting text as typed ('mute 24h', 'ban', 'kick') in words: 'mute for 24h', 'ban for good', 'kick out'.

    long=True spells the time out: 'mute for 24 hours'."""
    kind, _, dur = raw.strip().partition(" ")
    kind = kind.lower()
    if kind in {"mute", "ban"}:
        if dur and dur not in {"0", "forever"}:
            return f"{kind} for {long_duration(dur) if long else dur}"
        return f"{kind} for good"
    return _PLAIN.get(kind, raw)


def action_past_long(raw: str) -> str:
    """'mute 24h' becomes 'muted for 24 hours', 'ban' becomes 'banned for good', 'kick' becomes 'kicked out'."""
    kind, _, dur = raw.strip().partition(" ")
    kind = kind.lower()
    past = {"mute": "muted", "ban": "banned", "kick": "kicked out", "delete": "message deleted only",
            "warn": "warned only", "none": "nothing happens"}
    if kind in {"mute", "ban"}:
        if dur and dur not in {"0", "forever"}:
            return f"{past[kind]} for {long_duration(dur)}"
        return f"{past[kind]} for good"
    return past.get(kind, raw)


def action_done(action: str, seconds: int | None = None, expires_at: datetime | None = None) -> str:
    """Past tense for something that happened: 'Muted for 24h (until Sep 25, 13:00 UTC)'."""
    if action in {"mute", "ban"}:
        s = f"{_PAST[action]} {duration_words(seconds)}"
        if expires_at:
            s += f" (until {expires_at.astimezone(timezone.utc):%b %d, %H:%M} UTC)"
        return s
    return _PAST.get(action, action)


def action_would(action: str, seconds: int | None = None) -> str:
    """Conditional form for shadow mode and /test: 'mute for 24h'."""
    if action in {"mute", "ban"}:
        return f"{action} {duration_words(seconds)}"
    return _PLAIN.get(action, action)


def source_words(source: str) -> str:
    return SOURCE_WORDS.get(source, source)


def when(ts: datetime) -> str:
    return f"{ts.astimezone(timezone.utc):%b %d, %H:%M} UTC"


def ago(ts: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    s = int((now - ts).total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        n = s // 60
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if s < 86400:
        n = s // 3600
        return f"{n} hour{'s' if n != 1 else ''} ago"
    n = s // 86400
    return f"{n} day{'s' if n != 1 else ''} ago"
