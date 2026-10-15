"""Contact the admin: pending flow, delivery to owners, cooldown."""
from datetime import timedelta

from zyroguard import contact
from zyroguard.contact import _is_pending, contact_button, contact_line, deliver
from zyroguard.store import utcnow

from .test_pipeline_e2e import ADMIN, app  # noqa: F401  (fixture reuse)


def _dm(text: str, user_id: int = 4242):
    from datetime import datetime, timezone
    from aiogram.types import Message
    return Message.model_validate({
        "message_id": 7, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": user_id, "type": "private", "first_name": "Lead"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Lead", "username": "leadperson"},
        "text": text,
    })


def test_button_and_line(app):  # noqa: F811
    app.config = app.config.__class__(**{**app.config.__dict__, "contact_email": "raven@publicai.io"})
    kb = contact_button(app)
    assert kb.inline_keyboard[0][0].url == "https://t.me/ZyroGuardBot?start=contact"
    assert "raven@publicai.io" in contact_line(app)


async def test_pending_then_delivered_to_owner(app, monkeypatch):  # noqa: F811
    from aiogram.types import Message
    replies = []

    async def fake_answer(self, text, **kw):
        replies.append(text)
    monkeypatch.setattr(Message, "answer", fake_answer)
    contact._pending.clear(); contact._last_sent.clear()
    assert not _is_pending(_dm("hello"))
    contact._pending[4242] = utcnow()
    assert _is_pending(_dm("hello")) and not _is_pending(_dm("/help"))
    await deliver(_dm("Hi, I run a 3000 member group and want the bot"), app)
    sends = [c for c in app.bot.calls if c[0] == "send_message"]
    assert sends[0][1][0] == ADMIN and "leadperson" in sends[0][1][1] and "3000 member" in sends[0][1][1]
    assert replies and replies[-1].startswith("Sent")
    assert 4242 not in contact._pending
    # cooldown: a second message within 10 minutes is not forwarded again
    contact._pending[4242] = utcnow()
    app.bot.calls.clear()
    await deliver(_dm("again"), app)
    assert not [c for c in app.bot.calls if c[0] == "send_message"]
    assert "already sent" in replies[-1]


def test_pending_expires(app):  # noqa: F811
    contact._pending[4242] = utcnow() - timedelta(hours=1)
    assert not _is_pending(_dm("late"))
