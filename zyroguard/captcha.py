"""Join gate: CAS check, name/bio scan, button / math / emoji captcha, timeouts."""

from __future__ import annotations

import asyncio
import html
import logging
import random
from datetime import timedelta

import aiohttp
from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .actions import MUTED, Decision, Target, tg_call
from .app import App
from .rules import brand_in_name, check_bio, check_join_name
from .settings import ChatSettings, protected_names
from .store import from_iso, utcnow

log = logging.getLogger("zyroguard.captcha")
router = Router(name="captcha")

CAS_URL = "https://api.cas.chat/check"


def _present(cm) -> bool:
    """Is this person actually in the group? Telegram reports a muted user who left as
    'restricted' with is_member=False rather than 'left', so status alone is not enough."""
    if cm.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        return True
    if cm.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(cm, "is_member", False))
    return False


async def cas_check(app: App, user_id: int) -> tuple[bool, int | None]:
    cached = await app.store.cas_get(user_id, timedelta(hours=12))
    if cached is not None:
        return cached, None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(CAS_URL, params={"user_id": user_id}) as r:
                data = await r.json(content_type=None)
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("cas", f"{type(e).__name__}: {e}")
        return False, None
    listed = bool(data.get("ok"))
    offenses = (data.get("result") or {}).get("offenses") if listed else None
    await app.store.cas_put(user_id, listed, offenses)
    return listed, offenses


EMOJI_WORDS = [
    ("🍎", "apple"), ("🐱", "cat"), ("🐶", "dog"), ("🚗", "car"), ("🌳", "tree"), ("🏠", "house"),
    ("☀️", "sun"), ("🌙", "moon"), ("⭐", "star"), ("🐟", "fish"), ("🐦", "bird"), ("🌸", "flower"),
    ("🍕", "pizza"), ("⚽", "ball"), ("📚", "book"), ("🎸", "guitar"), ("✈️", "plane"), ("🚲", "bicycle"),
    ("🍌", "banana"), ("🐘", "elephant"), ("🔑", "key"), ("☂️", "umbrella"), ("🎂", "cake"), ("🐸", "frog"),
]


def _question(mode: str = "math") -> tuple[str, str, list[str]]:
    """Returns (question shown to the user, correct answer, shuffled options)."""
    if mode == "emoji":
        emoji, word = random.choice(EMOJI_WORDS)
        options = {word}
        while len(options) < 4:
            options.add(random.choice(EMOJI_WORDS)[1])
        opts = list(options)
        random.shuffle(opts)
        return emoji, word, opts
    a, b = random.randint(1, 9), random.randint(1, 9)
    answer = str(a + b)
    options = {answer}
    while len(options) < 4:
        options.add(str(random.randint(2, 18)))
    opts = list(options)
    random.shuffle(opts)
    return f"{a} + {b}", answer, opts


def _markup(chat_id: int, user_id: int, mode: str, options: list[str] | None) -> InlineKeyboardMarkup:
    if mode in {"math", "emoji"} and options:
        btns = [InlineKeyboardButton(text=o, callback_data=f"cap:{chat_id}:{user_id}:{o}") for o in options]
        rows = [btns] if mode == "math" else [btns[:2], btns[2:]]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="I'm human, let me in", callback_data=f"cap:{chat_id}:{user_id}:ok")
    ]])


def _prompt(mode: str, who: str, q: str, minutes: int, title: str | None = None) -> str:
    head = f"👋 <b>{html.escape(title)}</b> · quick check" if title else "👋 <b>Quick check</b>"
    when = f"within {minutes} minute{'s' if minutes != 1 else ''} to start chatting"
    if mode == "emoji":
        return f"{head}\n<b>Welcome {who}.</b> Which word matches {q}?\n\nTap the right one below {when}."
    if mode == "math":
        return f"{head}\n<b>Welcome {who}.</b> What is {q}?\n\nTap the answer below {when}."
    return f"{head}\n<b>Welcome {who}.</b>\n\nTap the button below {when}."


async def _title(app: App, chat_id: int, given: str | None = None) -> str | None:
    if given:
        return given
    row = await app.store.get_chat(chat_id)
    return row["title"] if row else None


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "there")}</a>'


async def start_captcha(app: App, cs: ChatSettings, chat_id: int, user_id: int, name: str,
                        chat_title: str | None = None) -> None:
    mode = cs.choice("captcha_mode")
    timeout = cs.seconds("captcha_time") or 120
    if cs.enforce:
        try:
            await tg_call(lambda: app.bot.restrict_chat_member(
                chat_id, user_id, permissions=MUTED, use_independent_chat_permissions=True))
        except Exception as e:  # noqa: BLE001
            await app.store.log_error("captcha.restrict", f"{type(e).__name__}: {e}", chat_id)
    answer = None
    options = None
    q = ""
    if mode in {"math", "emoji"}:
        q, answer, options = _question(mode)
    text = _prompt(mode, mention(user_id, name), q, timeout // 60 or 1, await _title(app, chat_id, chat_title))
    msg_id = None
    try:
        sent = await tg_call(lambda: app.bot.send_message(
            chat_id, text, parse_mode="HTML", reply_markup=_markup(chat_id, user_id, mode, options)))
        msg_id = sent.message_id if sent else None
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("captcha.send", f"{type(e).__name__}: {e}", chat_id)
    await app.store.captcha_put(chat_id, user_id, msg_id, utcnow() + timedelta(seconds=timeout), answer)


async def pass_captcha(app: App, cs: ChatSettings, chat_id: int, user_id: int, name: str,
                       chat_title: str | None = None) -> None:
    row = await app.store.captcha_get(chat_id, user_id)
    await app.store.captcha_delete(chat_id, user_id)
    await app.store.mark_verified(chat_id, user_id)
    if row and row["message_id"]:
        await app.enforcer.delete(chat_id, int(row["message_id"]))
    # always lift: the mute may have been placed while the group was still in enforce mode
    await app.enforcer.unmute(chat_id, user_id)
    greeting = cs.str("greeting").strip()
    if greeting:
        title = await _title(app, chat_id, chat_title)
        head = f"👋 <b>{html.escape(title)}</b>" if title else "👋"
        body = greeting.replace("{name}", mention(user_id, name))
        try:
            sent = await tg_call(lambda: app.bot.send_message(
                chat_id, f"{head}\n<b>Welcome, {mention(user_id, name)}.</b>\n\n{body}", parse_mode="HTML"))
            await app.enforcer.expire_later(cs, sent)
        except Exception:  # noqa: BLE001
            pass


async def fail_captcha(app: App, chat_id: int, user_id: int) -> None:
    cs = await app.settings.get(chat_id)
    row = await app.store.captcha_get(chat_id, user_id)
    if row is None:
        return   # already passed (or handled) while this was queued
    await app.store.captcha_delete(chat_id, user_id)
    member = await app.store.get_member(chat_id, user_id)
    if row and row["message_id"]:
        await app.enforcer.delete(chat_id, int(row["message_id"]))
    action, dur = cs.action("captcha_fail")
    if not cs.enforce:
        await app.enforcer.unmute(chat_id, user_id)
    target = Target(chat_id, user_id, member["username"] if member else None,
                    member["display_name"] if member else None)
    await app.enforcer.apply(
        target, Decision("captcha", "captcha not solved in time", action, dur,
                         delete_message=False, add_strike=False), cs,
    )


_join_locks: dict[tuple[int, int], asyncio.Lock] = {}


async def handle_join(app: App, chat_id: int, chat_title: str | None, user, cs: ChatSettings) -> None:
    """One join at a time per person: Telegram reports a join twice (chat_member update and
    service message) and both must not start a captcha."""
    key = (chat_id, user.id)
    lock = _join_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if await app.store.captcha_get(chat_id, user.id):
            return
        await _handle_join(app, chat_id, chat_title, user, cs)
    if len(_join_locks) > 2000:
        for k, lk in list(_join_locks.items()):
            if not lk.locked():
                _join_locks.pop(k, None)


async def _handle_join(app: App, chat_id: int, chat_title: str | None, user, cs: ChatSettings) -> None:
    user_id = user.id
    name = user.full_name
    await app.store.touch_member(chat_id, user_id, user.username, name, joined=True)
    if user.is_bot or await app.is_exempt(chat_id, user_id):
        return
    target = Target(chat_id, user_id, user.username, name, chat_title=chat_title)

    if cs.bool("cas") and app.config.cas_enabled:
        listed, offenses = await cas_check(app, user_id)
        if listed:
            action, dur = cs.action("cas_action")
            await app.enforcer.apply(target, Decision(
                "cas", f"on the known spammer list ({offenses or '?'} reports)", action, dur,
                delete_message=False, add_strike=False), cs)
            return

    brand = brand_in_name(name, user.username, protected_names(cs) + app.rules.protected_names)
    if brand and brand[1]:
        # "AcmeAl | Community": a fake admin account, no captcha can make that innocent
        action, dur = cs.action("impersonation_action")
        await app.enforcer.apply(target, Decision(
            "join", f"name imitates '{brand[0]}' with lookalike letters", action, dur,
            delete_message=False, add_strike=False, detail={"matched": ["brand_lookalike"]}), cs)
        return

    why = check_join_name(name, user.username, app.rules)
    if why:
        action, dur = cs.action("captcha_fail")
        await app.enforcer.apply(target, Decision("join", why, action, dur,
                                                  delete_message=False, add_strike=False), cs)
        return

    if cs.bool("bio_scan"):
        try:
            full = await app.bot.get_chat(user_id)
            why = check_bio(getattr(full, "bio", None), app.rules)
        except Exception:  # noqa: BLE001
            why = None
        if why:
            action, dur = cs.action("new_account_action")
            await app.enforcer.apply(target, Decision("join", why, action, dur,
                                                      delete_message=False, add_strike=False), cs)
            return

    if not cs.bool("captcha"):
        await app.store.mark_verified(chat_id, user_id, everywhere=False)
        return
    member = await app.store.get_member(chat_id, user_id)
    if cs.bool("captcha_every_join"):
        # every join is a fresh test, even for people who passed before and left
        if member and member["verified_at"]:
            await app.store.clear_verified(chat_id, user_id)
        await start_captcha(app, cs, chat_id, user_id, name, chat_title)
        return
    if member and member["verified_at"]:
        return
    if cs.bool("skip_verified_anywhere") and await app.store.is_verified_anywhere(user_id):
        await app.store.mark_verified(chat_id, user_id)
        return
    await start_captcha(app, cs, chat_id, user_id, name, chat_title)


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, app: App) -> None:
    chat = event.chat
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if not app.chat_allowed(chat.id):
        return
    old, new = event.old_chat_member.status, event.new_chat_member.status
    cs = await app.settings.get(chat.id)
    was_in, is_in = _present(event.old_chat_member), _present(event.new_chat_member)
    log.info("chat_member: %s (%s) in %s: %s%s -> %s%s", event.new_chat_member.user.full_name,
             event.new_chat_member.user.id, chat.title, old, "" if was_in else " (not in)", new, "" if is_in else " (not in)")
    if not was_in and is_in and new not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await handle_join(app, chat.id, chat.title, event.new_chat_member.user, cs)
    elif was_in and not is_in:
        await app.store.captcha_delete(chat.id, event.new_chat_member.user.id)
    if new in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR} or old in {
        ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        app.admins.invalidate(chat.id)


@router.message(F.new_chat_members | F.left_chat_member)
async def on_service_message(message: Message, app: App) -> None:
    if not app.chat_allowed(message.chat.id):
        return
    cs = await app.settings.get(message.chat.id)
    if cs.bool("clean_service") and cs.enforce:
        await app.enforcer.delete(message.chat.id, message.message_id)
    # Fallback: Telegram also announces joins here. If the chat_member update was
    # missed (restart, hiccup), gate the newcomer from this signal instead.
    for user in message.new_chat_members or []:
        if user.is_bot or await app.store.captcha_get(message.chat.id, user.id):
            continue
        member = await app.store.get_member(message.chat.id, user.id)
        joined = from_iso(member["joined_at"]) if member and member["joined_at"] else None
        if joined and (utcnow() - joined).total_seconds() < 30:
            continue  # chat_member already handled this join
        log.info("join via service message: %s (%s) in %s", user.full_name, user.id, message.chat.title)
        await handle_join(app, message.chat.id, message.chat.title, user, cs)


MAX_WRONG_TAPS = 3
_wrong: dict[tuple[int, int], int] = {}


@router.callback_query(F.data.startswith("cap:"))
async def on_captcha_tap(cb: CallbackQuery, app: App) -> None:
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
        await cb.answer("This button is for the new member.", show_alert=False)
        return
    row = await app.store.captcha_get(chat_id, user_id)
    if not row:
        await cb.answer("Already verified.")
        return
    cs = await app.settings.get(chat_id)
    expected = row["answer"]
    if expected and answer != expected:
        wrong = _wrong.get((chat_id, user_id), 0) + 1
        _wrong[(chat_id, user_id)] = wrong
        if wrong >= MAX_WRONG_TAPS:
            _wrong.pop((chat_id, user_id), None)
            await cb.answer("Too many wrong answers.", show_alert=True)
            await fail_captcha(app, chat_id, user_id)
            return
        await cb.answer(f"Not quite. Try again ({MAX_WRONG_TAPS - wrong} left).", show_alert=True)
        mode = cs.choice("captcha_mode")
        q, new_answer, opts = _question(mode)
        deadline = from_iso(row["deadline"])
        left = max(1, int((deadline - utcnow()).total_seconds() // 60)) if deadline else 2
        await app.store.captcha_put(chat_id, user_id, row["message_id"], deadline, new_answer)
        try:
            await cb.message.edit_text(
                _prompt(mode, mention(user_id, cb.from_user.full_name), q, left,
                        cb.message.chat.title if cb.message else None),
                parse_mode="HTML", reply_markup=_markup(chat_id, user_id, mode, opts))
        except Exception:  # noqa: BLE001
            pass
        return
    _wrong.pop((chat_id, user_id), None)
    await pass_captcha(app, cs, chat_id, user_id, cb.from_user.full_name,
                       cb.message.chat.title if cb.message else None)
    await cb.answer("Welcome!")
