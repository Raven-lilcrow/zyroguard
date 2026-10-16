"""Adding the bot: an owner posting as an anonymous admin is recognised, other anonymous adds are
refused with a hint, /permit refuses Telegram's system accounts, and an ALLOWED_CHAT_IDS group can be
added back by anyone after a removal."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import Message

from tests.test_pipeline_e2e import ADMIN, CHAT
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from tests.test_tenants import STRANGER, THEIR_GROUP, added_event, dm
from zyroguard.main import on_my_chat_member
from zyroguard.owner import cmd_permit

ANON = 1087968824   # the account Telegram shows for every anonymous admin
REPLIES: list[str] = []


async def _quiet(self, text="", *a, **kw):
    REPLIES.append(text)
    return SimpleNamespace(message_id=1)


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    REPLIES.clear()
    monkeypatch.setattr(Message, "answer", _quiet)
    monkeypatch.setattr(Message, "reply", _quiet)


def sent_to(app, chat_id: int) -> list[str]:
    return [c[1][1] for c in app.bot.calls if c[0] == "send_message" and c[1] and c[1][0] == chat_id]


async def kinds(app) -> list:
    from datetime import timedelta
    from zyroguard.store import utcnow
    return list(reversed(await app.store.owner_events_since(utcnow() - timedelta(days=1), limit=50)))


async def test_owner_adding_as_anonymous_admin_is_recognised(app):
    # the fake bot lists ADMIN (the owner) as an admin of every group
    await on_my_chat_member(added_event(THEIR_GROUP, ANON), app)
    assert "leave_chat" not in app.bot.names()
    assert THEIR_GROUP in app.allowed_chats and app.chat_allowed(THEIR_GROUP)
    assert await app.store.chat_owner(THEIR_GROUP) is None          # an owner's group, not a user's
    row = await app.store.get_chat(THEIR_GROUP)
    assert row["added_by"] == ADMIN
    added = [r for r in await kinds(app) if r["kind"] == "bot_added"]
    assert added and "anonymous" in added[-1]["detail"]


async def test_anonymous_add_without_an_owner_among_the_admins_is_refused_with_a_hint(app, monkeypatch):
    async def no_owner(chat_id, *, force=False):
        return {STRANGER: SimpleNamespace(user=SimpleNamespace(id=STRANGER))}
    monkeypatch.setattr(app.admins, "admins", no_owner)
    # even a (mistaken) permit for the anonymous account must not let it in
    await app.store.add_permit(ANON, None, 1, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, ANON), app)
    assert "leave_chat" in app.bot.names()
    assert THEIR_GROUP not in app.allowed_chats
    hint = " ".join(sent_to(app, THEIR_GROUP))
    assert "anonymous" in hint.lower() and "Remain anonymous" in hint
    refused = [r for r in await kinds(app) if r["kind"] == "bot_refused"]
    assert refused and "anonymous" in refused[-1]["detail"].lower()


async def test_permit_refuses_telegram_system_accounts(app):
    for uid in (ANON, 136817688, 777000):
        m = dm(f"/permit {uid}", ADMIN)
        await cmd_permit(m, app, SimpleNamespace(command="permit", args=str(uid)))
        assert await app.store.get_permit(uid) is None
    assert REPLIES and all("system account" in r for r in REPLIES)


async def test_permit_still_works_for_a_person(app):
    m = dm("/permit 7000 1 30d", ADMIN)
    await cmd_permit(m, app, SimpleNamespace(command="permit", args="7000 1 30d"))
    assert await app.store.get_permit(7000) is not None


async def test_allowlist_group_can_be_added_back_by_anyone_after_a_removal(app):
    assert CHAT in app.config.allowed_chat_ids
    await on_my_chat_member(added_event(CHAT, ADMIN, status="left"), app)      # removed
    assert CHAT not in app.allowed_chats
    await on_my_chat_member(added_event(CHAT, STRANGER), app)                  # someone adds it back
    assert "leave_chat" not in app.bot.names()
    assert CHAT in app.allowed_chats and app.chat_allowed(CHAT)


async def test_a_stranger_still_cannot_add_it_to_another_group(app):
    await on_my_chat_member(added_event(THEIR_GROUP, STRANGER), app)
    assert "leave_chat" in app.bot.names()
