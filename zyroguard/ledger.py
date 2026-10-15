"""The owner's activity record: what happens to the bot's groups and what their admins do.

Every event is saved in the owner_events table first (store.py records admin changes in the same
step that makes them), so a restart or an outage loses nothing. flush() sends whatever is unsent,
oldest first, to OWNER_LEDGER_URL: a private webhook (an n8n workflow) that writes a Google Sheet.
The webhook receives ready-made Google Sheets API calls; it only adds the spreadsheet id and the
Google sign-in, so the layout of the sheet is decided and tested here.

The sheet: "Groups" and "Access" (the state right now, rewritten every hour), and one tab per
month ("Oct 2026") with every event in Manila time, a daily summary of what the bot did on its
own, and a month-end snapshot. Important events also reach the owner on Telegram right away.
Nothing here ever copies the text of members' messages.
"""

from __future__ import annotations

import html
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from .app import App
from .store import from_iso, utcnow

log = logging.getLogger("zyroguard.ledger")

MANILA = ZoneInfo("Asia/Manila")

# kind -> (category, label, alert the owner on Telegram)
KINDS: dict[str, tuple[str, str, bool]] = {
    "bot_added": ("Access", "Bot added", True),
    "bot_refused": ("Access", "Refused", True),
    "bot_removed": ("Access", "Bot removed", True),
    "bot_left": ("Access", "Left: access ended", False),
    "bot_rights": ("Access", "Rights changed", True),          # alert only when rights were lost
    "access_given": ("Access", "Access given", False),
    "access_changed": ("Access", "Access changed", False),
    "access_revoked": ("Access", "Access revoked", False),
    "access_expired": ("Access", "Access expired", True),
    "purge": ("Access", "Group data deleted", False),
    "admin_action": ("Admin action", "Action", False),
    "undo": ("Admin action", "Undo", False),
    "setting": ("Admin change", "Setting changed", False),
    "setting_reset": ("Admin change", "Setting reset", False),
    "trusted_add": ("Admin change", "Trusted member added", False),
    "trusted_remove": ("Admin change", "Trusted member removed", False),
    "botadmin_add": ("Admin change", "Bot admin added", False),
    "botadmin_remove": ("Admin change", "Bot admin removed", False),
    "domain_allow": ("Admin change", "Website always allowed", False),
    "domain_block": ("Admin change", "Website always removed", False),
    "domain_remove": ("Admin change", "Website rule removed", False),
    "rule_add": ("Admin change", "Custom rule added", False),
    "rule_delete": ("Admin change", "Custom rule removed", False),
    "rule_off": ("Bot", "Custom rule switched off", False),
    "teach": ("Admin change", "Example taught", False),
    "forget": ("Admin change", "Example forgotten", False),
    "pause": ("Admin change", "Filtering paused", False),
    "resume": ("Admin change", "Filtering resumed", False),
    "file_add": ("ZyroMod", "File added", False),
    "file_remove": ("ZyroMod", "File removed", False),
    "files_clear": ("ZyroMod", "All files removed", False),
    "daily_summary": ("Bot", "Daily summary", False),
    "month_snapshot": ("Snapshot", "Month-end snapshot", False),
}

EVENT_COLS = ["Date", "Time", "Category", "Event", "Group", "Group ID", "Who", "Who ID", "Target", "Target ID",
              "Details", "Old value", "New value", "Mode", "Ref"]
EVENT_WIDTHS = [90, 50, 100, 160, 220, 130, 170, 100, 170, 100, 420, 160, 160, 90, 50]
GROUP_COLS = ["Group", "Group ID", "Status", "Bot working", "Mode", "ZyroMod", "Added on", "Added by",
              "Added by ID", "Allowed by", "Access ends", "Days left", "Members", "Members seen",
              "Bot actions 7d", "Admin actions 7d", "AI calls 24h", "Support files", "Custom rules",
              "Trusted members", "Public link", "Last message", "Last action", "Last checked"]
ACCESS_COLS = ["Person", "Username", "Telegram ID", "Status", "Groups used", "Their groups", "AI calls 24h",
               "Daily AI cap", "Given on", "Given by", "Ends", "Days left", "Note", "Last checked"]

# colours for the Category/Event column of a new month tab: (starts with, background, text)
_COLORS = {"green": ("E6F4EA", "137333"), "amber": ("FEF7E0", "B06000"), "red": ("FCE8E6", "C5221F"),
           "gray": ("F1F3F4", "5F6368"), "blue": ("E8F0FE", "1967D2")}
EVENT_COLORS = [("Bot added", "green"), ("Access given", "green"), ("Access changed", "green"),
                ("Refused", "red"), ("Bot removed", "red"), ("Rights changed", "amber"),
                ("Left: access ended", "amber"), ("Access expired", "gray"), ("Access revoked", "gray"),
                ("Group data deleted", "gray"), ("Daily summary", "blue"), ("Month-end snapshot", "blue")]

RIGHTS = {"can_delete_messages": "Delete messages", "can_restrict_members": "Ban users",
          "can_invite_users": "Invite users"}


@dataclass
class Status:
    """What /ledger shows: is the webhook working?"""
    last_ok: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    last_snapshot: datetime | None = None
    names: dict[int, str] = field(default_factory=dict)
    names_at: datetime = field(default_factory=utcnow)
    tabs: set[str] = field(default_factory=set)   # tabs known to exist since this process started


STATUS = Status()
_session: aiohttp.ClientSession | None = None


# ---- recording ---------------------------------------------------------------

def _person(user: Any) -> tuple[int | None, str | None]:
    """(id, 'Full Name (@username)') from an aiogram User, or (id, None) from a plain id."""
    if user is None:
        return None, None
    if isinstance(user, int):
        return user, None
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
    if getattr(user, "username", None):
        name = f"{name} (@{user.username})" if name else f"@{user.username}"
    return getattr(user, "id", None), name or None


async def note(app: App, kind: str, chat_id: int | None = None, *, actor: Any = None, target: Any = None,
               alert: bool | None = None, **detail: Any) -> int:
    """Record one event (never raises) and, for the important ones, tell the owner on Telegram."""
    actor_id, actor_name = _person(actor)
    target_id, target_name = _person(target)
    try:
        event_id = await app.store.owner_event_add(kind, chat_id, actor_id, actor_name=actor_name,
                                                   target_id=target_id, target_name=target_name, **detail)
    except Exception as e:  # noqa: BLE001
        log.warning("could not record %s: %s", kind, e)
        return 0
    wanted = KINDS.get(kind, ("", "", False))[2] if alert is None else alert
    if kind == "bot_rights" and alert is None:
        wanted = bool(detail.get("lost"))
    if wanted:
        await _alert(app, kind, chat_id, actor_id, actor_name, target_id, target_name, detail)
    return event_id


def _id(v: int | None) -> str:
    return f"<code>{v}</code>" if v is not None else "unknown"


async def _alert(app: App, kind: str, chat_id: int | None, actor_id: int | None, actor_name: str | None,
                 target_id: int | None, target_name: str | None, d: dict[str, Any]) -> None:
    title = html.escape(d.get("title") or await _title(app, chat_id) or "a group")
    who = html.escape(actor_name or "someone")
    if kind == "bot_added":
        lines = ["🛡 <b>ZyroGuard was added to a new group</b>", "",
                 f"Group: <b>{title}</b> (ID {_id(chat_id)})",
                 f"Added by: {who} (ID {_id(actor_id)})",
                 f"Allowed by: {html.escape(d.get('how', ''))}",
                 "Rights: " + ("all good" if d.get("rights_ok") else
                               "missing " + html.escape(", ".join(d.get("missing") or ["admin"]))),
                 "", "See every group with /groups."]
    elif kind == "bot_refused":
        lines = ["⚠️ <b>Someone tried to add ZyroGuard without access</b>", "",
                 f"Group: <b>{title}</b> (ID {_id(chat_id)})",
                 f"Person: {who} (ID {_id(actor_id)})",
                 f"Reason: {html.escape(d.get('reason', 'no access'))}",
                 "I left the group.", "",
                 (f"To let them in, give them access with /permit {actor_id}" if actor_id else
                  "Nothing else to do unless you know who it was.")]
    elif kind == "bot_removed":
        lines = [f"⚠️ <b>ZyroGuard was removed from {title}</b>", "",
                 f"Removed by: {who} (ID {_id(actor_id)})", f"Group ID: {_id(chat_id)}",
                 "It no longer guards that group.", "", "Check the history with /history."]
    elif kind == "bot_rights":
        lines = [f"⚠️ <b>ZyroGuard lost admin rights in {title}</b>", "",
                 "Missing: " + html.escape(", ".join(d.get("missing") or ["all admin rights"])),
                 f"Changed by: {who}", "It can't remove spam there until the rights are back.", "",
                 "Ask the group's admins to fix it, then run /diag there."]
    elif kind == "access_expired":
        groups = d.get("groups") or []
        lines = [f"⏰ <b>Access ended for {html.escape(target_name or str(target_id))}</b>", "",
                 f"Telegram ID: {_id(target_id)}",
                 ("I left: " + html.escape(", ".join(groups))) if groups else "They had no groups.",
                 "", f"Renew it with /permit {target_id}" if target_id else ""]
    else:
        category, label, _ = KINDS.get(kind, ("", kind, False))
        lines = [f"<b>{html.escape(label)}</b> · {title}"]
    text = "\n".join(line for line in lines if line is not None).strip()
    for owner in app.config.owner_ids:
        try:
            await app.bot.send_message(owner, text, parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            log.warning("owner alert to %s failed: %s", owner, e)


# ---- names, titles and formatting ------------------------------------------------

async def _title(app: App, chat_id: int | None) -> str | None:
    if chat_id is None:
        return None
    row = await app.store.get_chat(chat_id)
    return row["title"] if row and row["title"] else None


async def _name(app: App, user_id: int | None, given: str | None = None) -> str:
    """Best readable name for a person: the one recorded with the event, the bot's own chat
    with them, or the last group message they sent. Cached for an hour."""
    if given:
        return given
    if user_id is None:
        return ""
    if utcnow() - STATUS.names_at > timedelta(hours=1):
        STATUS.names.clear()
        STATUS.names_at = utcnow()
    if user_id in STATUS.names:
        return STATUS.names[user_id]
    name = ""
    try:
        chat = await app.bot.get_chat(user_id)
        _, name = _person(chat)
        name = name or ""
    except Exception:  # noqa: BLE001
        pass
    if not name:
        display, username = await app.store.known_name(user_id)
        name = " ".join(p for p in (display, f"(@{username})" if username and display else
                                    (f"@{username}" if username else None)) if p)
    if app.config.is_owner(user_id):
        name = f"{name} (owner)" if name else "Owner"
    STATUS.names[user_id] = name
    return name


def manila(dt: datetime | None) -> datetime | None:
    return dt.astimezone(MANILA) if dt else None


def stamp(dt: datetime | None) -> str:
    m = manila(dt)
    return m.strftime("%Y-%m-%d %H:%M") if m else ""


def month_tab(dt: datetime) -> str:
    return manila(dt).strftime("%b %Y")


def _dur(seconds: Any) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size and s % size == 0:
            return f"{s // size}{unit}"
    return f"{s}s"


def _clip(v: Any, n: int = 300) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _setting_label(key: str) -> tuple[str, str]:
    """(category, 'Label (key)') for a setting."""
    from .settings import SPECS
    spec = SPECS.get(key)
    category = "ZyroMod" if spec is not None and spec.group == "support" or key.startswith("support") else "Admin change"
    return category, f"{spec.label} ({key})" if spec else key


def _default(key: str) -> str:
    from .settings import SPECS
    spec = SPECS.get(key)
    return f"default ({spec.default})" if spec and spec.default not in ("", None) else "default"


async def event_row(app: App, r: Any) -> list[str]:
    """One owner_events row as a sheet row (EVENT_COLS)."""
    kind = r["kind"]
    d: dict[str, Any] = json.loads(r["detail"] or "{}")
    ts = from_iso(r["ts"]) or utcnow()
    category, label, _ = KINDS.get(kind, ("Other", kind, False))
    chat_id = r["chat_id"]
    title = d.get("title") or await _title(app, chat_id) or ""
    who = await _name(app, r["actor_id"], r["actor_name"]) if r["actor_id"] is not None or r["actor_name"] else (
        "ZyroGuard" if kind in {"rule_off", "daily_summary", "bot_left", "access_expired", "month_snapshot"} else "")
    target = await _name(app, r["target_id"], r["target_name"]) if r["target_id"] is not None or r["target_name"] else ""
    details, old, new, mode, ref = "", "", "", d.get("mode", ""), d.get("ref", "")

    if kind == "admin_action":
        label = (d.get("action") or "action").capitalize() + (f" {_dur(d.get('duration_s'))}" if d.get("duration_s") else "")
        details = _clip(d.get("reason"))
    elif kind == "undo":
        details = f"Undid #{d.get('ref')}: {d.get('action')} by {d.get('source')} ({_clip(d.get('reason'), 160)})"
    elif kind in {"setting", "setting_reset"}:
        category, details = _setting_label(d.get("key", ""))
        old = _clip(d.get("old")) if d.get("old") is not None else _default(d.get("key", ""))
        new = _clip(d.get("new")) if d.get("new") is not None else _default(d.get("key", ""))
    elif kind in {"domain_allow", "domain_block", "domain_remove"}:
        details = d.get("domain", "")
    elif kind in {"rule_add", "rule_delete", "rule_off"}:
        act = d.get("action") or ("strike ladder" if kind == "rule_add" else "")
        details = f"Rule #{d.get('rule')}: {_clip(d.get('pattern'), 200)}" + (
            f" → {act}{' ' + _dur(d.get('duration_s')) if d.get('duration_s') else ''}" if act else "")
        if kind == "rule_off":
            details += " (too slow, switched off automatically)"
    elif kind in {"teach", "forget"}:
        what = "spam" if d.get("label") == "spam" else "not spam"
        details = (f"Marked a message as {what}" + (" with Undo" if d.get("via") == "undo" else "")
                   if kind == "teach" else f"Example of {what} no longer counts")
        ref = d.get("example", ref)
    elif kind == "file_add":
        details = f"{d.get('file')}: {d.get('sections')} sections" + (
            f", {d.get('pages')} pages" if d.get("pages") else "") + (" (replaced the old version)" if d.get("replaced") else "")
    elif kind == "file_remove":
        details = d.get("file", "")
    elif kind == "files_clear":
        details = f"{d.get('files')} files removed, support turned off"
    elif kind == "pause":
        details = f"Paused for {d.get('duration', '')}"
    elif kind in {"bot_added", "bot_refused", "bot_removed", "bot_left", "bot_rights"}:
        details = {
            "bot_added": f"Allowed by: {d.get('how', '')}. Rights: " + (
                "all good" if d.get("rights_ok") else "missing " + ", ".join(d.get("missing") or ["admin"])),
            "bot_refused": f"{d.get('reason', 'no access')}. Left the group",
            "bot_removed": "Removed from the group",
            "bot_left": f"Access of {target or d.get('owner', '')} ended",
            "bot_rights": ("Lost: " + ", ".join(d.get("missing") or ["admin"])) if d.get("lost") else
                          ("All rights back" if d.get("rights_ok") else "Now missing: " + ", ".join(d.get("missing") or [])),
        }[kind]
        old, new = d.get("old_status", ""), d.get("new_status", "")
    elif kind in {"access_given", "access_changed"}:
        ends = from_iso(d.get("expires_at")) if d.get("expires_at") else None
        details = (f"{d.get('max_groups')} group{'s' if d.get('max_groups') != 1 else ''}, "
                   + (f"until {stamp(ends)}" if ends else "no end date")
                   + (f", note: {d.get('note')}" if d.get("note") else ""))
        old, new = d.get("old", ""), d.get("new", "")
    elif kind == "access_revoked":
        details = "Access taken back" + (f"; left and deleted: {', '.join(d.get('groups'))}" if d.get("groups") else "")
    elif kind == "access_expired":
        details = "End date passed" + (f"; left: {', '.join(d.get('groups'))}" if d.get("groups") else "; no groups")
    elif kind == "purge":
        details = f"{d.get('rows', 0)} records deleted"
    elif kind == "daily_summary":
        details = d.get("text", "")
    else:
        details = _clip(json.dumps(d, ensure_ascii=False))

    m = manila(ts)
    return [m.strftime("%Y-%m-%d"), m.strftime("%H:%M"), category, label, title,
            str(chat_id) if chat_id is not None else "", who,
            str(r["actor_id"]) if r["actor_id"] is not None else "", target,
            str(r["target_id"]) if r["target_id"] is not None else "", details, old, new,
            {"shadow": "Report-only", "enforce": "Enforce"}.get(mode, mode), f"#{ref}" if ref else ""]


# ---- Google Sheets calls for the webhook -------------------------------------------

def _range(tab: str, cell: str = "A1") -> str:
    return quote(f"'{tab}'!{cell}", safe="")


def _sheet_id(tab: str) -> int:
    """Stable id for a month tab ("Oct 2026" -> 202610) so one request can create and format it."""
    try:
        return int(datetime.strptime(tab, "%b %Y").strftime("%Y%m"))
    except ValueError:
        return 900000 + sum(ord(c) for c in tab)


def _rgb(hex6: str) -> dict[str, float]:
    return {"red": int(hex6[0:2], 16) / 255, "green": int(hex6[2:4], 16) / 255, "blue": int(hex6[4:6], 16) / 255}


def add_tab_op(tab: str, cols: list[str], widths: list[int], color_col: str | None = None,
               colors: list[tuple[str, str]] = ()) -> dict[str, Any]:
    """Create a tab with a bold frozen header, column widths, a filter and coloured labels. When the
    tab already exists Google refuses the whole request, which is what we want (ok_if)."""
    sid = _sheet_id(tab)
    reqs: list[dict[str, Any]] = [
        {"addSheet": {"properties": {"sheetId": sid, "title": tab,
                                     "gridProperties": {"frozenRowCount": 1, "columnCount": max(26, len(cols))}}}},
        {"updateCells": {"start": {"sheetId": sid, "rowIndex": 0, "columnIndex": 0}, "fields": "userEnteredValue,userEnteredFormat",
                         "rows": [{"values": [{"userEnteredValue": {"stringValue": c},
                                               "userEnteredFormat": {"textFormat": {"bold": True},
                                                                     "backgroundColor": _rgb("EFEFEF")}} for c in cols]}]}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0,
                                                 "endColumnIndex": len(cols)}}}},
    ]
    for i, w in enumerate(widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    if color_col:
        ci = cols.index(color_col)
        for prefix, color in colors:
            bg, fg = _COLORS[color]
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": ci, "endColumnIndex": ci + 1}],
                "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": prefix}]},
                                "format": {"backgroundColor": _rgb(bg), "textFormat": {"foregroundColor": _rgb(fg)}}}}}})
    return {"method": "POST", "path": ":batchUpdate", "body": {"requests": reqs}, "ok_if": "already exists"}


def append_op(tab: str, rows: list[list[str]]) -> dict[str, Any]:
    return {"method": "POST", "path": f"/values/{_range(tab)}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            "body": {"majorDimension": "ROWS", "values": rows}}


def replace_ops(tab: str, rows: list[list[str]]) -> list[dict[str, Any]]:
    return [{"method": "POST", "path": f"/values/{_range(tab, 'A1:AZ5000')}:clear", "body": {}},
            {"method": "PUT", "path": f"/values/{_range(tab)}?valueInputOption=RAW",
             "body": {"majorDimension": "ROWS", "values": rows}}]


async def post(app: App, ops: list[dict[str, Any]], what: str) -> bool:
    """Send Sheets calls to the owner webhook. True only when every call went through."""
    global _session
    url = app.config.owner_ledger_url
    if not url or not ops:
        return False
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    headers = {"X-ZyroGuard-Ledger-Secret": app.config.owner_ledger_secret or ""}
    try:
        async with _session.post(url, json={"ops": ops, "what": what}, headers=headers) as r:
            body = await r.text()
            try:
                ok = r.status == 200 and bool(json.loads(body).get("ok"))
            except (ValueError, AttributeError):
                ok = False
            if not ok:
                raise RuntimeError(f"webhook answered {r.status}: {body[:200]}")
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"[:300]
        if msg != STATUS.last_error or not STATUS.last_error_at or utcnow() - STATUS.last_error_at > timedelta(hours=1):
            await app.store.log_error("ledger", msg)   # once an hour per kind of failure, not every minute
            STATUS.last_error_at = utcnow()
        STATUS.last_error = msg
        log.warning("owner ledger %s failed: %s", what, msg)
        return False
    STATUS.last_ok, STATUS.last_error = utcnow(), None
    return True


async def close() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


# ---- sending events ------------------------------------------------------------

async def flush(app: App, limit: int = 300) -> int:
    """Send unsent events, oldest first, grouped by month tab. Returns how many were sent."""
    if not app.config.owner_ledger_url:
        return 0
    rows = await app.store.owner_events_unsent(limit)
    if not rows:
        return 0
    by_tab: dict[str, list[list[str]]] = {}
    snapshots: list[tuple[str, list[list[str]]]] = []
    for r in rows:
        ts = from_iso(r["ts"]) or utcnow()
        tab = month_tab(ts)
        if r["kind"] == "month_snapshot":
            snapshots.append((tab, snapshot_block(json.loads(r["detail"] or "{}"))))
            by_tab.setdefault(tab, [])
            continue
        try:
            by_tab.setdefault(tab, []).append(await event_row(app, r))
        except Exception as e:  # noqa: BLE001
            log.warning("cannot format event %s: %s", r["id"], e)
            by_tab.setdefault(tab, []).append([stamp(ts)[:10], stamp(ts)[11:], "Other", r["kind"], "", "", "", "", "", "",
                                               _clip(r["detail"]), "", "", "", ""])
    ops: list[dict[str, Any]] = []
    for tab, tab_rows in by_tab.items():
        if tab not in STATUS.tabs:
            ops.append(add_tab_op(tab, EVENT_COLS, EVENT_WIDTHS, "Event", EVENT_COLORS))
        if tab_rows:
            ops.append(append_op(tab, tab_rows))
    for tab, block in snapshots:
        ops.append(append_op(tab, block))
    ids = [int(r["id"]) for r in rows]
    ok = await post(app, ops, f"{len(rows)} events")
    await app.store.owner_events_mark(ids, ok)
    if ok:
        STATUS.tabs.update(by_tab)
    return len(ids) if ok else 0


# ---- the state right now: Groups and Access ------------------------------------------

def _days_left(ends: datetime | None) -> str:
    if not ends:
        return ""
    return str(max(0, math.ceil((ends - utcnow()).total_seconds() / 86400)))


async def _bot_state(app: App, chat_id: int, is_log_chat: bool) -> tuple[str, int | None, str]:
    """(bot working, Telegram member count, public link) for an active group."""
    working, count, link = "", None, ""
    try:
        member = await app.bot.get_chat_member(chat_id, app.bot_id)
        status = str(getattr(getattr(member, "status", ""), "value", getattr(member, "status", ""))).lower()
        if status.endswith("administrator") or status.endswith("creator"):
            missing = [label for attr, label in RIGHTS.items() if not getattr(member, attr, True)]
            working = "OK" if not missing else "Missing: " + ", ".join(missing)
        elif status.endswith("member"):
            working = "OK (log chat, no admin needed)" if is_log_chat else "Member only, not admin"
        else:
            working = "Not in group"
    except Exception as e:  # noqa: BLE001
        working = "Not in group" if "not found" in str(e).lower() or "kicked" in str(e).lower() else f"Can't check: {_clip(e, 80)}"
    try:
        count = int(await app.bot.get_chat_member_count(chat_id))
    except Exception:  # noqa: BLE001
        count = None
    try:
        chat = await app.bot.get_chat(chat_id)
        if getattr(chat, "username", None):
            link = f"https://t.me/{chat.username}"
    except Exception:  # noqa: BLE001
        pass
    return working, count, link


async def groups_rows(app: App) -> list[list[str]]:
    now = utcnow()
    week, day = now - timedelta(days=7), now - timedelta(days=1)
    chats = await app.store.list_chats(active_only=False)
    settings_log_chats = set()
    for c in chats:
        cs = await app.settings.get(int(c["chat_id"]))
        if cs.raw("modlog_chat_id"):
            try:
                settings_log_chats.add(int(cs.raw("modlog_chat_id")))
            except ValueError:
                pass
    out = [GROUP_COLS]
    for c in chats:
        cid = int(c["chat_id"])
        cs = await app.settings.get(cid)
        owner = c["owner_user_id"]
        permit = await app.store.get_permit(int(owner)) if owner else None
        ends = from_iso(permit["expires_at"]) if permit and permit["expires_at"] else None
        if owner and not app.config.is_owner(int(owner)):
            allowed_by = "User access"
            access_end = "Ended" if permit and not permit["active"] else (stamp(ends) if ends else "No end")
        elif cid in app.config.allowed_chat_ids:
            allowed_by, access_end = "Allowlist", "No end"
        else:
            allowed_by, access_end = "You", "No end"
        added_by_id = c["added_by"]
        if added_by_id is None and allowed_by == "You" and app.config.owner_ids:
            added_by_id = app.config.owner_ids[0]          # the bot only stays when the owner added it
        elif added_by_id is None and allowed_by == "User access":
            added_by_id = int(owner)
        added_by = await _name(app, added_by_id, c["added_by_name"]) if added_by_id else "Before tracking"
        if c["active"]:
            working, count, link = await _bot_state(app, cid, cid == app.config.modlog_chat_id or cid in settings_log_chats)
        else:
            working, count, link = "Not in group", None, ""
        files = len(await app.store.kb_documents(cid))
        rules = [r for r in await app.store.list_rules(cid, include_global=False)]
        trusted = len(await app.store.list_allowed_users(cid))
        bot_actions = await app.store.count_between("audit", cid, week, now, extra="AND actor_id IS NULL")
        admin_actions = await app.store.count_between("audit", cid, week, now, extra="AND actor_id IS NOT NULL")
        ai = await app.store.count_between("judge_usage", cid, day, now)
        lm, la = await app.store.chat_last_times(cid)
        out.append([
            c["title"] or str(cid), str(cid), "Active" if c["active"] else "Left", working,
            {"shadow": "Report-only", "enforce": "Enforce"}.get(cs.raw("mode"), cs.raw("mode")) if c["active"] else "",
            ("On" if cs.bool("support") else "Off") if c["active"] else "",
            stamp(from_iso(c["added_at"])), added_by, str(added_by_id or (owner or "")),
            allowed_by, access_end, _days_left(ends) if allowed_by == "User access" else "",
            str(count) if count is not None else "", str(await app.store.member_count(cid)),
            str(bot_actions), str(admin_actions), str(ai), str(files), str(len(rules)), str(trusted), link,
            stamp(lm), stamp(la), stamp(now)])
    return out


async def access_rows(app: App) -> list[list[str]]:
    now = utcnow()
    out = [ACCESS_COLS]
    for p in await app.store.list_permits():
        uid = int(p["user_id"])
        ends = from_iso(p["expires_at"]) if p["expires_at"] else None
        status = "Revoked" if not p["active"] else ("Expired" if ends and ends < now else "Active")
        groups = await app.store.chats_owned_by(uid)
        name = await _name(app, uid)
        out.append([
            name.split(" (@")[0] if name else "", f"@{p['username']}" if p["username"] else "", str(uid), status,
            f"{len(groups)} of {p['max_groups']}", ", ".join(g["title"] or str(g["chat_id"]) for g in groups),
            str(await app.store.ai_calls_for_owner_since(uid, now - timedelta(days=1))), str(p["daily_ai_cap"]),
            stamp(from_iso(p["created_at"])), await _name(app, p["created_by"]) if p["created_by"] else "",
            stamp(ends) if ends else "No end", _days_left(ends) if status == "Active" else ("0" if ends else ""),
            p["note"] or "", stamp(now)])
    return out


async def snapshot(app: App) -> bool:
    """Rewrite the Groups and Access tabs with the state right now."""
    if not app.config.owner_ledger_url:
        return False
    groups, access = await groups_rows(app), await access_rows(app)
    ops = [add_tab_op(tab, cols, [220] + [120] * (len(cols) - 1))
           for tab, cols in (("Groups", GROUP_COLS), ("Access", ACCESS_COLS)) if tab not in STATUS.tabs]
    ops += [*replace_ops("Groups", groups), *replace_ops("Access", access)]
    ok = await post(app, ops, "snapshot")
    if ok:
        STATUS.last_snapshot = utcnow()
        STATUS.tabs.update({"Groups", "Access"})
    return ok


def snapshot_block(d: dict[str, Any]) -> list[list[str]]:
    """Rows for the month-end snapshot appended under that month's events."""
    taken = from_iso(d.get("taken")) or utcnow()
    groups, access = d.get("groups") or [GROUP_COLS], d.get("access") or [ACCESS_COLS]
    active = sum(1 for g in groups[1:] if len(g) > 2 and g[2] == "Active")
    users = sum(1 for a in access[1:] if len(a) > 3 and a[3] == "Active")
    return [[""], [f"Snapshot at the end of {d.get('month', '')}, taken {stamp(taken)} (Manila)",
                   f"{active} active groups", f"{users} users with active access"],
            *groups, [""], *access]


# ---- daily summary and month end ----------------------------------------------------

_SOURCE = {"rule": "rules", "judge": "AI judge", "flood": "flood", "captcha": "captcha", "cas": "known spammer list",
           "join": "join checks", "safety": "safety check", "similar": "meaning match", "forward": "forwards"}


async def summary_text(app: App, chat_id: int, start: datetime, end: datetime, audit: list[Any]) -> str | None:
    rows = [a for a in audit if int(a["chat_id"]) == chat_id and a["actor_id"] is None]
    joined = await app.store.count_between("members", chat_id, start, end, ts_col="joined_at")
    verified = await app.store.count_between("members", chat_id, start, end, ts_col="verified_at")
    answered = await app.store.count_between("kb_questions", chat_id, start, end, extra="AND outcome='answered'")
    asked = await app.store.count_between("kb_questions", chat_id, start, end)
    ai = await app.store.count_between("judge_usage", chat_id, start, end)
    if not rows and not joined and not asked and not ai:
        return None
    enforce = [a for a in rows if a["mode"] == "enforce" and a["action"] != "none"]
    shadow = [a for a in rows if a["mode"] == "shadow" and a["action"] != "none"]
    by_action: dict[str, int] = {}
    for a in enforce:
        by_action[a["action"]] = by_action.get(a["action"], 0) + 1
    by_source: dict[str, int] = {}
    for a in rows:
        if a["action"] != "none":
            key = _SOURCE.get(a["source"], a["source"])
            by_source[key] = by_source.get(key, 0) + 1
    words = {"delete": "removed", "mute": "muted", "ban": "banned", "kick": "kicked", "warn": "warned", "flag": "flagged"}
    parts = []
    if enforce:
        parts.append(", ".join(f"{n} {words.get(k, k)}" for k, n in sorted(by_action.items(), key=lambda x: -x[1])))
    if shadow:
        parts.append(f"{len(shadow)} would-be actions in report-only mode")
    if by_source:
        parts.append("by " + ", ".join(f"{k} {n}" for k, n in sorted(by_source.items(), key=lambda x: -x[1])))
    undone = sum(1 for a in rows if a["undone_at"])
    if undone:
        parts.append(f"{undone} undone by admins")
    if joined or verified:
        parts.append(f"{joined} joined, {verified} passed the captcha")
    if asked:
        parts.append(f"{asked} questions, {answered} answered from files")
    if ai:
        parts.append(f"{ai} AI calls")
    return "; ".join(parts) if parts else "Quiet day"


async def daily_summaries(app: App, now: datetime | None = None) -> int:
    """After 00:05 Manila, one summary row per group for yesterday (only groups where something happened)."""
    local = (now or utcnow()).astimezone(MANILA)
    if local.hour == 0 and local.minute < 5:
        return 0
    day = (local - timedelta(days=1)).date()
    key = f"summary:{day.isoformat()}"
    if await app.store.ledger_get(key):
        return 0
    start = datetime(day.year, day.month, day.day, tzinfo=MANILA).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    audit = await app.store.audit_between(start, end)
    made = 0
    for c in await app.store.list_chats(active_only=False):
        cid = int(c["chat_id"])
        text = await summary_text(app, cid, start, end, audit)
        if text:
            await app.store.owner_event_add("daily_summary", cid, None, ts=end - timedelta(minutes=1), text=text,
                                            day=day.isoformat())
            made += 1
    await app.store.ledger_set(key, str(made))
    return made


async def month_end(app: App, now: datetime | None = None) -> bool:
    """On the last day of the month from 23:55 Manila (or later, if the bot was down then),
    record the Groups and Access tables at the bottom of that month's tab."""
    local = (now or utcnow()).astimezone(MANILA)
    tomorrow = local + timedelta(days=1)
    if tomorrow.month != local.month and (local.hour, local.minute) >= (23, 55):
        month_start = local.replace(day=1)
    else:
        month_start = (local.replace(day=1) - timedelta(days=1)).replace(day=1)   # catch up the month before
    label = month_start.strftime("%b %Y")
    key = f"month:{month_start.strftime('%Y-%m')}"
    if await app.store.ledger_get(key):
        return False
    if await app.store.ledger_get("started") is None:
        return False   # never back-fill months from before the record started
    first_day_next = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    ts = first_day_next.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=1)
    if ts.astimezone(timezone.utc) < from_iso(await app.store.ledger_get("started")):
        await app.store.ledger_set(key, "skipped")   # the record started after this month ended
        return False
    await app.store.owner_event_add("month_snapshot", None, None, ts=ts.astimezone(timezone.utc), month=label,
                                    taken=utcnow().isoformat(), groups=await groups_rows(app),
                                    access=await access_rows(app))
    await app.store.ledger_set(key, utcnow().isoformat())
    return True


async def start(app: App) -> None:
    """First run: remember when the record started and which permits had already ended,
    so nothing from before shows up again as new."""
    if await app.store.ledger_get("started") is None:
        await app.store.ledger_set("started", utcnow().isoformat())
    for p in await app.store.list_permits():
        if p["expires_at"] and from_iso(p["expires_at"]) < utcnow():
            key = f"expired:{p['user_id']}:{p['expires_at']}"
            if await app.store.ledger_get(key) is None:
                await app.store.ledger_set(key, "before")
