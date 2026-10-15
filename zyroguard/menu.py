"""The "/" command menu Telegram shows, registered per scope the way Rose does it.

Members in a group see three harmless commands, group admins see the
moderation set, private chats see setup and help, and the bot owner also
gets the owner commands. Registered at startup; failures are only logged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
)

log = logging.getLogger("zyroguard.menu")


def _cmds(pairs: Iterable[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in pairs]


MEMBER = _cmds([
    ("ask", "Ask me a question"),
    ("clear", "Forget our earlier questions"),
    ("help", "How ZyroGuard works"),
    ("report", "Reply to a message to report it"),
    ("id", "Show chat and user ids"),
])

ADMIN = _cmds([
    ("ban", "Ban a user (reply or @user, optional time)"),
    ("mute", "Mute a user (reply or @user, optional time)"),
    ("kick", "Remove a user, they can rejoin"),
    ("warn", "Give a strike"),
    ("unban", "Lift a ban"),
    ("unmute", "Lift a mute"),
    ("undo", "Reverse an action by its number"),
    ("strikes", "Show a user's strikes"),
    ("pardon", "Clear a user's strikes"),
    ("allow", "Trust a user, skip all checks"),
    ("disallow", "Stop trusting a user"),
    ("allowed", "Trusted users and link lists"),
    ("allowdomain", "Always allow links to a site"),
    ("blockdomain", "Always remove links to a site"),
    ("rule", "Your own spam patterns: add, list, del"),
    ("spam", "Teach me spam: reply, or add a /log number"),
    ("notspam", "Teach me a message is fine: reply or number"),
    ("examples", "What I learned from /spam"),
    ("showadmin", "Who has power in this group"),
    ("addadmin", "Let someone use my admin commands here"),
    ("removeadmin", "Take my admin commands back"),
    ("zyromod", "Support mode: answer from your files"),
    ("mode", "shadow (report only) or enforce"),
    ("pause", "Stop filtering for a while"),
    ("resume", "Start filtering again"),
    ("setsafety", "Safety check on or off"),
    ("settings", "Change how strict I am"),
    ("set", "Change one setting"),
    ("unset", "Put a setting back to default"),
    ("setgreeting", "Message after the captcha"),
    ("log", "My last actions"),
    ("why", "Why I took an action"),
    ("test", "Show what I would do with a text"),
    ("stats", "What I caught lately"),
    ("diag", "Health check"),
    ("help", "How ZyroGuard works"),
    ("id", "Show chat and user ids"),
    ("ask", "Ask me a question"),
    ("clear", "Forget our earlier questions"),
])

PRIVATE = _cmds([
    ("start", "What I am and how to set me up"),
    ("use", "Pick which of your groups to manage"),
    ("settings", "Change how strict I am"),
    ("diag", "Health check of your group"),
    ("mode", "shadow (report only) or enforce"),
    ("log", "My last actions in your group"),
    ("stats", "What I caught lately"),
    ("test", "Show what I would do with a text"),
    ("why", "Why I took an action"),
    ("undo", "Reverse an action by its number"),
    ("examples", "What I learned from /spam"),
    ("zyromod", "Support mode: answer from your files"),
    ("ban", "Ban a user by @user or id"),
    ("mute", "Mute a user by @user or id"),
    ("kick", "Remove a user, they can rejoin"),
    ("warn", "Give a strike"),
    ("unban", "Lift a ban"),
    ("unmute", "Lift a mute"),
    ("strikes", "Show a user's strikes"),
    ("pardon", "Clear a user's strikes"),
    ("allow", "Trust a user, skip all checks"),
    ("disallow", "Stop trusting a user"),
    ("allowed", "Trusted users and link lists"),
    ("allowdomain", "Always allow links to a site"),
    ("blockdomain", "Always remove links to a site"),
    ("rule", "Your own spam patterns: add, list, del"),
    ("showadmin", "Who has power in your group"),
    ("addadmin", "Let someone use my admin commands"),
    ("removeadmin", "Take my admin commands back"),
    ("pause", "Stop filtering for a while"),
    ("resume", "Start filtering again"),
    ("set", "Change one setting"),
    ("unset", "Put a setting back to default"),
    ("setsafety", "Safety check on or off"),
    ("help", "How ZyroGuard works"),
    ("id", "Show your id"),
    ("ask", "Ask me a question"),
    ("clear", "Forget our earlier questions"),
    ("contact", "Message the admin"),
])

OWNER_PRIVATE_EXTRA = _cmds([
    ("groups", "Owner: every group I am in"),
    ("usage", "Owner: AI usage per group"),
    ("reloadrules", "Owner: re-read the rules file"),
    ("permit", "Owner: let someone add me to their group"),
    ("revoke", "Owner: take a permit back"),
    ("permits", "Owner: who has a permit"),
    ("history", "Owner: what happened lately"),
    ("ledger", "Owner: activity sheet status"),
    ("purge", "Owner: delete records of a group I left"),
])


async def register(bot: Bot, owner_ids: Iterable[int], group_ids: Iterable[int]) -> None:
    """Set the menus. Best effort: a failure here must never stop the bot."""
    plans = [
        (BotCommandScopeDefault(), MEMBER),
        (BotCommandScopeAllGroupChats(), MEMBER),
        (BotCommandScopeAllChatAdministrators(), ADMIN),
        (BotCommandScopeAllPrivateChats(), PRIVATE),
    ]
    owners = list(owner_ids)
    for uid in owners:
        plans.append((BotCommandScopeChat(chat_id=uid), PRIVATE + OWNER_PRIVATE_EXTRA))
        for gid in group_ids:   # the admin menu even in groups where the owner is not a Telegram admin
            plans.append((BotCommandScopeChatMember(chat_id=gid, user_id=uid), ADMIN))
    done = 0
    for scope, cmds in plans:
        try:
            await bot.set_my_commands(cmds, scope=scope)
            done += 1
        except Exception as e:  # noqa: BLE001
            log.warning("could not set command menu for %s: %s", scope.type, e)
    log.info("command menus set (%d/%d scopes)", done, len(plans))
