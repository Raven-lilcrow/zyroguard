"""Per-group settings: typed defaults, validation, and the /settings inline menu.

Values are stored as strings in chat_settings and parsed on read. Actions are
stored in the same text form admins type them ("mute 24h", "ban", "kick").
"""

from __future__ import annotations

import html
import re

from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .durations import DurationError, format_duration, parse_action, parse_duration, format_action
from .store import Store


@dataclass(frozen=True)
class Spec:
    key: str
    kind: str  # bool, int, float, duration, action, choice, str or time
    default: str
    label: str
    help: str
    choices: tuple[str, ...] = ()
    menu_options: tuple[str, ...] = ()  # values offered as toggle buttons
    group: str = "general"
    max_len: int = 0  # str settings: longest value accepted, 0 means no limit


SPECS: dict[str, Spec] = {}


def _add(spec: Spec) -> None:
    SPECS[spec.key] = spec


_add(Spec("mode", "choice", "shadow", "Mode",
          "shadow = log only, enforce = act.", ("shadow", "enforce"), ("shadow", "enforce"), "general"))
_add(Spec("captcha", "bool", "on", "Captcha", "New members must pass a small captcha before they can talk.",
          menu_options=("on", "off"), group="join"))
_add(Spec("captcha_mode", "choice", "emoji", "Captcha mode",
          "emoji = pick the word for a picture; math = answer a simple sum; button = just tap.",
          ("emoji", "math", "button"), ("emoji", "math", "button"), "join"))
_add(Spec("captcha_time", "duration", "2m", "Captcha timeout",
          "How long a newcomer has to pass the captcha.", menu_options=("1m", "2m", "5m"), group="join"))
_add(Spec("captcha_fail", "action", "kick", "Captcha fail action",
          "What happens when the captcha times out (kick, ban 1d ...).", menu_options=("kick", "ban 1d", "ban"), group="join"))
_add(Spec("captcha_every_join", "bool", "on", "Captcha on every join",
          "Everyone gets the captcha each time they join, even if they passed it before and left.",
          menu_options=("on", "off"), group="join"))
_add(Spec("strict_unverified", "bool", "on", "Block unverified newcomers",
          "Delete everything an unverified newcomer sends until they pass.", menu_options=("on", "off"), group="join"))
_add(Spec("skip_verified_anywhere", "bool", "off", "Skip captcha if passed elsewhere",
          "Skip captcha for users who passed it in any group served by this bot.", menu_options=("on", "off"), group="join"))
_add(Spec("restrict_window", "duration", "24h", "New member limits last",
          "After verifying, new members can't post links or forwards for this long. 0 disables.", menu_options=("0", "24h", "72h"), group="join"))
_add(Spec("new_member_msgs", "int", "5", "New-member message count",
          "A member also counts as new until they have sent this many messages.", group="join"))
_add(Spec("cas", "bool", "on", "CAS check", "Act on people on the known spammer list (Combot Anti-Spam) when they join.",
          menu_options=("on", "off"), group="join"))
_add(Spec("cas_action", "action", "ban", "CAS action", "Action for CAS-listed users.",
          menu_options=("ban", "kick", "mute 7d"), group="join"))
_add(Spec("bio_scan", "bool", "off", "Bio scan", "Kick or ban people who join with a link or crypto pitch in their bio. Off by default: many real people have a link in their bio.",
          menu_options=("on", "off"), group="join"))
_add(Spec("clean_service", "bool", "on", "Clean join/leave",
          "Delete 'X joined / left' service messages.", menu_options=("on", "off"), group="join"))
_add(Spec("new_account_age", "duration", "1h", "New-account age",
          "Members who joined less than this long ago get the harsher action on any hard hit.", group="join"))
_add(Spec("new_account_action", "action", "ban 30d", "New-account action",
          "Action for hard hits from very new accounts.", group="join"))

_add(Spec("link_policy", "choice", "new", "Link policy",
          "all = allow links; new = block links from new members; allowlist = only allowlisted domains.",
          ("all", "new", "allowlist"), ("all", "new", "allowlist"), "filters"))
_add(Spec("media_unverified", "choice", "allow", "Media from new members",
          "allow = every image is judged by content (safety check); delete = remove all media from new members.", ("allow", "delete"), ("allow", "delete"), "filters"))
_add(Spec("forward_captcha", "bool", "on", "Captcha on forwards",
          "Anyone who forwards a message must pass a quick captcha. Not solved in time: the forward is removed "
          "and they get a strike (the strike ladder decides the punishment).",
          menu_options=("on", "off"), group="filters"))
_add(Spec("forward_captcha_time", "duration", "2m", "Forward captcha timeout",
          "How long someone has to solve the captcha after forwarding.", menu_options=("1m", "2m", "5m"), group="filters"))
_add(Spec("languages", "languages", "", "Allowed languages",
          "Languages people may write in here, e.g. en or en,tl. Empty = every language. A message in another "
          "language is just deleted: no strike, no mute. Short or mixed messages are never deleted for this.",
          group="filters"))
_add(Spec("language_scope", "choice", "new", "Language rule applies to",
          "new = only new members; all = everyone except admins and trusted users.",
          ("new", "all"), ("new", "all"), "filters"))
_add(Spec("protected_names", "names", "", "Protected names",
          "Names only admins may use, e.g. AcmeAI. Anyone else whose name copies one with lookalike letters "
          "(AcmeAl, Acm3AI) is removed when they join or post. Using the real name is allowed but watched.",
          group="filters"))
_add(Spec("impersonation_action", "action", "ban", "Impersonation action",
          "For lookalike brand names, and for messages the AI judge is 95% sure are impersonation or a wallet scam. "
          "Skips the strike ladder.", menu_options=("ban", "ban 30d", "mute 7d"), group="filters"))
_add(Spec("similarity", "choice", "shadow", "Meaning match",
          "Compare messages with known spam by meaning, in any language (teach it with /spam and /notspam as a reply). "
          "shadow = only record what it would have done; on = a close match adds to the spam score; off.",
          ("off", "shadow", "on"), ("off", "shadow", "on"), "filters"))
_add(Spec("similarity_learn", "bool", "off", "Learn from catches",
          "Messages the AI judge was 95% sure about, and strong rule hits, become spam examples for this group. "
          "Each one waits 24 hours first and is dropped if the action is undone. Needs the meaning match (not off).",
          menu_options=("on", "off"), group="filters"))
_add(Spec("flood_count", "int", "5", "Flood count", "Messages inside the flood window that count as flooding.",
          group="filters"))
_add(Spec("flood_window", "duration", "10s", "Flood window", "Window for the flood count.", group="filters"))
_add(Spec("flood_action", "action", "mute 1h", "Flood action", "Action taken on flooding.",
          menu_options=("mute 1h", "mute 24h", "kick"), group="filters"))
_add(Spec("repeat_count", "int", "3", "Repeat count",
          "Identical text posted by this many different users in 10 minutes is treated as a spam wave.", group="filters"))
_add(Spec("job_rules", "bool", "on", "Fake job rules",
          "Find fake job and task offers (like videos for pay, rate products, salary per day + 'message me'). "
          "off = this group keeps the rules and handling it had before they were added.",
          menu_options=("on", "off"), group="filters"))
_add(Spec("phishing_rules", "bool", "on", "Account phishing rules",
          "Find account phishing: 'your account will be deleted, verify here', fake Telegram or GCash support, and "
          "anyone asking for a login code, OTP or two-step password. These always go to the AI judge. "
          "off = this group keeps the rules it had before they were added.",
          menu_options=("on", "off"), group="filters"))
_add(Spec("job_strike_after", "int", "5", "Job scams: strike from",
          "Fake job and task offers are always checked by the AI judge, then only deleted (no strike, no mute) until "
          "the same person has posted this many here; from that one on the strike ladder applies. "
          "1 = strike from the first one, 0 = never strike, just delete.", group="filters"))

_add(Spec("judge", "bool", "on", "AI judge", "Ask the AI judge about messages the rules are unsure of.",
          menu_options=("on", "off"), group="judge"))
_add(Spec("judge_context", "str", "", "About this group",
          "One line for the AI judge about what is normal here, e.g. 'an online shop community, product links and prices are normal'.",
          group="judge"))
_add(Spec("judge_examples", "bool", "off", "Show the judge examples",
          "The AI judge also sees up to 3 labeled messages closest in meaning, so it follows this group's earlier "
          "/spam, /notspam and /undo decisions. Needs the meaning match (not off).",
          menu_options=("on", "off"), group="judge"))
_add(Spec("judge_threshold", "float", "0.85", "Judge threshold", "How sure the AI judge must be before I act. 0.85 = 85%.",
          menu_options=("0.7", "0.85", "0.95"), group="judge"))
_add(Spec("judge_action", "action", "delete", "Judge action",
          "Action when Claude says spam: delete (adds a strike) or mute 12h, ban ...", menu_options=("delete", "mute 12h", "ban 7d"), group="judge"))
_add(Spec("judge_daily_cap", "int", "500", "Judge daily cap", "Most AI spam checks per day for this group.",
          menu_options=("100", "500", "2000"),
          group="judge"))

_add(Spec("strike1", "action", "mute 24h", "Strike 1", "Action on the first strike.",
          menu_options=("mute 1h", "mute 24h", "mute 7d"), group="strikes"))
_add(Spec("strike2", "action", "ban 30d", "Strike 2", "Action on the second strike.",
          menu_options=("mute 7d", "ban 30d", "ban"), group="strikes"))
_add(Spec("strike3", "action", "ban", "Strike 3+", "Action on the third and later strikes.",
          menu_options=("ban 30d", "ban"), group="strikes"))
_add(Spec("strike_ttl", "duration", "30d", "Strike expiry", "Strikes older than this are forgotten.",
          menu_options=("7d", "30d", "90d"), group="strikes"))
_add(Spec("warn_limit", "int", "3", "Warn limit", "How many /warn before the warn action fires.",
          group="strikes"))
_add(Spec("warn_action", "action", "mute 1d", "Warn action", "Action when the warn limit is reached.",
          menu_options=("mute 1d", "ban 7d", "kick"),
          group="strikes"))

_add(Spec("safety", "bool", "on", "Safety check",
          "Remove explicit, violent, harassing or hateful images and messages (AI checked).",
          menu_options=("on", "off"), group="safety"))
_add(Spec("safety_daily_cap", "int", "300", "Safety check daily cap",
          "Most safety checks per day for this group (images and flagged text). Separate from the spam judge.",
          group="safety"))
_add(Spec("safety_action", "action", "mute 1d", "Safety action",
          "What happens to the sender. The message is always deleted.",
          menu_options=("delete", "mute 1d", "ban 7d", "ban"), group="safety"))

_add(Spec("assistant", "bool", "on", "Answer questions",
          "Members can ask me things by @mentioning me, replying to me, or /ask.",
          menu_options=("on", "off"), group="assistant"))
_add(Spec("assistant_daily_cap", "int", "200", "Questions per day",
          "Max questions I answer per day in this group.", menu_options=("50", "200", "1000"), group="assistant"))

_add(Spec("support", "bool", "off", "Answer from your files",
          "Support mode: /ask questions are answered only from the files you gave me in /zyromod. "
          "Needs at least one file and a support email.", group="support"))
_add(Spec("support_hint", "bool", "on", "Remind members to use /ask",
          "When someone asks about your product without /ask, I show them how to ask me (once a day per person).",
          menu_options=("on", "off"), group="support"))
_add(Spec("support_email", "email", "", "Support email",
          "Shown when I can't answer from your files. Required for support mode.", group="support"))
_add(Spec("support_link", "url", "", "Contact link",
          "Optional button under my can't-answer message: a ticket page, Discord, website, anything.",
          group="support"))
_add(Spec("support_link_label", "str", "Contact the team", "Contact button text",
          "The text on that button.", group="support", max_len=40))
_add(Spec("support_text", "str", "", "Can't-answer message",
          "Your own wording for when I can't answer. Leave it empty for the default. The email is added under it "
          "unless your message already has it.",
          group="support", max_len=500))
_add(Spec("support_instructions", "str", "", "Instructions for me",
          "How I should answer in this group: tone, what to do and not do, and links or details to include. "
          "Up to 1500 characters. I still answer only from your files and these instructions.",
          group="support", max_len=1500))

_add(Spec("group_notice", "bool", "on", "Notice in the group",
          "In enforce mode, post a short line in the group when I remove spam (who, what happened, for how long).",
          menu_options=("on", "off")))
_add(Spec("notice_ttl", "duration", "10m", "Notices disappear after",
          "My notices, greetings and command replies in the group are deleted after this. 0 = keep them.",
          menu_options=("5m", "10m", "1h", "0")))
_add(Spec("modlog_chat_id", "str", "", "Reports go to", "Chat id my reports go to. Empty = your private chat with me (or the server log group).",
          group="general"))
_add(Spec("digest_time", "time", "09:00", "Daily summary time", "Time of the daily summary, Manila time, like 09:00.",
          group="general"))
_add(Spec("greeting", "str", "", "Greeting", "Optional text sent after a member verifies. Empty = none.",
          group="join"))

GROUPS = {
    "general": "General",
    "join": "Join gate",
    "filters": "Filters",
    "judge": "AI judge",
    "strikes": "Strikes",
    "assistant": "Questions",
    "support": "Support",
    "safety": "Safety check",
}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}")
LINK_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly", "rebrand.ly",
                   "shorturl.at", "tiny.cc", "s.id", "rb.gy", "lnkd.in", "t.ly", "v.gd"}
_EMPTY = {"", "none", "off", "-", "remove"}


def link_host(url: str) -> str:
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def validate(key: str, raw: str) -> str:
    """Normalise and validate a raw admin-typed value. Raises ValueError."""
    spec = SPECS.get(key)
    if not spec:
        raise ValueError(f"Unknown setting '{key}'.")
    value = raw.strip()
    if key == "captcha_fail" and value.split(":")[0].split()[0].lower() not in {"kick", "ban"} if value else False:
        raise ValueError("Captcha fail action must be kick or ban, otherwise people stay muted with no way in.")
    if spec.kind == "bool":
        v = value.lower()
        if v in {"on", "yes", "true", "1"}:
            return "on"
        if v in {"off", "no", "false", "0"}:
            return "off"
        raise ValueError(f"{spec.label}: use on or off.")
    if spec.kind == "int":
        try:
            n = int(value)
        except ValueError as e:
            raise ValueError(f"{spec.label}: needs a whole number.") from e
        if n < 0:
            raise ValueError(f"{spec.label}: must be 0 or more.")
        return str(n)
    if spec.kind == "float":
        try:
            f = float(value)
        except ValueError as e:
            raise ValueError(f"{spec.label}: needs a number like 0.85.") from e
        if not 0 <= f <= 1:
            raise ValueError(f"{spec.label}: must be between 0 and 1.")
        return f"{f:g}"
    if spec.kind == "duration":
        try:
            d = parse_duration(value)
        except DurationError as e:
            raise ValueError(f"{spec.label}: {e}") from e
        return format_duration(d.seconds) if not d.permanent else "0"
    if spec.kind == "action":
        try:
            action, dur = parse_action(value.split())
        except DurationError as e:
            raise ValueError(f"{spec.label}: {e}") from e
        return format_action(action, dur)
    if spec.kind == "choice":
        v = value.lower()
        if v not in spec.choices:
            raise ValueError(f"{spec.label}: choose one of {', '.join(spec.choices)}.")
        return v
    if spec.kind == "languages":
        from .language import parse_languages
        return parse_languages(value)
    if spec.kind == "names":
        if value.lower() in {"", "none", "off", "-"}:
            return ""
        names = [n.strip() for n in value.split(",") if n.strip()]
        short = [n for n in names if len(re.sub(r"[^a-z]", "", n.lower())) < 4]
        if short:
            raise ValueError(f"{spec.label}: '{short[0]}' is too short, use at least 4 letters so normal names are not hit.")
        if len(names) > 10:
            raise ValueError(f"{spec.label}: at most 10 names.")
        return ", ".join(dict.fromkeys(names))
    if spec.kind == "email":
        if value.lower() in _EMPTY:
            return ""
        v = value.strip("<> ")
        v = v[7:] if v.lower().startswith("mailto:") else v
        if len(v) > 120 or not EMAIL.fullmatch(v):
            raise ValueError(f"{spec.label}: that does not look like an email address, like support@yourcompany.com")
        return v
    if spec.kind == "url":
        if value.lower() in _EMPTY:
            return ""
        v = value.strip("<> ")
        if not v.lower().startswith(("http://", "https://")):
            v = "https://" + v
        host = link_host(v)
        if len(v) > 200 or re.search(r"[\s<>\"']", v) or "." not in host:
            raise ValueError(f"{spec.label}: send a full link, like https://yourcompany.com/support")
        if host in LINK_SHORTENERS:
            raise ValueError(f"{spec.label}: short links hide where they go. Send the full link instead.")
        return v
    if spec.kind == "str" and spec.max_len and len(value) > spec.max_len:
        raise ValueError(f"{spec.label}: at most {spec.max_len} characters, this one has {len(value)}.")
    if spec.kind == "time":
        parts = value.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"{spec.label}: use HH:MM.")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"{spec.label}: use HH:MM.")
        return f"{h:02d}:{m:02d}"
    return value


class ChatSettings:
    """Typed view over a chat's settings dict."""

    def __init__(self, chat_id: int, raw: dict[str, str], default_mode: str):
        self.chat_id = chat_id
        self._raw = raw
        self._default_mode = default_mode

    def raw(self, key: str) -> str:
        if key in self._raw:
            return self._raw[key]
        if key == "mode":
            return self._default_mode
        return SPECS[key].default

    def bool(self, key: str) -> bool:
        return self.raw(key) == "on"

    def int(self, key: str) -> int:
        return int(self.raw(key))

    def float(self, key: str) -> float:
        return float(self.raw(key))

    def seconds(self, key: str) -> int | None:
        d = parse_duration(self.raw(key))
        return d.seconds

    def action(self, key: str) -> tuple[str, Any]:
        return parse_action(self.raw(key).split())

    def choice(self, key: str) -> str:
        return self.raw(key)

    def str(self, key: str) -> str:
        return self.raw(key)

    @property
    def enforce(self) -> bool:
        return self.raw("mode") == "enforce"

    @property
    def modlog_chat_id(self) -> int | None:
        v = self.raw("modlog_chat_id").strip()
        try:
            return int(v) if v else None
        except ValueError:
            return None

    def as_dict(self) -> dict[str, str]:
        return {k: self.raw(k) for k in SPECS}


def protected_names(cs: ChatSettings) -> list[str]:
    return [n.strip() for n in cs.raw("protected_names").split(",") if n.strip()]


class SettingsService:
    def __init__(self, store: Store, default_mode: str):
        self.store = store
        self.default_mode = default_mode
        self._cache: dict[int, dict[str, str]] = {}

    async def get(self, chat_id: int) -> ChatSettings:
        raw = self._cache.get(chat_id)
        if raw is None:
            raw = await self.store.get_settings(chat_id)
            self._cache[chat_id] = raw
        return ChatSettings(chat_id, raw, self.default_mode)

    async def set(self, chat_id: int, key: str, raw_value: str, by: int | None) -> str:
        value = validate(key, raw_value)
        await self.store.set_setting(chat_id, key, value, by)
        self._cache.setdefault(chat_id, {})[key] = value
        return value

    async def reset(self, chat_id: int, key: str, by: int | None = None) -> None:
        await self.store.delete_setting(chat_id, key, by)
        self._cache.get(chat_id, {}).pop(key, None)

    def invalidate(self, chat_id: int) -> None:
        self._cache.pop(chat_id, None)

    # Inline menu

    def menu_markup(self, cs: ChatSettings, group: str | None) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if group is None:
            for gid, label in GROUPS.items():
                rows.append([InlineKeyboardButton(text=label, callback_data=f"st:g:{cs.chat_id}:{gid}")])
            rows.append([InlineKeyboardButton(text="Close", callback_data=f"st:x:{cs.chat_id}")])
            return InlineKeyboardMarkup(inline_keyboard=rows)
        for spec in SPECS.values():
            if spec.group != group or not spec.menu_options:
                continue
            current = cs.raw(spec.key)
            buttons = []
            for opt in spec.menu_options:
                mark = "• " if opt == current else ""
                buttons.append(InlineKeyboardButton(
                    text=f"{mark}{opt}", callback_data=f"st:s:{cs.chat_id}:{spec.key}:{opt}"
                ))
            rows.append(buttons)
        rows.append([InlineKeyboardButton(text="« Back", callback_data=f"st:g:{cs.chat_id}:_")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def shown(spec: Spec, value: str) -> str:
        """Value in plain words: actions become 'mute for 24h', empty strings 'none'."""
        if spec.kind == "action":
            from .text import action_words
            return action_words(value)
        if spec.kind == "languages":
            from .language import name
            return ", ".join(name(c) for c in value.split(",") if c) or "all"
        return value or "none"

    @staticmethod
    def _mode_line(cs: ChatSettings) -> str:
        mode = cs.raw("mode")
        meaning = "spam is deleted and punished" if mode == "enforce" else "report only, nothing deleted"
        return f"<b>Mode: {mode}</b> · {meaning}"

    def menu_text(self, cs: ChatSettings, group: str | None, title: str | None) -> str:
        name = html.escape(title or str(cs.chat_id))
        if group is None:
            return "\n".join([f"⚙️ <b>{name}</b> · settings", self._mode_line(cs), "",
                              "Pick a section below. The dot marks the current value.",
                              "No button for it? Send /set followed by the name and value, right here.",
                              "Everything as text: /settings list"])
        specs = [s for s in SPECS.values() if s.group == group]
        lines = [f"⚙️ <b>{name}</b> · {GROUPS[group]}",
                 f"<b>{len(specs)} setting{'s' if len(specs) != 1 else ''}</b>"]
        for spec in specs:
            lines.append("")
            lines.append(f"<b>{html.escape(spec.label)}: {html.escape(self.shown(spec, cs.raw(spec.key)))}</b> · <code>{spec.key}</code>")
            lines.append(html.escape(spec.help))
        lines.append("")
        lines.append("Tap a value below to change it, or send /set followed by the name and value.")
        return "\n".join(lines)

    def list_text(self, cs: ChatSettings, title: str | None = None) -> str:
        out = [f"⚙️ <b>{html.escape(title or str(cs.chat_id))}</b> · all settings", self._mode_line(cs)]
        for gid, label in GROUPS.items():
            out += ["", f"<b>{label}</b>"]
            for spec in SPECS.values():
                if spec.group == gid and spec.key != "mode":
                    out.append(f"{html.escape(spec.label)}: {html.escape(self.shown(spec, cs.raw(spec.key)))} · <code>{spec.key}</code>")
        out += ["", "Change one: send /set followed by the grey name and the value, e.g. <code>/set captcha off</code>",
                "Back to default: /unset followed by the name. What each one means: /help settings"]
        return "\n".join(out)
