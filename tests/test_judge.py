import pytest

from zyroguard.judge import Judge, parse_verdict
from zyroguard.store import Store


def test_parse_clean_json():
    r = parse_verdict('{"spam": true, "category": "crypto_scam", "confidence": 0.93, "reason": "usdt pitch"}')
    assert r.spam and r.category == "crypto_scam" and r.confidence == 0.93 and r.error is None


def test_parse_json_with_noise():
    r = parse_verdict('Sure:\n```json\n{"spam": false, "category": "clean", "confidence": 0.2}\n```')
    assert not r.spam and r.error is None


def test_parse_garbage_fails_closed():
    r = parse_verdict("I think this is spam")
    assert not r.spam and r.error


def test_confidence_clamped():
    r = parse_verdict('{"spam": true, "category": "x", "confidence": 7}')
    assert r.confidence == 1.0


@pytest.mark.asyncio
async def test_no_key_is_skipped():
    store = Store(":memory:")
    await store.open()
    j = Judge(store, None, "claude-opus-5", "low")
    r = await j.classify(chat_id=1, text="hi", sender_facts={}, matched_rules=[])
    assert r.skipped == "no_key" and not r.spam
    await store.close()


@pytest.mark.asyncio
async def test_daily_cap(monkeypatch):
    store = Store(":memory:")
    await store.open()
    j = Judge(store, "sk-test", "claude-opus-5", "low")
    for _ in range(3):
        await store.add_judge_usage(1, "claude-opus-5", 10, 5, 0, 0, "x")
    r = await j.classify(chat_id=1, text="hi", sender_facts={}, matched_rules=[], daily_cap=3)
    assert r.skipped == "cap"
    await store.close()


@pytest.mark.asyncio
async def test_api_error_fails_closed():
    store = Store(":memory:")
    await store.open()
    j = Judge(store, "sk-test", "claude-opus-5", "low")

    class Boom:
        async def create(self, **kw):
            raise RuntimeError("network down")

    class FakeClient:
        messages = Boom()

    j.client = FakeClient()
    r = await j.classify(chat_id=1, text="hi", sender_facts={}, matched_rules=[])
    assert r.error and not r.spam
    errs = await store.recent_errors(5)
    assert errs and errs[0]["where_"] == "judge"
    await store.close()


@pytest.mark.asyncio
async def test_success_path_records_usage():
    store = Store(":memory:")
    await store.open()
    j = Judge(store, "sk-test", "claude-opus-5", "low")

    class Block:
        type = "text"
        text = '{"spam": true, "category": "dm_bait", "confidence": 0.9, "reason": "asks to dm"}'

    class Usage:
        input_tokens = 120
        output_tokens = 30
        cache_read_input_tokens = 100
        cache_creation_input_tokens = 0

    class Resp:
        content = [Block()]
        usage = Usage()

    class Msgs:
        async def create(self, **kw):
            assert kw["model"] == "claude-opus-5"
            assert kw["output_config"] == {"effort": "low"}
            assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
            return Resp()

    class FakeClient:
        messages = Msgs()

    j.client = FakeClient()
    r = await j.classify(chat_id=7, text="dm me", sender_facts={"new_member": True}, matched_rules=["contact_pitch"])
    assert r.spam and r.confidence == 0.9 and r.usable
    from datetime import timedelta
    from zyroguard.store import utcnow
    assert await store.judge_calls_since(7, utcnow() - timedelta(hours=1)) == 1
    await store.close()
