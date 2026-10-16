"""Security fixes: callback replay, regex limits, log-target validation, secret redaction, private-chat limits."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tests.test_pipeline_e2e import ADMIN, CHAT, SPAMMER, msg
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from tests.test_tenants import ANSWERS, added_event, dm
from tests.test_tenants import _stub_answer  # noqa: F401  (autouse fixture)
from zyroguard.callbacks import genuine
from zyroguard.commands import _log_target_problem
from zyroguard.durations import DurationError, parse_duration
from zyroguard.main import on_my_chat_member
from zyroguard.rules import pattern_problem


def _cb(data: str, buttons: list[str]) -> CallbackQuery:
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b, callback_data=b) for b in buttons]])
    m = Message.model_validate({
        "message_id": 7, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": CHAT, "type": "supergroup", "title": "Test group"},
        "text": "captcha", "reply_markup": kb.model_dump(),
    })
    return CallbackQuery.model_validate({
        "id": "1", "from": {"id": SPAMMER, "is_bot": False, "first_name": "x"}, "chat_instance": "c",
        "data": data, "message": m.model_dump(),
    })


def test_callback_must_match_a_button_on_that_message():
    assert genuine(_cb("undo:12", ["undo:12", "why:12"]))
    assert not genuine(_cb("undo:12", ["cap:1:2:ok"]))        # replayed against a captcha prompt
    assert not genuine(_cb("h:start", ["cap:1:2:ok"]))
    assert not genuine(SimpleNamespace(data="x", message=None))


def test_dangerous_patterns_are_refused():
    assert pattern_problem("free crypto") is None
    assert pattern_problem("(?i)dm me for signals") is None
    assert pattern_problem("(a+)+$")
    assert pattern_problem("(a|aa)*b")
    assert pattern_problem(r"(\w+)\s\1")
    assert pattern_problem("x" * 201)
    assert pattern_problem(".*a.*b.*c")
    assert pattern_problem("(unclosed")
    # repeats side by side that match the same characters
    assert pattern_problem(r"\s*\s*\s*\s*\s*x")
    assert pattern_problem(r"[a-z]+[a-z]+[a-z]+[a-z]+\d")
    assert pattern_problem(r"\w*\w*\w*\w*!")
    assert pattern_problem(r"\w+\s*\w+!")         # \s* can match nothing, so the \w+ meet
    assert pattern_problem("(?P<x>a)(?P=x)")
    # the ordinary shapes still pass
    for ok in (r"free\s*crypto", r"\w+\s+\w+", r"https?://\S+", r"(?i)(?:buy|sell)\s+usdt",
               r"\d{3}-\d{4}", r"^\s*join\s*$", "(?i)usdt.*commission"):
        assert pattern_problem(ok) is None, ok


SLOW = r"[a-z]+[a-z]+[a-z]+[a-z]+[a-z]+[a-z]+\d"   # refused when added, but an older saved rule could look like this


def test_a_slow_custom_rule_cannot_hold_up_evaluate():
    import time
    from pathlib import Path
    from zyroguard.rules import CUSTOM_BUDGET_S, Context, MessageView, compile_custom, evaluate, load_rules
    rules = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")
    rows = [{"id": i, "chat_id": CHAT, "pattern": SLOW, "action": "ban", "duration_s": None, "note": None}
            for i in range(1, 11)]
    rows.append({"id": 11, "chat_id": CHAT, "pattern": "hello", "action": "ban", "duration_s": None, "note": None})
    ctx = Context(custom_rules=compile_custom(rows))
    view = MessageView(text="a" * 4000, is_new_member=False)   # Telegram allows 4096
    start = time.monotonic()
    v = evaluate(view, rules, ctx)
    took = time.monotonic() - start
    assert took < CUSTOM_BUDGET_S + 0.5, took                    # without the limit, one such rule took 13.7 s on 100 characters
    assert not v.matched or not v.matched[0].startswith("custom_")
    assert ctx.slow_rules and all(r.regex.pattern == SLOW for r in ctx.slow_rules)

    # a rule already found slow is skipped, so the next look at the same message is quick
    ctx = Context(custom_rules=compile_custom(rows[:1] + rows[-1:]))
    evaluate(view, rules, ctx)
    assert [r.db_id for r in ctx.slow_rules] == [1]
    view.text = "hello " + "a" * 4000
    v = evaluate(view, rules, ctx)
    assert v.kind == "hard" and v.matched[0] == "custom_11"


async def test_a_slow_custom_rule_is_switched_off_and_the_admins_told(app):
    from zyroguard.pipeline import process
    from tests.test_pipeline_e2e import MODLOG
    await app.store.add_rule(CHAT, SLOW, "ban", None, None, ADMIN)      # straight into the DB, past the guard
    await process(app, msg("a" * 3000))
    assert await app.store.list_rules(CHAT) == []
    notes = [c[1][1] for c in app.bot.calls if c[0] == "send_message" and c[1][0] == MODLOG]
    assert any("switched off" in n and "took too long" in n for n in notes), notes
    assert "ban_chat_member" not in app.bot.names()


@pytest.mark.asyncio
async def test_log_target_must_be_controlled_by_the_setter(app):
    other_group = -1005559999
    assert await _log_target_problem(app, SPAMMER, str(SPAMMER)) is None          # their own private chat
    assert await _log_target_problem(app, ADMIN, str(other_group)) is None         # bot owner may pick anything
    assert await _log_target_problem(app, SPAMMER, str(other_group))               # not an admin there
    app.bot.admins[other_group] = [SPAMMER]
    assert await _log_target_problem(app, SPAMMER, str(other_group)) is None
    assert await _log_target_problem(app, SPAMMER, "abc")


@pytest.mark.asyncio
async def test_bot_token_never_lands_in_the_errors_table(app):
    await app.store.log_error("photo", "ClientResponseError url=https://api.telegram.org/file/bot123456789:AAHfakeTOKENvalue_1234567890abc/photos/x.jpg")
    rows = await app.store.recent_errors(1)
    assert "AAHfake" not in rows[0]["message"] and "bot<redacted>" in rows[0]["message"]


@pytest.mark.asyncio
async def test_rights_change_by_another_admin_does_not_make_the_bot_leave(app):
    group = -1005550011
    await on_my_chat_member(added_event(group, ADMIN), app)          # owner added it
    assert group in app.allowed_chats
    app.bot.calls.clear()
    await on_my_chat_member(added_event(group, 4242), app)           # some group admin edits my rights
    assert "leave_chat" not in app.bot.names()
    assert group in app.allowed_chats


@pytest.mark.asyncio
async def test_private_test_command_needs_a_group_you_control(app, monkeypatch):
    from zyroguard.diag import cmd_test
    called = []

    async def fake_classify(**kw):
        called.append(kw)
        raise AssertionError("judge must not be called for a stranger")
    monkeypatch.setattr(app.judge, "classify", fake_classify)
    m = dm("/test free usdt dm me", 999999)
    await cmd_test(m, app, SimpleNamespace(args="free usdt dm me", command="test"))
    assert not called
    assert ANSWERS and "do not manage any group" in ANSWERS[-1][0][0]


@pytest.mark.asyncio
async def test_private_role(app):
    from zyroguard.assistant import _private_role
    assert await _private_role(app, ADMIN) == "staff"
    assert await _private_role(app, 424242) is None
    await app.store.touch_member(CHAT, 424242, "lurker", "Lurker", joined=True)
    assert await _private_role(app, 424242) is None                   # joined but never took part
    await app.store.touch_member(CHAT, 424242, "lurker", "Lurker", count_message=True)
    assert await _private_role(app, 424242) == "member"


def test_duration_cap():
    with pytest.raises(DurationError):
        parse_duration("99999999999w")
    assert parse_duration("5w").seconds == 5 * 7 * 86400


@pytest.mark.asyncio
async def test_edit_reruns_safety_check(app, monkeypatch):
    from zyroguard import pipeline
    seen = []

    async def fake_safety(a, m):
        seen.append(m.message_id)
        return False
    monkeypatch.setattr("zyroguard.safety.check", fake_safety)
    edited = msg("hello", user_id=SPAMMER, message_id=77, edit_date=int(datetime.now(timezone.utc).timestamp()))
    await pipeline.on_edit(edited, app)
    assert seen == [77]


async def test_captcha_off_group_does_not_verify_everywhere(app):
    await app.store.mark_verified(CHAT, 777001, everywhere=False)
    assert not await app.store.is_verified_anywhere(777001)
    await app.store.mark_verified(CHAT, 777002)
    assert await app.store.is_verified_anywhere(777002)


async def test_assistant_group_answers_never_carry_links(app, monkeypatch):
    from zyroguard import assistant as asst
    sent = []

    async def fake_reply(self, text, **kw):
        sent.append(text)
    monkeypatch.setattr(Message, "reply", fake_reply)

    class FakeAssistant:
        configured = True

        def on_cooldown(self, *a):
            return False

        async def ask(self, *a, **kw):
            return "Sure: go to https://example.com/signup or example.com", None
    app.assistant = FakeAssistant()
    await asst.answer(app, msg("@ZyroGuardBot where do I sign up?", user_id=SPAMMER), "where do I sign up?")
    assert sent and "example.com" not in sent[-1] and "[link removed]" in sent[-1]
