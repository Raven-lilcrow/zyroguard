"""Carrying out decisions: delete, warn, kick, mute or ban, the strike ladder,
timed lifts, audit rows, mod-log messages and the n8n event.

Every path goes through ``Enforcer.apply`` so shadow mode, audit and the
mod-log stay consistent no matter who decided (rule, judge, captcha, admin).
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup

from .durations import Duration, clamp_for_telegram, format_duration, until_date
from .settings import ChatSettings
from .store import Store
from .text import action_done, action_words, action_would, source_words

log = logging.getLogger("zyroguard.actions")

MUTED = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False, can_change_info=False, can_invite_users=False,
    can_pin_messages=False, can_manage_topics=False,
)
# All True: Telegram then removes the per-user restriction entirely, so the person is a
# plain member again and follows the group's own permissions. Anything less leaves them
# "restricted" forever with a private permission set.
UNMUTED = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_change_info=True, can_invite_users=True,
    can_pin_messages=True, can_manage_topics=True,
)


def plain_error(what: str, e: Exception) -> str:
    """Telegram's error text in the admin's words."""
    raw = str(getattr(e, "message", e) or "").lower()
    if "administrator" in raw or "can't remove chat owner" in raw or "chat owner" in raw:
        return f"{what} failed: they are an admin, Telegram will not let me"
    if "not enough rights" in raw or "have no rights" in raw or isinstance(e, TelegramForbiddenError):
        return f"{what} failed: I am missing the 'Ban users' right, see /diag"
    if "message to delete not found" in raw or "message can't be deleted" in raw:
        return f"{what} failed: the message was already gone"
    if "user not found" in raw or "participant_id_invalid" in raw:
        return f"{what} failed: they are not in the group"
    if "user_not_participant" in raw or "not a member" in raw:
        return f"{what} failed: they are not in the group"
    return f"{what} failed: Telegram refused ({raw[:80]}). Run /diag"


async def tg_call(coro_factory, *, retries: int = 3):
    """Run a Telegram call, honouring RetryAfter."""
    for attempt in range(retries):
        try:
            return await coro_factory()
        except TelegramRetryAfter as e:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(e.retry_after + 0.5)
    return None


@dataclass
class Decision:
    """What should happen to one user/message."""
    source: str                     # who decided: rule, judge, flood, captcha, cas, admin or join
    reason: str
    action: str                     # delete, warn, kick, mute, ban, none or flag
    duration: Duration | None = None
    delete_message: bool = True
    add_strike: bool = True         # a delete or warn from the rules adds a strike; admin actions do not
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Target:
    chat_id: int
    user_id: int | None
    username: str | None = None
    display_name: str | None = None
    message_id: int | None = None
    text: str | None = None
    text_hash: str | None = None
    chat_title: str | None = None
    thread_id: int | None = None      # forum topic the message was in, so notices land there too


@dataclass
class Outcome:
    audit_id: int
    action: str
    duration: Duration | None
    expires_at: datetime | None
    strikes: int
    shadow: bool
    errors: list[str]
    note: str | None = None

    def describe(self) -> str:
        """Past tense, plain words: 'Muted for 24h (until Sep 25, 13:00 UTC)'."""
        seconds = self.duration.seconds if self.duration else None
        return action_done(self.action, seconds, self.expires_at)

    def would(self) -> str:
        """Conditional form for shadow mode: 'mute for 24h'."""
        return action_would(self.action, self.duration.seconds if self.duration else None)


class Enforcer:
    def __init__(self, bot: Bot, store: Store, *, n8n_url: str | None, n8n_secret: str | None,
                 global_modlog: int | None, bot_username: str = "", owner_ids: list[int] | None = None):
        self.bot = bot
        self.owner_ids = set(owner_ids or [])
        self.store = store
        self.n8n_url = n8n_url
        self.n8n_secret = n8n_secret
        self.global_modlog = global_modlog
        self.bot_username = bot_username
        self._session: aiohttp.ClientSession | None = None
        self.last_n8n_error: str | None = None
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        # set by the app: tells whether a user is an admin, an owner or allowlisted in a chat.
        # Automatic actions on such users are dropped even if a stale cache let them through.
        self.exempt = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    # Primitives

    async def delete(self, chat_id: int, message_id: int) -> str | None:
        try:
            await tg_call(lambda: self.bot.delete_message(chat_id, message_id))
            return None
        except TelegramBadRequest as e:
            return plain_error("delete", e)
        except TelegramForbiddenError:
            return "delete failed: I am missing the 'Delete messages' right, see /diag"

    async def mute(self, chat_id: int, user_id: int, duration: Duration | None) -> tuple[str | None, str | None]:
        d, note = clamp_for_telegram(duration or Duration(None))
        try:
            await tg_call(lambda: self.bot.restrict_chat_member(
                chat_id, user_id, permissions=MUTED, until_date=until_date(d), use_independent_chat_permissions=True,
            ))
            return None, note
        except TelegramBadRequest as e:
            return plain_error("mute", e), note
        except TelegramForbiddenError as e:
            return plain_error("mute", e), note

    async def unmute(self, chat_id: int, user_id: int) -> str | None:
        try:
            await tg_call(lambda: self.bot.restrict_chat_member(
                chat_id, user_id, permissions=UNMUTED, use_independent_chat_permissions=True,
            ))
            return None
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            return plain_error("unmute", e)

    async def ban(self, chat_id: int, user_id: int, duration: Duration | None) -> tuple[str | None, str | None]:
        d, note = clamp_for_telegram(duration or Duration(None))
        try:
            await tg_call(lambda: self.bot.ban_chat_member(
                chat_id, user_id, until_date=until_date(d), revoke_messages=False,
            ))
            return None, note
        except TelegramBadRequest as e:
            return plain_error("ban", e), note
        except TelegramForbiddenError as e:
            return plain_error("ban", e), note

    async def unban(self, chat_id: int, user_id: int) -> str | None:
        try:
            await tg_call(lambda: self.bot.unban_chat_member(chat_id, user_id, only_if_banned=True))
            return None
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            return plain_error("unban", e)

    async def kick(self, chat_id: int, user_id: int) -> str | None:
        """Ban then unban so the user may rejoin."""
        err, _ = await self.ban(chat_id, user_id, Duration(45))
        if err:
            return err.replace("ban", "kick", 1)
        await asyncio.sleep(0.3)
        return await self.unban(chat_id, user_id)

    # The one entry point

    async def apply(self, target: Target, decision: Decision, cs: ChatSettings, *,
                    actor_id: int | None = None, force_enforce: bool = False) -> Outcome:
        """One user at a time per group, so a burst of messages cannot race up the strike ladder."""
        if decision.source != "admin" and target.user_id and self.exempt is not None:
            try:
                protected = await self.exempt(target.chat_id, target.user_id)
            except Exception:  # noqa: BLE001
                protected = False
            if protected:
                log.info("skipped automatic %s on admin/trusted user %s in %s", decision.action,
                         target.user_id, target.chat_id)
                return Outcome(0, "none", None, None, 0, not cs.enforce, [], "admin or trusted user, left alone")
        lock = self._locks.setdefault((target.chat_id, target.user_id or 0), asyncio.Lock())
        async with lock:
            return await self._apply(target, decision, cs, actor_id=actor_id, force_enforce=force_enforce)

    async def say(self, cs: ChatSettings, text: str, *, reply_to: int | None = None, thread_id: int | None = None,
                  reply_markup=None):
        """Post a line in the group (in the given forum topic) and, unless notice_ttl is 0, delete it again later."""
        try:
            sent = await tg_call(lambda: self.bot.send_message(
                cs.chat_id, text, parse_mode="HTML", disable_web_page_preview=True,
                reply_to_message_id=reply_to, message_thread_id=thread_id, reply_markup=reply_markup,
                allow_sending_without_reply=True))
        except Exception as e:  # noqa: BLE001
            log.warning("group notice failed: %s", e)
            return None
        await self.expire_later(cs, sent)
        return sent

    async def expire_later(self, cs: ChatSettings, sent) -> None:
        """Queue one of my own group messages for deletion after notice_ttl."""
        ttl = cs.seconds("notice_ttl")
        if not ttl or sent is None or not getattr(sent, "message_id", None):
            return
        await self.store.expire_message(cs.chat_id, sent.message_id, datetime.now(timezone.utc) + timedelta(seconds=ttl))

    async def delete_expired(self) -> int:
        """Scheduler hook: delete queued notices whose time is up."""
        n = 0
        for row in await self.store.due_expiring():
            chat_id, message_id = int(row["chat_id"]), int(row["message_id"])
            await self.delete(chat_id, message_id)
            await self.store.forget_expiring(chat_id, message_id)
            n += 1
        return n

    async def _apply(self, target: Target, decision: Decision, cs: ChatSettings, *,
                     actor_id: int | None = None, force_enforce: bool = False) -> Outcome:
        enforce = cs.enforce or force_enforce
        errors: list[str] = []
        action = decision.action
        duration = decision.duration
        note: str | None = None
        strikes = 0
        added_strike = False
        burst = False

        # strike ladder: a delete or warn from detection escalates by strike count
        if decision.add_strike and target.user_id and action in {"delete", "warn"}:
            ttl_s = cs.seconds("strike_ttl")
            counts = enforce or decision.source == "admin"   # shadow mode never records strikes
            last = await self.store.last_strike_at(target.chat_id, target.user_id)
            burst = action == "delete" and last is not None and (datetime.now(timezone.utc) - last) < timedelta(seconds=90)
            if counts and not burst:
                strikes = await self.store.add_strike(
                    target.chat_id, target.user_id, decision.reason,
                    timedelta(seconds=ttl_s) if ttl_s else None, None,
                )
                added_strike = True
            else:
                # a burst reuses the step the user is already on; shadow shows what the next step would be
                active = await self.store.active_strikes(target.chat_id, target.user_id)
                strikes = active if burst else active + 1
            if action == "warn":
                # manual warns: escalate only when the warn limit is reached
                if strikes >= cs.int("warn_limit"):
                    action, duration = cs.action("warn_action")
                    if action == "none":
                        action = "warn"
                    else:
                        await self.store.clear_strikes(target.chat_id, target.user_id)
            else:
                key = "strike1" if strikes <= 1 else "strike2" if strikes == 2 else "strike3"
                ladder_action, ladder_dur = cs.action(key)
                if ladder_action != "none":
                    action, duration = ladder_action, ladder_dur
        elif target.user_id and action != "none":
            strikes = await self.store.active_strikes(target.chat_id, target.user_id)

        expires_at = duration.expires_at() if duration and not duration.permanent and action in {"mute", "ban"} else None

        if enforce:
            if decision.delete_message and target.message_id:
                err = await self.delete(target.chat_id, target.message_id)
                if err:
                    errors.append(err)
            if target.user_id and action in {"mute", "ban", "kick"}:
                if action == "mute":
                    err, note = await self.mute(target.chat_id, target.user_id, duration)
                elif action == "ban":
                    err, note = await self.ban(target.chat_id, target.user_id, duration)
                else:
                    err = await self.kick(target.chat_id, target.user_id)
                if err:
                    errors.append(err)

        audit_id = await self.store.add_audit(
            chat_id=target.chat_id, user_id=target.user_id, username=target.username,
            message_id=target.message_id, text_hash=target.text_hash,
            text_excerpt=(target.text or "")[:300] or None, source=decision.source,
            reason=decision.reason, detail={**decision.detail, "strikes": strikes, "errors": errors,
                                            "delete": decision.delete_message},
            action=action, duration_s=duration.seconds if duration else None,
            expires_at=expires_at, mode="enforce" if enforce else "shadow", actor_id=actor_id,
        )
        if added_strike:
            # link the strike we just added to this audit row for undo
            await self.store.db.execute(
                "UPDATE strikes SET audit_id=? WHERE id=(SELECT MAX(id) FROM strikes WHERE chat_id=? AND user_id=?)",
                (audit_id, target.chat_id, target.user_id),
            )
            await self.store.db.commit()
        if enforce and expires_at and target.user_id and action == "ban":
            await self.store.schedule_lift(target.chat_id, target.user_id, "ban", expires_at, audit_id)
        if enforce and expires_at and target.user_id and action == "mute":
            # Telegram lifts mutes itself; we record it so /why and the digest can show the expiry
            await self.store.schedule_lift(target.chat_id, target.user_id, "mute", expires_at, audit_id)

        outcome = Outcome(audit_id, action, duration, expires_at, strikes, not enforce, errors, note)
        if enforce and decision.source != "admin" and not burst and cs.bool("group_notice") and action != "none":
            await self.say(cs, self._notice_text(target, decision, outcome), thread_id=target.thread_id)
        await self.report(target, decision, outcome, cs)
        return outcome

    @staticmethod
    def _notice_text(target: Target, decision: Decision, outcome: Outcome) -> str:
        who = f"@{target.username}" if target.username else (target.display_name or "a member")
        what = html.escape(outcome.describe())
        if outcome.action in {"delete", "warn"} and decision.delete_message:
            what = "Message removed"
        elif decision.delete_message:
            what = f"Message removed, {what[0].lower() + what[1:]}"
        return (f"🛡 <b>{what}</b>: {html.escape(who)}. Reason: {html.escape(decision.reason)}.\n"
                f"Admins can undo this with <code>/undo {outcome.audit_id}</code>.")

    # Undo

    async def undo(self, audit_id: int, by: int | None) -> str:
        row = await self.store.get_audit(audit_id)
        if not row:
            return "No such action."
        if row.undone_at:
            return "Already undone."
        msgs = []
        if row.user_id and row.mode == "enforce":
            if row.action == "ban":
                err = await self.unban(row.chat_id, row.user_id)
                msgs.append(err or "The user is unbanned.")
            elif row.action == "mute":
                err = await self.unmute(row.chat_id, row.user_id)
                msgs.append(err or "The user can talk again.")
            elif row.action == "kick":
                msgs.append("They were kicked, so they can simply rejoin on their own.")
            await self.store.cancel_lifts(row.chat_id, row.user_id)
        await self.store.remove_strike_by_audit(audit_id)
        await self.store.mark_undone(audit_id, by)
        if row.message_id and row.detail.get("delete"):
            msgs.append("The deleted message cannot be brought back.")
        if row.mode == "shadow":
            return f"Marked action {audit_id} as undone. It was shadow mode, so nothing had really happened."
        return f"Undone action {audit_id}. " + (" ".join(msgs) if msgs else "The strike was removed.")

    # Reporting

    async def _modlog_target(self, cs: ChatSettings) -> int | None:
        """Where reports go: the group's own log chat if set, else the private chat of the
        user who added the bot, else the server-wide log group (for the owner's groups)."""
        if cs.modlog_chat_id:
            return cs.modlog_chat_id
        owner = await self.store.chat_owner(cs.chat_id)
        if owner is not None and owner not in self.owner_ids:
            return owner
        return self.global_modlog

    @staticmethod
    def _next_step(cs: ChatSettings, strikes: int) -> str | None:
        """What the ladder does on the following strike, in words."""
        raw = cs.raw("strike2" if strikes <= 1 else "strike3")
        if not raw or raw.split()[0] == "none":
            return None
        return action_words(raw, long=True)

    async def report(self, target: Target, decision: Decision, outcome: Outcome, cs: ChatSettings) -> None:
        who = f"@{target.username}" if target.username else (target.display_name or "no username")
        where = html.escape(target.chat_title or str(target.chat_id))
        seconds = outcome.duration.seconds if outcome.duration else None
        if outcome.shadow:
            lines = [f"👀 <b>{where}</b> · shadow mode",
                     f"<b>Would {html.escape(outcome.would())}.</b> Nothing was done."]
        else:
            lines = [f"🛡 <b>{where}</b>",
                     f"<b>{html.escape(action_done(outcome.action, seconds))}</b>"]
            if outcome.expires_at:
                lines.append(f"Until: {outcome.expires_at.astimezone(timezone.utc):%b %d, %H:%M} UTC")
        lines.append("")
        lines.append(f"User: {html.escape(who)} · id <code>{target.user_id}</code>")
        lines.append(f"Reason: {html.escape(decision.reason)}")
        lines.append(f"Found by: {source_words(decision.source)}")
        if outcome.strikes:
            nxt = self._next_step(cs, outcome.strikes)
            lines.append(f"Strikes: {outcome.strikes}" + (f" · next time: {nxt}" if nxt else ""))
        if decision.delete_message and target.message_id:
            lines.append("Message: " + ("would be removed" if outcome.shadow else "removed"))
        if outcome.note:
            lines.append(f"Note: {html.escape(outcome.note)}")
        if outcome.errors:
            lines.append("⚠️ Problem: " + html.escape("; ".join(outcome.errors)))
        if target.text:
            lines.append("")
            lines.append("Their message:")
            lines.append(f"<i>{html.escape(target.text[:400])}</i>")
        lines.append("")
        lines.append(f"Action #{outcome.audit_id} · Undo: <code>/undo {outcome.audit_id}</code>")
        text = "\n".join(lines)
        buttons = [
            InlineKeyboardButton(text="Undo", callback_data=f"undo:{outcome.audit_id}"),
            InlineKeyboardButton(text="Why", callback_data=f"why:{outcome.audit_id}"),
        ]
        from .similar import usable
        if decision.source in {"rule", "judge"} and usable((target.text or "")[:300]):
            buttons.append(InlineKeyboardButton(text="Teach as spam", callback_data=f"teach:{outcome.audit_id}"))
        kb = InlineKeyboardMarkup(inline_keyboard=[buttons])
        modlog = await self._modlog_target(cs)
        if modlog:
            try:
                await tg_call(lambda: self.bot.send_message(modlog, text, parse_mode="HTML", reply_markup=kb))
            except Exception as e:  # noqa: BLE001
                log.warning("modlog send failed: %s", e)
                await self.store.log_error("modlog", f"{type(e).__name__}: {e}", target.chat_id)
        await self.post_event({
            "event": "action", "audit_id": outcome.audit_id, "chat_id": target.chat_id,
            "chat_title": target.chat_title, "user_id": target.user_id, "username": target.username,
            "reason": decision.reason, "source": decision.source, "action": outcome.action,
            "duration": format_duration(outcome.duration.seconds) if outcome.duration else None,
            "expires_at": outcome.expires_at.isoformat() if outcome.expires_at else None,
            "shadow": outcome.shadow, "strikes": outcome.strikes,
            "message_text": (target.text or "")[:1000], "errors": outcome.errors,
        })

    async def notify(self, cs: ChatSettings, text: str) -> None:
        """Plain informational line to the mod log (an automatic unban, a judge failure)."""
        modlog = await self._modlog_target(cs)
        if not modlog:
            return
        try:
            await tg_call(lambda: self.bot.send_message(modlog, text, parse_mode="HTML"))
        except Exception as e:  # noqa: BLE001
            log.warning("modlog notify failed: %s", e)

    async def post_event(self, payload: dict[str, Any]) -> None:
        if not self.n8n_url:
            return
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
        headers = {"X-ZyroGuard-Secret": self.n8n_secret} if self.n8n_secret else {}
        try:
            async with self._session.post(self.n8n_url, json=payload, headers=headers) as r:
                if r.status >= 300:
                    self.last_n8n_error = f"n8n returned {r.status}"
                    await self.store.log_error("n8n", self.last_n8n_error, payload.get("chat_id"))
                else:
                    self.last_n8n_error = None
        except Exception as e:  # noqa: BLE001
            self.last_n8n_error = f"{type(e).__name__}: {e}"[:200]
            await self.store.log_error("n8n", self.last_n8n_error, payload.get("chat_id"))

    async def ping_n8n(self) -> tuple[bool, str]:
        if not self.n8n_url:
            return False, "N8N_EVENT_URL not set (optional)"
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
        headers = {"X-ZyroGuard-Secret": self.n8n_secret} if self.n8n_secret else {}
        try:
            async with self._session.post(self.n8n_url, json={"event": "ping"}, headers=headers) as r:
                return r.status < 300, f"HTTP {r.status}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"[:200]
