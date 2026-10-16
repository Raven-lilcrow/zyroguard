"""Assistant: prompt build, cap, usage recording, cooldown, and the group trigger."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiogram.types import Message

from zyroguard.assistant import Assistant, build_system_prompt, question_text, wants_answer
from zyroguard.store import Store, utcnow


class FakeMessages:
    def __init__(self, text="Plain answer."):
        self.text = text
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=120, output_tokens=30, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )


class FakeClient:
    def __init__(self, text="Plain answer."):
        self.messages = FakeMessages(text)


def test_system_prompt_contains_guide_without_html():
    p = build_system_prompt()
    assert "can't check that from here" in p
    # when it can't answer, it asks what they mean and never guesses (for example install commands for an app it does not know)
    assert "never guess" in p and "ask one short question" in p and "in case you meant" in p
    assert "give what general knowledge you can" not in p
    assert "/setstrike 1 mute 24h" in p
    assert "<b>" not in p and "&lt;" not in p


async def test_ask_records_usage_and_respects_cap():
    store = Store(":memory:")
    await store.open()
    client = FakeClient()
    a = Assistant(store, client, "claude-opus-5", "medium")
    text, problem = await a.ask(-1, "how do I mute someone?", asker="@ana", daily_cap=1)
    assert text == "Plain answer." and problem is None
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5" and call["output_config"] == {"effort": "medium"}
    assert "Question from @ana" in call["messages"][0]["content"]
    assert await store.assistant_calls_since(-1, utcnow() - timedelta(days=1)) == 1
    assert await store.judge_calls_since(-1, utcnow() - timedelta(days=1)) == 0  # not counted as judge
    text, problem = await a.ask(-1, "again?", asker="@ana", daily_cap=1)
    assert text is None and problem == "cap"
    await store.close()


async def test_ask_without_client_and_cooldown():
    store = Store(":memory:")
    await store.open()
    a = Assistant(store, None, "m", "low")
    assert await a.ask(-1, "hi", asker="x") == (None, "no_key")
    assert not a.on_cooldown(-1, 5)
    assert a.on_cooldown(-1, 5)
    await store.close()


def _msg(text, reply_from=None, bot_id=999):
    data = {
        "message_id": 1, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": -100, "type": "supergroup", "title": "T"},
        "from": {"id": 5, "is_bot": False, "first_name": "U"},
        "text": text,
    }
    if reply_from is not None:
        data["reply_to_message"] = {
            "message_id": 0, "date": data["date"], "chat": data["chat"],
            "from": {"id": reply_from, "is_bot": reply_from == bot_id, "first_name": "B"}, "text": "earlier"}
    return Message.model_validate(data)


def test_group_trigger_and_question_text():
    app = SimpleNamespace(bot_username="ZyroGuardBot", bot_id=999)
    assert wants_answer(app, _msg("@ZyroGuardBot what is a captcha?"))
    assert wants_answer(app, _msg("why?", reply_from=999))
    assert not wants_answer(app, _msg("why?", reply_from=42))
    assert not wants_answer(app, _msg("hello everyone"))
    assert not wants_answer(app, _msg("/ask @ZyroGuardBot"))
    assert question_text(app, _msg("@zyroguardbot  what is a captcha?")) == "what is a captcha?"


def test_to_html_codes_commands_and_escapes():
    from zyroguard.assistant import to_html
    out = to_html("Use `/mute @user 1h` — done. 1 < 2 & `a<b`")
    assert out == "Use <code>/mute @user 1h</code>: done. 1 &lt; 2 &amp; <code>a&lt;b</code>"


def test_help_topics_wrap_commands_in_code():
    from zyroguard.help import TOPICS
    assert "<code>/rule add free crypto giveaway</code>" in TOPICS["rules"]
    assert "<code>/kick</code> – remove them." in TOPICS["punish"]


async def test_history_is_used_and_cleared():
    store = Store(":memory:")
    await store.open()
    client = FakeClient()
    a = Assistant(store, client, "m", "low")
    await a.ask(-1, "first?", asker="@u", user_id=5)
    await a.ask(-1, "second?", asker="@u", user_id=5)
    msgs = client.messages.calls[1]["messages"]
    assert [x["role"] for x in msgs] == ["user", "assistant", "user"]
    assert "first?" in msgs[0]["content"] and msgs[1]["content"] == "Plain answer."
    # other people and other chats do not share memory
    await a.ask(-1, "hello", asker="@v", user_id=6)
    assert len(client.messages.calls[2]["messages"]) == 1
    assert a.clear(-1, 5) == 1
    await a.ask(-1, "third?", asker="@u", user_id=5)
    assert len(client.messages.calls[3]["messages"]) == 1
    assert a.clear(-1) == 2  # u again, and v
    assert a.history(-1, 5) == []
    await store.close()


async def test_history_expires_and_is_capped():
    from zyroguard.assistant import HISTORY_TURNS
    store = Store(":memory:")
    await store.open()
    a = Assistant(store, FakeClient(), "m", "low")
    for i in range(HISTORY_TURNS + 3):
        await a.ask(-1, f"q{i}", asker="@u", user_id=5)
    assert len(a.history(-1, 5)) == 2 * HISTORY_TURNS
    a._history[(-1, 5)]["ts"] = utcnow() - timedelta(hours=1)
    assert a.history(-1, 5) == []
    await store.close()
