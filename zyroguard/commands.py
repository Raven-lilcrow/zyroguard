"""Admin commands: moderation, lists, settings shortcuts, undo callbacks.

Rose-style conventions: reply to a message or give an @username or id, and durations
like 2h or 3d.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta


from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .actions import Decision, Target
from .app import App
from .callbacks import genuine, reject
from .pipeline import extra_text, message_text, real_reply, thread_of
from .durations import HELP_TEXT, DurationError, format_action, format_duration, parse_action, parse_duration
from .rules import pattern_problem
from .settings import SPECS
from .store import from_iso, utcnow
from .text import action_done, action_words, action_would, ago, long_duration, when

router = Router(name="commands")
GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}


# Helpers

async def require_admin(m: Message, app: App) -> bool:
    """True when this person may run admin commands for the group the command targets.

    In a group: the group itself. In a private chat: the group chosen with /use
    (or the only group the person controls). Anyone else is skipped so the
    message still runs through the spam pipeline."""
    if not m.from_user:
        return False
    if m.chat.type == ChatType.PRIVATE:
        chat_id = await pick_group(m, app)
        return chat_id is not None
    if m.chat.type not in GROUPS:
        return False
    if not app.chat_allowed(m.chat.id):
        raise SkipHandler
    if m.sender_chat and m.sender_chat.id == m.chat.id:
        return True  # anonymous admin
    if await app.controls(m.from_user.id, m.chat.id):
        return True
    raise SkipHandler


NO_GROUP = "You do not manage any group I guard yet. Add me to your group and make me an admin first.\n"


def cid(m: Message, app: App) -> int:
    """The group a command is about: the group itself, or the one chosen in a private chat."""
    if m.chat.type == ChatType.PRIVATE and m.from_user:
        return app.dm_context.get(m.from_user.id, m.chat.id)
    return m.chat.id


async def chat_title(m: Message, app: App) -> str | None:
    if m.chat.type != ChatType.PRIVATE:
        return m.chat.title
    row = await app.store.get_chat(cid(m, app))
    return row["title"] if row else None


async def pick_group(m: Message, app: App) -> int | None:
    """Private chat: which group is this person managing? Remembers /use, else the
    only group they control. Otherwise asks them to choose and returns None."""
    uid = m.from_user.id
    chosen = app.dm_context.get(uid)
    if chosen is not None:
        if await app.controls(uid, chosen) and chosen in app.allowed_chats:
            return chosen
        app.dm_context.pop(uid, None)
    mine = await app.groups_for(uid)
    if len(mine) == 1:
        app.dm_context[uid] = int(mine[0]["chat_id"])
        return app.dm_context[uid]
    if not mine:
        from .contact import contact_button, contact_line
        await m.answer(NO_GROUP + contact_line(app), reply_markup=contact_button(app))
        return None
    await m.answer("Which group is this for? Pick one:", reply_markup=group_picker(mine))
    return None


def group_picker(chats) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=(c["title"] or str(c["chat_id"]))[:40], callback_data=f"use:{c['chat_id']}")]
            for c in chats[:20]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("use"), F.chat.type == ChatType.PRIVATE)
async def cmd_use(m: Message, app: App, command: CommandObject) -> None:
    if not m.from_user:
        return
    mine = await app.groups_for(m.from_user.id)
    if not mine:
        from .contact import contact_button, contact_line
        await m.answer(NO_GROUP + contact_line(app), reply_markup=contact_button(app))
        return
    arg = (command.args or "").strip()
    if arg.lstrip("-").isdigit():
        wanted = int(arg)
        if any(int(c["chat_id"]) == wanted for c in mine):
            app.dm_context[m.from_user.id] = wanted
            await m.answer(f"Working on {html.escape(await chat_title(m, app) or str(wanted))} now. "
                           "Every command you send here applies to that group.", parse_mode="HTML")
            return
        await m.answer("That is not one of your groups.")
    current = app.dm_context.get(m.from_user.id)
    cur_title = next((c["title"] for c in mine if int(c["chat_id"]) == current), None)
    lines = [f"🗂 <b>Your groups</b> · {len(mine)}",
             f"<b>Working on: {html.escape(cur_title)}</b>" if cur_title else "<b>No group picked yet.</b>", ""]
    for c in mine:
        mark = " · current" if int(c["chat_id"]) == current else ""
        lines.append(f"{html.escape(c['title'] or '')} · <code>{c['chat_id']}</code>{mark}")
    lines.append("")
    lines.append("Tap one to work on it, or send /use followed by the id.")
    await m.answer("\n".join(lines), parse_mode="HTML", reply_markup=group_picker(mine))


@router.callback_query(F.data.startswith("use:"))
async def cb_use(cb: CallbackQuery, app: App) -> None:
    if not genuine(cb):
        await reject(cb)
        return
    chat_id = int(cb.data.split(":")[1])
    if chat_id not in app.allowed_chats or not await app.controls(cb.from_user.id, chat_id):
        await cb.answer("That is not one of your groups.", show_alert=True)
        return
    app.dm_context[cb.from_user.id] = chat_id
    row = await app.store.get_chat(chat_id)
    title = (row["title"] if row else None) or str(chat_id)
    try:
        await cb.message.edit_text(f"Working on {html.escape(title)} now. Every command you send here applies to that group.\n"
                                   "Send /use to switch, /settings to configure, /diag to check.", parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


# Placeholder senders Telegram uses: anonymous admin, a channel posting, Telegram itself.
SERVICE_ACCOUNTS = {1087968824, 136817688, 777000}


def args(command: CommandObject) -> list[str]:
    return (command.args or "").split()


async def resolve_target(m: Message, app: App, tokens: list[str]) -> tuple[Target | None, list[str]]:
    """Reply target, else @username / numeric id from the first token. In a forum topic the
    automatic reply to the topic header does not count as a reply."""
    r = real_reply(m)
    if r and r.from_user and r.from_user.id not in SERVICE_ACCOUNTS:
        u = r.from_user
        return Target(cid(m, app), u.id, u.username, u.full_name, r.message_id,
                      r.text or r.caption, None, await chat_title(m, app), thread_of(m)), tokens
    if not tokens:
        return None, tokens
    tok = tokens[0]
    if tok.startswith("@"):
        cur = await app.store.db.execute(
            "SELECT user_id, username, display_name FROM members WHERE chat_id=? AND lower(username)=? "
            "ORDER BY last_msg_at DESC LIMIT 1", (cid(m, app), tok[1:].lower()))
        row = await cur.fetchone()
        if not row:
            return None, tokens
        return Target(cid(m, app), int(row["user_id"]), row["username"], row["display_name"],
                      chat_title=await chat_title(m, app), thread_id=thread_of(m)), tokens[1:]
    if tok.lstrip("-").isdigit():
        uid = int(tok)
        member = await app.store.get_member(cid(m, app), uid)
        return Target(cid(m, app), uid, member["username"] if member else None,
                      member["display_name"] if member else None, chat_title=await chat_title(m, app),
                      thread_id=thread_of(m)), tokens[1:]
    return None, tokens


def who(t: Target) -> str:
    return f"@{t.username}" if t.username else html.escape(t.display_name or str(t.user_id))


MAX_MESSAGE = 4000


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Telegram allows 4096 characters per message. Split on line breaks, never mid-tag."""
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


async def reply(m: Message, text: str, markup=None) -> list:
    parts = split_message(text)
    sent = []
    for i, part in enumerate(parts):
        r = await m.reply(part, parse_mode="HTML", disable_web_page_preview=True,
                          reply_markup=markup if i == len(parts) - 1 else None)
        sent.append(r)
    return sent


async def reply_temp(m: Message, app: App, text: str) -> None:
    """Reply, and in a group queue the reply for deletion after notice_ttl."""
    sent = await reply(m, text)
    if m.chat.type == ChatType.PRIVATE:
        return
    cs = await app.settings.get(m.chat.id)
    for r in sent:
        await app.enforcer.expire_later(cs, r)


async def user_label(app: App, chat_id: int, uid: int) -> str:
    """@name (id) or Display name (id), never a bare number."""
    mem = await app.store.get_member(chat_id, uid)
    if mem and mem["username"]:
        return f"@{mem['username']} (<code>{uid}</code>)"
    if mem and mem["display_name"]:
        return f"{html.escape(mem['display_name'])} (<code>{uid}</code>)"
    return f"id <code>{uid}</code>"


async def _manual(m: Message, app: App, command: CommandObject, action: str, *, needs_duration: bool = False,
                  delete_replied: bool = False) -> None:
    if not await require_admin(m, app):
        return
    target, rest = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        toks = args(command)
        if toks and toks[0].startswith("@"):
            await reply_temp(m, app, f"I have not seen {html.escape(toks[0])} write in this group yet, so I cannot find them by name. "
                           "Reply to one of their messages instead, or use their id from /id.")
        else:
            tail = f"\n{HELP_TEXT}" if action in {"mute", "ban"} else ""
            await reply_temp(m, app, f"Reply to the user's message with /{command.command}, or write /{command.command} @username.{tail}")
        return
    if await app.admins.is_admin(cid(m, app), target.user_id):
        await reply_temp(m, app, "That user is an admin, so I will not touch them.")
        return
    duration = None
    reason_tokens = rest
    if action in {"mute", "ban"}:
        if rest:
            try:
                duration = parse_duration(rest[0])
                reason_tokens = rest[1:]
            except DurationError:
                if needs_duration:
                    await reply_temp(m, app, f"/{command.command} needs a time, e.g. /{command.command} 2h. {HELP_TEXT}")
                    return
                duration = None
        elif needs_duration:
            await reply_temp(m, app, f"/{command.command} needs a time, e.g. /{command.command} 2h. {HELP_TEXT}")
            return
    reason = " ".join(reason_tokens) or f"manual /{command.command}"
    cs = await app.settings.get(cid(m, app))
    outcome = await app.enforcer.apply(
        target, Decision("admin", reason, action, duration, delete_message=delete_replied,
                         add_strike=(action == "warn")),
        cs, actor_id=m.from_user.id if m.from_user else None, force_enforce=(action != "warn"),
    )
    head = html.escape(outcome.describe())
    if outcome.shadow:
        head = f"Shadow mode, so not really done: {html.escape(outcome.would())}"
    lines = [f"<b>{head}</b>", f"Who: {who(target)}"]
    if action == "warn":
        limit = cs.int("warn_limit")
        if outcome.action != "warn":
            lines.append(f"That was warn {limit} of {limit}, so the warn action fired and their strikes were cleared.")
        else:
            lines.append(f"Strikes on them now: {outcome.strikes} of {limit}")
    if outcome.note:
        lines.append(f"Note: {html.escape(outcome.note)}")
    if outcome.errors:
        lines.append(f"⚠️ Problem: {html.escape('; '.join(outcome.errors))}")
    lines.append(f"Undo: <code>/undo {outcome.audit_id}</code>")
    await reply_temp(m, app, "\n".join(lines))


# Moderation

@router.message(Command("ban"))
async def cmd_ban(m: Message, app: App, command: CommandObject) -> None:
    await _manual(m, app, command, "ban")


@router.message(Command("mute"))
async def cmd_mute(m: Message, app: App, command: CommandObject) -> None:
    await _manual(m, app, command, "mute")


@router.message(Command("kick"))
async def cmd_kick(m: Message, app: App, command: CommandObject) -> None:
    await _manual(m, app, command, "kick")


@router.message(Command("warn"))
async def cmd_warn(m: Message, app: App, command: CommandObject) -> None:
    # a strike always takes the message with it: when used as a reply, the replied message is deleted
    await _manual(m, app, command, "warn", delete_replied=True)


@router.message(Command("unban"))
async def cmd_unban(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply_temp(m, app, "Write /unban @username or /unban &lt;id&gt; (a banned user has no message to reply to).")
        return
    err = await app.enforcer.unban(cid(m, app), target.user_id)
    await app.store.cancel_lifts(cid(m, app), target.user_id, "ban")
    await reply_temp(m, app, err or f"<b>Unbanned {who(target)}.</b> They can join again.")


@router.message(Command("unmute"))
async def cmd_unmute(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply_temp(m, app, "Reply to the user or give @username / id.")
        return
    err = await app.enforcer.unmute(cid(m, app), target.user_id)
    await app.store.cancel_lifts(cid(m, app), target.user_id, "mute")
    await app.store.captcha_delete(cid(m, app), target.user_id)
    await app.store.mark_verified(cid(m, app), target.user_id)
    await reply_temp(m, app, err or f"<b>Unmuted {who(target)}.</b> They can talk again.")


@router.message(Command("strikes", "warns"))
async def cmd_strikes(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply(m, "Reply to the user or give @username / id.")
        return
    rows = await app.store.list_strikes(cid(m, app), target.user_id)
    title = html.escape(await chat_title(m, app) or str(cid(m, app)))
    if not rows:
        await reply(m, f"⚠️ <b>{title}</b>\n<b>{who(target)} has no active strikes.</b> · id <code>{target.user_id}</code>")
        return
    cs = await app.settings.get(cid(m, app))
    n = len(rows)
    lines = [f"⚠️ <b>{title}</b>",
             f"<b>{who(target)} has {n} active strike{'s' if n != 1 else ''}</b> · id <code>{target.user_id}</code>", ""]
    for i, r in enumerate(rows, 1):
        given = from_iso(r["created_at"])
        exp = from_iso(r["expires_at"]) if r["expires_at"] else None
        bits = [html.escape(r["reason"]), f"given {when(given) if given else '?'}"]
        if exp:
            bits.append(f"forgotten {exp:%b %d}")
        lines.append(f"{i}. " + " · ".join(bits))
    nxt = app.enforcer._next_step(cs, n)
    lines.append("")
    lines.append(f"Next strike: {nxt}" if nxt else "Next strike: the same again")
    lines.append(f"Clear them: <code>/pardon {who(target)}</code>")
    await reply(m, "\n".join(lines))


@router.message(Command("pardon", "resetwarns"))
async def cmd_pardon(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply_temp(m, app, "Reply to the user or give @username / id.")
        return
    n = await app.store.clear_strikes(cid(m, app), target.user_id)
    await reply_temp(m, app, f"<b>Cleared {n} strike(s) for {who(target)}.</b> They start fresh.")


_last_report: dict[int, datetime] = {}


@router.message(Command("report"))
async def cmd_report(m: Message, app: App) -> None:
    if not m.from_user:
        return
    if m.chat.type not in GROUPS:
        await m.reply("Use /report inside the group, as a reply to the message you want to report.")
        return
    if not app.chat_allowed(m.chat.id):
        raise SkipHandler
    r = real_reply(m)
    if r is None:
        await app.enforcer.delete(m.chat.id, m.message_id)
        return
    last = _last_report.get(m.from_user.id)
    if last and (utcnow() - last) < timedelta(minutes=5):
        return
    _last_report[m.from_user.id] = utcnow()
    if len(_last_report) > 5000:
        _last_report.clear()
    cs = await app.settings.get(cid(m, app))
    modlog = await app.enforcer._modlog_target(cs)
    if not modlog:
        return
    try:
        await app.bot.forward_message(modlog, cid(m, app), r.message_id)
        await app.bot.send_message(
            modlog, f"Reported by {html.escape(m.from_user.full_name)} in {html.escape(await chat_title(m, app) or '')}. "
                    f"Reply to it in the group with /ban, /mute or /kick, or /spam to remove it and teach me.",
            parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("report", f"{type(e).__name__}: {e}", cid(m, app))
        return
    await app.enforcer.delete(cid(m, app), m.message_id)


# Meaning match: teach by example

TOO_SHORT = "That message is too short to learn from. I need at least a sentence."


def _lib_problem(app: App) -> str | None:
    lib = app.similar
    if lib is not None and lib.ready:
        return None
    why = f" ({html.escape(lib.problem)})" if lib is not None and lib.problem else ""
    return f"The meaning match is not running right now{why}. Try again in a minute, or check /diag."


def _teach_how(word: str) -> str:
    return (f"<b>How to teach me with /{word}</b>\n"
            f"• In the group, reply to the message with /{word}. Type it in the reply yourself: "
            "picking it from the command menu sends it without the reply.\n"
            f"• Already removed by me? Send /{word} with its action number from /log or the report, "
            f"like <code>/{word} 412</code>. This works in our private chat too.\n"
            "Bot owner: add 'all' to share it with every group.")


async def _learned_text(app: App, chat_id: int, label: str, ex_id: int | None, is_new: bool, shared: bool) -> str:
    """What teaching did, and whether it changes anything yet in that group."""
    kind = "spam" if label == "spam" else "a normal message"
    where = "every group" if shared else "this group"
    head = f"Learned #{ex_id} as {kind} for {where}." if is_new else f"I already knew that one (#{ex_id})."
    state = (await app.settings.get(chat_id)).choice("similarity")
    if state == "on":
        tip = ("Messages that mean the same thing, in any language, now score higher, so the AI judge looks at them."
               if label == "spam" else "Messages like it now count less as spam.")
    elif state == "shadow":
        tip = ("The meaning match is still in its trial here: it records matches but doesn't change what I do yet. "
               "Your examples start counting once an admin sends /set similarity on.")
    else:
        tip = "The meaning match is off in this group, so this is only saved. /set similarity on turns it on."
    return f"{head}\n{tip}\n/examples shows everything I learned."


async def teach_from_action(app: App, row, label: str, by: int | None, shared: bool = False) -> tuple[bool, str]:
    """Learn from the text I saved with an action (the message itself may be long gone).
    (learned, message for the admin)."""
    from .similar import usable
    problem = _lib_problem(app)
    if problem:
        return False, problem
    if not row.text_excerpt or not usable(row.text_excerpt):
        return False, f"Action #{row.id} has no message text I can learn from. I need at least a sentence."
    ex_id, is_new = await app.similar.add(0 if shared else row.chat_id, label, row.text_excerpt, "admin", by,
                                          audit_id=row.id if label == "spam" and not shared else None)
    if ex_id is None:
        return False, TOO_SHORT
    if label == "spam" and row.mode == "shadow" and row.message_id:
        await app.enforcer.delete(row.chat_id, row.message_id)   # shadow left it up; teaching it as spam takes it down
    return True, await _learned_text(app, row.chat_id, label, ex_id, is_new, shared)


async def _teach(m: Message, app: App, command: CommandObject, label: str) -> None:
    if not await require_admin(m, app):
        return
    word = "spam" if label == "spam" else "notspam"
    problem = _lib_problem(app)
    if problem:
        await reply_temp(m, app, problem)
        return
    toks = [t.lower() for t in args(command)]
    shared = "all" in toks and app.config.is_owner(m.from_user.id)
    number = next((int(t.lstrip("#")) for t in toks if t.lstrip("#").isdigit()), None)
    r = real_reply(m) if m.chat.type in GROUPS else None
    if r is None and number is not None:
        row = await app.store.get_audit(number)
        if not row or row.chat_id != cid(m, app):
            await reply_temp(m, app, "There is no action with that number in this group. See /log.")
            return
        _, text = await teach_from_action(app, row, label, m.from_user.id, shared)
        if m.chat.type in GROUPS:
            await app.enforcer.delete(m.chat.id, m.message_id)
        await reply_temp(m, app, text)
        return
    if r is None:
        await reply_temp(m, app, _teach_how(word))
        return
    text = f"{message_text(r)}\n{extra_text(r)}".strip()
    from .similar import usable
    if not usable(text):
        await reply_temp(m, app, TOO_SHORT)
        return
    ex_id, is_new = await app.similar.add(0 if shared else m.chat.id, label, text, "admin", m.from_user.id)
    if label == "spam":
        await app.enforcer.delete(m.chat.id, r.message_id)
    await app.enforcer.delete(m.chat.id, m.message_id)
    await reply_temp(m, app, await _learned_text(app, m.chat.id, label, ex_id, is_new, shared))


@router.callback_query(F.data.startswith("teach:"))
async def cb_teach(cb: CallbackQuery, app: App) -> None:
    """The Teach as spam button on a report."""
    if not genuine(cb):
        await reject(cb)
        return
    try:
        audit_id = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    row = await app.store.get_audit(audit_id)
    if not row:
        await cb.answer("Unknown action.")
        return
    if not await app.controls(cb.from_user.id, row.chat_id):
        await cb.answer("Admins of that group only.", show_alert=True)
        return
    learned, text = await teach_from_action(app, row, "spam", cb.from_user.id)
    await cb.answer(text[:190], show_alert=True)
    if not learned:
        return
    rows = [[b for b in line if b.callback_data != cb.data] for line in cb.message.reply_markup.inline_keyboard]
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n{html.escape(text.splitlines()[0])} "
                                   f"(by {html.escape(cb.from_user.full_name)})", parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r]))
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("spam"))
async def cmd_spam(m: Message, app: App, command: CommandObject) -> None:
    await _teach(m, app, command, "spam")


@router.message(Command("notspam"))
async def cmd_notspam(m: Message, app: App, command: CommandObject) -> None:
    await _teach(m, app, command, "ham")


@router.message(Command("examples"))
async def cmd_examples(m: Message, app: App) -> None:
    if not await require_admin(m, app):
        return
    chat_id = cid(m, app)
    title = html.escape(await chat_title(m, app) or str(chat_id))
    cs = await app.settings.get(chat_id)
    lib = app.similar
    state = ("not running" + (f" ({html.escape(lib.problem)})" if lib is not None and lib.problem else "")
             if lib is None or not lib.ready else cs.choice("similarity"))
    n = await app.store.examples_count(chat_id)
    lines = [f"🧠 <b>{title}</b> · meaning match", f"<b>Status: {state}</b>", "",
             f"Spam examples: {n.get('group_spam', 0)} for this group, {n.get('shared_spam', 0)} shared",
             f"Normal examples: {n.get('group_ham', 0)} for this group, {n.get('shared_ham', 0)} shared"]
    if n.get("held"):
        lines.append(f"Waiting: {n['held']} learned from sure catches, they count after 24 hours unless undone")
    lines.append("")
    for r in await app.store.examples_recent(chat_id, 10):
        tag = "spam" if r["label"] == "spam" else "normal"
        scope = "shared" if r["chat_id"] == 0 else "group"
        source = "learned, waiting" if r["held_until"] else "learned" if r["source"] == "auto" else r["source"]
        lines.append(f"#{r['id']} {tag} · {scope} · {source}: {html.escape(r['text'][:70])}")
    lines += ["", "Teach it: reply /spam or /notspam to a message, or send /spam with an action number from /log, "
                  "or tap Teach as spam on a report. Remove one: /forget followed by the number."]
    if state == "shadow":
        lines.append("It is in shadow, so matches are only recorded. When the log looks right: /set similarity on")
    await reply(m, "\n".join(lines))


@router.message(Command("forget"))
async def cmd_forget(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks or not toks[0].lstrip("#").isdigit() or app.similar is None:
        await reply_temp(m, app, "How to use: /forget followed by the example number from /examples.")
        return
    row = await app.store.example_get(int(toks[0].lstrip("#")))
    chat_id = cid(m, app)
    allowed = row is not None and (row["chat_id"] == chat_id or (row["chat_id"] == 0 and app.config.is_owner(m.from_user.id)))
    if not allowed:
        await reply_temp(m, app, "There is no example with that number for this group. See /examples.")
        return
    await app.similar.forget(int(row["id"]), m.from_user.id if m.from_user else None)
    await reply_temp(m, app, f"Forgot example #{row['id']}. It no longer counts. /examples shows what is left.")


# Lists

@router.message(Command("allow", "approve"))
async def cmd_allow(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply(m, "Reply to the user or give @username / id.")
        return
    await app.store.allow_user(cid(m, app), target.user_id, m.from_user.id if m.from_user else None)
    await reply(m, f"<b>{who(target)} is now trusted here.</b> None of my filters apply to them. Undo with /disallow.")


@router.message(Command("disallow", "unapprove"))
async def cmd_disallow(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply(m, "Reply to the user or give @username / id.")
        return
    ok = await app.store.disallow_user(cid(m, app), target.user_id, m.from_user.id if m.from_user else None)
    await reply(m, f"<b>{who(target)} is no longer trusted.</b> My filters apply to them again." if ok else "They were not on the trusted list.")


@router.message(Command("allowed", "approved"))
async def cmd_allowed(m: Message, app: App) -> None:
    if not await require_admin(m, app):
        return
    ids = await app.store.list_allowed_users(cid(m, app))
    doms = await app.store.domains(cid(m, app))
    title = html.escape(await chat_title(m, app) or str(cid(m, app)))
    users = ", ".join([await user_label(app, cid(m, app), i) for i in ids]) or "none"
    lines = [f"🤝 <b>{title}</b> · trusted list", "",
             f"Trusted users: {users}",
             f"Always allowed links: {', '.join(sorted(doms['allow'])) or 'none'}",
             f"Always removed links: {', '.join(sorted(doms['block'])) or 'none'}", "",
             "Change: /allow, /disallow, /allowdomain, /blockdomain. Trusted users skip every filter."]
    await reply(m, "\n".join(lines))


_DOMAIN = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)


async def _domain_cmd(m: Message, app: App, command: CommandObject, kind: str) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        await reply(m, f"How to use: /{command.command} example.com")
        return
    added = []
    for t in toks:
        mt = _DOMAIN.match(t)
        if mt:
            await app.store.set_domain(cid(m, app), mt.group(1).lower(), kind, m.from_user.id if m.from_user else None)
            added.append(mt.group(1).lower())
    if not added:
        await reply(m, "That does not look like a website address. Example: example.com")
        return
    what = "always allowed" if kind == "allow" else "always removed"
    await reply(m, f"<b>Links to {', '.join(added)} are now {what} here.</b>")


@router.message(Command("allowdomain"))
async def cmd_allowdomain(m: Message, app: App, command: CommandObject) -> None:
    await _domain_cmd(m, app, command, "allow")


@router.message(Command("blockdomain"))
async def cmd_blockdomain(m: Message, app: App, command: CommandObject) -> None:
    await _domain_cmd(m, app, command, "block")


@router.message(Command("rule"))
async def cmd_rule(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    await rule_cmd(m, app, command, cid(m, app))


async def rule_cmd(m: Message, app: App, command: CommandObject, chat_id: int) -> None:
    raw = (command.args or "").strip()
    sub, _, rest = raw.partition(" ")
    sub = sub.lower()
    if sub == "list":
        rows = await app.store.list_rules(chat_id)
        if not rows:
            await reply(m, "No custom rules yet. Add one: /rule add &lt;pattern&gt; [action]\n"
                           "Example: /rule add (?i)dm me for signals ban 30d")
            return
        title = html.escape(await chat_title(m, app) or str(chat_id))
        lines = [f"📐 <b>{title}</b> · custom rules", f"<b>{len(rows)} rule{'s' if len(rows) != 1 else ''}</b>"]
        for r in rows:
            scope = " · all groups" if r["chat_id"] == 0 else ""
            act = action_words(format_action(r["action"], None)) if r["action"] else "the normal strike ladder"
            if r["action"] in {"mute", "ban"}:
                act = action_words(f"{r['action']} {format_duration(r['duration_s'])}")
            lines.append("")
            lines.append(f"<b>#{r['id']}</b> · {act}{scope}")
            lines.append(f"<code>{html.escape(r['pattern'])}</code>")
        lines.append("")
        lines.append("Remove one: /rule del followed by the number · try one: /test followed by a message")
        await reply(m, "\n".join(lines))
        return
    if sub in {"del", "delete", "rm"}:
        if not rest.strip().isdigit():
            await reply(m, "How to use: /rule del &lt;number&gt; (numbers are in /rule list)")
            return
        ok = await app.store.delete_rule(chat_id, int(rest.strip()), m.from_user.id if m.from_user else None)
        await reply(m, "<b>Rule removed.</b>" if ok else "There is no rule with that number here. See /rule list.")
        return
    if sub == "add":
        # pattern may contain spaces: take everything up to a trailing action
        toks = rest.split()
        if not toks:
            await reply(m, "How to use: /rule add &lt;pattern&gt; [delete | warn | kick | mute &lt;time&gt; | ban &lt;time&gt;]\n"
                           "Example: /rule add (?i)usdt.*commission ban 7d")
            return
        action: str | None = None
        dur_s: int | None = None
        pattern = rest
        actions = {"delete", "warn", "kick", "mute", "ban", "tmute", "tban"}
        # scan from the end for "<action> [dur]"
        if len(toks) >= 2 and toks[-2].lower() in actions:
            try:
                action, dur = parse_action(toks[-2:])
                dur_s = dur.seconds if dur else None
                pattern = " ".join(toks[:-2])
            except DurationError:
                pass
        if action is None and toks[-1].lower() in actions and len(toks) >= 2:
            try:
                action, dur = parse_action(toks[-1:])
                dur_s = dur.seconds if dur else None
                pattern = " ".join(toks[:-1])
            except DurationError:
                pass
        if not pattern.strip():
            await reply(m, "The pattern is empty. Example: /rule add (?i)dm me for signals")
            return
        problem = pattern_problem(pattern)
        if problem:
            await reply(m, f"{html.escape(problem)} Plain words work fine, e.g. /rule add free crypto")
            return
        rid = await app.store.add_rule(chat_id, pattern.strip(), action, dur_s, None,
                                       m.from_user.id if m.from_user else None)
        shown = action_words(format_action(action, None)) if action else "the normal strike ladder"
        if action in {"mute", "ban"}:
            shown = action_words(f"{action} {format_duration(dur_s)}")
        await reply(m, f"<b>Added rule {rid}.</b> Messages matching <code>{html.escape(pattern.strip())}</code> get: {shown}.")
        return
    await reply(m, "How to use:\n/rule add &lt;pattern&gt; [action]\n/rule del &lt;number&gt;\n/rule list")


# Visibility

def _parse_since(tok: str | None) -> timedelta:
    try:
        d = parse_duration(tok or "24h")
        return timedelta(seconds=d.seconds or 86400)
    except DurationError:
        return timedelta(hours=24)


@router.message(Command("stats"))
async def cmd_stats(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    since = _parse_since(toks[0] if toks else None)
    s = await app.store.stats(cid(m, app), utcnow() - since)
    from .scheduler import digest_text
    from .support import digest_line
    support = digest_line(await app.store.kb_question_counts(cid(m, app), utcnow() - since))
    if support:
        s = {**s, "support_line": support}
    title = await chat_title(m, app) or str(cid(m, app))
    await reply(m, digest_text(title, s, f"last {long_duration(format_duration(int(since.total_seconds())))}"))


@router.message(Command("log"))
async def cmd_log(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    n = int(toks[0]) if toks and toks[0].isdigit() else 10
    rows = await app.store.recent_audit(cid(m, app), min(n, 30))
    if not rows:
        await reply(m, "I have not taken any action here yet. Try /test with a spammy text to see how I decide.")
        return
    title = await chat_title(m, app)
    lines = [f"📋 <b>{html.escape(title or str(cid(m, app)))}</b> · last {len(rows)} action{'s' if len(rows) != 1 else ''}"]
    for r in rows:
        seconds = r.duration_s if r.action in {"mute", "ban"} else None
        if r.mode == "shadow":
            what = f"Would {action_would(r.action, seconds)} (shadow)"
        else:
            what = action_done(r.action, seconds)
        tag = " · UNDONE" if r.undone_at else ""
        user = f"@{r.username}" if r.username else f"id {r.user_id}"
        lines.append("")
        lines.append(f"<b>#{r.id}</b> · {ago(r.ts)}{tag}")
        lines.append(f"{html.escape(what)} · {html.escape(user)}")
        lines.append(html.escape(r.reason[:80]))
    lines.append("")
    lines.append("Tap a button below, or send /why or /undo with the number.")
    buttons = [[InlineKeyboardButton(text=f"Why {r.id}", callback_data=f"why:{r.id}"),
                InlineKeyboardButton(text=f"Undo {r.id}", callback_data=f"undo:{r.id}")]
               for r in rows[:5] if not r.undone_at]
    await reply(m, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None)


@router.message(Command("undo"))
async def cmd_undo(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks or not toks[0].lstrip("#").isdigit():
        await reply(m, "How to use: /undo followed by the action number. The number is in every report and in /log.")
        return
    row = await app.store.get_audit(int(toks[0].lstrip("#")))
    if not row or row.chat_id != cid(m, app):
        await reply(m, "There is no action with that number in this group. See /log.")
        return
    result = await app.enforcer.undo(row.id, m.from_user.id if m.from_user else None)
    await reply(m, html.escape(result) + await _learn_from_undo(app, row, result, m.from_user.id if m.from_user else None))


async def _learn_from_undo(app: App, row, result: str, by: int | None) -> str:
    """An undone action means the message was fine: teach the meaning match. Returns a note."""
    if app.similar is None or not result.startswith(("Undone", "Marked")):
        return ""
    try:
        new_id = await app.similar.learn_from_undo(row, by)
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("similar.undo", f"{type(e).__name__}: {e}", row.chat_id)
        return ""
    return f"\nI also saved that message as a normal one (#{new_id}), so similar messages count less." if new_id else ""


@router.callback_query(F.data.startswith("undo:"))
async def cb_undo(cb: CallbackQuery, app: App) -> None:
    if not genuine(cb):
        await reject(cb)
        return
    try:
        audit_id = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    row = await app.store.get_audit(audit_id)
    if not row:
        await cb.answer("Unknown action.")
        return
    if not await app.controls(cb.from_user.id, row.chat_id):
        await cb.answer("Admins of that group only.", show_alert=True)
        return
    result = await app.enforcer.undo(audit_id, cb.from_user.id)
    result += await _learn_from_undo(app, row, result, cb.from_user.id)
    await cb.answer(result[:190], show_alert=True)
    try:
        await cb.message.edit_text(cb.message.html_text + f"\n\n{html.escape(result)}", parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass


# Settings shortcuts

async def _set(m: Message, app: App, key: str, value: str) -> None:
    if key == "modlog_chat_id" and value.strip() and m.from_user:
        problem = await _log_target_problem(app, m.from_user.id, value.strip())
        if problem:
            await reply(m, problem)
            return
    from .support import CONTACT_KEYS, SUPPORT_KEYS, report_change, setting_problem
    if key in SUPPORT_KEYS and m.from_user:
        problem = await setting_problem(app, m.from_user.id, cid(m, app), key, value)
        if problem:
            await reply(m, html.escape(problem))
            return
    old = (await app.settings.get(cid(m, app))).raw(key)
    try:
        stored = await app.settings.set(cid(m, app), key, value, m.from_user.id if m.from_user else None)
    except ValueError as e:
        await reply(m, html.escape(str(e)))
        return
    if key in CONTACT_KEYS:
        await report_change(app, cid(m, app), key, old, stored, m.from_user)
    if key == "mode":
        if stored == "enforce":
            await reply(m, "<b>Saved. Mode is now enforce:</b> spam is deleted and senders are punished. "
                           "Switch back any time with <code>/mode shadow</code>.")
        else:
            await reply(m, "<b>Saved. Mode is now shadow:</b> I only report what I would do. "
                           "Turn it on for real with <code>/mode enforce</code>.")
        return
    shown = (action_words(stored, long=True) if SPECS[key].kind == "action" else
             app.settings.shown(SPECS[key], stored) if SPECS[key].kind == "languages" else stored)
    await reply(m, f"<b>Saved.</b> {SPECS[key].label} is now: {html.escape(shown)}")


async def _log_target_problem(app: App, user_id: int, value: str) -> str | None:
    """Reports go only where the setter is allowed to send them: their own private chat,
    or a chat where they are an admin and I am present."""
    if not value.lstrip("-").isdigit():
        return "The log target must be a chat id. Send /id in the log group to get it."
    target = int(value)
    if target == user_id or app.config.is_owner(user_id):
        return None
    if await app.store.chat_owner(target) not in (None, user_id) and not await app.controls(user_id, target):
        return "That chat belongs to someone else."
    admins = await app.admins.admins(target, force=True)
    if user_id in admins:
        return None
    return ("I cannot use that chat. Add me to your log group, make sure you are an admin there, "
            "send /id there and use that id. Or leave it empty to get reports in our private chat.")


@router.message(Command("set"))
async def cmd_set(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if len(toks) < 2:
        await reply(m, "How to use: /set &lt;setting&gt; &lt;value&gt;\nExample: /set captcha off\nAll settings: /settings list")
        return
    key = toks[0].lower()
    if key not in SPECS:
        await reply(m, f"There is no setting called '{html.escape(key)}'. See /settings list for the names.")
        return
    await _set(m, app, key, " ".join(toks[1:]))


@router.message(Command("unset"))
async def cmd_unset(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks or toks[0].lower() not in SPECS:
        await reply(m, "How to use: /unset &lt;setting&gt;. Puts it back to the default.")
        return
    from .support import CONTACT_KEYS, SUPPORT_KEYS, report_change, setting_problem
    key = toks[0].lower()
    if key in SUPPORT_KEYS and m.from_user:
        problem = await setting_problem(app, m.from_user.id, cid(m, app), key, None)
        if problem:
            await reply(m, html.escape(problem))
            return
    old = (await app.settings.get(cid(m, app))).raw(key)
    await app.settings.reset(cid(m, app), key, m.from_user.id if m.from_user else None)
    if key in CONTACT_KEYS:
        await report_change(app, cid(m, app), key, old, SPECS[key].default, m.from_user)
    await reply(m, f"<b>Reset.</b> {SPECS[toks[0].lower()].label} is back to the default: {SPECS[toks[0].lower()].default}")


@router.message(Command("mode"))
async def cmd_mode(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        cs = await app.settings.get(cid(m, app))
        mode = cs.raw("mode")
        meaning = ("spam is deleted and the sender is punished" if mode == "enforce"
                   else "I only report what I would do, nothing is deleted")
        title = html.escape(await chat_title(m, app) or str(cid(m, app)))
        await reply(m, f"⚙️ <b>{title}</b> · mode\n<b>{mode}</b> · {meaning}\n\nChange it: /mode shadow or /mode enforce")
        return
    await _set(m, app, "mode", toks[0])


@router.message(Command("pause"))
async def cmd_pause(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    try:
        d = parse_duration(toks[0] if toks else "30m")
    except DurationError as e:
        await reply(m, html.escape(str(e)))
        return
    if d.permanent:
        await reply(m, "Give a time, e.g. /pause 30m. To stop for good use /mode shadow.")
        return
    app.paused_until[cid(m, app)] = utcnow() + timedelta(seconds=d.seconds or 0)
    from . import ledger
    await ledger.note(app, "pause", cid(m, app), actor=m.from_user, duration=long_duration(str(d)))
    await reply(m, f"<b>Paused for {long_duration(str(d))}.</b> I will not filter anything until then. End early with /resume.")


@router.message(Command("resume"))
async def cmd_resume(m: Message, app: App) -> None:
    if not await require_admin(m, app):
        return
    was_paused = app.paused_until.pop(cid(m, app), None)
    if was_paused:
        from . import ledger
        await ledger.note(app, "resume", cid(m, app), actor=m.from_user)
    await reply(m, "<b>Resumed.</b> Filtering is back on.")


async def _set_action(m: Message, app: App, command: CommandObject, key: str) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        cs = await app.settings.get(cid(m, app))
        await reply(m, f"{SPECS[key].label} is: {action_words(cs.raw(key), long=True)}\n"
                       f"Change it: /{command.command} delete | warn | kick | mute &lt;time&gt; | ban &lt;time&gt;")
        return
    await _set(m, app, key, " ".join(toks))


async def _set_duration(m: Message, app: App, command: CommandObject, key: str) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        cs = await app.settings.get(cid(m, app))
        await reply(m, f"{SPECS[key].label} is: {long_duration(cs.raw(key))}\nChange it: /{command.command} followed by a time. {HELP_TEXT}")
        return
    await _set(m, app, key, toks[0])


@router.message(Command("setstrike"))
async def cmd_setstrike(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if len(toks) < 2 or toks[0] not in {"1", "2", "3"}:
        await reply(m, "How to use: /setstrike &lt;1, 2 or 3&gt; &lt;action&gt;\nExample: /setstrike 1 mute 24h")
        return
    await _set(m, app, f"strike{toks[0]}", " ".join(toks[1:]))


@router.message(Command("setstriketime"))
async def cmd_setstriketime(m: Message, app: App, command: CommandObject) -> None:
    await _set_duration(m, app, command, "strike_ttl")


@router.message(Command("setwarnlimit"))
async def cmd_setwarnlimit(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        await reply(m, "How to use: /setwarnlimit 3 (warns before the warn action fires)")
        return
    await _set(m, app, "warn_limit", toks[0])


@router.message(Command("setwarnmode"))
async def cmd_setwarnmode(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "warn_action")


@router.message(Command("setflood"))
async def cmd_setflood(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        cs = await app.settings.get(cid(m, app))
        await reply(m, f"Flood rule: more than {cs.raw('flood_count')} messages in {cs.raw('flood_window')} gets: "
                       f"{action_words(cs.raw('flood_action'))}\n"
                       "Change it: /setflood &lt;count&gt; [window], e.g. /setflood 5 10s. /setflood 0 turns it off.")
        return
    if toks[0] in {"0", "off"}:
        await _set(m, app, "flood_count", "0")
        return
    try:
        await app.settings.set(cid(m, app), "flood_count", toks[0], m.from_user.id if m.from_user else None)
        if len(toks) > 1:
            await app.settings.set(cid(m, app), "flood_window", toks[1], m.from_user.id if m.from_user else None)
    except ValueError as e:
        await reply(m, html.escape(str(e)))
        return
    cs = await app.settings.get(cid(m, app))
    await reply(m, f"<b>Saved.</b> More than {cs.raw('flood_count')} messages in {cs.raw('flood_window')} now gets: "
                   f"{action_words(cs.raw('flood_action'))}")


@router.message(Command("setfloodmode"))
async def cmd_setfloodmode(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "flood_action")


@router.message(Command("setnewaccount"))
async def cmd_setnewaccount(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if len(toks) < 2:
        await reply(m, "How to use: /setnewaccount &lt;age&gt; &lt;action&gt;\nExample: /setnewaccount 1h ban 30d")
        return
    try:
        await app.settings.set(cid(m, app), "new_account_age", toks[0], m.from_user.id if m.from_user else None)
        await app.settings.set(cid(m, app), "new_account_action", " ".join(toks[1:]), m.from_user.id if m.from_user else None)
    except ValueError as e:
        await reply(m, html.escape(str(e)))
        return
    cs = await app.settings.get(cid(m, app))
    await reply(m, f"<b>Saved.</b> Spam from accounts I first saw less than {cs.raw('new_account_age')} ago now gets: "
                   f"{action_words(cs.raw('new_account_action'))}")


@router.message(Command("setcaptchatime"))
async def cmd_setcaptchatime(m: Message, app: App, command: CommandObject) -> None:
    await _set_duration(m, app, command, "captcha_time")


@router.message(Command("setcaptchafail"))
async def cmd_setcaptchafail(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "captcha_fail")


@router.message(Command("setrestrict"))
async def cmd_setrestrict(m: Message, app: App, command: CommandObject) -> None:
    await _set_duration(m, app, command, "restrict_window")


@router.message(Command("setjudgemode"))
async def cmd_setjudgemode(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "judge_action")


@router.message(Command("setcasmode"))
async def cmd_setcasmode(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "cas_action")


@router.message(Command("setsafety"))
async def cmd_setsafety(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    toks = args(command)
    if not toks:
        cs = await app.settings.get(cid(m, app))
        await reply(m, f"Safety check is {cs.raw('safety')}. Sender gets: {action_words(cs.raw('safety_action'))}\n"
                       "Change it: /setsafety on or /setsafety off, /setsafetymode mute 1d")
        return
    await _set(m, app, "safety", toks[0])


@router.message(Command("setsafetymode"))
async def cmd_setsafetymode(m: Message, app: App, command: CommandObject) -> None:
    await _set_action(m, app, command, "safety_action")


@router.message(Command("setgreeting"))
async def cmd_setgreeting(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    if not (command.args or "").strip():
        await app.settings.set(cid(m, app), "greeting", "", m.from_user.id if m.from_user else None)
        await reply(m, "<b>Greeting removed.</b> New members get no message after the captcha. "
                       "Set one: <code>/setgreeting Welcome {name}!</code>")
        return
    await _set(m, app, "greeting", command.args or "")


# /settings menu

async def send_settings_menu(app: App, user_id: int, chat_id: int, title: str | None) -> bool:
    cs = await app.settings.get(chat_id)
    try:
        await app.bot.send_message(user_id, app.settings.menu_text(cs, None, title), parse_mode="HTML",
                                   reply_markup=app.settings.menu_markup(cs, None))
        return True
    except Exception:  # noqa: BLE001
        return False


@router.message(Command("settings"))
async def cmd_settings(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app) or not m.from_user:
        return
    chat_id = cid(m, app)
    cs = await app.settings.get(chat_id)
    title = await chat_title(m, app)
    if (command.args or "").strip().lower() == "list":
        await reply(m, app.settings.list_text(cs, title))
        return
    if m.chat.type == ChatType.PRIVATE:
        await send_settings_menu(app, m.from_user.id, chat_id, title)
        return
    if await send_settings_menu(app, m.from_user.id, chat_id, title):
        await reply(m, "Sent the settings menu to your private chat with me.")
    else:
        await reply(m, "I cannot message you privately yet. Open a chat with me, press Start, then run /settings again. "
                       "Or use the text version: /settings list")


@router.callback_query(F.data.startswith("st:"))
async def cb_settings(cb: CallbackQuery, app: App) -> None:
    if not genuine(cb) or cb.message.chat.type != ChatType.PRIVATE:
        await reject(cb)
        return
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return
    op, chat_id = parts[1], int(parts[2])
    if chat_id not in app.allowed_chats or not await app.controls(cb.from_user.id, chat_id):
        await cb.answer("Admins of that group only.", show_alert=True)
        return
    cur = await app.store.db.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,))
    row = await cur.fetchone()
    title = row["title"] if row else None
    if op == "x":
        try:
            await cb.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await cb.answer()
        return
    group: str | None = None
    if op == "g":
        group = None if parts[3] == "_" else parts[3]
    elif op == "s" and len(parts) >= 5:
        key, value = parts[3], ":".join(parts[4:])
        try:
            await app.settings.set(chat_id, key, value, cb.from_user.id)
        except ValueError as e:
            await cb.answer(str(e)[:190], show_alert=True)
            return
        group = SPECS[key].group
    cs = await app.settings.get(chat_id)
    try:
        await cb.message.edit_text(app.settings.menu_text(cs, group, title), parse_mode="HTML",
                                   reply_markup=app.settings.menu_markup(cs, group))
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("Saved." if op == "s" else None)
