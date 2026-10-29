"""/strikes (and /warns) and /pardon: a user's active strikes, in a group and in the private chat,
by reply, @username, id, or a member picked from the @ autocomplete who has no username."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram import Dispatcher
from aiogram.filters import CommandObject
from aiogram.types import Message, Update

from zyroguard import access_requests, assistant, captcha, commands as c, contact, diag, forwards, help as help_mod, owner, pipeline, support
from zyroguard.main import membership

from .test_pipeline_e2e import ADMIN, BOT, CHAT, SPAMMER, app, msg  # noqa: F401

NONAME = 777


def _cmd(arg: str | None = None, name: str = "strikes") -> CommandObject:
    return CommandObject(prefix="/", command=name, args=arg)


def _private(text: str, uid: int = ADMIN) -> Message:
    return Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "Ad"},
        "from": {"id": uid, "is_bot": False, "first_name": "Ad"}, "text": text,
    })


def _reply_to_spammer() -> dict:
    return {"reply_to_message": {"message_id": 41, "date": 1, "chat": {"id": CHAT, "type": "supergroup"},
                                 "from": {"id": SPAMMER, "is_bot": False, "first_name": "Spam", "username": "spammy"},
                                 "text": "hi"}}


def _picked(text: str, name: str, uid: int = NONAME) -> dict:
    """Entities the client sends when a member without a username is chosen from the @ list."""
    start = text.index(name)
    return {"entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])},
                         {"type": "text_mention", "offset": start, "length": len(name),
                          "user": {"id": uid, "is_bot": False, "first_name": name}}]}


@pytest.fixture
def out(monkeypatch):
    o = SimpleNamespace(sent=[], errors=[])

    async def send(self, text, **kw):
        o.sent.append(text)
    monkeypatch.setattr(Message, "reply", send)
    monkeypatch.setattr(Message, "answer", send)
    return o


async def test_by_username_lists_each_strike(app, out):  # noqa: F811
    await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam")
    await app.store.add_strike(CHAT, SPAMMER, "scam links", timedelta(days=30), None)
    await app.store.add_strike(CHAT, SPAMMER, "fake admin", timedelta(days=30), None)
    await c.cmd_strikes(msg("/strikes @spammy", user_id=ADMIN), app, _cmd("@spammy"))
    text = out.sent[-1]
    assert "has 2 active strikes" in text and "scam links" in text and "fake admin" in text
    assert "/pardon @spammy" in text


async def test_by_id_and_by_reply(app, out):  # noqa: F811
    await app.store.add_strike(CHAT, SPAMMER, "scam links", timedelta(days=30), None)
    await c.cmd_strikes(msg(f"/strikes {SPAMMER}", user_id=ADMIN), app, _cmd(str(SPAMMER)))
    assert "has 1 active strike" in out.sent[-1]
    await c.cmd_strikes(msg("/strikes", user_id=ADMIN, **_reply_to_spammer()), app, _cmd())
    assert "has 1 active strike" in out.sent[-1]


async def test_member_without_username_picked_from_the_list(app, out):  # noqa: F811
    await app.store.add_strike(CHAT, NONAME, "flood", timedelta(days=30), None)
    text = "/strikes No Name"
    await c.cmd_strikes(msg(text, user_id=ADMIN, **_picked(text, "No Name")), app, _cmd("No Name"))
    assert "No Name has 1 active strike" in out.sent[-1] and f"<code>{NONAME}</code>" in out.sent[-1]
    assert f"/pardon {NONAME}" in out.sent[-1]   # no username to type, so the id is suggested
    # the words after the name are still the reason for /warn and friends
    text = "/warn No Name stop flooding"
    target, rest = await c.resolve_target(msg(text, user_id=ADMIN, **_picked(text, "No Name")), app, ["No", "Name", "stop", "flooding"])
    assert target.user_id == NONAME and rest == ["stop", "flooding"]
    # a mention that is not the first word is not the target
    text = "/strikes 555 cc No Name"
    target, rest = await c.resolve_target(msg(text, user_id=ADMIN, **_picked(text, "No Name")), app, ["555", "cc", "No", "Name"])
    assert target.user_id == SPAMMER


async def test_says_why_when_nobody_is_found(app, out):  # noqa: F811
    await c.cmd_strikes(msg("/strikes @ghost", user_id=ADMIN), app, _cmd("@ghost"))
    assert "not seen @ghost write" in out.sent[-1] and "/id" in out.sent[-1]
    await c.cmd_strikes(msg("/strikes", user_id=ADMIN), app, _cmd())
    assert "Reply to the user's message with /strikes, or write /strikes @username." in out.sent[-1]
    await c.cmd_pardon(msg("/pardon @ghost", user_id=ADMIN), app, _cmd("@ghost", "pardon"))
    assert "not seen @ghost write" in out.sent[-1]


async def test_no_strikes_and_expired_ones(app, out):  # noqa: F811
    await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam")
    await c.cmd_strikes(msg("/strikes @spammy", user_id=ADMIN), app, _cmd("@spammy"))
    assert "no active strikes" in out.sent[-1]
    await app.store.add_strike(CHAT, SPAMMER, "old", timedelta(seconds=-5), None)
    await c.cmd_strikes(msg("/strikes @spammy", user_id=ADMIN), app, _cmd("@spammy"))
    assert "no active strikes" in out.sent[-1]


async def test_pardon_clears_them(app, out):  # noqa: F811
    await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam")
    await app.store.add_strike(CHAT, SPAMMER, "scam links", timedelta(days=30), None)
    await c.cmd_pardon(msg("/pardon @spammy", user_id=ADMIN), app, _cmd("@spammy", "pardon"))
    assert "Cleared 1 strike" in out.sent[-1]
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0


async def test_private_chat_uses_the_managed_group(app, out):  # noqa: F811
    await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam")
    await app.store.add_strike(CHAT, SPAMMER, "scam links", None, None)
    await c.cmd_strikes(_private("/strikes @spammy"), app, _cmd("@spammy"))
    assert "has 1 active strike" in out.sent[-1] and "Test group" in out.sent[-1]


def _update(text: str, uid: int = ADMIN, private: bool = False, mid: int = 42, **extra) -> Update:
    chat = {"id": uid, "type": "private", "first_name": "Ad"} if private else {"id": CHAT, "type": "supergroup", "title": "Test group"}
    m = {"message_id": mid, "date": int(datetime.now(timezone.utc).timestamp()), "chat": chat,
         "from": {"id": uid, "is_bot": False, "first_name": "Ad", "username": "ad"}, "text": text,
         "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}], **extra}
    return Update.model_validate({"update_id": mid, "message": m})


async def test_the_whole_dispatcher_routes_it_to_the_command(app, out):  # noqa: F811
    """Every router main.py registers, in the same order, so nothing earlier can swallow it."""
    async def me():
        return SimpleNamespace(username="ZyroGuardBot", id=BOT)
    app.bot.me = me
    dp = Dispatcher()
    dp["app"] = app
    dp.message.outer_middleware(assistant.MentionedCommands())
    for r in (owner.router, access_requests.router, support.router, contact.router, help_mod.router, diag.router,
              c.router, assistant.router, membership, captcha.router, forwards.router, pipeline.router):
        dp.include_router(r)

    @dp.errors()
    async def on_error(event):
        out.errors.append(repr(event.exception))

    await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam")
    await app.store.add_strike(CHAT, SPAMMER, "scam links", timedelta(days=30), None)
    for text, kw in [("/strikes @spammy", {}), ("/strikes@ZyroGuardBot @spammy", {}), ("/warns @spammy", {}),
                     ("/strikes", _reply_to_spammer()), ("/strikes @spammy", {"private": True})]:
        out.sent.clear()
        await dp.feed_update(app.bot, _update(text, **kw))
        assert out.sent and "@spammy has 1 active strike" in out.sent[-1], text
    out.sent.clear()
    await dp.feed_update(app.bot, _update("/strikes @spammy", uid=SPAMMER))   # not an admin: silently skipped
    assert out.sent == [] and out.errors == []
