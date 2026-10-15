"""Disappearing group notices and the admin safety net."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiogram.types import Message

from tests.test_pipeline_e2e import ADMIN, CHAT, ROYAL, SPAMMER, msg
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from zyroguard.actions import Decision, Target
from zyroguard.pipeline import process


def _group_sends(bot):
    return [c for c in bot.calls if c[0] == "send_message" and c[1][0] == CHAT]


async def test_enforce_posts_a_notice_that_is_queued_for_deletion(app):
    await process(app, msg(ROYAL))
    sends = _group_sends(app.bot)
    assert len(sends) == 1
    text = sends[0][1][1]
    assert "Message removed" in text and "@spammy" in text and "/undo" in text
    queued = await app.store.db.execute_fetchall("SELECT chat_id, message_id, delete_at FROM expiring_messages")
    assert len(queued) == 1 and queued[0][0] == CHAT
    due = datetime.fromisoformat(queued[0][2])
    assert timedelta(minutes=9) < due - datetime.now(timezone.utc) < timedelta(minutes=11)


async def test_burst_does_not_repeat_the_notice(app):
    await process(app, msg(ROYAL))
    await process(app, msg(ROYAL, message_id=43))
    assert len(_group_sends(app.bot)) == 1


async def test_shadow_mode_posts_nothing_in_the_group(app):
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    await process(app, msg(ROYAL))
    assert not _group_sends(app.bot)


async def test_notice_can_be_turned_off_or_kept_forever(app):
    await app.settings.set(CHAT, "group_notice", "off", ADMIN)
    await process(app, msg(ROYAL))
    assert not _group_sends(app.bot)

    await app.settings.set(CHAT, "group_notice", "on", ADMIN)
    await app.settings.set(CHAT, "notice_ttl", "0", ADMIN)
    await app.store.db.execute("UPDATE strikes SET created_at=? WHERE chat_id=?",
                               ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), CHAT))
    await app.store.db.commit()
    await process(app, msg(ROYAL, message_id=50))
    assert len(_group_sends(app.bot)) == 1
    rows = await app.store.db.execute_fetchall("SELECT COUNT(*) FROM expiring_messages")
    assert rows[0][0] == 0


async def test_sweep_deletes_due_notices_only(app):
    now = datetime.now(timezone.utc)
    await app.store.expire_message(CHAT, 501, now - timedelta(seconds=1))
    await app.store.expire_message(CHAT, 502, now + timedelta(minutes=5))
    app.bot.calls.clear()
    assert await app.enforcer.delete_expired() == 1
    deleted = [c for c in app.bot.calls if c[0] == "delete_message"]
    assert len(deleted) == 1 and deleted[0][1][1] == 501
    left = await app.store.db.execute_fetchall("SELECT message_id FROM expiring_messages")
    assert [r[0] for r in left] == [502]


async def test_admin_command_replies_in_a_group_disappear(app, monkeypatch):
    from zyroguard.commands import cmd_warn
    sent = []

    async def fake_reply(self, text, **kw):
        sent.append(text)
        return SimpleNamespace(message_id=9001, chat=self.chat)
    monkeypatch.setattr(Message, "reply", fake_reply)
    m = msg("/warn spam", user_id=ADMIN, message_id=60)
    m = m.model_copy(update={"reply_to_message": msg("buy now", user_id=SPAMMER, message_id=59)})
    await cmd_warn(m, app, SimpleNamespace(args="spam", command="warn"))
    assert sent and "Warned" in sent[-1] or "warn" in sent[-1].lower()
    rows = await app.store.db.execute_fetchall("SELECT message_id FROM expiring_messages")
    assert [r[0] for r in rows] == [9001]


async def test_automatic_actions_never_touch_an_admin(app):
    app.bot.admins[CHAT] = [SPAMMER]           # promoted, but the cache may still say otherwise
    target = Target(CHAT, SPAMMER, "spammy", "Spam", 42, ROYAL, None, "Test group")
    cs = await app.settings.get(CHAT)
    out = await app.enforcer.apply(target, Decision("rule", "spam", "delete", None), cs)
    assert out.action == "none" and "left alone" in (out.note or "")
    assert "delete_message" not in app.bot.names() and "restrict_chat_member" not in app.bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0


async def test_overall_daily_summary_goes_to_the_log_group_once(app):
    from tests.test_pipeline_e2e import MODLOG
    from zyroguard import scheduler
    await process(app, msg(ROYAL))
    app.bot.calls.clear()
    scheduler.OVERALL_TIME = "00:00"
    sent = set()
    await scheduler.send_overall_digest(app, sent)
    await scheduler.send_overall_digest(app, sent)
    await scheduler.send_overall_digest(app, set())      # after a restart: the database remembers
    sends = [c for c in app.bot.calls if c[0] == "send_message" and c[1][0] == MODLOG]
    assert len(sends) == 1
    text = sends[0][1][1]
    assert "All groups" in text and "Test group" in text and "1 action" in text
