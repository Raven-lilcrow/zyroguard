"""Shared runtime state handed to every handler as ``app``."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner

from .actions import Enforcer
from .config import Config
from .judge import Judge
from .rules import Context, RuleSet, compile_custom, load_rules
from .settings import ChatSettings, SettingsService, protected_names
from .store import Store, from_iso, utcnow

log = logging.getLogger("zyroguard.app")


@dataclass
class BotRights:
    is_admin: bool = False
    can_delete: bool = False
    can_restrict: bool = False
    can_invite: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.is_admin and self.can_delete and self.can_restrict


class AdminCache:
    """Live admin lookups with a short TTL. Never trusts stored state."""

    TTL = 300

    def __init__(self, bot: Bot):
        self.bot = bot
        self._admins: dict[int, tuple[float, dict[int, Any]]] = {}
        self._rights: dict[int, tuple[float, BotRights]] = {}
        self._lock = asyncio.Lock()

    async def admins(self, chat_id: int, *, force: bool = False) -> dict[int, Any]:
        now = time.monotonic()
        self._evict(now)
        cached = self._admins.get(chat_id)
        if cached and not force and now - cached[0] < self.TTL:
            return cached[1]
        async with self._lock:
            cached = self._admins.get(chat_id)
            if cached and not force and now - cached[0] < self.TTL:
                return cached[1]
            try:
                members = await self.bot.get_chat_administrators(chat_id)
                data = {m.user.id: m for m in members}
            except TelegramAPIError as e:
                log.warning("get_chat_administrators(%s) failed: %s", chat_id, e)
                data = cached[1] if cached else {}
                # keep the old list, and retry soon rather than treating nobody as admin for 5 minutes
                self._admins[chat_id] = (now - self.TTL + 30, data)
                return data
            self._admins[chat_id] = (now, data)
            return data

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self.admins(chat_id)

    def invalidate(self, chat_id: int) -> None:
        self._admins.pop(chat_id, None)
        self._rights.pop(chat_id, None)

    def _evict(self, now: float) -> None:
        if len(self._admins) > 500:
            for cid, (ts, _) in list(self._admins.items()):
                if now - ts > self.TTL:
                    self._admins.pop(cid, None)
        if len(self._rights) > 500:
            for cid, (ts, _) in list(self._rights.items()):
                if now - ts > self.TTL:
                    self._rights.pop(cid, None)

    async def bot_rights(self, chat_id: int, *, force: bool = False) -> BotRights:
        now = time.monotonic()
        cached = self._rights.get(chat_id)
        if cached and not force and now - cached[0] < self.TTL:
            return cached[1]
        try:
            me = await self.bot.get_chat_member(chat_id, self.bot.id)
        except TelegramAPIError as e:
            r = BotRights(error=str(getattr(e, "message", e)))
            self._rights[chat_id] = (now, r)
            return r
        if isinstance(me, ChatMemberOwner):
            r = BotRights(True, True, True, True)
        elif isinstance(me, ChatMemberAdministrator):
            r = BotRights(True, bool(me.can_delete_messages), bool(me.can_restrict_members),
                          bool(me.can_invite_users))
        else:
            r = BotRights(False, error=f"bot status is {me.status}")
        self._rights[chat_id] = (now, r)
        return r


class FloodTracker:
    """In-memory sliding windows for flood and repeat-wave detection."""

    def __init__(self):
        self._per_user: dict[tuple[int, int], deque[float]] = {}
        self._per_text: dict[tuple[int, str], dict[int, float]] = {}
        self._last_album: dict[tuple[int, int], str] = {}
        self._last_prune = time.monotonic()

    def hit_user(self, chat_id: int, user_id: int, window_s: int, album_id: str | None = None) -> int:
        """Count a message. The photos of one album arrive as separate messages and count once."""
        now = time.monotonic()
        dq = self._per_user.setdefault((chat_id, user_id), deque())
        if album_id and self._last_album.get((chat_id, user_id)) == album_id:
            pass
        else:
            dq.append(now)
            if album_id:
                self._last_album[(chat_id, user_id)] = album_id
        while dq and now - dq[0] > window_s:
            dq.popleft()
        self._maybe_prune(now)
        return len(dq)

    def reset_user(self, chat_id: int, user_id: int) -> None:
        self._per_user.pop((chat_id, user_id), None)

    def hit_text(self, chat_id: int, text_hash: str, user_id: int, window_s: int) -> int:
        now = time.monotonic()
        users = self._per_text.setdefault((chat_id, text_hash), {})
        users[user_id] = now
        for uid, ts in list(users.items()):
            if now - ts > window_s:
                del users[uid]
        return len(users)

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 600:
            return
        self._last_prune = now
        for key, dq in list(self._per_user.items()):
            if not dq or now - dq[-1] > 3600:
                del self._per_user[key]
                self._last_album.pop(key, None)
        for key, users in list(self._per_text.items()):
            if not users or now - max(users.values()) > 3600:
                del self._per_text[key]


@dataclass
class ChatStats:
    plain_messages_seen: int = 0
    last_update_at: datetime | None = None


@dataclass
class App:
    config: Config
    bot: Bot
    store: Store
    settings: SettingsService
    judge: Judge
    enforcer: Enforcer
    admins: AdminCache
    rules: RuleSet
    flood: FloodTracker = field(default_factory=FloodTracker)
    started_at: datetime = field(default_factory=utcnow)
    last_update_at: datetime | None = None
    paused_until: dict[int, datetime] = field(default_factory=dict)
    chat_stats: dict[int, ChatStats] = field(default_factory=dict)
    bot_username: str = ""
    bot_id: int = 0
    onboarded: set[int] = field(default_factory=set)
    assistant: Any = None  # zyroguard.assistant.Assistant, set in main
    similar: Any = None    # zyroguard.similar.Library, set in main when the meaning match is on
    kb: Any = None         # zyroguard.kb.KnowledgeBase, support mode's files (needs the meaning match)
    # groups the bot serves: the static list plus groups registered by an owner or a user with access
    allowed_chats: set[int] = field(default_factory=set)
    dm_context: dict[int, int] = field(default_factory=dict)   # for each user_id, the chat_id chosen with /use
    access_ends: dict[int, datetime] = field(default_factory=dict)   # for each user's group, when their access ends

    async def load_allowed(self) -> None:
        """Static list plus every group the bot accepted earlier (added by an owner or by a user with access).
        A chat row is only active while the bot is in that group, so active rows are all trusted."""
        self.allowed_chats = set(self.config.allowed_chat_ids)
        for c in await self.store.list_chats():
            self.allowed_chats.add(int(c["chat_id"]))
        await self.refresh_access()

    async def refresh_access(self) -> None:
        """When each user's group stops being served: their access end date, or now when it was revoked."""
        ends: dict[int, datetime] = {}
        for c in await self.store.list_chats():
            owner = c["owner_user_id"]
            if not owner or self.config.is_owner(int(owner)):
                continue
            p = await self.store.get_permit(int(owner))
            if not p or not p["active"]:
                ends[int(c["chat_id"])] = datetime.min.replace(tzinfo=timezone.utc)
            elif p["expires_at"]:
                ends[int(c["chat_id"])] = from_iso(p["expires_at"]) or datetime.min.replace(tzinfo=timezone.utc)
        self.access_ends = ends

    def chat_allowed(self, chat_id: int) -> bool:
        """Only groups the bot accepted, and only while their user's access lasts. An empty
        ALLOWED_CHAT_IDS does not open the bot to every group."""
        if chat_id not in self.allowed_chats:
            return False
        ends = self.access_ends.get(chat_id)
        return ends is None or ends > utcnow()

    async def controls(self, user_id: int, chat_id: int) -> bool:
        """May this person run admin commands for that group (from anywhere)?"""
        if self.config.is_owner(user_id):
            return True
        if await self.store.chat_owner(chat_id) == user_id:
            return True
        if await self.admins.is_admin(chat_id, user_id):
            return True
        return await self.store.is_bot_admin(chat_id, user_id)

    async def groups_for(self, user_id: int) -> list[Any]:
        """Groups this person may manage: all of them for owners, otherwise the ones they added or are an admin of."""
        out = []
        for c in await self.store.list_chats():
            cid = int(c["chat_id"])
            if self.config.is_owner(user_id) or c["owner_user_id"] == user_id or await self.admins.is_admin(cid, user_id):
                out.append(c)
        return out

    async def ai_budget_ok(self, chat_id: int) -> bool:
        """Daily AI cap per user, shared by all of their groups. The owners' groups are unlimited here."""
        owner = await self.store.chat_owner(chat_id)
        if owner is None or self.config.is_owner(owner):
            return True
        p = await self.store.get_permit(owner)
        if not p:
            return True
        used = await self.store.ai_calls_for_owner_since(owner, utcnow() - timedelta(days=1))
        return used < int(p["daily_ai_cap"])

    def reload_rules(self) -> RuleSet:
        self.rules = load_rules(Path(self.config.rules_path))
        return self.rules

    def is_paused(self, chat_id: int) -> bool:
        until = self.paused_until.get(chat_id)
        if until and until > utcnow():
            return True
        self.paused_until.pop(chat_id, None)
        return False

    async def rule_context(self, cs: ChatSettings) -> Context:
        doms = await self.store.domains(cs.chat_id)
        rows = await self.store.list_rules(cs.chat_id)
        return Context(
            link_policy=cs.choice("link_policy"),
            media_unverified=cs.choice("media_unverified"),
            flood_count=cs.int("flood_count"),
            repeat_count=cs.int("repeat_count"),
            allow_domains=doms["allow"],
            block_domains=doms["block"],
            custom_rules=compile_custom(rows),
            bot_username=self.bot_username,
            allowed_languages={c for c in cs.raw("languages").split(",") if c},
            language_scope=cs.choice("language_scope"),
            protected_names=protected_names(cs),
            job_rules=cs.bool("job_rules"),
            phishing_rules=cs.bool("phishing_rules"),
        )

    async def drop_slow_rules(self, cs: ChatSettings, ctx: Context) -> None:
        """Switch off the custom rules evaluate() found too slow and tell the group's admins.
        A slow pattern holds up every group the bot serves, so it is not given a second try."""
        for rule in ctx.slow_rules:
            if rule.db_id is None or not await self.store.disable_rule(rule.db_id):
                continue
            log.warning("switched off slow custom rule %s in %s", rule.db_id, cs.chat_id)
            await self.store.log_error("custom_rule", f"rule {rule.db_id} timed out and was switched off", cs.chat_id)
            if rule.source == "global":
                continue   # the bot owner's rule; the errors log above is where they look
            chat = await self.store.get_chat(cs.chat_id)
            title = html.escape(chat["title"] if chat and chat["title"] else str(cs.chat_id))
            await self.enforcer.notify(cs, "\n".join([
                f"⚠️ <b>{title}</b> · custom rule switched off",
                f"<b>Rule {rule.db_id} took too long to check a message, so I turned it off.</b>",
                f"<code>{html.escape(rule.regex.pattern)}</code>",
                "",
                "A slow rule holds up the bot in every group, so it cannot stay on.",
                "Try a simpler version with /rule add, plain words work best: /rule add free crypto",
            ]))

    async def is_exempt(self, chat_id: int, user_id: int) -> bool:
        if user_id == self.bot_id or self.config.is_owner(user_id):
            return True
        if await self.admins.is_admin(chat_id, user_id):
            return True
        if await self.store.is_bot_admin(chat_id, user_id):
            return True
        return await self.store.is_allowed_user(chat_id, user_id)

    def stat(self, chat_id: int) -> ChatStats:
        return self.chat_stats.setdefault(chat_id, ChatStats())

    @property
    def uptime(self) -> timedelta:
        return utcnow() - self.started_at
