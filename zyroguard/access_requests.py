"""Owner side of access requests.

A "Contact the admin" message reaches the owner with three buttons: Yes, No and Ban. Yes walks through
three short steps instead of typing the whole /permit line: how many groups (1, 2, 3 or a typed number),
for how long (typed, like 30d, 3mo or 1y, or No end) and an optional note. No declines quietly. Ban stops
that person's messages through Contact the admin (Undo is one tap away; giving them access lifts it).
/permit with only the person starts the same steps; the full one-line /permit still works at once.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import ledger
from .app import App
from .commands import SERVICE_ACCOUNTS
from .durations import DurationError
from .store import from_iso, utcnow

log = logging.getLogger("zyroguard.access")
router = Router(name="access_requests")

DRAFT_TTL_S = 30 * 60
MAX_GROUPS = 100
TIME_HELP = "Type how long, like 7d, 4w, 3mo or 1y (3 months works too), or tap No end."

# ---- how long access lasts ---------------------------------------------------------------

NO_END = {"no end", "noend", "none", "never", "forever", "unlimited", "permanent", "perm", "0"}
_PART = re.compile(r"(\d+)(years?|yrs?|y|months?|mos?|weeks?|wks?|w|days?|d)")
_DAYS = {"y": 365, "m": 30, "w": 7, "d": 1}


def access_length(raw: str) -> timedelta | None:
    """7d, 4w, 3mo, 1y, words like "3 months", or combined like 1y6mo. None means no end.
    Minutes and hours are refused on purpose: "3m" means three minutes everywhere else in the bot."""
    text = " ".join((raw or "").lower().split())
    if text in NO_END:
        return None
    compact = text.replace(" ", "")
    pos, days = 0, 0
    for mt in _PART.finditer(compact):
        if mt.start() != pos:
            break
        unit = mt.group(2)
        days += int(mt.group(1)) * _DAYS["m" if unit.startswith("mo") else unit[0]]
        pos = mt.end()
    if pos == 0 or pos != len(compact) or days == 0:
        raise DurationError(f"I could not read “{html.escape(raw.strip())}”. {TIME_HELP}")
    if days > 3660:
        raise DurationError("That is too long. Use up to 10 years, or No end.")
    return timedelta(days=days)


def length_words(length: timedelta | None) -> str:
    if length is None:
        return "no end date"
    ends = utcnow() + length
    return f"until {ends:%b %d, %Y}"


# ---- who can't get access ----------------------------------------------------------------

def refusal(app: App, user_id: int) -> str | None:
    if user_id in SERVICE_ACCOUNTS or user_id == app.bot_id:
        return ("That id is a Telegram system account (for example the one every anonymous admin posts as), "
                "not a person, so it can't get access.\n"
                "Ask the person to send /id to me in a private chat and use that number.")
    if app.config.is_owner(user_id):
        return "That is an owner account. Owners can add me to any group without access, so nothing to do."
    return None


async def label(app: App, user_id: int, username: str | None = None) -> str:
    if not username:
        _, username = await app.store.known_name(user_id)
    return f"@{html.escape(username)}" if username else f"<code>{user_id}</code>"


async def current_terms(app: App, user_id: int) -> str:
    """Their access right now, in words, or "" when they have none."""
    p = await app.store.get_permit(user_id)
    if not p or not p["active"]:
        return ""
    ends = from_iso(p["expires_at"]) if p["expires_at"] else None
    if ends and ends < utcnow():
        return ""
    used = len(await app.store.chats_owned_by(user_id))
    return (f"They have access now: {used} of {p['max_groups']} group(s) used, "
            + (f"until {ends:%b %d, %Y}" if ends else "no end date") + ".")


# ---- giving access (shared with the one-line /permit) --------------------------------------

async def grant(app: App, actor: Any, user_id: int, username: str | None, max_groups: int,
                length: timedelta | None, note: str | None) -> str:
    """Save the access, record it, lift a contact block, send the person the setup steps.
    Returns the owner's confirmation."""
    expires = utcnow() + length if length else None
    before = await app.store.get_permit(user_id)
    await app.store.add_permit(user_id, username, max_groups, expires, note, actor.id)
    await app.store.contact_unblock(user_id)
    await app.refresh_access()

    def _terms(groups, ends, n):
        ends_dt = from_iso(ends) if isinstance(ends, str) else ends
        return f"{groups} group(s), " + (f"until {ends_dt:%Y-%m-%d %H:%M} UTC" if ends_dt else "no end date") + (f", {n}" if n else "")

    changed = before is not None and before["active"] and not (
        before["expires_at"] and from_iso(before["expires_at"]) < utcnow())
    await ledger.note(app, "access_changed" if changed else "access_given", None, actor=actor,
                      target=user_id, max_groups=max_groups, expires_at=expires.isoformat() if expires else None,
                      note=note, username=username,
                      old=_terms(before["max_groups"], before["expires_at"], before["note"]) if before else "",
                      new=_terms(max_groups, expires, note))
    who = await label(app, user_id, username)
    until = f"until {expires:%b %d, %Y}" if expires else "with no end date"
    told = True
    try:
        await app.bot.send_message(
            user_id,
            "<b>You can now add ZyroGuard to your group.</b>\n"
            "1. Add @" + app.bot_username + " to the group.\n"
            "2. Make it an admin with Delete messages, Ban users and Invite users via link.\n"
            "3. Send /diag here to check everything.\n\n"
            "You manage the group from this private chat: /settings, /mode, /log and the rest. My reports come here too.",
            parse_mode="HTML")
    except Exception:  # noqa: BLE001
        told = False
    return (f"<b>Access {'changed' if changed else 'given'}</b> for {who}: up to {max_groups} group(s), {until}"
            + (f", note: {html.escape(note)}" if note else "") + ".\n"
            + ("I sent them the steps in their private chat." if told else
               "I could not message them yet (they have not started a chat with me). Tell them to press Start on @"
               + app.bot_username + " and add me to their group.") + "\n"
            "Take it back any time with /revoke.")


# ---- the steps -------------------------------------------------------------------------------

@dataclass
class Draft:
    user_id: int
    username: str | None
    step: str = "groups"              # groups (buttons), groups_typed, time, note
    groups: int = 1
    length: timedelta | None = None
    started: datetime = field(default_factory=utcnow)


_drafts: dict[int, Draft] = {}        # owner id -> the access being set up


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def request_markup(user_id: int) -> InlineKeyboardMarkup:
    """Under a contact message: give access, decline, or stop their messages."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn("✅ Yes", f"pm:yes:{user_id}"), _btn("No", f"pm:no:{user_id}"), _btn("🚫 Ban", f"pm:ban:{user_id}")]])


def _cancel_row(user_id: int) -> list[InlineKeyboardButton]:
    return [_btn("Cancel", f"pm:x:{user_id}")]


async def _say(app: App, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await app.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


async def ask_groups(app: App, owner_id: int, d: Draft) -> None:
    now = await current_terms(app, d.user_id)
    who = await label(app, d.user_id, d.username)
    await _say(app, owner_id,
               f"🎫 <b>Access for {who}</b>\n" + (f"{now} This replaces it.\n" if now else "")
               + "\n<b>How many groups</b> can they add me to?",
               InlineKeyboardMarkup(inline_keyboard=[
                   [_btn(str(n), f"pm:g:{d.user_id}:{n}") for n in (1, 2, 3)] + [_btn("Custom", f"pm:gc:{d.user_id}")],
                   _cancel_row(d.user_id)]))


async def ask_time(app: App, owner_id: int, d: Draft) -> None:
    await _say(app, owner_id,
               f"{d.groups} group{'s' if d.groups != 1 else ''}. <b>For how long?</b>\n{TIME_HELP}",
               InlineKeyboardMarkup(inline_keyboard=[[_btn("No end", f"pm:noend:{d.user_id}")], _cancel_row(d.user_id)]))


async def ask_note(app: App, owner_id: int, d: Draft) -> None:
    await _say(app, owner_id,
               f"{d.groups} group{'s' if d.groups != 1 else ''}, {length_words(d.length)}.\n"
               "<b>Add a note</b> for your records, like a plan or company name. Type it, or tap Skip.",
               InlineKeyboardMarkup(inline_keyboard=[[_btn("Skip", f"pm:skip:{d.user_id}")], _cancel_row(d.user_id)]))


async def finish(app: App, owner: Any, d: Draft, note: str | None) -> None:
    _drafts.pop(owner.id, None)
    text = await grant(app, owner, d.user_id, d.username, d.groups, d.length, note)
    await _say(app, owner.id, text)


async def start(app: App, owner: Any, user_id: int, username: str | None = None) -> None:
    """Begin the steps for one person (from Yes, or from /permit with only the person)."""
    why = refusal(app, user_id)
    if why:
        await _say(app, owner.id, why)
        return
    d = Draft(user_id=user_id, username=username)
    _drafts[owner.id] = d
    await ask_groups(app, owner.id, d)


def _open_draft(owner_id: int, user_id: int | None = None) -> Draft | None:
    d = _drafts.get(owner_id)
    if d and (utcnow() - d.started).total_seconds() > DRAFT_TTL_S:
        _drafts.pop(owner_id, None)
        return None
    if d and user_id is not None and d.user_id != user_id:
        return None
    return d


# ---- buttons -----------------------------------------------------------------------------------

@router.callback_query(F.data.startswith("pm:"))
async def cb_access(cb: CallbackQuery, app: App) -> None:
    from .callbacks import genuine, reject
    if not cb.from_user or not app.config.is_owner(cb.from_user.id) or not cb.message:
        await cb.answer()
        return
    if not genuine(cb):
        await reject(cb)
        return
    parts = (cb.data or "").split(":")
    try:
        action, user_id = parts[1], int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    value = parts[3] if len(parts) > 3 else None
    owner = cb.from_user
    who = await label(app, user_id)
    try:   # the choice is made: take the buttons off that message
        await app.bot.edit_message_reply_markup(chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                                                reply_markup=None)
    except Exception:  # noqa: BLE001
        pass

    if action == "yes":
        await start(app, owner, user_id)
    elif action == "no":
        await ledger.note(app, "request_declined", None, actor=owner, target=user_id)
        await _say(app, owner.id, f"Declined. Nothing was sent to {who}. They can still write again later; "
                                  "tap Ban on their next message to stop that.")
    elif action == "ban":
        await app.store.contact_block(user_id, owner.id)
        await ledger.note(app, "contact_blocked", None, actor=owner, target=user_id)
        await _say(app, owner.id, f"🚫 <b>Blocked.</b> {who} can't send you messages through Contact the admin any more. "
                                  "Any access they already have is unchanged (/revoke takes it back).",
                   InlineKeyboardMarkup(inline_keyboard=[[_btn("Undo", f"pm:unban:{user_id}")]]))
    elif action == "unban":
        if await app.store.contact_unblock(user_id):
            await ledger.note(app, "contact_unblocked", None, actor=owner, target=user_id)
        await _say(app, owner.id, f"Unblocked. {who} can contact you again.")
    else:
        d = _open_draft(owner.id, user_id)
        if not d:
            await cb.answer("This one is closed. Send /permit with their id to start again.", show_alert=True)
            return
        if action == "x":
            _drafts.pop(owner.id, None)
            await _say(app, owner.id, "Cancelled. Nothing changed.")
        elif action == "g" and value and value.isdigit():
            d.groups, d.step = max(1, int(value)), "time"
            await ask_time(app, owner.id, d)
        elif action == "gc":
            d.step = "groups_typed"
            await _say(app, owner.id, f"Type the number of groups, from 1 to {MAX_GROUPS}. For example: 5",
                       InlineKeyboardMarkup(inline_keyboard=[_cancel_row(user_id)]))
        elif action == "noend":
            d.length, d.step = None, "note"
            await ask_note(app, owner.id, d)
        elif action == "skip":
            await finish(app, owner, d, None)
    await cb.answer()


# ---- typed answers -------------------------------------------------------------------------------

def _awaiting(m: Message) -> bool:
    """An owner's plain text while a step is waiting for it (commands always pass through)."""
    if not m.from_user or m.chat.type != ChatType.PRIVATE or not m.text or m.text.startswith("/"):
        return False
    d = _open_draft(m.from_user.id)
    return bool(d and d.step in {"groups_typed", "time", "note"})


@router.message(_awaiting)
async def typed(m: Message, app: App) -> None:
    owner = m.from_user
    d = _drafts[owner.id]
    text = m.text.strip()
    if text.lower() in {"cancel", "stop"}:
        _drafts.pop(owner.id, None)
        await _say(app, owner.id, "Cancelled. Nothing changed.")
        return
    if d.step == "groups_typed":
        if not text.isdigit() or not 1 <= int(text) <= MAX_GROUPS:
            await _say(app, owner.id, f"Type a number from 1 to {MAX_GROUPS}, like 5.",
                       InlineKeyboardMarkup(inline_keyboard=[_cancel_row(d.user_id)]))
            return
        d.groups, d.step = int(text), "time"
        await ask_time(app, owner.id, d)
    elif d.step == "time":
        try:
            d.length = access_length(text)
        except DurationError as e:
            await _say(app, owner.id, str(e),
                       InlineKeyboardMarkup(inline_keyboard=[[_btn("No end", f"pm:noend:{d.user_id}")],
                                                             _cancel_row(d.user_id)]))
            return
        d.step = "note"
        await ask_note(app, owner.id, d)
    elif d.step == "note":
        await finish(app, owner, d, text[:200])
