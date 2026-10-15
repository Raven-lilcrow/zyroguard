"""Bot-owner commands (OWNER_IDS): /groups, /usage, /permit, /revoke, /permits, /purge, /addadmin, /addmod, /showadmin."""

from __future__ import annotations

import html
from datetime import timedelta

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .app import App
from .commands import GROUPS, args, cid, reply, resolve_target, who
from .durations import DurationError, parse_duration
from .store import from_iso, utcnow

router = Router(name="owner")


def is_owner(m: Message, app: App) -> bool:
    return bool(m.from_user and app.config.is_owner(m.from_user.id))


@router.message(Command("groups"))
async def cmd_groups(m: Message, app: App) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    chats = await app.store.list_chats(active_only=False)
    if not chats:
        await reply(m, "No groups yet.")
        return
    active = [c for c in chats if c["active"]]
    lines = [f"🗂 <b>All groups</b> · {len(active)} active"]
    for c in chats:
        cid = int(c["chat_id"])
        cs = await app.settings.get(cid)
        n = await app.store.member_count(cid)
        head = f"<b>{html.escape(c['title'] or str(cid))}</b> · {cs.raw('mode')} · {n} members seen"
        if not c["active"]:
            head = f"<b>{html.escape(c['title'] or str(cid))}</b> · I left this one"
        detail = f"id <code>{cid}</code>"
        if c["owner_user_id"]:
            detail += f" · permit holder <code>{c['owner_user_id']}</code>"
        lines += ["", head, detail]
    lines += ["", "Manage one from here: /use followed by the group name"]
    await reply(m, "\n".join(lines))


@router.message(Command("usage"))
async def cmd_usage(m: Message, app: App, command: CommandObject) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    toks = (command.args or "").split()
    try:
        d = parse_duration(toks[0] if toks else "7d")
        since = utcnow() - timedelta(seconds=d.seconds or 7 * 86400)
    except DurationError:
        since = utcnow() - timedelta(days=7)
    rows = await app.store.judge_usage_summary(since)
    if not rows:
        await reply(m, "No AI calls in that period.")
        return
    lines = [f"💳 <b>AI usage</b> · since {since:%b %d}", ""]
    tot_calls = tot_in = tot_out = 0
    for r in rows:
        calls, inp, out = int(r["calls"]), int(r["inp"] or 0), int(r["out"] or 0)
        tot_calls += calls
        tot_in += inp
        tot_out += out
        chat = await app.store.get_chat(int(r["chat_id"]))
        name = html.escape(chat["title"]) if chat and chat["title"] else f"chat {r['chat_id']}"
        kind = {"judge": "spam and safety checks", "questions": "questions"}.get(r["kind"], r["kind"])
        lines.append(f"{name} · {kind}: {calls} calls, {inp + out:,} tokens")
    lines.append("")
    lines.append(f"<b>Total: {tot_calls} calls, {tot_in + tot_out:,} tokens</b> ({tot_in:,} in, {tot_out:,} out) · model {html.escape(rows[0]['model'])}")
    await reply(m, "\n".join(lines))


# ---- bot admins and mods (owner only, per group) ---------------------------

async def _owner_target(m: Message, app: App, command: CommandObject):
    if not is_owner(m, app):
        raise SkipHandler
    if m.chat.type not in GROUPS:
        from .commands import pick_group
        if await pick_group(m, app) is None:
            return None
    target, _ = await resolve_target(m, app, args(command))
    if not target or not target.user_id:
        await reply(m, "Reply to the user or give @username / id.")
        return None
    return target


@router.message(Command("addadmin", "add_admin"))
async def cmd_addadmin(m: Message, app: App, command: CommandObject) -> None:
    target = await _owner_target(m, app, command)
    if not target:
        return
    await app.store.add_bot_admin(cid(m, app), target.user_id, m.from_user.id)
    await reply(m, f"{who(target)} can now use the admin commands here and is exempt from all filters.")


@router.message(Command("removeadmin", "remove_admin"))
async def cmd_removeadmin(m: Message, app: App, command: CommandObject) -> None:
    target = await _owner_target(m, app, command)
    if not target:
        return
    ok = await app.store.remove_bot_admin(cid(m, app), target.user_id)
    await reply(m, f"{who(target)} is no longer a bot admin here." if ok else f"{who(target)} was not a bot admin.")


@router.message(Command("addmod", "add_mod"))
async def cmd_addmod(m: Message, app: App, command: CommandObject) -> None:
    target = await _owner_target(m, app, command)
    if not target:
        return
    await app.store.allow_user(cid(m, app), target.user_id, m.from_user.id)
    await reply(m, f"{who(target)} is now trusted here: my filters skip them, no admin commands. Same as /allow.")


@router.message(Command("removemod", "remove_mod"))
async def cmd_removemod(m: Message, app: App, command: CommandObject) -> None:
    target = await _owner_target(m, app, command)
    if not target:
        return
    ok = await app.store.disallow_user(cid(m, app), target.user_id)
    await reply(m, f"{who(target)} is no longer trusted here." if ok else f"{who(target)} was not on the trusted list.")


@router.message(Command("showadmin", "show_admin", "showadmins"))
async def cmd_showadmin(m: Message, app: App) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    if m.chat.type not in GROUPS:
        from .commands import pick_group
        if await pick_group(m, app) is None:
            return
    chat_id = cid(m, app)

    async def label(uid: int) -> str:
        mem = await app.store.get_member(chat_id, uid)
        if mem and mem["username"]:
            return f"@{mem['username']} (<code>{uid}</code>)"
        if mem and mem["display_name"]:
            return f"{html.escape(mem['display_name'])} (<code>{uid}</code>)"
        return f"<code>{uid}</code>"

    tg_admins = [i for i in (await app.admins.admins(chat_id)) if i != app.bot_id]

    async def section(title: str, note: str, ids: list[int]) -> list[str]:
        out = [f"<b>{title}</b> · {note}"]
        out += [await label(i) for i in ids] or ["none"]
        out.append("")
        return out

    row = await app.store.get_chat(chat_id)
    lines: list[str] = [f"👥 <b>{html.escape(row['title'] if row and row['title'] else str(chat_id))}</b> · who controls me",
                        f"<b>{len(tg_admins)} group admin{'s' if len(tg_admins) != 1 else ''}</b>", ""]
    lines += await section("Bot owners", "full control, set on the server", sorted(app.config.owner_ids))
    lines += await section("Group admins", "Telegram admins, can use all my commands", sorted(tg_admins))
    lines += await section("Bot admins", "added with /addadmin, can use my commands", await app.store.list_bot_admins(chat_id))
    lines += await section("Trusted users", "added with /allow or /addmod, my filters skip them", await app.store.list_allowed_users(chat_id))
    await reply(m, "\n".join(lines).rstrip())


# ---- permits: who may add me to their own groups ---------------------------

async def _user_from_token(app: App, tok: str) -> tuple[int | None, str | None]:
    """@username (seen anywhere) or numeric id -> (user_id, username)."""
    if tok.lstrip("-").isdigit():
        cur = await app.store.db.execute(
            "SELECT username FROM members WHERE user_id=? ORDER BY last_msg_at DESC LIMIT 1", (int(tok),))
        row = await cur.fetchone()
        return int(tok), (row["username"] if row else None)
    if tok.startswith("@"):
        cur = await app.store.db.execute(
            "SELECT user_id, username FROM members WHERE lower(username)=? ORDER BY last_msg_at DESC LIMIT 1",
            (tok[1:].lower(),))
        row = await cur.fetchone()
        if row:
            return int(row["user_id"]), row["username"]
    return None, None


@router.message(Command("permit"))
async def cmd_permit(m: Message, app: App, command: CommandObject) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    toks = args(command)
    usage = ("How to use: /permit @user or id, then optionally how many groups, how many days, and a note.\n"
             "Example: /permit @alice 2 30d paid plan\n"
             "Do not know their id? Ask them to send /id to me in a private chat.")
    if not toks:
        await reply(m, usage)
        return
    user_id, username = await _user_from_token(app, toks[0])
    if user_id is None:
        await reply(m, "I do not know that username yet. " + usage)
        return
    rest = toks[1:]
    max_groups = 1
    expires = None
    if rest and rest[0].isdigit():
        max_groups = max(1, int(rest.pop(0)))
    if rest:
        try:
            d = parse_duration(rest[0])
            if d.seconds:
                expires = utcnow() + timedelta(seconds=d.seconds)
            rest.pop(0)
        except DurationError:
            pass
    note = " ".join(rest) or None
    await app.store.add_permit(user_id, username, max_groups, expires, note, m.from_user.id)
    label = f"@{username}" if username else f"<code>{user_id}</code>"
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
    await reply(m, f"<b>Permit given</b> to {label}: up to {max_groups} group(s), {until}.\n"
                  + ("I sent them the steps in their private chat." if told else
                     "I could not message them yet (they have not started a chat with me). Tell them to press Start on @"
                     + app.bot_username + " and add me to their group.") + "\n"
                  "Take it back any time with /revoke.")


@router.message(Command("revoke"))
async def cmd_revoke(m: Message, app: App, command: CommandObject) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    toks = args(command)
    if not toks:
        await reply(m, "How to use: /revoke @user or id. I leave all their groups and delete their records.")
        return
    user_id, username = await _user_from_token(app, toks[0])
    if user_id is None:
        await reply(m, "I do not know that username. Use the numeric id from /permits.")
        return
    had = await app.store.revoke_permit(user_id)
    left = 0
    for c in await app.store.chats_owned_by(user_id, active_only=False):
        cid = int(c["chat_id"])
        try:
            await app.bot.leave_chat(cid)
        except Exception:  # noqa: BLE001
            pass
        await app.store.purge_chat(cid)
        app.allowed_chats.discard(cid)
        app.settings.invalidate(cid)
        app.admins.invalidate(cid)
        app.dm_context = {u: g for u, g in app.dm_context.items() if g != cid}
        left += 1
    app.dm_context.pop(user_id, None)
    label = f"@{username}" if username else f"<code>{user_id}</code>"
    if not had and not left:
        await reply(m, f"{label} had no permit.")
        return
    await reply(m, f"<b>Permit revoked</b> for {label}. Left {left} group(s) and deleted their records.")


@router.message(Command("permits"))
async def cmd_permits(m: Message, app: App) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    rows = await app.store.list_permits()
    if not rows:
        await reply(m, "No permits yet. Give one with /permit @user.")
        return
    lines = [f"🎫 <b>Permits</b> · {len(rows)}"]
    for p in rows:
        uid = int(p["user_id"])
        label = f"@{p['username']}" if p["username"] else f"<code>{uid}</code>"
        groups = await app.store.chats_owned_by(uid)
        state = "active" if p["active"] else "revoked"
        exp = from_iso(p["expires_at"]) if p["expires_at"] else None
        if exp and exp < utcnow():
            state = "expired"
        when = f", ends {exp:%b %d, %Y}" if exp else ""
        note = f", {html.escape(p['note'])}" if p["note"] else ""
        names = ", ".join(html.escape(g["title"] or str(g["chat_id"])) for g in groups) or "no group yet"
        lines += ["", f"<b>{label}</b> · {state} · {len(groups)}/{p['max_groups']} groups{when}{note}",
                  f"id <code>{uid}</code> · {names}"]
    lines += ["", "Give one: /permit @user · take it back: /revoke @user"]
    await reply(m, "\n".join(lines))


@router.message(Command("purge"))
async def cmd_purge(m: Message, app: App, command: CommandObject) -> None:
    if not is_owner(m, app):
        raise SkipHandler
    toks = args(command)
    if not toks or not toks[0].lstrip("-").isdigit():
        await reply(m, "How to use: /purge &lt;group id&gt;. Deletes every record of a group I have already left. Ids are in /groups.")
        return
    cid = int(toks[0])
    row = await app.store.get_chat(cid)
    if row and row["active"]:
        await reply(m, "I am still in that group. Remove me from it first, then /purge.")
        return
    n = await app.store.purge_chat(cid)
    app.allowed_chats.discard(cid)
    app.settings.invalidate(cid)
    app.admins.invalidate(cid)
    await reply(m, f"Deleted {n} record(s) for group <code>{cid}</code>.")


@router.message(Command("reloadrules"))
async def cmd_reloadrules(m: Message, app: App) -> None:
    """Re-read rules.yaml without a restart. A broken file is reported and the old rules stay."""
    if not is_owner(m, app):
        raise SkipHandler
    try:
        rules = app.reload_rules()
    except Exception as e:  # noqa: BLE001
        await reply(m, f"<b>Rules not reloaded.</b> The file has a problem: {html.escape(str(e))[:300]}\n"
                       "The previous rules are still in use.")
        return
    await reply(m, f"<b>Rules reloaded.</b> {len(rules.hard)} hard rules, {len(rules.soft)} soft rules, "
                   f"{len(rules.safety_triggers)} safety triggers.")
