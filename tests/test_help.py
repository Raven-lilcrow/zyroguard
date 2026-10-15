"""/start and /help: one introduction with the four main features, then feature pages (2026-10-13)."""
import re
from datetime import datetime, timezone

from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, Message

from zyroguard import help as h

from .test_pipeline_e2e import ADMIN, app, msg  # noqa: F401

FEATURES = ("Spam and phishing protection", "Group cleaning", "Teaching by example", "Admin tools")


def _private(text: str, uid: int = ADMIN, markup=None) -> Message:
    return Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "Ad"},
        "from": {"id": uid, "is_bot": False, "first_name": "Ad"}, "text": text,
        **({"reply_markup": markup.model_dump()} if markup else {}),
    })


def test_intro_names_the_four_features_and_buttons_open_them():
    for feature in FEATURES:
        assert f"<b>{feature}</b>" in h.OVERVIEW
    keys = [k for row in h.MENU_ROWS for _, k in row]
    assert keys[:4] == ["protect", "cleaning", "teach", "admin"]
    assert all(k in h.TOPICS for k in keys)
    for key, feature in zip(keys, FEATURES):
        assert feature in h.TOPICS[key]


def test_every_button_and_help_reference_leads_somewhere():
    for feature, pages in h.DETAILS.items():
        assert h.help_markup(feature).inline_keyboard[-1][0].callback_data == "h:_"
        for _, key in pages:
            assert key in h.TOPICS
            assert h.help_markup(key).inline_keyboard[-1][0].callback_data == f"h:{feature}"   # Back to its feature
    pages = "\n".join(h.TOPICS.values()) + "\n".join(" ".join(v) for v in h.COMMANDS.values())
    for name in re.findall(r"/help (\w+)", pages):
        assert name in h.TOPICS or name in h.COMMANDS, name


def test_old_topic_names_still_work_for_the_assistant_and_links():
    for key in ("start", "punish", "trust", "rules", "settings", "time", "check", "support", "safety", "private"):
        assert key in h.TOPICS
    assert h.TOPICS["moderation"] == h.TOPICS["admin"]
    from zyroguard.assistant import build_system_prompt
    assert "Getting started" in build_system_prompt()


def test_teaching_page_matches_how_spam_works_now():
    page = h.TOPICS["teach"]
    assert "<code>/spam 412</code>" in page and "Teach as spam" in page
    assert "/set similarity on" in page and "trial" in page


async def test_start_and_help_open_with_the_same_introduction(app, monkeypatch):  # noqa: F811
    sent = []

    async def answer(self, text, **kw):
        sent.append((text, kw.get("reply_markup")))
    monkeypatch.setattr(Message, "answer", answer)
    await h.cmd_start(_private("/start"), app)
    await h.cmd_help(_private("/help"), app, CommandObject(prefix="/", command="help", args=None))
    (start_text, start_kb), (help_text, help_kb) = sent
    assert start_text.startswith(h.OVERVIEW) and help_text == h.OVERVIEW
    assert "Contact the admin" in " ".join(b.text for row in start_kb.inline_keyboard for b in row)
    assert [b.callback_data for b in help_kb.inline_keyboard[0]] == ["h:protect", "h:cleaning"]
    assert start_kb.inline_keyboard[-1][0].callback_data == "h:x"


async def test_start_in_a_group_sends_the_introduction_privately(app, monkeypatch):  # noqa: F811
    replies = []

    async def reply(self, text, **kw):
        replies.append(text)
    monkeypatch.setattr(Message, "reply", reply)
    await h.cmd_start(msg("/start", user_id=ADMIN), app)
    dm = [c for c in app.bot.calls if c[0] == "send_message" and c[1][0] == ADMIN]
    assert dm and dm[-1][1][1] == h.OVERVIEW and replies == ["Sent to your private chat with me."]


async def test_feature_and_detail_buttons_navigate(app, monkeypatch):  # noqa: F811
    edits = []

    async def edit_text(self, text, **kw):
        edits.append((text, kw.get("reply_markup")))

    async def answer(self, *a, **kw):
        pass
    monkeypatch.setattr(Message, "edit_text", edit_text)
    monkeypatch.setattr(CallbackQuery, "answer", answer)

    def press(data: str, markup) -> CallbackQuery:
        return CallbackQuery.model_validate({
            "id": "1", "from": {"id": ADMIN, "is_bot": False, "first_name": "Ad"}, "chat_instance": "c",
            "data": data, "message": _private("x", markup=markup).model_dump()})
    await h.cb_help(press("h:teach", h.help_markup(None)), app)
    assert "Teaching by example" in edits[-1][0]
    await h.cb_help(press("h:safety", h.help_markup("cleaning")), app)
    text, kb = edits[-1]
    assert "Safety check" in text and kb.inline_keyboard[-1][0].callback_data == "h:cleaning"
    await h.cb_help(press("h:_", kb), app)           # not a button on this page: refused, nothing changes
    assert len(edits) == 2
    await h.cb_help(press("h:cleaning", kb), app)    # Back
    text, kb = edits[-1]
    assert "Group cleaning" in text and kb.inline_keyboard[-1][0].callback_data == "h:_"
    await h.cb_help(press("h:_", kb), app)
    assert edits[-1][0] == h.OVERVIEW
