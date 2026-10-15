"""Drives the real pipeline + enforcer + store against a fake Telegram bot."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message, User

from zyroguard.actions import Enforcer
from zyroguard.app import AdminCache, App
from zyroguard.captcha import handle_join
from zyroguard.config import load_config
from zyroguard.judge import Judge
from zyroguard.pipeline import process
from zyroguard.rules import load_rules
from zyroguard.settings import SettingsService
from zyroguard.store import Store

CHAT = -1001234567890
ADMIN = 111
BOT = 999
SPAMMER = 555
MODLOG = -1009999
ROYAL = "We are Royal Dynasty International Co., Ltd. looking for USDT holders, 10% to 25% commission per transaction"


class FakeBot:
    """Records every Telegram call; answers the few getters the code needs."""

    def __init__(self):
        self.id = BOT
        self.calls: list[tuple[str, tuple, dict]] = []
        self._msg_id = 1000
        self.admins: dict[int, list[int]] = {}   # chat_id -> extra admin ids (ADMIN is admin everywhere)

    def __getattr__(self, name):
        async def call(*a, **kw):
            self.calls.append((name, a, kw))
            if name == "get_chat_administrators":
                ids = [ADMIN] + self.admins.get(a[0] if a else kw.get("chat_id"), [])
                return [ChatMemberOwner(user=User(id=i, is_bot=False, first_name=f"u{i}"), is_anonymous=False) for i in ids]
            if name == "get_chat_member":
                return ChatMemberAdministrator(
                    user=User(id=BOT, is_bot=True, first_name="ZyroGuard"), can_be_edited=False,
                    is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                    can_manage_video_chats=True, can_restrict_members=True, can_promote_members=False,
                    can_change_info=False, can_invite_users=True, can_post_stories=False,
                    can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False)
            if name == "get_chat":
                return SimpleNamespace(bio=None)
            if name == "send_message":
                self._msg_id += 1
                return SimpleNamespace(message_id=self._msg_id)
            return True
        return call

    def names(self):
        return [c[0] for c in self.calls]


def msg(text: str, user_id: int = SPAMMER, message_id: int = 42, **extra) -> Message:
    return Message.model_validate({
        "message_id": message_id,
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": CHAT, "type": "supergroup", "title": "Test group"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Spam", "username": "spammy"},
        "text": text,
        **extra,
    })


@pytest.fixture
async def app(tmp_path):
    cfg = load_config({
        "BOT_TOKEN": "123:abc", "OWNER_IDS": str(ADMIN), "ALLOWED_CHAT_IDS": str(CHAT),
        "MODLOG_CHAT_ID": str(MODLOG), "MODE": "enforce", "DB_PATH": str(tmp_path / "t.db"),
        "CAS_ENABLED": "false",
        "RULES_PATH": str(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml"),
    })
    store = Store(str(cfg.db_path))
    await store.open()
    bot = FakeBot()
    enforcer = Enforcer(bot, store, n8n_url=None, n8n_secret=None, global_modlog=MODLOG, bot_username="ZyroGuardBot")
    a = App(config=cfg, bot=bot, store=store, settings=SettingsService(store, cfg.default_mode),
            judge=Judge(store, None, cfg.judge_model, cfg.judge_effort), enforcer=enforcer,
            admins=AdminCache(bot), rules=load_rules(cfg.rules_path), bot_username="ZyroGuardBot", bot_id=BOT)
    await store.upsert_chat(CHAT, "Test group")
    await a.load_allowed()
    enforcer.exempt = a.is_exempt
    yield a
    await enforcer.close()
    await store.close()


async def test_spam_is_deleted_muted_then_banned(app):
    bot = app.bot
    await process(app, msg(ROYAL))
    names = bot.names()
    assert "delete_message" in names and "restrict_chat_member" in names
    assert "send_message" in names  # mod log
    modlog_call = [c for c in bot.calls if c[0] == "send_message"][-1]
    assert modlog_call[1][0] == MODLOG and "mute" in modlog_call[1][1].lower()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1
    rows = await app.store.recent_audit(CHAT, 5)
    assert rows[0].action == "mute" and rows[0].duration_s == 86400 and rows[0].mode == "enforce"

    # a second message inside the same burst reuses step 1: still a mute, no extra strike
    bot.calls.clear()
    await process(app, msg(ROYAL, message_id=43))
    assert "restrict_chat_member" in bot.names() and "ban_chat_member" not in bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1

    # two minutes later it is a new offence: strike 2, banned 30 days
    await app.store.db.execute("UPDATE strikes SET created_at=? WHERE chat_id=? AND user_id=?",
                               ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(), CHAT, SPAMMER))
    await app.store.db.commit()
    bot.calls.clear()
    await process(app, msg(ROYAL, message_id=44))
    assert "ban_chat_member" in bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 2
    lifts = await app.store.due_lifts()  # not due yet, 30d ban
    assert lifts == []


async def test_burst_of_spam_is_one_strike_even_when_concurrent(app):
    import asyncio
    await asyncio.gather(*(process(app, msg(ROYAL, message_id=50 + i)) for i in range(4)))
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1
    assert "ban_chat_member" not in app.bot.names()


async def test_shadow_mode_does_not_record_strikes(app):
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    await process(app, msg(ROYAL))
    await process(app, msg(ROYAL, message_id=43))
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    assert "restrict_chat_member" not in app.bot.names()
    # first real hit after switching to enforce is still strike 1
    await app.settings.set(CHAT, "mode", "enforce", ADMIN)
    app.bot.calls.clear()
    await process(app, msg(ROYAL, message_id=44))
    assert "restrict_chat_member" in app.bot.names() and "ban_chat_member" not in app.bot.names()


async def test_album_counts_once_for_flood(app):
    photo = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    await app.settings.set(CHAT, "media_unverified", "allow", ADMIN)
    await app.store.touch_member(CHAT, 700, "old", "Old")
    for i in range(8):
        await process(app, msg("", user_id=700, message_id=60 + i, photo=photo, media_group_id="album1"))
    assert "restrict_chat_member" not in app.bot.names()


async def test_admin_and_clean_messages_untouched(app):
    await process(app, msg(ROYAL, user_id=ADMIN))
    await process(app, msg("what time is the meetup?", user_id=777))
    assert "delete_message" not in app.bot.names()
    assert await app.store.recent_audit(CHAT, 5) == []


async def test_shadow_mode_logs_but_does_not_act(app):
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    await process(app, msg(ROYAL))
    names = app.bot.names()
    assert "delete_message" not in names and "restrict_chat_member" not in names
    assert "send_message" in names
    rows = await app.store.recent_audit(CHAT, 5)
    assert rows and rows[0].mode == "shadow"


async def test_paused_does_nothing(app):
    from datetime import timedelta
    from zyroguard.store import utcnow
    app.paused_until[CHAT] = utcnow() + timedelta(minutes=5)
    await process(app, msg(ROYAL))
    assert "delete_message" not in app.bot.names()


async def test_allowlisted_user_is_exempt(app):
    await app.store.allow_user(CHAT, SPAMMER, ADMIN)
    await process(app, msg(ROYAL))
    assert "delete_message" not in app.bot.names()


async def test_link_from_new_member_hits_but_old_member_passes(app):
    # new member: joined now
    await app.store.touch_member(CHAT, 600, "newbie", "New", joined=True)
    await process(app, msg("check https://example.com/offer", user_id=600))
    assert "delete_message" in app.bot.names()
    app.bot.calls.clear()
    # pre-existing member with no join record who has been talking here: not "new"
    for _ in range(6):
        await app.store.touch_member(CHAT, 601, "regular", "Regular", count_message=True)
    await process(app, msg("check https://example.com/offer", user_id=601))
    assert "delete_message" not in app.bot.names()


async def test_undo_reverses_mute(app):
    await process(app, msg(ROYAL))
    row = (await app.store.recent_audit(CHAT, 1))[0]
    app.bot.calls.clear()
    text = await app.enforcer.undo(row.id, ADMIN)
    assert "restrict_chat_member" in app.bot.names()  # unmute
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    assert (await app.store.get_audit(row.id)).undone_at is not None
    assert "undone" in text.lower() or "lifted" in text.lower() or "unmut" in text.lower()


async def test_join_gate_restricts_and_posts_captcha(app):
    cs = await app.settings.get(CHAT)
    user = User(id=700, is_bot=False, first_name="Newcomer", username="newc")
    await handle_join(app, CHAT, "Test group", user, cs)
    names = app.bot.names()
    assert "restrict_chat_member" in names and "send_message" in names
    pending = await app.store.captcha_get(CHAT, 700)
    assert pending is not None
    # strict_unverified: their message is dropped before rules even run
    app.bot.calls.clear()
    await process(app, msg("hello", user_id=700))
    assert "delete_message" in app.bot.names()


async def test_join_with_scam_name_is_actioned(app):
    cs = await app.settings.get(CHAT)
    user = User(id=701, is_bot=False, first_name="Binance Support t.me/claim", username="supp")
    await handle_join(app, CHAT, "Test group", user, cs)
    names = app.bot.names()
    assert "ban_chat_member" in names  # captcha_fail default kick = ban+unban
    assert await app.store.captcha_get(CHAT, 701) is None


async def test_owner_join_is_not_gated(app):
    cs = await app.settings.get(CHAT)
    owner = User(id=ADMIN, is_bot=False, first_name="Owner https://t.me/x")
    await handle_join(app, CHAT, "Test group", owner, cs)
    names = app.bot.names()
    assert "restrict_chat_member" not in names and "ban_chat_member" not in names


async def test_bot_admin_and_mod(app):
    from zyroguard.commands import require_admin
    from aiogram.dispatcher.event.bases import SkipHandler
    with pytest.raises(SkipHandler):
        await require_admin(msg("/ban", user_id=SPAMMER), app)
    await app.store.add_bot_admin(CHAT, SPAMMER, ADMIN)
    assert await require_admin(msg("/ban", user_id=SPAMMER), app)
    await process(app, msg(ROYAL))
    assert "delete_message" not in app.bot.names()
    assert await app.store.remove_bot_admin(CHAT, SPAMMER)
    await app.store.allow_user(CHAT, SPAMMER, ADMIN)
    await process(app, msg(ROYAL, message_id=44))
    assert "delete_message" not in app.bot.names()
    with pytest.raises(SkipHandler):
        await require_admin(msg("/ban", user_id=SPAMMER), app)


async def test_captcha_every_join_repeats_for_verified_user(app):
    cs = await app.settings.get(CHAT)
    user = User(id=800, is_bot=False, first_name="Returning", username="ret")
    await app.store.touch_member(CHAT, 800, "ret", "Returning", joined=True)
    await app.store.mark_verified(CHAT, 800)
    await handle_join(app, CHAT, "Test group", user, cs)
    assert await app.store.captcha_get(CHAT, 800) is not None  # asked again (default on)
    assert not (await app.store.get_member(CHAT, 800))["verified_at"]
    # switched off: a verified user walks straight in
    await app.settings.set(CHAT, "captcha_every_join", "off", ADMIN)
    await app.store.captcha_delete(CHAT, 800)
    await app.store.mark_verified(CHAT, 800)
    app.bot.calls.clear()
    await handle_join(app, CHAT, "Test group", user, await app.settings.get(CHAT))
    assert await app.store.captcha_get(CHAT, 800) is None and "restrict_chat_member" not in app.bot.names()


async def test_photo_from_new_member_is_only_deleted(app):
    await app.store.touch_member(CHAT, 610, "newbie", "New", joined=True)  # joined just now = new account too
    await app.settings.set(CHAT, "media_unverified", "delete", ADMIN)  # the strict house rule, off by default
    await process(app, msg("", user_id=610, photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]))
    names = app.bot.names()
    assert "delete_message" in names and "ban_chat_member" not in names and "restrict_chat_member" not in names
    assert await app.store.active_strikes(CHAT, 610) == 0


def test_emoji_captcha_question():
    from zyroguard.captcha import EMOJI_WORDS, _question
    q, answer, opts = _question("emoji")
    assert (q, answer) in EMOJI_WORDS and answer in opts and len(opts) == 4 and len(set(opts)) == 4


def test_present_handles_restricted_leavers():
    from aiogram.enums import ChatMemberStatus
    from zyroguard.captcha import _present
    assert _present(SimpleNamespace(status=ChatMemberStatus.MEMBER))
    assert not _present(SimpleNamespace(status=ChatMemberStatus.LEFT))
    assert not _present(SimpleNamespace(status=ChatMemberStatus.KICKED))
    assert _present(SimpleNamespace(status=ChatMemberStatus.RESTRICTED, is_member=True))
    assert not _present(SimpleNamespace(status=ChatMemberStatus.RESTRICTED, is_member=False))
