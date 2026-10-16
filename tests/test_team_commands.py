"""Users run their own team (/addadmin, /removeadmin, /addmod, /removemod, /showadmin) in their own
groups; the remaining owner commands stay with the bot's owners only, even in /help."""
from datetime import datetime, timezone

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandObject
from aiogram.types import Message

from zyroguard import help as h
from zyroguard import menu, owner

from .test_pipeline_e2e import ADMIN, CHAT, app, msg  # noqa: F401
from .test_support import _admin

PAYER = 777        # added the bot to this group with their access, not a Telegram admin here
PLAIN = 222        # Telegram admin who can't add admins
FULL = 333         # Telegram admin who can add admins
HELPER = 888       # @helper, the person being added
STRANGER = 444


@pytest.fixture
async def team(app, monkeypatch):  # noqa: F811
    async def admins(chat_id, force=False):
        return {PLAIN: _admin(PLAIN, False), FULL: _admin(FULL, True)}
    monkeypatch.setattr(app.admins, "admins", admins)
    await app.store.set_chat_owner(CHAT, PAYER)
    await app.store.touch_member(CHAT, HELPER, "helper", "Helper")
    replies = []

    async def reply(self, text, **kw):
        replies.append(text)
    monkeypatch.setattr(Message, "reply", reply)
    return replies


def _cmd(name: str, arg: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command=name, args=arg)


def _reports(app) -> list[str]:  # noqa: F811
    return [c[1][1] for c in app.bot.calls if c[0] == "send_message"]


async def test_the_paying_user_and_admins_who_can_add_admins_manage_bot_admins(app, team):  # noqa: F811
    await owner.cmd_addadmin(msg("/addadmin @helper", user_id=PAYER), app, _cmd("addadmin", "@helper"))
    assert await app.store.is_bot_admin(CHAT, HELPER)
    assert "@helper can now use the admin commands here" in team[-1]
    assert any("made @helper a bot admin" in r for r in _reports(app))          # the change is reported
    await owner.cmd_removeadmin(msg("/removeadmin @helper", user_id=FULL), app, _cmd("removeadmin", "@helper"))
    assert not await app.store.is_bot_admin(CHAT, HELPER)
    assert any("removed @helper as a bot admin" in r for r in _reports(app))


async def test_plain_admins_and_bot_admins_cannot_hand_out_commands(app, team):  # noqa: F811
    await owner.cmd_addadmin(msg("/addadmin @helper", user_id=PLAIN), app, _cmd("addadmin", "@helper"))
    assert team[-1] == owner.TEAM_ONLY and not await app.store.is_bot_admin(CHAT, HELPER)
    await app.store.add_bot_admin(CHAT, HELPER, PAYER)
    await owner.cmd_addadmin(msg("/addadmin 999", user_id=HELPER), app, _cmd("addadmin", "999"))
    assert team[-1] == owner.TEAM_ONLY and not await app.store.is_bot_admin(CHAT, 999)   # no chains of bot admins


async def test_members_are_skipped_quietly(app, team):  # noqa: F811
    for handler, name in ((owner.cmd_addadmin, "addadmin"), (owner.cmd_addmod, "addmod")):
        with pytest.raises(SkipHandler):
            await handler(msg(f"/{name} @helper", user_id=STRANGER), app, _cmd(name, "@helper"))
    with pytest.raises(SkipHandler):
        await owner.cmd_showadmin(msg("/showadmin", user_id=STRANGER), app)
    assert team == []


async def test_any_admin_can_trust_someone_and_see_the_team(app, team):  # noqa: F811
    await owner.cmd_addmod(msg("/addmod @helper", user_id=PLAIN), app, _cmd("addmod", "@helper"))
    assert HELPER in await app.store.list_allowed_users(CHAT)
    await owner.cmd_showadmin(msg("/showadmin", user_id=PLAIN), app)
    shown = team[-1]
    assert "Bot owners" not in shown and str(ADMIN) not in shown                 # owners stay private
    assert "Added me here" in shown and str(PAYER) in shown and "@helper" in shown
    await owner.cmd_showadmin(msg("/showadmin", user_id=ADMIN), app)
    assert "Bot owners" in team[-1]


def _private(text: str, uid: int) -> Message:
    return Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "A"},
        "from": {"id": uid, "is_bot": False, "first_name": "A"}, "text": text})


async def test_help_shows_owner_commands_to_the_owners_only(app, monkeypatch):  # noqa: F811
    sent = []

    async def answer(self, text, **kw):
        sent.append(text)
    monkeypatch.setattr(Message, "answer", answer)
    for name in ("permit", "purge", "groups", "usage"):
        await h.cmd_help(_private(f"/help {name}", PAYER), app, _cmd("help", name))
        assert sent[-1].startswith("I have no help page called"), name
        await h.cmd_help(_private(f"/help {name}", ADMIN), app, _cmd("help", name))
        assert f"/{name}</b> · command" in sent[-1], name
    await h.cmd_help(_private("/help addadmin", PAYER), app, _cmd("help", "addadmin"))
    assert "/addadmin</b> · command" in sent[-1] and "Owner only" not in sent[-1]


def test_menus_and_help_pages():
    admin = {c.command: c.description for c in menu.ADMIN}
    assert {"showadmin", "addadmin", "removeadmin"} <= set(admin) and not any("Owner" in d for d in admin.values())
    owner_menu = {c.command for c in menu.OWNER_PRIVATE_EXTRA}
    assert {"permit", "revoke", "permits", "groups", "usage", "purge"} <= owner_menu
    assert not owner_menu & {"addadmin", "removeadmin", "addmod", "removemod", "showadmin"}
    for name in ("addadmin", "removeadmin", "addmod", "removemod", "showadmin"):
        assert not h.owner_only(name), name
    assert "<code>/addadmin @user</code>" in h.TOPICS["trust"] and "/showadmin" in h.TOPICS["admin"]
    assert "the owner's" not in h.TOPICS["private"]
