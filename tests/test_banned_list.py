"""/listbanned: everyone the bot banned in a group, by username, newest first. In a group it answers
for that group; in the private chat it asks which group first, then pages with buttons."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from zyroguard import commands as c
from zyroguard import help as h
from zyroguard import menu
from zyroguard.store import utcnow

from .test_pipeline_e2e import ADMIN, CHAT, app, msg  # noqa: F401

OTHER = -1009876543210
STRANGER = 444


def _cmd(arg: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command="listbanned", args=arg)


def _private(text: str, uid: int = ADMIN) -> Message:
    return Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "Ad"},
        "from": {"id": uid, "is_bot": False, "first_name": "Ad"}, "text": text,
    })


def _press(data: str, markup: InlineKeyboardMarkup | None, uid: int = ADMIN) -> CallbackQuery:
    m = Message.model_validate({
        "message_id": 7, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "Ad"}, "text": "list",
        **({"reply_markup": markup.model_dump()} if markup else {}),
    })
    return CallbackQuery.model_validate({
        "id": "1", "from": {"id": uid, "is_bot": False, "first_name": "Ad"}, "chat_instance": "c",
        "data": data, "message": m.model_dump(),
    })


def _buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


async def _ban(app, uid: int, username: str | None, *, chat: int = CHAT, mode: str = "enforce",  # noqa: F811
               action: str = "ban", seconds: int | None = None, reason: str = "scam links") -> int:
    expires = utcnow() + timedelta(seconds=seconds) if seconds else None
    audit_id = await app.store.add_audit(
        chat_id=chat, user_id=uid, username=username, message_id=None, text_hash=None, text_excerpt=None,
        source="rule", reason=reason, detail=None, action=action, duration_s=seconds, expires_at=expires, mode=mode)
    if expires and action == "ban" and mode == "enforce":
        await app.store.schedule_lift(chat, uid, "ban", expires, audit_id)
    return audit_id


@pytest.fixture
def out(monkeypatch):
    """Everything the bot sent or edited, as (text, markup), plus the alerts shown on a button."""
    o = SimpleNamespace(sent=[], alerts=[])

    async def send(self, text, **kw):
        o.sent.append((text, kw.get("reply_markup")))

    async def cb_answer(self, text=None, **kw):
        if text:
            o.alerts.append(text)
    monkeypatch.setattr(Message, "reply", send)
    monkeypatch.setattr(Message, "answer", send)
    monkeypatch.setattr(Message, "edit_text", send)
    monkeypatch.setattr(CallbackQuery, "answer", cb_answer)
    return o


@pytest.fixture
def nobody_is_admin(app, monkeypatch):  # noqa: F811
    async def admins(chat_id, force=False):
        return {}
    monkeypatch.setattr(app.admins, "admins", admins)


async def test_in_a_group_it_lists_everyone_banned_there_by_username(app, out):  # noqa: F811
    await _ban(app, 500, "spammy")
    await _ban(app, 501, "timed", seconds=7 * 86400, reason="fake admin")
    await app.store.touch_member(CHAT, 502, None, "Nameless One")
    await _ban(app, 502, None)
    undone = await _ban(app, 503, "undone")
    await app.store.mark_undone(undone, ADMIN)
    await _ban(app, 504, "shadowed", mode="shadow")
    await _ban(app, 505, "kicked", action="kick")
    await _ban(app, 506, "muted", action="mute")
    await _ban(app, 507, "elsewhere", chat=OTHER)

    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    text, markup = out.sent[-1]
    assert "banned users · 3" in text and "Newest first." in text
    assert "@spammy (<code>500</code>)" in text and "forever" in text
    assert "@timed (<code>501</code>)" in text and "1w, until" in text and "fake admin" in text
    assert "Nameless One (<code>502</code>)" in text
    for gone in ("undone", "shadowed", "kicked", "muted", "elsewhere"):
        assert gone not in text
    assert text.index("Nameless One") < text.index("@timed") < text.index("@spammy")   # newest first
    assert "/unban" in text and "/why" in text
    assert markup is None   # three people fit on one page


async def test_each_person_once_with_their_newest_name_and_how_often(app, out):  # noqa: F811
    first = await _ban(app, 500, "oldname", seconds=3600)
    await app.store.cancel_lifts(CHAT, 500, "ban")                      # /unban before the hour was up
    await _ban(app, 500, "oldname")
    await app.store.touch_member(CHAT, 500, "newname", "New")           # they renamed themselves since
    await _ban(app, 501, "brief", seconds=60)
    await app.store.db.execute("UPDATE audit SET expires_at=? WHERE user_id=501",
                               ((utcnow() - timedelta(days=1)).isoformat(),))
    await app.store.db.commit()

    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    text, _ = out.sent[-1]
    assert "banned users · 2" in text
    assert text.count("<code>500</code>") == 1 and "@newname" in text and "oldname" not in text
    assert "forever · banned 2×" in text and f"#{first}" not in text   # the newest ban is the one shown
    assert "@brief (<code>501</code>)" in text and "1m, over" in text


async def test_a_lifted_timed_ban_says_so(app, out):  # noqa: F811
    await _ban(app, 500, "early", seconds=7 * 86400)
    await app.store.cancel_lifts(CHAT, 500, "ban")
    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    assert "1w, lifted early" in out.sent[-1][0]


async def test_nothing_banned_yet_and_the_shadow_mode_hint(app, out):  # noqa: F811
    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    text, markup = out.sent[-1]
    assert "not banned anyone here yet" in text and "shadow" not in text and markup is None
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    assert "shadow mode" in out.sent[-1][0]


async def test_members_are_skipped_quietly(app, out, nobody_is_admin):  # noqa: F811
    with pytest.raises(SkipHandler):
        await c.cmd_listbanned(msg("/listbanned", user_id=STRANGER), app, _cmd())
    assert out.sent == []


async def test_private_chat_asks_which_group_then_shows_it(app, out):  # noqa: F811
    await app.store.upsert_chat(OTHER, "Other group")
    await app.load_allowed()
    await _ban(app, 500, "spammy")
    await _ban(app, 507, "elsewhere", chat=OTHER)

    await c.cmd_listbanned(_private("/listbanned"), app, _cmd())
    text, picker = out.sent[-1]
    assert "Which group" in text
    assert sorted(_buttons(picker)) == sorted([f"banned:{CHAT}:0", f"banned:{OTHER}:0"])

    await c.cb_banned(_press(f"banned:{CHAT}:0", picker), app)
    text, markup = out.sent[-1]
    assert "Test group" in text and "@spammy" in text and "elsewhere" not in text and markup is None

    await c.cb_banned(_press(f"banned:{OTHER}:0", picker), app)
    text, _ = out.sent[-1]
    assert "Other group" in text and "@elsewhere" in text and "spammy" not in text
    assert out.alerts == []


async def test_one_group_or_an_id_skips_the_question(app, out):  # noqa: F811
    await _ban(app, 500, "spammy")
    await c.cmd_listbanned(_private("/listbanned"), app, _cmd())
    assert "@spammy" in out.sent[-1][0]                                 # the only group: no question asked
    await c.cmd_listbanned(_private(f"/listbanned {CHAT}"), app, _cmd(str(CHAT)))
    assert "@spammy" in out.sent[-1][0]
    await c.cmd_listbanned(_private("/listbanned -100999"), app, _cmd("-100999"))
    assert "not one of your groups" in out.sent[-1][0]


async def test_people_without_a_group_are_pointed_to_the_owner(app, out, nobody_is_admin):  # noqa: F811
    await c.cmd_listbanned(_private("/listbanned", uid=STRANGER), app, _cmd())
    assert out.sent[-1][0].startswith(c.NO_GROUP)


async def test_strangers_cannot_use_the_buttons(app, out, nobody_is_admin):  # noqa: F811
    await _ban(app, 500, "spammy")
    picker = c.banned_picker([{"chat_id": CHAT, "title": "Test group"}])
    await c.cb_banned(_press(f"banned:{CHAT}:0", picker, uid=STRANGER), app)
    assert out.sent == [] and out.alerts == ["That is not one of your groups."]
    await c.cb_banned(_press(f"banned:{CHAT}:0", None), app)           # data that was never a button here
    assert out.sent == [] and len(out.alerts) == 2


async def test_long_lists_come_in_pages(app, out):  # noqa: F811
    for i in range(45):
        await _ban(app, 1000 + i, f"user{i}")
    await c.cmd_listbanned(msg("/listbanned", user_id=ADMIN), app, _cmd())
    text, markup = out.sent[-1]
    assert "banned users · 45" in text and "1–20 of 45" in text
    assert "@user44" in text and "@user25" in text and "@user24" not in text
    assert _buttons(markup) == [f"banned:{CHAT}:20"]
    assert len(text) < c.MAX_MESSAGE

    await c.cb_banned(_press(f"banned:{CHAT}:20", markup), app)
    text, markup = out.sent[-1]
    assert "21–40 of 45" in text and "@user24" in text and "@user5" in text and "@user4 " not in text
    assert _buttons(markup) == [f"banned:{CHAT}:0", f"banned:{CHAT}:40"]

    await c.cb_banned(_press(f"banned:{CHAT}:40", markup), app)
    text, markup = out.sent[-1]
    assert "41–45 of 45" in text and "@user0 " in text and _buttons(markup) == [f"banned:{CHAT}:20"]

    text, _ = await c.banned_page(app, CHAT, 999)                      # past the end: the last page
    assert "41–45 of 45" in text


async def test_the_other_spellings_reach_the_same_handler(app):  # noqa: F811
    handler = next(x for x in c.router.message.handlers if x.callback is c.cmd_listbanned)
    accepts = handler.filters[0].callback
    for text in ("/listbanned", "/list_banned", "/list-banned", "/banned"):
        got = await accepts(msg(text, user_id=ADMIN), app.bot)
        assert got and got["command"].command == text[1:], text
    assert not await accepts(msg("/log", user_id=ADMIN), app.bot)


def test_help_and_the_menus_know_it():
    assert "listbanned" in h.COMMANDS and "/listbanned" in h.command_help("listbanned")
    for topic in ("admin", "punish", "check", "private"):
        assert "/listbanned" in h.TOPICS[topic], topic
    assert "listbanned" in {x.command for x in menu.ADMIN}
    assert "listbanned" in {x.command for x in menu.PRIVATE}
