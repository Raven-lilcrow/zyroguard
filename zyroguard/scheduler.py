"""Background loop: lift expired bans, expire strikes, time out captchas,
post the daily digest."""

from __future__ import annotations

import html
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .app import App
from .store import from_iso, utcnow

log = logging.getLogger("zyroguard.scheduler")
MANILA = ZoneInfo("Asia/Manila")


async def _lift_state(app: App, chat_id: int, user_id: int, action: str, expires_at) -> str:
    """'ours': our restriction is still on, lift it. 'ended': Telegram already lifted it on
    time (it does that for timed mutes and bans), so only report it. 'changed': an admin
    has since banned or muted the person for good or for a different time: leave it alone."""
    try:
        member = await app.bot.get_chat_member(chat_id, user_id)
    except Exception:  # noqa: BLE001
        return "ours"   # cannot tell, keep the old behaviour
    status = getattr(member, "status", "")
    until = getattr(member, "until_date", None)
    if status in {"member", "left"}:
        return "ended"
    if (action == "ban") != (status == "kicked") or status not in {"kicked", "restricted"}:
        return "changed"   # a mute became a ban or the other way round
    if until is None:
        return "changed"   # permanent now: an admin changed it
    if expires_at is not None and abs((until - expires_at).total_seconds()) > 300:
        return "changed"   # a different restriction than ours
    return "ours"


async def lift_due(app: App) -> int:
    n = 0
    for row in await app.store.due_lifts():
        chat_id, user_id, action = int(row["chat_id"]), int(row["user_id"]), row["action"]
        cs = await app.settings.get(chat_id)
        await app.store.mark_lift_done(int(row["id"]))
        state = await _lift_state(app, chat_id, user_id, action, from_iso(row["expires_at"]))
        if state == "changed":
            log.info("lift skipped for %s in %s: restriction was changed by an admin", user_id, chat_id)
            continue
        err = None
        if action == "ban":
            if state == "ours":
                err = await app.enforcer.unban(chat_id, user_id)
            note = "Ban ended, they can join again"
        else:
            if state == "ours":
                err = await app.enforcer.unmute(chat_id, user_id)
            note = "Mute ended, they can talk again"
        member = await app.store.get_member(chat_id, user_id)
        who = f"@{member['username']}" if member and member["username"] else (
            html.escape(member["display_name"]) if member and member["display_name"] else "no username")
        chat = await app.store.get_chat(chat_id)
        title = html.escape(chat["title"] if chat and chat["title"] else str(chat_id))
        lines = [f"⏰ <b>{title}</b>", f"<b>{note}.</b>", "",
                 f"User: {who} · id <code>{user_id}</code>"]
        if err:
            lines.append(f"⚠️ Problem: {html.escape(err)}")
        lines += ["", f"Action #{row['audit_id']} · details: <code>/why {row['audit_id']}</code>"]
        await app.enforcer.notify(cs, "\n".join(lines))
        await app.enforcer.post_event({"event": "lift", "chat_id": chat_id, "user_id": user_id,
                                       "action": action, "audit_id": row["audit_id"], "error": err})
        n += 1
    return n


async def timeout_captchas(app: App) -> int:
    from .captcha import fail_captcha
    n = 0
    for row in await app.store.captcha_overdue():
        try:
            await fail_captcha(app, int(row["chat_id"]), int(row["user_id"]))
        except Exception as e:  # noqa: BLE001
            await app.store.log_error("captcha.timeout", f"{type(e).__name__}: {e}", int(row["chat_id"]))
            await app.store.captcha_delete(int(row["chat_id"]), int(row["user_id"]))
        n += 1
    return n


_DID = {"mute": "muted", "ban": "banned", "kick": "kicked out", "delete": "message removed",
        "warn": "warned", "none": "nothing"}


def digest_text(title: str, stats: dict, period: str | None = None) -> str:
    from .text import source_words
    period = period or f"daily summary, {datetime.now(MANILA):%b %d}"
    lines = [f"📊 <b>{html.escape(title)}</b> · {html.escape(period)}"]
    if not stats["total"]:
        lines.append("<b>Quiet.</b> Nothing caught, nothing done.")
        if stats.get("judge_calls"):
            lines.append(f"AI judge asked: {stats['judge_calls']} time{'s' if stats['judge_calls'] != 1 else ''}, all came back clean.")
        if stats.get("support_line"):
            lines.append(stats["support_line"])
        return "\n".join(lines)
    head = f"<b>{stats['total']} action{'s' if stats['total'] != 1 else ''}</b>"
    if stats["shadow"]:
        head += f" · {stats['shadow']} in shadow, nothing really done"
    if stats["undone"]:
        head += f" · {stats['undone']} undone"
    lines.append(head)
    if stats["by_action"]:
        lines += ["", "<b>What I did</b>"]
        lines += [f"{v} × {_DID.get(k, k)}" for k, v in sorted(stats["by_action"].items(), key=lambda kv: -kv[1])]
    if stats["by_source"]:
        lines += ["", "<b>Caught by</b>"]
        lines += [f"{v} × {source_words(k)}" for k, v in sorted(stats["by_source"].items(), key=lambda kv: -kv[1])]
    if stats["top_reasons"]:
        lines += ["", "<b>Top reasons</b>"]
        lines += [f"{n} × {html.escape(r)}" for r, n in stats["top_reasons"][:5]]
    lines += ["", f"AI judge asked: {stats['judge_calls']} time{'s' if stats['judge_calls'] != 1 else ''}"]
    if stats.get("support_line"):
        lines.append(stats["support_line"])
    lines.append("Details: /log · a mistake: /undo followed by the number")
    return "\n".join(lines)


OVERALL_TIME = "09:00"   # Manila, for the server-wide summary in the owner's log group


async def send_overall_digest(app: App, sent_today: set[tuple[int, str]]) -> None:
    """One short block per group, sent to the server-wide log group once a day."""
    if not app.config.modlog_chat_id:
        return
    now_local = datetime.now(MANILA)
    today = now_local.strftime("%Y-%m-%d")
    if (0, today) in sent_today or OVERALL_TIME > now_local.strftime("%H:%M"):
        return
    sent_today.add((0, today))
    if await app.store.digest_sent_on(0) == today:
        return
    await app.store.mark_digest_sent(0, today)
    from .commands import split_message
    from .text import source_words
    since = utcnow() - timedelta(days=1)
    blocks: list[tuple[int, str]] = []
    quiet: list[str] = []
    total = judge = 0
    for chat in await app.store.list_chats():
        chat_id = int(chat["chat_id"])
        if chat_id == app.config.modlog_chat_id:
            continue
        st = await app.store.stats(chat_id, since)
        total += st["total"]
        judge += st["judge_calls"]
        name = html.escape(chat["title"] or str(chat_id))
        if not st["total"]:
            quiet.append(name)
            continue
        cs = await app.settings.get(chat_id)
        head = f"<b>{st['total']} action{'s' if st['total'] != 1 else ''}</b>"
        if cs.raw("mode") == "shadow":
            head += " · shadow, nothing really done"
        if st["undone"]:
            head += f" · {st['undone']} undone"
        block = [f"<b>{name}</b>", head]
        if st["by_action"]:
            block.append("Did: " + ", ".join(f"{v} × {_DID.get(k, k)}"
                                             for k, v in sorted(st["by_action"].items(), key=lambda kv: -kv[1])))
        if st["by_source"]:
            block.append("Caught by: " + ", ".join(f"{v} × {source_words(k)}"
                                                   for k, v in sorted(st["by_source"].items(), key=lambda kv: -kv[1])))
        if st["top_reasons"]:
            block.append("Top reasons: " + " · ".join(f"{n} × {html.escape(r)}" for r, n in st["top_reasons"][:3]))
        if st["judge_calls"]:
            block.append(f"AI judge asked: {st['judge_calls']} time{'s' if st['judge_calls'] != 1 else ''}")
        blocks.append((st["total"], "\n".join(block)))
    lines = [f"📊 <b>All groups</b> · {now_local:%b %d}",
             f"<b>{total} action{'s' if total != 1 else ''}</b> · AI judge asked {judge} time{'s' if judge != 1 else ''}"]
    for _, block in sorted(blocks, key=lambda x: -x[0]):
        lines += ["", block]
    if quiet:
        lines += ["", "<b>Quiet</b>", ", ".join(quiet)]
    lines += ["", "Per group: /use to pick one here, then /log or /stats 24h"]
    try:
        for part in split_message("\n".join(lines)):
            await app.bot.send_message(app.config.modlog_chat_id, part, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        log.warning("overall digest failed: %s", e)


async def send_digests(app: App, sent_today: set[tuple[int, str]]) -> None:
    now_local = datetime.now(MANILA)
    today = now_local.strftime("%Y-%m-%d")
    hhmm = now_local.strftime("%H:%M")
    for chat in await app.store.list_chats():
        chat_id = int(chat["chat_id"])
        cs = await app.settings.get(chat_id)
        key = (chat_id, today)
        if key in sent_today or cs.raw("digest_time") > hhmm:
            continue
        sent_today.add(key)
        if await app.store.digest_sent_on(chat_id) == today:
            continue   # already sent before a restart
        await app.store.mark_digest_sent(chat_id, today)
        stats = await app.store.stats(chat_id, utcnow() - timedelta(days=1))
        from .support import digest_line
        support = digest_line(await app.store.kb_question_counts(chat_id, utcnow() - timedelta(days=1)))
        if stats["total"] == 0 and stats["judge_calls"] == 0 and not support:
            continue
        if support:
            stats = {**stats, "support_line": support}
        await app.enforcer.notify(cs, digest_text(chat["title"] or str(chat_id), stats))
        await app.enforcer.post_event({"event": "digest", "chat_id": chat_id, "chat_title": chat["title"], **stats})


async def drop_ended_permits(app: App) -> int:
    """Leave the groups of users whose access was revoked or ran out. Records stay
    until the owner runs /purge, so a renewal does not start from zero. Runs every 5 minutes;
    app.chat_allowed already stops serving such a group the moment its access ends."""
    from . import ledger
    await app.refresh_access()
    ended: dict[int, list[str]] = {}
    left = 0
    for chat in await app.store.list_chats():
        owner = chat["owner_user_id"]
        if not owner or app.config.is_owner(int(owner)):
            continue
        if await app.store.permit_valid_for_existing(int(owner)):
            continue
        chat_id = int(chat["chat_id"])
        try:
            await app.bot.send_message(chat_id, "My permit for this group has ended, so I am leaving. "
                                                "Contact the admin to renew it.")
        except Exception:  # noqa: BLE001
            pass
        try:
            await app.bot.leave_chat(chat_id)
        except Exception:  # noqa: BLE001
            pass
        await app.store.deactivate_chat(chat_id)
        app.allowed_chats.discard(chat_id)
        ended.setdefault(int(owner), []).append(chat["title"] or str(chat_id))
        await ledger.note(app, "bot_left", chat_id, target=int(owner), title=chat["title"])
        try:
            from .contact import contact_button
            await app.bot.send_message(int(owner), f"Your permit ended, so I left {chat['title'] or chat_id}. "
                                                   "Contact the admin to renew it.", reply_markup=contact_button(app))
        except Exception:  # noqa: BLE001
            pass
        left += 1
        log.info("left %s (%s): permit of %s ended", chat["title"], chat_id, owner)
    # one "access expired" record (and owner alert) each time someone's access ends, groups or not
    for p in await app.store.list_permits():
        if not p["active"] or not p["expires_at"] or (from_iso(p["expires_at"]) or utcnow()) > utcnow():
            continue
        key = f"expired:{p['user_id']}:{p['expires_at']}"
        if await app.store.ledger_get(key) is not None:
            continue
        await app.store.ledger_set(key, utcnow().isoformat())
        await ledger.note(app, "access_expired", None, target=int(p["user_id"]),
                          groups=ended.get(int(p["user_id"]), []), username=p["username"])
    return left


async def _ledger_tick(app: App, tick: int) -> None:
    """The owner's activity record: send events every minute; daily summaries and the month-end
    snapshot are checked every 5 minutes; Groups and Access are rewritten every hour."""
    from . import ledger
    try:
        if tick % 15 == 0:
            await ledger.daily_summaries(app)
            await ledger.month_end(app)
        await ledger.flush(app)
        if tick % 180 == 0:
            await ledger.snapshot(app)
    except Exception as e:  # noqa: BLE001
        log.warning("activity record tick failed: %s", e)


async def run(app: App, interval: float = 20.0) -> None:
    sent_today: set[tuple[int, str]] = set()
    tick = 0
    while True:
        try:
            await lift_due(app)
            await timeout_captchas(app)
            from .forwards import timeout_overdue
            await timeout_overdue(app)
            await app.enforcer.delete_expired()
            if tick % 3 == 0:
                await send_digests(app, sent_today)
                await send_overall_digest(app, sent_today)
                await _ledger_tick(app, tick)
            if tick % 15 == 0 and app.similar is not None:
                promoted = await app.similar.promote_due()
                if promoted:
                    log.info("meaning match: %d held examples now count", promoted)
            if tick % 15 == 0:
                await drop_ended_permits(app)
            if tick % 180 == 0:
                purged = await app.store.purge_expired_strikes()
                if purged:
                    log.info("purged %d expired strikes", purged)
                old = await app.store.purge_old_rows()
                if old:
                    log.info("purged %d old usage/audit rows", old)
                sent_today = {k for k in sent_today if k[1] == datetime.now(MANILA).strftime("%Y-%m-%d")}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler tick failed")
            try:
                await app.store.log_error("scheduler", f"{type(e).__name__}: {e}")
            except Exception:  # noqa: BLE001
                pass
        tick += 1
        await asyncio.sleep(interval)
