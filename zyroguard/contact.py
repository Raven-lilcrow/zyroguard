"""Contact the admin: a button anyone can tap to send the bot owner a message.

Flow: the person taps "Contact the admin" (deep link t.me/<bot>?start=contact, or /contact
in a private chat), the bot asks for the message, and the next text the person
sends is delivered to every owner as a Telegram message and, when an n8n
webhook is configured, emailed to CONTACT_EMAIL. One message per person per
10 minutes, 2000 characters max. Nothing else about the person is stored.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

import aiohttp
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .app import App
from .store import utcnow

log = logging.getLogger("zyroguard.contact")
router = Router(name="contact")

PENDING_TTL_S = 10 * 60
COOLDOWN_S = 10 * 60
MAX_CHARS = 2000

_pending: dict[int, datetime] = {}   # when each user_id tapped the button
_last_sent: dict[int, datetime] = {}
BLOCKED = "Sorry, I can't pass on messages from you."


def _prune() -> None:
    """Keep the in-memory maps small: drop expired entries once they pile up."""
    if len(_pending) + len(_last_sent) < 2000:
        return
    now = utcnow()
    for d, ttl in ((_pending, PENDING_TTL_S), (_last_sent, COOLDOWN_S)):
        for uid, ts in list(d.items()):
            if (now - ts).total_seconds() > ttl:
                d.pop(uid, None)


def contact_button(app: App) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Contact the admin", url=f"https://t.me/{app.bot_username}?start=contact"),
    ]])


def contact_line(app: App) -> str:
    email = app.config.contact_email
    return "Want ZyroGuard for your own group, or need to reach the admin? Tap the button" + (
        f" or email {email}." if email else ".")


async def begin(m: Message, app: App) -> None:
    if m.chat.type != ChatType.PRIVATE or not m.from_user:
        return
    _prune()
    if await app.store.contact_blocked(m.from_user.id):
        await m.answer(BLOCKED)
        return
    _pending[m.from_user.id] = utcnow()
    await m.answer("Write your message in one go: who you are, what group it is for, and what you need.\n"
                   "I will pass it straight to the admin. Send /cancel to stop.")


@router.message(CommandStart(deep_link=True, magic=F.args == "contact"))
async def start_contact(m: Message, app: App) -> None:
    await begin(m, app)


@router.message(Command("contact"))
async def cmd_contact(m: Message, app: App) -> None:
    if m.chat.type != ChatType.PRIVATE:
        await m.reply("Open a private chat with me and send /contact there.", reply_markup=contact_button(app))
        return
    await begin(m, app)


@router.callback_query(F.data == "contact:start")
async def cb_contact(cb: CallbackQuery, app: App) -> None:
    from .callbacks import genuine
    if genuine(cb) and cb.message and cb.message.chat.type == ChatType.PRIVATE:
        if await app.store.contact_blocked(cb.from_user.id):
            await cb.message.answer(BLOCKED)
            await cb.answer()
            return
        _pending[cb.from_user.id] = utcnow()
        await cb.message.answer("Write your message in one go: who you are, what group it is for, and what you need.\n"
                                "I will pass it straight to the admin. Send /cancel to stop.")
    await cb.answer()


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def cmd_cancel(m: Message, app: App) -> None:
    if m.from_user and _pending.pop(m.from_user.id, None):
        await m.answer("Cancelled. Nothing was sent.")


def _is_pending(m: Message) -> bool:
    if not m.from_user or m.chat.type != ChatType.PRIVATE or not m.text or m.text.startswith("/"):
        return False
    since = _pending.get(m.from_user.id)
    if since is None:
        return False
    if (utcnow() - since).total_seconds() > PENDING_TTL_S:
        _pending.pop(m.from_user.id, None)
        return False
    return True


async def _email(app: App, subject: str, body: str) -> bool:
    url = app.config.contact_webhook_url
    if not url:
        return False
    headers = {"X-ZyroGuard-Secret": app.config.contact_webhook_secret} if app.config.contact_webhook_secret else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(url, json={"to": app.config.contact_email, "subject": subject, "text": body},
                              headers=headers) as r:
                if r.status >= 300:
                    await app.store.log_error("contact.email", f"webhook returned {r.status}")
                    return False
                return True
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("contact.email", f"{type(e).__name__}: {e}")
        return False


@router.message(_is_pending)
async def deliver(m: Message, app: App) -> None:
    user = m.from_user
    _pending.pop(user.id, None)
    if await app.store.contact_blocked(user.id):
        await m.answer(BLOCKED)
        return
    last = _last_sent.get(user.id)
    if last and (utcnow() - last) < timedelta(seconds=COOLDOWN_S):
        await m.answer("You already sent a message a few minutes ago. The admin has it, please wait for a reply.")
        return
    text = (m.text or "").strip()[:MAX_CHARS]
    who = f"@{user.username}" if user.username else html.escape(user.full_name)
    header = f"New message via ZyroGuard from {who} (id {user.id}, {html.escape(user.full_name)})"
    from .access_requests import current_terms, request_markup
    now = await current_terms(app, user.id)
    delivered = 0
    for owner_id in app.config.owner_ids:
        try:
            await app.bot.send_message(
                owner_id, f"✉️ <b>Contact</b> · via ZyroGuard\n<b>From {who}</b> · {html.escape(user.full_name)} · id <code>{user.id}</code>\n\n"
                          f"{html.escape(text)}\n\n"
                          + (f"{now}\n" if now else "")
                          + f'<a href="tg://user?id={user.id}">Reply to them</a>\n\n'
                          "<b>Give them access?</b> Yes asks how many groups and for how long. "
                          "Ban stops their messages here.",
                parse_mode="HTML", reply_markup=request_markup(user.id))
            delivered += 1
        except Exception as e:  # noqa: BLE001
            await app.store.log_error("contact.dm", f"{type(e).__name__}: {e}")
    emailed = await _email(app, f"ZyroGuard contact from {who}", f"{header}\n\n{text}\n\nTelegram: tg://user?id={user.id}")
    _last_sent[user.id] = utcnow()
    log.info("contact message from %s (%s): dm=%d email=%s", who, user.id, delivered, emailed)
    if delivered or emailed:
        extra = f" You can also email {app.config.contact_email}." if app.config.contact_email else ""
        await m.answer("Sent. The admin will get back to you here on Telegram." + extra)
    else:
        email = app.config.contact_email
        await m.answer("I could not deliver it right now." + (f" Please email {email} directly." if email else ""))
