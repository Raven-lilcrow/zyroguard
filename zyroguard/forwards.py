"""Forward check: anyone who forwards a message into a group must pass a quick captcha.

A forward that already passed the spam checks stays up while the sender answers.
Solved: the prompt disappears and the forward stays. Not solved within
forward_captcha_time (or three wrong taps): the forward is removed and the sender
gets a strike, so the strike ladder decides the punishment (by default a 1-day
mute first, then a 30-day ban, then a permanent ban). Admins, owners and trusted
users are never asked. Only runs in enforce mode.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .actions import Decision, Target
from .app import App
from .captcha import _question, mention
from .settings import ChatSettings
from .store import from_iso, utcnow
from .text import action_past_long, long_duration

log = logging.getLogger("zyroguard.forwards")
router = Router(name="forwards")

MAX_WRONG_TAPS = 3


def _markup(chat_id: int, user_id: int, mode: str, options: list[str] | None) -> InlineKeyboardMarkup:
    if mode in {"math", "emoji"} and options:
        btns = [InlineKeyboardButton(text=o, callback_data=f"fwc:{chat_id}:{user_id}:{o}") for o in options]
        rows = [btns] if mode == "math" else [btns[:2], btns[2:]]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="I'm human, keep my forward", callback_data=f"fwc:{chat_id}:{user_id}:ok")]])


async def _punishment(app: App, cs: ChatSettings, chat_id: int, user_id: int) -> str:
    """What the next strike does to this person, in words: 'muted for 1 day'."""
    active = await app.store.active_strikes(chat_id, user_id)
    key = "strike1" if active < 1 else "strike2" if active == 1 else "strike3"
    raw = cs.raw(key)
    return "the message is removed" if raw.split()[0] == "none" else action_past_long(raw)


def _prompt(title: str | None, who: str, mode: str, q: str, minutes: int, punishment: str) -> str:
    head = f"🔁 <b>{html.escape(title)}</b> · forward check" if title else "🔁 <b>Forward check</b>"
    ask = {"emoji": f"Which word matches {q}?", "math": f"What is {q}?"}.get(mode, "Tap the button.")
    when = f"{minutes} minute{'s' if minutes != 1 else ''}"
    return (f"{head}\n<b>{who}, you forwarded a message.</b> {ask}\n\n"
            f"Answer within {when} to keep it. Otherwise it is removed and you are {punishment}.")


def applies(app: App, m: Message) -> bool:
    """Cheap pre-check before any database work."""
    return bool(m.forward_origin is not None and m.from_user and not m.from_user.is_bot
                and m.sender_chat is None and not m.is_automatic_forward and not m.edit_date
                and app.chat_allowed(m.chat.id))


async def challenge(app: App, m: Message) -> bool:
    """Ask the forwarder for a captcha. Returns True when a check is (now) pending."""
    if not applies(app, m):
        return False
    chat_id, user = m.chat.id, m.from_user
    cs = await app.settings.get(chat_id)
    if not cs.enforce or not cs.bool("forward_captcha") or app.is_paused(chat_id):
        return False
    if await app.is_exempt(chat_id, user.id):
        return False
    if await app.store.forward_get(chat_id, user.id):
        # one check covers everything they forward meanwhile (an album, a batch)
        await app.store.forward_add_message(chat_id, user.id, m.message_id)
        return True
    mode = cs.choice("captcha_mode")
    q, answer, options = _question(mode) if mode in {"math", "emoji"} else ("", None, None)
    timeout = cs.seconds("forward_captcha_time") or 120
    thread = m.message_thread_id if m.is_topic_message else None
    text = _prompt(m.chat.title, mention(user.id, user.full_name), mode, q, max(1, timeout // 60),
                   await _punishment(app, cs, chat_id, user.id))
    sent = await app.enforcer.say(cs, text, reply_to=m.message_id, thread_id=thread,
                                  reply_markup=_markup(chat_id, user.id, mode, options))
    await app.store.forward_put(chat_id, user.id, sent.message_id if sent else None, [m.message_id],
                                utcnow() + timedelta(seconds=timeout), answer, thread)
    log.info("forward check for %s (%s) in %s", user.full_name, user.id, m.chat.title)
    return True


async def fail(app: App, chat_id: int, user_id: int, reason: str) -> None:
    row = await app.store.forward_get(chat_id, user_id)
    if row is None:
        return   # already solved or handled
    await app.store.forward_delete(chat_id, user_id)
    if row["prompt_id"]:
        await app.enforcer.delete(chat_id, int(row["prompt_id"]))
    ids = [int(i) for i in json.loads(row["message_ids"] or "[]")]
    for extra in ids[1:]:
        await app.enforcer.delete(chat_id, extra)
    cs = await app.settings.get(chat_id)
    member = await app.store.get_member(chat_id, user_id)
    chat = await app.store.get_chat(chat_id)
    target = Target(chat_id, user_id, member["username"] if member else None,
                    member["display_name"] if member else None, ids[0] if ids else None,
                    f"[{len(ids)} forwarded message{'s' if len(ids) != 1 else ''}]", None,
                    chat["title"] if chat else None, row["thread_id"])
    # a strike on a delete: the ladder decides (by default a 1-day mute first, then a 30-day ban, then a permanent ban)
    await app.enforcer.apply(target, Decision("forward", reason, "delete", None, delete_message=bool(ids),
                                              add_strike=True), cs)


async def timeout_overdue(app: App) -> int:
    n = 0
    for row in await app.store.forward_overdue():
        chat_id, user_id = int(row["chat_id"]), int(row["user_id"])
        try:
            minutes = long_duration((await app.settings.get(chat_id)).raw("forward_captcha_time"))
            await fail(app, chat_id, user_id, f"forwarded a message and did not pass the check within {minutes}")
        except Exception as e:  # noqa: BLE001
            await app.store.log_error("forward.timeout", f"{type(e).__name__}: {e}", chat_id)
            await app.store.forward_delete(chat_id, user_id)
        n += 1
    return n


@router.callback_query(F.data.startswith("fwc:"))
async def on_tap(cb: CallbackQuery, app: App) -> None:
    from .callbacks import genuine, reject
    if not genuine(cb):
        await reject(cb)
        return
    try:
        _, chat_s, user_s, answer = cb.data.split(":", 3)
        chat_id, user_id = int(chat_s), int(user_s)
    except ValueError:
        await cb.answer()
        return
    if cb.from_user.id != user_id:
        await cb.answer("This check is for the person who forwarded the message.")
        return
    row = await app.store.forward_get(chat_id, user_id)
    if not row:
        await cb.answer("This check is already done.")
        return
    expected = row["answer"]
    if expected and answer != expected:
        wrong = int(row["wrong"] or 0) + 1
        if wrong >= MAX_WRONG_TAPS:
            await cb.answer("Too many wrong answers.", show_alert=True)
            await fail(app, chat_id, user_id, "forwarded a message and failed the check three times")
            return
        cs = await app.settings.get(chat_id)
        mode = cs.choice("captcha_mode")
        q, new_answer, opts = _question(mode)
        await app.store.forward_update(chat_id, user_id, answer=new_answer, wrong=wrong)
        await cb.answer(f"Not quite. Try again ({MAX_WRONG_TAPS - wrong} left).", show_alert=True)
        deadline = from_iso(row["deadline"])
        left = max(1, int((deadline - utcnow()).total_seconds() // 60)) if deadline else 1
        try:
            await cb.message.edit_text(
                _prompt(cb.message.chat.title, mention(user_id, cb.from_user.full_name), mode, q, left,
                        await _punishment(app, cs, chat_id, user_id)),
                parse_mode="HTML", reply_markup=_markup(chat_id, user_id, mode, opts))
        except Exception:  # noqa: BLE001
            pass
        return
    await app.store.forward_delete(chat_id, user_id)
    if row["prompt_id"]:
        await app.enforcer.delete(chat_id, int(row["prompt_id"]))
    await cb.answer("Thanks, your forward stays.")
