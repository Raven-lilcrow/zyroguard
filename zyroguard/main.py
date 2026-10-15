"""Entry point: wire everything and long-poll."""

from __future__ import annotations

import asyncio
import html
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import assistant, captcha, commands, contact, diag, forwards, help as help_mod, http, kb, ledger, menu, owner, pipeline, scheduler, similar, support
from .actions import Enforcer
from .app import AdminCache, App
from .config import VERSION, load_config
from .judge import Judge
from .rules import load_rules
from .settings import SettingsService
from .store import Store

log = logging.getLogger("zyroguard")
membership = Router(name="membership")


_RIGHTS = {"can_delete_messages": "Delete messages", "can_restrict_members": "Ban users",
           "can_invite_users": "Invite users"}


def _missing_rights(member) -> list[str]:
    """Rights ZyroGuard needs and does not have; everything when it is not an admin."""
    status = getattr(member, "status", None)
    if status == ChatMemberStatus.CREATOR:
        return []
    if status != ChatMemberStatus.ADMINISTRATOR:
        return list(_RIGHTS.values())
    return [label for attr, label in _RIGHTS.items() if not getattr(member, attr, False)]


def _status(status) -> str:
    return str(getattr(status, "value", status) or "")


def _refusal(why: str) -> str:
    """store.permit_valid's reason in words for the owner's record."""
    if why == "no permit":
        return "No access"
    if why == "permit expired":
        return "Access expired"
    if why.startswith("already using"):
        return "Group limit reached (" + why.removeprefix("already using ").replace(" allowed groups", "") + ")"
    return why


async def _leave(app: App, chat, why: str) -> None:
    log.warning("added to %s (%s) by someone without a permit (%s), leaving", chat.title, chat.id, why)
    try:
        from .contact import contact_button, contact_line
        await app.bot.send_message(chat.id, "This bot only guards groups of people with a permit, so I am leaving.\n"
                                   + contact_line(app), reply_markup=contact_button(app))
    except Exception:  # noqa: BLE001
        pass
    try:
        await app.bot.leave_chat(chat.id)
    finally:
        await app.store.deactivate_chat(chat.id)


async def _welcome_owner(app: App, owner_id: int, chat, rights_ok: bool) -> None:
    """Private-chat onboarding for the permit holder who just added the bot."""
    title = chat.title or str(chat.id)
    lines = [f"🛡 <b>{html.escape(title)}</b>",
             "<b>I am guarding this group now.</b>" if rights_ok else
             "<b>I am in, but not an admin yet.</b> Make me an admin there with Delete messages, Ban users and Invite users via link, then send /diag here.",
             "",
             "<b>Manage it from this chat</b>",
             "/diag checks that I have what I need",
             "/settings changes how strict I am",
             "/mode shadow or /mode enforce",
             "/help explains the rest",
             "",
             "I post every action I take (with an undo number) here in this chat. "
             "Prefer a log group instead? Add me to a private group, send /id there, then send /set modlog_chat_id followed by that id here.",
             "",
             "Your settings, records and logs are separate from every other group I guard."]
    try:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Run check", callback_data=f"act:diag:{chat.id}"),
            InlineKeyboardButton(text="Open settings", callback_data=f"act:settings:{chat.id}"),
        ]])
        await app.bot.send_message(owner_id, "\n".join(line for line in lines if line), parse_mode="HTML", reply_markup=markup)
    except Exception as e:  # noqa: BLE001
        log.info("could not DM new owner %s: %s", owner_id, e)


@membership.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, app: App) -> None:
    chat = event.chat
    new = event.new_chat_member.status
    old = event.old_chat_member.status
    if chat.type == ChatType.CHANNEL:
        if new not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
            if old in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                await ledger.note(app, "bot_refused", chat.id, actor=event.from_user, title=chat.title,
                                  reason="A channel (ZyroGuard only guards groups)")
            try:
                await app.bot.leave_chat(chat.id)   # I guard groups, not channels
            except Exception:  # noqa: BLE001
                pass
        return
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if new in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        known_row = await app.store.get_chat(chat.id)
        if known_row and known_row["active"] and not (event.from_user and event.from_user.id == app.bot_id):
            await ledger.note(app, "bot_removed", chat.id, actor=event.from_user, title=chat.title,
                              old_status=_status(old), new_status=_status(new))
        await app.store.deactivate_chat(chat.id)
        app.allowed_chats.discard(chat.id)
        app.dm_context = {u: g for u, g in app.dm_context.items() if g != chat.id}
        app.admins.invalidate(chat.id)
        log.info("removed from %s (%s)", chat.title, chat.id)
        return
    adder = event.from_user
    known = await app.store.get_chat(chat.id)
    registered_owner = int(known["owner_user_id"]) if known and known["owner_user_id"] else None
    owner_id: int | None = None
    if chat.id in app.allowed_chats:
        owner_id = registered_owner          # already ours: a rights change, not a new add
        if owner_id is not None and not (await app.store.permit_valid_for_existing(owner_id)):
            await ledger.note(app, "bot_left", chat.id, target=owner_id, title=chat.title)
            await _leave(app, chat, "owner's permit ended")
            return
    elif adder and app.config.is_owner(adder.id):
        owner_id = None                      # bot owner added it: behaves like the static list
    elif adder:
        ok, why = await app.store.permit_valid(adder.id)
        if not ok:
            await ledger.note(app, "bot_refused", chat.id, actor=adder, title=chat.title, reason=_refusal(why))
            await _leave(app, chat, f"{adder.id}: {why}")
            return
        # a permit holder may only register groups they actually run
        app.admins.invalidate(chat.id)
        if adder.id not in await app.admins.admins(chat.id, force=True):
            try:
                await app.bot.send_message(adder.id, f"I left {chat.title or 'that group'}: you need to be an admin "
                                                     "of a group to add me to it.")
            except Exception:  # noqa: BLE001
                pass
            await ledger.note(app, "bot_refused", chat.id, actor=adder, title=chat.title,
                              reason="Has access but is not an admin of this group")
            await _leave(app, chat, f"{adder.id}: not an admin of the group")
            return
        owner_id = adder.id
    else:
        await ledger.note(app, "bot_refused", chat.id, title=chat.title, reason="Added by an unknown person")
        await _leave(app, chat, "unknown adder")
        return

    was_ours = chat.id in app.allowed_chats
    is_new = await app.store.upsert_chat(chat.id, chat.title)
    log.info("in %s (%s) as %s, added by %s, customer %s", chat.title, chat.id, new, adder.id if adder else None, owner_id)
    if owner_id is not None or adder and app.config.is_owner(adder.id):
        await app.store.set_chat_owner(chat.id, owner_id)
    missing = _missing_rights(event.new_chat_member)
    if old in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        _, adder_name = ledger._person(adder)
        await app.store.set_chat_added_by(chat.id, adder.id if adder else None, adder_name)
        if owner_id is not None:
            p = await app.store.get_permit(owner_id)
            used = len(await app.store.chats_owned_by(owner_id))
            how = f"User access ({used} of {p['max_groups'] if p else '?'} groups)"
        elif chat.id in app.config.allowed_chat_ids:
            how = "Allowlist"
        else:
            how = "You"
        await ledger.note(app, "bot_added", chat.id, actor=adder, title=chat.title, how=how,
                          rights_ok=not missing, missing=missing, old_status=_status(old), new_status=_status(new),
                          mode=(await app.settings.get(chat.id)).raw("mode"))
    elif was_ours:
        old_missing = _missing_rights(event.old_chat_member)
        if set(missing) != set(old_missing):
            await ledger.note(app, "bot_rights", chat.id, actor=adder, title=chat.title,
                              lost=bool(set(missing) - set(old_missing)), rights_ok=not missing, missing=missing,
                              old_status=_status(old), new_status=_status(new))
    app.allowed_chats.add(chat.id)
    await app.refresh_access()
    app.admins.invalidate(chat.id)
    rights = await app.admins.bot_rights(chat.id, force=True) if new == ChatMemberStatus.ADMINISTRATOR else None
    rights_ok = bool(rights and rights.ok)
    if is_new:
        from . import menu
        await menu.register(app.bot, app.config.owner_ids, sorted(app.allowed_chats))
        if owner_id is not None:
            app.dm_context[owner_id] = chat.id
            await _welcome_owner(app, owner_id, chat, rights_ok)
    if new == ChatMemberStatus.ADMINISTRATOR:
        if is_new or not rights_ok:
            try:
                await app.bot.send_message(
                    chat.id,
                    "ZyroGuard is here. " + ("All rights look good. " if rights_ok else
                                            "I still need Delete messages, Ban users and Invite users via link. ")
                    + ("Admins: I start in report-only mode. Send /diag to check me and /mode enforce when you are ready."
                       if (await app.settings.get(chat.id)).raw("mode") == "shadow" else
                       "Admins: send /diag to check me and /settings to adjust me.")
                    + (" Manage me in our private chat." if owner_id is not None else ""))
            except Exception:  # noqa: BLE001
                pass
    elif is_new:
        try:
            await app.bot.send_message(chat.id, "Thanks for adding ZyroGuard. Promote me to admin with "
                                                "Delete messages, Ban users and Invite users via link, then run /diag.")
        except Exception:  # noqa: BLE001
            pass


@membership.message(F.migrate_to_chat_id)
async def on_migrate(message: Message, app: App) -> None:
    """Telegram upgraded a basic group to a supergroup: same group, new id."""
    old_id, new_id = message.chat.id, int(message.migrate_to_chat_id)
    if old_id not in app.allowed_chats and not app.chat_allowed(old_id):
        return
    await app.store.migrate_chat(old_id, new_id)
    app.allowed_chats.discard(old_id)
    app.allowed_chats.add(new_id)
    app.dm_context = {u: (new_id if g == old_id else g) for u, g in app.dm_context.items()}
    app.settings.invalidate(old_id)
    app.settings.invalidate(new_id)
    app.admins.invalidate(old_id)
    app.paused_until.pop(old_id, None)
    log.info("group %s migrated to supergroup %s", old_id, new_id)


async def run() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    store = Store(config.db_path)
    try:
        await store.open()
    except Exception as e:  # noqa: BLE001
        log.error("cannot open the database at %s: %s", config.db_path, e)
        raise SystemExit(1)
    try:
        rules = load_rules(Path(config.rules_path))
    except Exception as e:  # noqa: BLE001
        log.error("rules.yaml has a problem, fix it and restart: %s", e)
        raise SystemExit(1)
    # allow_sending_without_reply: when the message I answer was deleted meanwhile (by its sender or by me),
    # my reply still goes out instead of failing with "message to be replied not found"
    bot = Bot(config.bot_token, default=DefaultBotProperties(link_preview_is_disabled=True,
                                                             allow_sending_without_reply=True))
    try:
        me = await bot.get_me()
    except Exception as e:  # noqa: BLE001
        log.error("cannot reach Telegram (token or network): %s", str(e).replace(config.bot_token, "<token>"))
        raise SystemExit(1)

    enforcer = Enforcer(bot, store, n8n_url=config.n8n_event_url, n8n_secret=config.n8n_event_secret,
                        global_modlog=config.modlog_chat_id, bot_username=me.username or "", owner_ids=config.owner_ids)
    app = App(
        config=config, bot=bot, store=store,
        settings=SettingsService(store, config.default_mode),
        judge=Judge(store, config.anthropic_api_key, config.judge_model, config.judge_effort),
        enforcer=enforcer, admins=AdminCache(bot), rules=rules,
        bot_username=me.username or "", bot_id=me.id,
    )
    app.assistant = assistant.Assistant(store, app.judge.client, config.judge_model, config.assistant_effort)
    enforcer.exempt = app.is_exempt

    dp = Dispatcher()
    dp["app"] = app
    dp.message.outer_middleware(assistant.MentionedCommands())   # "@zyroguard_bot /spam" works like "/spam"
    dp.include_router(owner.router)
    dp.include_router(support.router)   # before contact and help: /cancel, /start?start=zyromod, setup replies
    dp.include_router(contact.router)   # before help: it owns /start?start=contact
    dp.include_router(help_mod.router)
    dp.include_router(diag.router)
    dp.include_router(commands.router)
    dp.include_router(assistant.router)
    dp.include_router(membership)
    dp.include_router(captcha.router)
    dp.include_router(forwards.router)
    dp.include_router(pipeline.router)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        log.exception("unhandled error: %s", event.exception)
        chat_id = None
        upd = event.update
        if upd.message:
            chat_id = upd.message.chat.id
        elif upd.callback_query and upd.callback_query.message:
            chat_id = upd.callback_query.message.chat.id
        try:
            await store.log_error("handler", f"{type(event.exception).__name__}: {event.exception}", chat_id)
        except Exception:  # noqa: BLE001
            pass

    for chat_id in config.allowed_chat_ids:
        try:
            chat = await bot.get_chat(chat_id)
            await store.upsert_chat(chat_id, chat.title)
        except Exception as e:  # noqa: BLE001
            log.warning("cannot read allowed chat %s: %s", chat_id, e)
    await app.load_allowed()
    await ledger.start(app)

    await menu.register(bot, config.owner_ids, sorted(app.allowed_chats))

    background: list[asyncio.Task] = []
    if config.similarity_enabled:
        # the model takes a few seconds to load; messages are checked without it until then
        app.similar = similar.Library(store, similar.Embedder())
        background.append(asyncio.create_task(app.similar.start(), name="similar"))
        app.kb = kb.KnowledgeBase(store, app.similar)   # support mode searches with the same model

    runner = await http.start(app)
    sched = asyncio.create_task(scheduler.run(app), name="scheduler")
    log.info("ZyroGuard %s starting as @%s (mode default %s, judge %s, %d allowed chats)",
             VERSION, me.username, config.default_mode, config.judge_model, len(config.allowed_chat_ids))
    try:
        await dp.start_polling(
            bot, allowed_updates=["message", "edited_message", "callback_query", "chat_member", "my_chat_member"],
            handle_signals=True,
        )
    finally:
        for t in [sched, *background]:
            t.cancel()
        await asyncio.gather(sched, *background, return_exceptions=True)
        await runner.cleanup()
        await enforcer.close()
        await ledger.close()
        await store.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
