"""Meaning match (similar.py) with a stand-in embedder: word overlap instead of the real model,
so the plumbing (library, scoring, shadow, teaching, undo) is tested without downloading anything."""
import hashlib
import re

import numpy as np
import pytest
from aiogram.types import Message

from zyroguard.pipeline import process
from zyroguard.rules import Context, MessageView, evaluate
from zyroguard.similar import CLOSE, GOOD, STRONG, Library, Match

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401
from .test_languages import RULES


class WordEmbedder:
    """Bag of words hashed into 256 buckets. Same words, same vector."""
    model = "test-words"

    def load(self):
        pass

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(256, dtype=np.float32)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
            out.append(v / max(np.linalg.norm(v), 1e-9))
        return np.array(out, dtype=np.float32)


PITCH = "earn passive income daily with our private trading desk, message the manager today"


def test_points_follow_the_thresholds():
    assert Match(spam=STRONG).points == 4
    assert Match(spam=CLOSE).points == 2
    assert Match(spam=CLOSE - 0.01).points == 0
    assert Match(spam=0.75, ham=GOOD).points == -2       # closer to a known-good message
    assert Match(spam=0.95, ham=GOOD).points == 4        # but not when it is closer to spam


def test_a_match_alone_reaches_the_judge_but_never_acts():
    v = evaluate(MessageView(text="hello there friends", is_new_member=False, similar_points=4), RULES, Context())
    assert v.kind == "judge" and "similar_spam" in v.matched
    v = evaluate(MessageView(text="hello there friends", is_new_member=True, similar_points=4), RULES, Context())
    assert v.kind == "judge"


@pytest.fixture
async def lib(app):  # noqa: F811
    library = Library(app.store, WordEmbedder())
    await library.start()
    app.similar = library
    return library


async def test_library_seeds_itself_once(app, lib):  # noqa: F811
    n = len(lib._ids)
    assert n >= 10 and lib.ready
    again = Library(app.store, WordEmbedder())
    await again.start()
    assert len(again._ids) == n      # not seeded twice


async def test_add_match_and_forget(app, lib):  # noqa: F811
    ex_id, new = await lib.add(CHAT, "spam", PITCH, "admin", ADMIN)
    assert new
    assert (await lib.add(CHAT, "spam", PITCH, "admin", ADMIN)) == (ex_id, False)   # not stored twice
    m = await lib.match(CHAT, PITCH)
    assert m.spam_id == ex_id and m.points == 4
    other = await lib.match(CHAT + 1, PITCH)                                           # another group
    assert other.spam_id != ex_id
    assert await lib.forget(ex_id)
    assert (await lib.match(CHAT, PITCH)).spam_id != ex_id


async def test_short_messages_are_skipped(lib):
    assert await lib.match(CHAT, "gm all") is None


async def test_shadow_records_but_does_not_count(app, lib):  # noqa: F811
    await lib.add(CHAT, "spam", PITCH, "admin", ADMIN)
    for _ in range(8):
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)
    await process(app, msg(PITCH))                     # the default similarity is shadow
    assert "delete_message" not in app.bot.names()
    cur = await app.store.db.execute("SELECT applied, kind_with, kind_without FROM similarity_log")
    assert [tuple(r) for r in await cur.fetchall()] == [(0, "judge", "clean")]


async def test_on_sends_a_match_to_the_judge(app, lib, monkeypatch):  # noqa: F811
    from zyroguard.judge import JudgeResult
    seen = {}

    async def classify(**kw):
        seen.update(kw)
        return JudgeResult(True, "promo_spam", 0.93, "trading desk pitch")
    monkeypatch.setattr(app.judge, "classify", classify)
    await app.settings.set(CHAT, "similarity", "on", ADMIN)
    await lib.add(CHAT, "spam", PITCH, "admin", ADMIN)
    for _ in range(8):
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)
    await process(app, msg(PITCH))
    assert "similar_spam" in seen["matched_rules"]
    assert "delete_message" in app.bot.names()


def reply_to(text: str, command: str, user_id: int = ADMIN) -> Message:
    original = msg(text, message_id=70).model_dump()
    return Message.model_validate({**msg(command, user_id=user_id, message_id=71).model_dump(),
                                   "reply_to_message": original})


async def test_spam_command_teaches_and_removes_the_message(app, lib, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from zyroguard.commands import _teach
    from aiogram.filters import CommandObject
    replies = []

    async def quiet_reply(self, text, **kw):   # Message.reply needs a bound bot; record instead
        replies.append(text)
        return SimpleNamespace(message_id=72, chat=self.chat)
    monkeypatch.setattr(Message, "reply", quiet_reply)
    m = reply_to(PITCH, "/spam")
    await _teach(m, app, CommandObject(prefix="/", command="spam", args=None), "spam")
    deleted = [c[1][1] for c in app.bot.calls if c[0] == "delete_message"]
    assert 70 in deleted and 71 in deleted
    rows = await app.store.examples_recent(CHAT, 5)
    assert rows[0]["label"] == "spam" and rows[0]["chat_id"] == CHAT and rows[0]["source"] == "admin"
    assert replies and replies[0].startswith("Learned #")


async def test_undo_turns_the_message_into_a_known_good_example(app, lib):  # noqa: F811
    from zyroguard.commands import _learn_from_undo
    await lib.add(CHAT, "spam", PITCH, "admin", ADMIN)
    await app.settings.set(CHAT, "similarity", "on", ADMIN)
    from zyroguard.judge import JudgeResult

    async def classify(**kw):
        return JudgeResult(True, "promo_spam", 0.93, "pitch")
    app.judge.classify = classify
    await process(app, msg(PITCH))
    row = (await app.store.recent_audit(CHAT, 1))[0]
    result = await app.enforcer.undo(row.id, ADMIN)
    note = await _learn_from_undo(app, row, result, ADMIN)
    assert "normal one" in note
    m = await lib.match(CHAT, PITCH)
    assert m.points == -2 or m.spam_id is None      # the identical spam example was switched off


# /spam that works after I already removed the message

def _quiet_replies(monkeypatch) -> list[str]:
    from types import SimpleNamespace
    replies: list[str] = []

    async def quiet_reply(self, text, **kw):   # Message.reply needs a bound bot; record instead
        replies.append(text)
        return SimpleNamespace(message_id=72, chat=self.chat)
    monkeypatch.setattr(Message, "reply", quiet_reply)
    return replies


def _cmd(name: str, arg: str | None = None):
    from aiogram.filters import CommandObject
    return CommandObject(prefix="/", command=name, args=arg)


async def test_spam_says_whether_examples_count_yet(app, lib, monkeypatch):  # noqa: F811
    from zyroguard.commands import _teach
    replies = _quiet_replies(monkeypatch)
    await _teach(reply_to(PITCH, "/spam"), app, _cmd("spam"), "spam")            # the default similarity is shadow
    assert replies[0].startswith("Learned #") and "still in its trial" in replies[0]
    assert "/set similarity on" in replies[0] and "score higher" not in replies[0]
    await app.settings.set(CHAT, "similarity", "on", ADMIN)
    await _teach(reply_to("join the vip pump group for guaranteed daily profits now", "/spam"), app, _cmd("spam"), "spam")
    assert "now score higher" in replies[1]
    await app.settings.set(CHAT, "similarity", "off", ADMIN)
    await _teach(reply_to("a normal chat message about the weekend meetup plans", "/notspam"), app, _cmd("notspam"), "ham")
    assert "off in this group" in replies[2]


async def test_bare_spam_explains_both_ways(app, lib, monkeypatch):  # noqa: F811
    from zyroguard.commands import _teach
    replies = _quiet_replies(monkeypatch)
    before = await app.store.examples_count(CHAT)
    await _teach(msg("/spam", user_id=ADMIN, message_id=82), app, _cmd("spam"), "spam")
    assert "reply to the message with /spam" in replies[0] and "<code>/spam 412</code>" in replies[0]
    assert "command menu" in replies[0] and await app.store.examples_count(CHAT) == before


async def test_spam_with_an_action_number_learns_what_i_already_removed(app, lib, monkeypatch):  # noqa: F811
    from zyroguard.commands import _learn_from_undo, _teach
    from .test_pipeline_e2e import ROYAL
    replies = _quiet_replies(monkeypatch)
    await process(app, msg(ROYAL))                                  # enforce: removed, sender muted
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.source == "rule" and row.mode == "enforce"
    app.bot.calls.clear()
    await _teach(msg(f"/spam {row.id}", user_id=ADMIN, message_id=80), app, _cmd("spam", str(row.id)), "spam")
    assert replies[0].startswith("Learned #")
    cur = await app.store.db.execute("SELECT id, label, source, audit_id, active, chat_id FROM examples ORDER BY id DESC LIMIT 1")
    ex = await cur.fetchone()
    assert (ex["label"], ex["source"], ex["audit_id"], ex["active"], ex["chat_id"]) == ("spam", "admin", row.id, 1, CHAT)
    assert [c[1][1] for c in app.bot.calls if c[0] == "delete_message"] == [80]   # only my command, the spam was gone
    # a number from another group, or no such action
    await _teach(msg("/spam 99999", user_id=ADMIN, message_id=81), app, _cmd("spam", "99999"), "spam")
    assert "no action with that number" in replies[-1]
    # undoing the action means the message was fine after all: the example stops counting
    result = await app.enforcer.undo(row.id, ADMIN)
    await _learn_from_undo(app, row, result, ADMIN)
    cur = await app.store.db.execute("SELECT active FROM examples WHERE id=?", (ex["id"],))
    assert (await cur.fetchone())[0] == 0


async def test_teaching_a_shadow_catch_takes_the_message_down(app, lib):  # noqa: F811
    from zyroguard.commands import teach_from_action
    from .test_pipeline_e2e import ROYAL
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    await process(app, msg(ROYAL, message_id=90))
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.mode == "shadow"
    app.bot.calls.clear()
    learned, text = await teach_from_action(app, row, "spam", ADMIN)
    assert learned and text.startswith("Learned #")
    assert [c[1][1] for c in app.bot.calls if c[0] == "delete_message"] == [90]


async def test_reports_have_a_teach_button_that_works(app, lib, monkeypatch):  # noqa: F811
    from datetime import datetime, timezone
    from aiogram.types import CallbackQuery
    from zyroguard.commands import cb_teach
    from .test_pipeline_e2e import MODLOG, ROYAL
    await process(app, msg(ROYAL))
    row = (await app.store.recent_audit(CHAT, 1))[0]
    report = [c for c in app.bot.calls if c[0] == "send_message" and c[1][0] == MODLOG][-1]
    kb = report[2]["reply_markup"]
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [f"undo:{row.id}", f"why:{row.id}", f"teach:{row.id}"]
    answers, edits = [], []

    async def answer(self, text=None, **kw):
        answers.append(text)

    async def edit_text(self, text, **kw):
        edits.append((text, kw.get("reply_markup")))
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(Message, "edit_text", edit_text)

    def press(user_id: int) -> CallbackQuery:
        m = Message.model_validate({"message_id": 9, "date": int(datetime.now(timezone.utc).timestamp()),
                                    "chat": {"id": MODLOG, "type": "supergroup", "title": "log"},
                                    "text": "report", "reply_markup": kb.model_dump()})
        return CallbackQuery.model_validate({"id": "1", "from": {"id": user_id, "is_bot": False, "first_name": "Ad"},
                                             "chat_instance": "c", "data": f"teach:{row.id}", "message": m.model_dump()})
    await cb_teach(press(SPAMMER), app)
    assert answers == ["Admins of that group only."] and edits == []
    await cb_teach(press(ADMIN), app)
    assert answers[1].startswith("Learned #")
    text, markup = edits[0]
    assert "Learned #" in text and "(by Ad)" in text
    assert [b.callback_data for b in markup.inline_keyboard[0]] == [f"undo:{row.id}", f"why:{row.id}"]
    assert (await app.store.examples_recent(CHAT, 1))[0]["label"] == "spam"
