"""Forward captcha, forum-topic reply targeting, whole-word staff names."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram.filters import CommandObject

from zyroguard import forwards
from zyroguard.commands import resolve_target
from zyroguard.pipeline import _handle, real_reply
from zyroguard.rules import impersonation_word, load_rules

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")
NOW = int(datetime.now(timezone.utc).timestamp())
ORIGIN = {"type": "hidden_user", "sender_user_name": "Someone", "date": NOW}


async def _regular(app, uid=SPAMMER):  # noqa: F811
    for _ in range(12):
        await app.store.touch_member(CHAT, uid, "spammy", "Spam", count_message=True)


def _tap(app, data: str, user_id: int = SPAMMER, markup=None):  # noqa: F811
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append(text)

    async def edit_text(*a, **kw):
        return None

    message = SimpleNamespace(reply_markup=markup, chat=SimpleNamespace(title="Test group"), edit_text=edit_text)
    cb = SimpleNamespace(data=data, message=message, answer=answer,
                         from_user=SimpleNamespace(id=user_id, full_name="Spam"))
    return cb, answers


def _prompt_markup(app):  # noqa: F811
    sent = [c for c in app.bot.calls if c[0] == "send_message" and c[2].get("reply_markup")]
    return sent[-1][2]["reply_markup"]


async def test_forward_asks_for_a_check_and_leaves_the_message(app):  # noqa: F811
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    assert "delete_message" not in app.bot.names()
    row = await app.store.forward_get(CHAT, SPAMMER)
    assert row is not None
    prompt = [c for c in app.bot.calls if c[0] == "send_message" and c[2].get("reply_markup")][-1]
    assert "forwarded a message" in prompt[1][1] and "muted for 1 day" in prompt[1][1]
    assert prompt[2]["reply_to_message_id"] == 42


async def test_not_solved_in_time_removes_it_and_mutes_for_a_day(app):  # noqa: F811
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    await _handle(msg("and this", message_id=43, forward_origin=ORIGIN), app, edited=False)   # same check
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await app.store.db.execute("UPDATE pending_forward SET deadline=?", (past,))
    await app.store.db.commit()
    app.bot.calls.clear()
    assert await forwards.timeout_overdue(app) == 1
    deleted = [c[1][1] for c in app.bot.calls if c[0] == "delete_message"]
    assert 42 in deleted and 43 in deleted
    rows = await app.store.recent_audit(CHAT, 1)
    assert rows[0].source == "forward" and rows[0].action == "mute" and rows[0].duration_s == 86400
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1
    assert await app.store.forward_get(CHAT, SPAMMER) is None


async def test_right_answer_keeps_the_forward(app):  # noqa: F811
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    row = await app.store.forward_get(CHAT, SPAMMER)
    markup = _prompt_markup(app)
    data = f"fwc:{CHAT}:{SPAMMER}:{row['answer']}"
    app.bot.calls.clear()
    cb, answers = _tap(app, data, markup=markup)
    await forwards.on_tap(cb, app)
    assert await app.store.forward_get(CHAT, SPAMMER) is None
    assert "restrict_chat_member" not in app.bot.names()
    assert [c[1][1] for c in app.bot.calls if c[0] == "delete_message"] != [42]   # only the prompt goes
    assert answers[-1] == "Thanks, your forward stays."


async def test_three_wrong_taps_fail_the_check(app):  # noqa: F811
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    for _ in range(3):
        row = await app.store.forward_get(CHAT, SPAMMER)
        if row is None:
            break
        markup = _prompt_markup(app)
        wrong = next(b.callback_data for r in markup.inline_keyboard for b in r
                     if not b.callback_data.endswith(":" + row["answer"]))
        cb, _ = _tap(app, wrong, markup=markup)
        await forwards.on_tap(cb, app)
    assert await app.store.forward_get(CHAT, SPAMMER) is None
    assert "restrict_chat_member" in app.bot.names()


async def test_someone_else_cannot_answer(app):  # noqa: F811
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    row = await app.store.forward_get(CHAT, SPAMMER)
    cb, answers = _tap(app, f"fwc:{CHAT}:{SPAMMER}:{row['answer']}", user_id=777, markup=_prompt_markup(app))
    await forwards.on_tap(cb, app)
    assert await app.store.forward_get(CHAT, SPAMMER) is not None


async def test_admins_are_never_asked(app):  # noqa: F811
    await _handle(msg("look at this", user_id=ADMIN, forward_origin=ORIGIN), app, edited=False)
    assert await app.store.forward_get(CHAT, ADMIN) is None


async def test_can_be_switched_off(app):  # noqa: F811
    await app.settings.set(CHAT, "forward_captcha", "off", ADMIN)
    await _regular(app)
    await _handle(msg("look at this", forward_origin=ORIGIN), app, edited=False)
    assert await app.store.forward_get(CHAT, SPAMMER) is None


# Forum topics

TOPIC_HEAD = {"message_id": 500, "date": NOW, "chat": {"id": CHAT, "type": "supergroup", "title": "Test group"},
              "from": {"id": 4242, "is_bot": False, "first_name": "Topic", "username": "topic_opener"},
              "forum_topic_created": {"name": "General chat", "icon_color": 7322096}}


async def test_ban_in_a_topic_targets_the_named_user_not_the_topic_opener(app):  # noqa: F811
    await _regular(app)
    m = msg("/ban @spammy", user_id=ADMIN, is_topic_message=True, message_thread_id=500, reply_to_message=TOPIC_HEAD)
    assert real_reply(m) is None
    target, _ = await resolve_target(m, app, ["@spammy"])
    assert target.user_id == SPAMMER and target.thread_id == 500


async def test_a_real_reply_inside_a_topic_still_counts(app):  # noqa: F811
    replied = {**TOPIC_HEAD, "message_id": 600, "from": {"id": SPAMMER, "is_bot": False, "first_name": "Spam"},
               "text": "spam", "forum_topic_created": None, "is_topic_message": True, "message_thread_id": 500}
    m = msg("/ban", user_id=ADMIN, is_topic_message=True, message_thread_id=500, reply_to_message=replied)
    target, _ = await resolve_target(m, app, [])
    assert target.user_id == SPAMMER and target.message_id == 600


async def test_group_notice_lands_in_the_topic(app):  # noqa: F811
    from zyroguard.pipeline import process
    royal = "We are Royal Dynasty International Co., Ltd. looking for USDT holders, 10% to 25% commission per transaction"
    await process(app, msg(royal, is_topic_message=True, message_thread_id=500, reply_to_message=TOPIC_HEAD))
    notices = [c for c in app.bot.calls if c[0] == "send_message" and c[1][0] == CHAT]
    assert notices and notices[-1][2].get("message_thread_id") == 500


# Staff words in names

def test_staff_words_only_count_as_whole_words():
    w = RULES.impersonation
    for name, user in [("BadmintonFan", None), ("Lakers Supporter", None), ("unofficial fan club", None),
                       ("Adminsky", None)]:
        assert impersonation_word(name, user, w) is None, name
    for name, user in [("JohnAdmin", None), ("Crypto", "crypto_support"), ("Official Team", None),
                       ("SupportTeam", None), ("ᴀᴅᴍɪɴ", None), ("Group Administrator", None),
                       ("Moderators", None), ("Customer Care 24/7", None)]:
        assert impersonation_word(name, user, w), name


def _unused(c: CommandObject) -> None:  # keeps the import meaningful for type checkers
    _ = c
