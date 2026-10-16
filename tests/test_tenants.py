"""Several users: access, self-service adding, private-chat control, log routing, purge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import ChatMemberUpdated, Message

from tests.test_pipeline_e2e import ADMIN, BOT, CHAT, MODLOG, ROYAL, msg
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from zyroguard.commands import cid, pick_group, require_admin
from zyroguard.main import on_my_chat_member
from zyroguard.pipeline import process

CUSTOMER = 7000
STRANGER = 7001
THEIR_GROUP = -1005550001


def added_event(chat_id: int, by: int, status: str = "administrator") -> ChatMemberUpdated:
    now = int(datetime.now(timezone.utc).timestamp())
    bot_user = {"id": BOT, "is_bot": True, "first_name": "ZyroGuard", "username": "ZyroGuardBot"}
    new = {"status": status, "user": bot_user}
    if status == "administrator":
        new.update({"can_be_edited": False, "is_anonymous": False, "can_manage_chat": True,
                    "can_delete_messages": True, "can_manage_video_chats": True, "can_restrict_members": True,
                    "can_promote_members": False, "can_change_info": False, "can_invite_users": True,
                    "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False,
                    "can_send_welcome_messages": False})
    return ChatMemberUpdated.model_validate({
        "chat": {"id": chat_id, "type": "supergroup", "title": "Customer group"},
        "from": {"id": by, "is_bot": False, "first_name": "Cust"},
        "date": now,
        "old_chat_member": {"status": "left", "user": bot_user},
        "new_chat_member": new,
    })


def dm(text: str, user_id: int) -> Message:
    m = Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": user_id, "type": "private", "first_name": "Cust"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Cust"},
        "text": text,
    })
    m.as_(None)
    return m


async def _quiet_answer(self, *a, **kw):  # Message.answer needs a bound bot; record instead
    ANSWERS.append((a, kw))
    return SimpleNamespace(message_id=1)


ANSWERS: list = []


@pytest.fixture(autouse=True)
def _stub_answer(monkeypatch, app):
    ANSWERS.clear()
    monkeypatch.setattr(Message, "answer", _quiet_answer)
    # the user with access runs their own groups; the stranger runs nothing
    app.bot.admins[THEIR_GROUP] = [CUSTOMER]
    app.bot.admins[THEIR_GROUP - 1] = [CUSTOMER]


async def test_permit_holder_must_be_admin_of_the_group(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN)
    someone_elses_group = -1005550777
    await on_my_chat_member(added_event(someone_elses_group, CUSTOMER), app)
    assert "leave_chat" in app.bot.names()
    assert someone_elses_group not in app.allowed_chats


async def test_stranger_adding_bot_is_refused(app):
    await on_my_chat_member(added_event(THEIR_GROUP, STRANGER), app)
    names = app.bot.names()
    assert "leave_chat" in names
    assert THEIR_GROUP not in app.allowed_chats
    assert not app.chat_allowed(THEIR_GROUP)


async def test_permit_holder_adds_bot_and_gets_onboarding_dm(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, "trial", ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    assert "leave_chat" not in app.bot.names()
    assert THEIR_GROUP in app.allowed_chats
    assert await app.store.chat_owner(THEIR_GROUP) == CUSTOMER
    dms = [c for c in app.bot.calls if c[0] == "send_message" and c[1][0] == CUSTOMER]
    assert dms and "Manage it from this chat" in dms[0][1][1]
    assert app.dm_context[CUSTOMER] == THEIR_GROUP
    # second group is over their limit
    ok, why = await app.store.permit_valid(CUSTOMER)
    assert not ok and "1 of 1" in why
    app.bot.calls.clear()
    await on_my_chat_member(added_event(THEIR_GROUP - 1, CUSTOMER), app)
    assert "leave_chat" in app.bot.names()


async def test_expired_permit_is_refused(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, datetime.now(timezone.utc) - timedelta(days=1), None, ADMIN)
    ok, why = await app.store.permit_valid(CUSTOMER)
    assert not ok and why == "permit expired"
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    assert "leave_chat" in app.bot.names()


async def test_actions_in_customer_group_are_logged_to_their_private_chat(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    app.bot.calls.clear()
    spam = Message.model_validate({
        "message_id": 9, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": THEIR_GROUP, "type": "supergroup", "title": "Customer group"},
        "from": {"id": 8123, "is_bot": False, "first_name": "Spam", "username": "sp"},
        "text": ROYAL,
    })
    await process(app, spam)
    sends = [c for c in app.bot.calls if c[0] == "send_message"]
    assert sends, "expected a log post"
    targets = {c[1][0] for c in sends}
    assert CUSTOMER in targets and MODLOG not in targets
    # The owner's own group still logs to the global log group
    app.bot.calls.clear()
    await process(app, msg(ROYAL))
    targets = {c[1][0] for c in app.bot.calls if c[0] == "send_message"}
    assert MODLOG in targets and CUSTOMER not in targets


async def test_customer_controls_only_their_group(app):
    await app.store.add_permit(CUSTOMER, "cust", 2, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    assert await app.controls(CUSTOMER, THEIR_GROUP)
    assert not await app.controls(CUSTOMER, CHAT)
    assert await app.controls(ADMIN, THEIR_GROUP)  # bot owner controls everything
    # private chat: with one group, it is picked automatically
    m = dm("/settings", CUSTOMER)
    assert await require_admin(m, app)
    assert cid(m, app) == THEIR_GROUP
    # a stranger in private chat gets the contact hint, no group
    s = dm("/settings", STRANGER)
    assert not await require_admin(s, app)
    assert ANSWERS and "do not manage any group" in ANSWERS[-1][0][0]
    # a member of the owner's group cannot run admin commands there
    with pytest.raises(SkipHandler):
        await require_admin(msg("/ban", user_id=CUSTOMER), app)


async def test_owner_with_several_groups_is_asked_to_pick(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    app.dm_context.clear()
    m = dm("/diag", ADMIN)
    assert await pick_group(m, app) is None
    assert "Which group" in ANSWERS[-1][0][0]
    app.dm_context[ADMIN] = THEIR_GROUP
    assert await pick_group(m, app) == THEIR_GROUP


async def test_revoke_purges_everything(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    await app.settings.set(THEIR_GROUP, "captcha", "off", CUSTOMER)
    await app.store.add_strike(THEIR_GROUP, 8123, "rule", timedelta(days=1), None)
    assert await app.store.revoke_permit(CUSTOMER)
    n = await app.store.purge_chat(THEIR_GROUP)
    assert n >= 3
    assert await app.store.get_chat(THEIR_GROUP) is None
    assert await app.store.active_strikes(THEIR_GROUP, 8123) == 0
    assert await app.store.get_settings(THEIR_GROUP) == {}
    # The owner's group untouched
    assert await app.store.get_chat(CHAT) is not None


async def test_ai_budget_per_customer(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN, daily_ai_cap=2)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    assert await app.ai_budget_ok(THEIR_GROUP)
    for _ in range(2):
        await app.store.add_judge_usage(THEIR_GROUP, "claude-opus-5", 10, 5, 0, 0, "spam")
    assert not await app.ai_budget_ok(THEIR_GROUP)
    assert await app.ai_budget_ok(CHAT)  # owner group is never capped here


async def test_owner_added_group_survives_restart(app):
    OWNER_GROUP = -1005550009
    await on_my_chat_member(added_event(OWNER_GROUP, ADMIN), app)
    assert OWNER_GROUP in app.allowed_chats
    app.allowed_chats.clear()
    await app.load_allowed()          # what startup does
    assert OWNER_GROUP in app.allowed_chats
    # removed from the group, so forgotten
    await on_my_chat_member(added_event(OWNER_GROUP, ADMIN, status="left"), app)
    assert OWNER_GROUP not in app.allowed_chats
    await app.load_allowed()
    assert OWNER_GROUP not in app.allowed_chats
