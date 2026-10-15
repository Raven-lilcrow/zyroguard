"""Troubleshooting: /diag, /test, /why, /id."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .app import App
from .callbacks import genuine, reject
from .commands import chat_title, cid, require_admin
from .pipeline import job_offer, phishing_offer, real_reply
from .durations import format_duration
from .rules import Context, MessageView, evaluate
from .text import action_done, action_past_long, action_words, action_would, ago, long_duration, source_words, when


router = Router(name="diag")
GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}


OK, BAD, OFF = "✅", "❌", "➖"


async def diag_report(app: App, chat_id: int, title: str | None) -> str:
    cs = await app.settings.get(chat_id)
    rights = await app.admins.bot_rights(chat_id, force=True)
    admins = await app.admins.admins(chat_id, force=True)
    todo: list[str] = []

    def mark(ok: bool, text: str, fix: str | None = None) -> str:
        if not ok and fix:
            todo.append(fix)
        return f"{OK if ok else BAD} {text}"

    # -- rights
    try:
        me = await app.bot.get_me()
        reads_all = bool(me.can_read_all_group_messages) or rights.is_admin
    except Exception:  # noqa: BLE001
        reads_all = rights.is_admin
    rights_line = " · ".join([
        mark(rights.is_admin, "admin", "Make me an admin: group settings › Administrators › Add admin › ZyroGuard."),
        mark(rights.can_delete, "delete messages", "In my admin rights, turn on 'Delete messages'."),
        mark(rights.can_restrict, "mute and ban", "In my admin rights, turn on 'Ban users'."),
        mark(rights.can_invite, "invite users", "In my admin rights, turn on 'Invite users via link'."),
        mark(reads_all, "read all messages",
             "Open @BotFather › /setprivacy › pick this bot › Disable, then remove and re-add me to the group."),
    ])

    # -- connections
    customer = await app.store.chat_owner(chat_id)
    is_customer = customer is not None and not app.config.is_owner(customer)
    conn = []
    if cs.bool("judge"):
        ok, why = await app.judge.ping()
        conn.append(mark(ok, "AI judge" if ok else f"AI judge ({html.escape(why)})",
                         ("The AI judge is not answering. Rules still work. Tell the admin with /contact, or turn it off with /set judge off."
                          if is_customer else
                          "The AI judge is not answering. Check the API key and base URL in .env on the server, "
                          "or turn it off with /set judge off (rules still work without it).")))
    else:
        conn.append(f"{OFF} AI judge off")
    modlog = cs.modlog_chat_id or app.config.modlog_chat_id
    if is_customer and not cs.modlog_chat_id:
        reports_to = "the private chat of the person who added me"
        conn.append(f"{OK} reports")
    else:
        conn.append(mark(bool(modlog), "log group", "Set a private group where I post what I did: run /id there, "
                         "then /set modlog_chat_id &lt;that id&gt; here."))
        reports_to = f"chat <code>{cs.modlog_chat_id}</code>" if cs.modlog_chat_id else "the server log group"
    conn.append(mark(await app.store.writable(), "database",
                     "I cannot write to my database. Tell the admin with /contact." if is_customer else
                     "I cannot write to my database. Check the /data volume on the server."))
    if app.config.n8n_event_url and not is_customer:
        ok, why = await app.enforcer.ping_n8n()
        conn.append(mark(ok, "n8n" if ok else f"n8n ({html.escape(why)})",
                         "n8n is not accepting my reports (optional feature). Import n8n/ZyroGuard-Modlog.json into n8n "
                         "and switch it on, or remove N8N_EVENT_URL from .env to stop this warning."))
    if not len(admins):
        conn.append(mark(False, "admin list", "I could not read the admin list. Make sure I am an admin and try again."))

    # -- mode
    mode = cs.raw("mode")
    if app.is_paused(chat_id):
        mode_line = "paused, I watch but do nothing until /resume"
    elif mode == "enforce":
        mode_line = "enforce, spam is deleted and the sender is punished"
    else:
        mode_line = "shadow, I only report what I would do"

    # -- setup
    if cs.bool("captcha"):
        how = {"emoji": "pick the right word for a picture", "math": "answer a small sum",
               "button": "tap a button"}.get(cs.raw("captcha_mode"), "pass the captcha")
        newcomers = f"must {how} within {long_duration(cs.raw('captcha_time'))}, else {action_past_long(cs.raw('captcha_fail'))}"
    else:
        newcomers = "no captcha, anyone can talk right away"
    if cs.bool("safety"):
        explicit = f"checked by AI, message deleted and the sender {action_past_long(cs.raw('safety_action'))}"
    else:
        explicit = "not checked (turn on: /setsafety on)"
    langs = [c for c in cs.raw("languages").split(",") if c]
    if langs:
        from .language import name as language_name
        who = "new members" if cs.raw("language_scope") == "new" else "everyone"
        languages = (f"{', '.join(language_name(c) for c in langs)} only; other languages from {who} "
                     "are deleted (no strike)")
    else:
        languages = "all allowed (limit with /set languages en)"
    ladder = (f"1st {action_past_long(cs.raw('strike1'))} · 2nd {action_past_long(cs.raw('strike2'))} · "
              f"3rd {action_past_long(cs.raw('strike3'))}")

    head = f"🔎 <b>{html.escape(title or str(chat_id))}</b> · check"
    verdict = "<b>Everything looks good.</b>" if not todo else f"<b>{len(todo)} thing{'s' if len(todo) != 1 else ''} to fix.</b>"
    lines = [head, verdict, "",
             f"Rights: {rights_line}",
             f"Connections: {' · '.join(conn)}",
             f"Reports go to: {reports_to}",
             f"Mode: {mode_line}", "",
             f"New members: {newcomers}",
             f"Explicit content: {explicit}",
             f"Languages: {languages}",
             f"Spam ladder: {ladder}",
             f"Strikes forgotten after: {long_duration(cs.raw('strike_ttl'))}", ""]
    if todo:
        lines.append("<b>Fix these</b>")
        lines.extend(f"{i}. {t}" for i, t in enumerate(todo, 1))
    elif mode == "shadow":
        lines.append("Next step: leave it like this for a day, watch my reports, then send <code>/mode enforce</code>.")
    else:
        lines.append("Next step: send /test followed by a spammy message to see what I would do with it.")
    return "\n".join(lines)


def _diag_buttons(chat_id: int, text: str) -> InlineKeyboardMarkup | None:
    if "Everything looks good" in text and "Mode: shadow" in text:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Switch to enforce", callback_data=f"act:enforce:{chat_id}")]])
    return None


@router.message(Command("diag"))
async def cmd_diag(m: Message, app: App) -> None:
    if not await require_admin(m, app):
        return
    chat_id = cid(m, app)
    text = await diag_report(app, chat_id, await chat_title(m, app))
    await m.reply(text, parse_mode="HTML", reply_markup=_diag_buttons(chat_id, text))


@router.callback_query(F.data.startswith("act:"))
async def cb_action(cb: CallbackQuery, app: App) -> None:
    """Buttons on onboarding and /diag: run the check, open settings, switch to enforce (with a confirm step)."""
    if not genuine(cb):
        await reject(cb)
        return
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return
    op, chat_id = parts[1], int(parts[2])
    if op == "cancel":
        await cb.answer("Left as it is.")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return
    if chat_id not in app.allowed_chats or not await app.controls(cb.from_user.id, chat_id):
        await cb.answer("Admins of that group only.", show_alert=True)
        return
    row = await app.store.get_chat(chat_id)
    title = row["title"] if row else None
    if op == "diag":
        text = await diag_report(app, chat_id, title)
        await cb.message.answer(text, parse_mode="HTML", reply_markup=_diag_buttons(chat_id, text))
        await cb.answer()
    elif op == "settings":
        from .commands import send_settings_menu
        await send_settings_menu(app, cb.from_user.id, chat_id, title)
        await cb.answer()
    elif op == "enforce":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Yes, start deleting", callback_data=f"act:enforce2:{chat_id}"),
            InlineKeyboardButton(text="Cancel", callback_data=f"act:cancel:{chat_id}")]])
        await cb.message.answer(f"Switch <b>{html.escape(title or str(chat_id))}</b> to enforce? From then on spam is really "
                                "deleted and senders are muted or banned.", parse_mode="HTML", reply_markup=kb)
        await cb.answer()
    elif op == "enforce2":
        await app.settings.set(chat_id, "mode", "enforce", cb.from_user.id)
        try:
            await cb.message.edit_text("<b>Mode is now enforce.</b> Spam is deleted and senders are punished. "
                                       "Switch back any time with <code>/mode shadow</code>.", parse_mode="HTML")
        except Exception:  # noqa: BLE001
            pass
        await cb.answer("Enforce mode is on.")
    else:
        await cb.answer()


@router.message(Command("test"))
async def cmd_test(m: Message, app: App, command: CommandObject) -> None:
    if not await require_admin(m, app):
        return
    text = command.args or ""
    src = real_reply(m)
    if not text and src:
        text = src.text or src.caption or ""
    if not text:
        await m.reply("How to use: /test <some text>, or reply to a message with /test.\n"
                      "I show what I would do if a new member sent it. Nothing is deleted.")
        return
    cs = await app.settings.get(cid(m, app))
    ctx = await app.rule_context(cs) if cid(m, app) in app.allowed_chats else Context()
    view = MessageView(text=text, is_new_member=True,
                       has_inline_buttons=bool(src and src.reply_markup and src.reply_markup.inline_keyboard),
                       forwarded_from_channel=bool(src and src.forward_origin and getattr(src.forward_origin, "type", "") == "channel"),
                       via_bot=bool(src and src.via_bot))
    v = evaluate(view, app.rules, ctx)
    if ctx.slow_rules:
        await app.drop_slow_rules(cs, ctx)
    first = action_words(cs.raw("strike1"))
    jobs_on = cs.bool("job_rules")
    job = jobs_on and job_offer(v.matched) and (v.kind == "judge" or v.score < 100)
    job_to_judge = job and v.kind == "hard" and cs.bool("judge") and app.judge.configured
    phish_to_judge = (not job and v.kind == "hard" and v.score < 100 and cs.bool("phishing_rules")
                      and phishing_offer(v.matched) and cs.bool("judge") and app.judge.configured)
    limit = cs.int("job_strike_after")
    job_do = ("delete it, no strike" + (f" (from job scam number {limit} by the same person: {first})" if limit > 1
                                        else "") if limit != 1 else f"delete it, then {first}")
    title = html.escape(await chat_title(m, app) or str(cid(m, app)))
    lines: list[str] = [f"🧪 <b>{title}</b> · test"]
    facts: list[str] = []
    if "language_not_allowed" in v.matched:
        lines.append("<b>Result: not a language this group allows.</b>")
        facts.append(f"Why: {html.escape(v.reason)}")
        facts.append("What I would do: delete it, nothing else (no strike, no mute)")
    elif v.kind == "hard" and not job_to_judge and not phish_to_judge:
        lines.append("<b>Result: spam.</b> The rules alone are sure.")
        facts.append(f"Why: {html.escape(v.reason)}")
        if job:
            facts.append(f"What I would do: {job_do}")
        elif v.action:
            dur = f" {format_duration(v.duration.seconds)}" if v.duration else ""
            facts.append(f"What I would do: delete it, then {action_words(v.action + dur)}")
        else:
            facts.append(f"What I would do: delete it, then {first} (1st hit, repeat hits get harsher)")
    elif v.kind == "judge" or job_to_judge or phish_to_judge:
        facts.append("Rules: looks like a job or task offer, those always go to the AI judge" if job_to_judge else
                     "Rules: looks like account phishing, those always go to the AI judge" if phish_to_judge else
                     f"Rules: not sure on their own ({html.escape(v.reason)}), so I asked the AI judge")
        res = await app.judge.classify(chat_id=cid(m, app), text=text, sender_facts={"new_member": True, "test": True},
                                       matched_rules=v.matched, daily_cap=cs.int("judge_daily_cap"),
                                       enabled=cs.bool("judge"))
        if res.error:
            lines.append("<b>Result: unknown.</b> The AI judge did not answer.")
            facts.append("What I would do: report it to you, not delete it")
        elif res.skipped:
            why_skipped = {"cap": "the daily limit is reached", "disabled": "it is turned off here (/set judge on)",
                           "no_key": "it is not set up on this server"}.get(res.skipped, res.skipped)
            lines.append("<b>Result: not checked.</b>")
            facts.append(f"AI judge: skipped, {why_skipped}")
            facts.append("What I would do: nothing")
        else:
            sure = int(res.confidence * 100)
            facts.append(f"AI judge: {'spam' if res.spam else 'not spam'} ({html.escape(res.category)}, {sure}% sure). "
                         f"{html.escape(res.reason)}")
            if res.spam and res.confidence >= cs.float("judge_threshold"):
                lines.append("<b>Result: spam.</b> The AI judge is sure enough.")
                gentle = job or (jobs_on and res.category == "job_scam")
                facts.append(f"What I would do: {job_do if gentle else action_words(cs.raw('judge_action'))}")
            else:
                lines.append("<b>Result: looks fine.</b>")
                need = int(cs.float("judge_threshold") * 100)
                facts.append(f"What I would do: nothing (I only act when the judge is at least {need}% sure)")
    else:
        lines.append("<b>Result: looks fine.</b>")
        facts.append("What I would do: nothing")
    if v.detail.get("domains"):
        facts.append(f"Links in it: {html.escape(', '.join(v.detail['domains']))}")
    if v.detail.get("language"):
        from .language import name as language_name
        lang = v.detail["language"]
        facts.append("Language: " + ("not sure (too short or mixed)" if lang == "unknown" else language_name(lang)))
    facts.append("Mode here: enforce, so this would really happen" if cs.raw("mode") == "enforce"
                 else "Mode here: shadow, so I would only report it")
    lines += [""] + facts + ["", f"Tested as a new member. Their message:\n<i>{html.escape(text[:300])}</i>"]
    await m.reply("\n".join(lines), parse_mode="HTML")


def explain(row, title: str | None = None) -> str:
    seconds = row.duration_s if row.action in {"mute", "ban"} else None
    where = html.escape(title or str(row.chat_id))
    if row.mode == "shadow":
        lines = [f"👀 <b>{where}</b> · shadow mode",
                 f"<b>Would {html.escape(action_would(row.action, seconds))}.</b> Nothing was done."]
    else:
        lines = [f"🛡 <b>{where}</b>",
                 f"<b>{html.escape(action_done(row.action, seconds))}</b>"]
        if row.expires_at:
            lines.append(f"Until: {when(row.expires_at)}")
    lines.append(f"When: {when(row.ts)} ({ago(row.ts)})")
    lines.append("")
    user = f"@{row.username}" if row.username else "no username"
    lines.append(f"User: {html.escape(user)} · id <code>{row.user_id}</code>")
    lines.append(f"Reason: {html.escape(row.reason)}")
    lines.append(f"Found by: {source_words(row.source)}")
    d = row.detail or {}
    if d.get("judge"):
        j = d["judge"]
        if j.get("error"):
            lines.append("AI judge: could not answer at the time")
        else:
            sure = int(float(j.get("confidence") or 0) * 100)
            lines.append(f"AI judge: {'spam' if j.get('spam') else 'not spam'} "
                         f"({html.escape(str(j.get('category') or ''))}, {sure}% sure). "
                         f"{html.escape(str(j.get('reason') or ''))}")
    if d.get("safety"):
        sf = d["safety"]
        sure = int(float(sf.get("confidence") or 0) * 100)
        what = "image" if sf.get("image") else "message"
        lines.append(f"Safety check: the {what} was judged {html.escape(str(sf.get('category') or ''))} "
                     f"({sure}% sure). {html.escape(str(sf.get('reason') or ''))}")
    if d.get("strikes"):
        lines.append(f"Strikes: {d['strikes']}")
    if d.get("delete") and row.message_id:
        lines.append("Message: " + ("would be removed" if row.mode == "shadow" else "removed"))
    if d.get("matched"):
        lines.append(f"Rule ids: {html.escape(', '.join(d['matched']))}")
    if d.get("errors"):
        lines.append(f"⚠️ Problem: {html.escape('; '.join(d['errors']))}")
    if row.undone_at:
        lines.append(f"Undone: {when(row.undone_at)}")
    if row.text_excerpt:
        lines.append("")
        lines.append("Their message:")
        lines.append(f"<i>{html.escape(row.text_excerpt[:300])}</i>")
    lines.append("")
    if row.undone_at:
        lines.append(f"Action #{row.id} · already undone")
    else:
        lines.append(f"Action #{row.id} · Undo: <code>/undo {row.id}</code>")
    return "\n".join(lines)


async def explain_for(app: App, row) -> str:
    chat = await app.store.get_chat(row.chat_id)
    return explain(row, chat["title"] if chat else None)


@router.message(Command("why"))
async def cmd_why(m: Message, app: App, command: CommandObject) -> None:
    if m.chat.type in GROUPS and not await require_admin(m, app):
        return
    toks = (command.args or "").split()
    row = None
    if toks and toks[0].lstrip("#").isdigit():
        row = await app.store.get_audit(int(toks[0].lstrip("#")))
    elif real_reply(m) and real_reply(m).from_user:
        u = real_reply(m).from_user
        row = await app.store.find_audit_by_message(m.chat.id, real_reply(m).message_id) \
            or await app.store.last_audit_for_user(m.chat.id, u.id)
    if not row:
        await m.reply("How to use: /why followed by the action number, or reply to the user's message with /why. "
                      "The number is in every report and in /log.")
        return
    if m.chat.type in GROUPS and row.chat_id != m.chat.id and not app.config.is_owner(m.from_user.id):
        await m.reply("That action happened in another group.")
        return
    if m.chat.type == ChatType.PRIVATE and not await app.controls(m.from_user.id, row.chat_id):
        await m.reply("Only admins of that group can see this.")
        return
    await m.reply(await explain_for(app, row), parse_mode="HTML")


@router.callback_query(F.data.startswith("why:"))
async def cb_why(cb: CallbackQuery, app: App) -> None:
    if not genuine(cb):
        await reject(cb)
        return
    try:
        row = await app.store.get_audit(int(cb.data.split(":", 1)[1]))
    except ValueError:
        row = None
    if not row:
        await cb.answer("Unknown action.")
        return
    if not await app.controls(cb.from_user.id, row.chat_id):
        await cb.answer("Admins of that group only.", show_alert=True)
        return
    try:
        await cb.message.reply(await explain_for(app, row), parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.message(Command("id"))
async def cmd_id(m: Message, app: App) -> None:
    if m.chat.type in GROUPS and m.from_user and await app.store.captcha_get(m.chat.id, m.from_user.id):
        raise SkipHandler   # still on the captcha: let the pipeline handle the message
    if m.chat.type == ChatType.PRIVATE:
        lines = ["🆔 <b>Our private chat</b> · ids", f"<b>Your id: <code>{m.from_user.id}</code></b>", ""]
    else:
        lines = [f"🆔 <b>{html.escape(m.chat.title or str(m.chat.id))}</b> · ids",
                 f"<b>This group: <code>{m.chat.id}</code></b>", ""]
        if m.from_user:
            lines.append(f"You: <code>{m.from_user.id}</code>")
    if real_reply(m) and real_reply(m).from_user:
        u = real_reply(m).from_user
        who = f"@{u.username}" if u.username else html.escape(u.full_name)
        lines.append(f"{who}: <code>{u.id}</code>")
    lines.append("")
    lines.append("Tap a number to copy it. A group id is what /set modlog_chat_id wants.")
    await m.reply("\n".join(lines), parse_mode="HTML")

