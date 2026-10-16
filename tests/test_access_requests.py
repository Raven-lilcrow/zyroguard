"""Access requests: Yes / No / Ban under a contact message, the groups -> time -> note
steps, typed answers, contact blocks, /permit with only the person, and access lengths in months and years."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.types import CallbackQuery, Message

from tests.test_pipeline_e2e import ADMIN
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from tests.test_tenants import dm
from zyroguard import access_requests as ar
from zyroguard import contact
from zyroguard.durations import DurationError
from zyroguard.owner import cmd_permit
from zyroguard.store import from_iso, utcnow

LEAD = 4242
REPLIES: list[str] = []
ALERTS: list[str] = []


async def _quiet(self, text="", *a, **kw):
    REPLIES.append(text)
    return SimpleNamespace(message_id=1)


async def _cb_answer(self, text=None, *a, **kw):
    if text:
        ALERTS.append(text)


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    REPLIES.clear()
    ALERTS.clear()
    ar._drafts.clear()
    contact._pending.clear()
    contact._last_sent.clear()
    monkeypatch.setattr(Message, "answer", _quiet)
    monkeypatch.setattr(Message, "reply", _quiet)
    monkeypatch.setattr(CallbackQuery, "answer", _cb_answer)


def lead_dm(text: str) -> Message:
    return Message.model_validate({
        "message_id": 7, "date": int(utcnow().timestamp()),
        "chat": {"id": LEAD, "type": "private", "first_name": "Lead"},
        "from": {"id": LEAD, "is_bot": False, "first_name": "Lead", "username": "leadperson"},
        "text": text})


def sent(app, chat_id: int) -> list[tuple[str, object]]:
    return [(c[1][1], c[2].get("reply_markup")) for c in app.bot.calls
            if c[0] == "send_message" and c[1] and c[1][0] == chat_id]


def buttons(markup) -> list[str]:
    return [b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row]


async def press(app, data: str, by: int = ADMIN) -> None:
    """Tap a button the owner was actually sent (the newest message that has it)."""
    markup = next((mk for _, mk in reversed(sent(app, ADMIN)) if data in buttons(mk)), None)
    assert markup is not None, f"no button {data}"
    msg = Message.model_validate({
        "message_id": 9, "date": int(utcnow().timestamp()),
        "chat": {"id": by, "type": "private", "first_name": "Ad"},
        "from": {"id": app.bot_id, "is_bot": True, "first_name": "ZyroGuard"},
        "text": "x", "reply_markup": markup.model_dump()})
    cb = CallbackQuery.model_validate({"id": "1", "from": {"id": by, "is_bot": False, "first_name": "Ad"},
                                       "chat_instance": "c", "data": data, "message": msg.model_dump()})
    await ar.cb_access(cb, app)


async def owner_types(app, text: str) -> None:
    m = dm(text, ADMIN)
    assert ar._awaiting(m), "no step was waiting for typed text"
    await ar.typed(m, app)


async def request_from_lead(app, text: str = "Hi, I run a 3000 member group and want the bot") -> None:
    await contact.begin(lead_dm("/start contact"), app)
    await contact.deliver(lead_dm(text), app)


# ---- access length ------------------------------------------------------------------------

def test_access_length_reads_days_weeks_months_years_and_no_end():
    days = {"7d": 7, "4w": 28, "3mo": 90, "3 months": 90, "1 month": 30, "1y": 365, "2 years": 730,
            "1y6mo": 545, "30 days": 30, "2 weeks": 14}
    for raw, n in days.items():
        assert ar.access_length(raw) == timedelta(days=n), raw
    for raw in ("no end", "forever", "never", "0", "No End"):
        assert ar.access_length(raw) is None
    for raw in ("3m", "12h", "abc", "0d", "30", "11y"):   # 3m would be three minutes elsewhere
        with pytest.raises(DurationError):
            ar.access_length(raw)


# ---- the request message ------------------------------------------------------------------------

async def test_contact_message_reaches_the_owner_with_yes_no_ban(app):
    await request_from_lead(app)
    text, markup = sent(app, ADMIN)[-1]
    assert "3000 member" in text and "Give them access?" in text
    assert buttons(markup) == [f"pm:yes:{LEAD}", f"pm:no:{LEAD}", f"pm:ban:{LEAD}"]
    assert REPLIES[-1].startswith("Sent")


async def test_yes_walks_through_groups_time_and_note(app):
    await request_from_lead(app)
    await press(app, f"pm:yes:{LEAD}")
    text, markup = sent(app, ADMIN)[-1]
    assert "How many groups" in text
    assert buttons(markup) == [f"pm:g:{LEAD}:1", f"pm:g:{LEAD}:2", f"pm:g:{LEAD}:3", f"pm:gc:{LEAD}", f"pm:x:{LEAD}"]
    await press(app, f"pm:g:{LEAD}:2")
    assert "For how long" in sent(app, ADMIN)[-1][0]
    await owner_types(app, "3mo")
    assert "Add a note" in sent(app, ADMIN)[-1][0]
    await press(app, f"pm:skip:{LEAD}")
    p = await app.store.get_permit(LEAD)
    assert p and p["max_groups"] == 2 and p["note"] is None
    days = (from_iso(p["expires_at"]) - utcnow()).days
    assert 88 <= days <= 90
    assert "Access given" in sent(app, ADMIN)[-1][0]
    assert any("You can now add ZyroGuard" in t for t, _ in sent(app, LEAD))
    assert ADMIN not in ar._drafts


async def test_custom_groups_no_end_and_a_typed_note(app):
    await request_from_lead(app)
    await press(app, f"pm:yes:{LEAD}")
    await press(app, f"pm:gc:{LEAD}")
    await owner_types(app, "lots")                         # not a number: asked again, nothing saved
    assert "Type a number" in sent(app, ADMIN)[-1][0]
    await owner_types(app, "5")
    await owner_types(app, "3m")                           # minutes are refused for access
    assert "could not read" in sent(app, ADMIN)[-1][0]
    await press(app, f"pm:noend:{LEAD}")
    await owner_types(app, "Agency X, paid yearly")
    p = await app.store.get_permit(LEAD)
    assert p["max_groups"] == 5 and p["expires_at"] is None and p["note"] == "Agency X, paid yearly"


async def test_cancel_changes_nothing(app):
    await request_from_lead(app)
    await press(app, f"pm:yes:{LEAD}")
    await press(app, f"pm:x:{LEAD}")
    assert await app.store.get_permit(LEAD) is None and ADMIN not in ar._drafts
    assert not ar._awaiting(dm("5", ADMIN))


async def test_no_declines_without_telling_them(app):
    await request_from_lead(app)
    before = len(sent(app, LEAD))
    await press(app, f"pm:no:{LEAD}")
    assert await app.store.get_permit(LEAD) is None
    assert len(sent(app, LEAD)) == before
    assert "Declined" in sent(app, ADMIN)[-1][0]
    rows = await app.store.owner_events_since(utcnow() - timedelta(hours=1))
    assert any(r["kind"] == "request_declined" and r["target_id"] == LEAD for r in rows)


async def test_ban_stops_their_messages_until_undo_or_access(app):
    await request_from_lead(app)
    await press(app, f"pm:ban:{LEAD}")
    assert await app.store.contact_blocked(LEAD)
    owner_msgs = len(sent(app, ADMIN))
    await request_from_lead(app, "let me in please")
    assert REPLIES[-1] == contact.BLOCKED and len(sent(app, ADMIN)) == owner_msgs   # nothing reached the owner
    await press(app, f"pm:unban:{LEAD}")
    assert not await app.store.contact_blocked(LEAD)
    # giving access lifts a block too
    await app.store.contact_block(LEAD, ADMIN)
    await cmd_permit(dm(f"/permit {LEAD} 1 30d", ADMIN), app, SimpleNamespace(command="permit", args=f"{LEAD} 1 30d"))
    assert not await app.store.contact_blocked(LEAD)


# ---- /permit ------------------------------------------------------------------------------------

async def test_permit_with_only_the_person_asks_with_buttons(app):
    await cmd_permit(dm(f"/permit {LEAD}", ADMIN), app, SimpleNamespace(command="permit", args=str(LEAD)))
    assert await app.store.get_permit(LEAD) is None
    assert "How many groups" in sent(app, ADMIN)[-1][0]


async def test_one_line_permit_still_works_and_now_reads_months(app):
    await cmd_permit(dm("/permit 7000 2 3mo paid plan", ADMIN), app,
                     SimpleNamespace(command="permit", args="7000 2 3mo paid plan"))
    p = await app.store.get_permit(7000)
    assert p["max_groups"] == 2 and p["note"] == "paid plan" and 88 <= (from_iso(p["expires_at"]) - utcnow()).days <= 90
    await cmd_permit(dm("/permit 7001 1 forever", ADMIN), app, SimpleNamespace(command="permit", args="7001 1 forever"))
    assert (await app.store.get_permit(7001))["expires_at"] is None
    await cmd_permit(dm("/permit 7002 1 3m", ADMIN), app, SimpleNamespace(command="permit", args="7002 1 3m"))
    assert await app.store.get_permit(7002) is None and "could not read" in REPLIES[-1]


async def test_owner_accounts_never_need_access(app):
    await cmd_permit(dm(f"/permit {ADMIN}", ADMIN), app, SimpleNamespace(command="permit", args=str(ADMIN)))
    assert await app.store.get_permit(ADMIN) is None and "owner account" in REPLIES[-1]


async def test_only_owners_can_use_the_buttons(app):
    await request_from_lead(app)
    await press(app, f"pm:yes:{LEAD}", by=LEAD)
    assert LEAD not in ar._drafts and ADMIN not in ar._drafts
    assert await app.store.get_permit(LEAD) is None


async def test_a_button_from_another_request_is_closed(app):
    await request_from_lead(app)
    await press(app, f"pm:yes:{LEAD}")
    await cmd_permit(dm("/permit 7000", ADMIN), app, SimpleNamespace(command="permit", args="7000"))  # a newer one
    await press(app, f"pm:g:{LEAD}:1")
    assert ALERTS and "closed" in ALERTS[-1]
    assert await app.store.get_permit(LEAD) is None


async def test_owner_messages_go_to_the_assistant_when_nothing_is_waiting(app):
    assert not ar._awaiting(dm("how do I see my groups?", ADMIN))
    await cmd_permit(dm(f"/permit {LEAD}", ADMIN), app, SimpleNamespace(command="permit", args=str(LEAD)))
    assert not ar._awaiting(dm("5", ADMIN))                 # the groups step uses buttons
    assert not ar._awaiting(dm("/groups", ADMIN))           # commands always pass through
