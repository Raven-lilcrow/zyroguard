from datetime import timedelta

import pytest

from zyroguard.settings import SettingsService, validate
from zyroguard.store import Store, utcnow


@pytest.mark.asyncio
async def test_settings_defaults_and_override():
    store = Store(":memory:")
    await store.open()
    svc = SettingsService(store, "shadow")
    cs = await svc.get(-100)
    assert cs.raw("mode") == "shadow" and not cs.enforce
    assert cs.action("strike1") == ("mute", cs.action("strike1")[1])
    assert cs.seconds("strike_ttl") == 30 * 86400
    await svc.set(-100, "mode", "enforce", 1)
    await svc.set(-100, "strike1", "tmute 2h", 1)
    cs = await svc.get(-100)
    assert cs.enforce and cs.raw("strike1") == "mute 2h"
    svc.invalidate(-100)
    cs = await svc.get(-100)
    assert cs.raw("strike1") == "mute 2h"
    other = await svc.get(-200)
    assert other.raw("mode") == "shadow"
    await store.close()


def test_validate():
    assert validate("captcha", "yes") == "on"
    assert validate("judge_threshold", "0.9") == "0.9"
    assert validate("captcha_time", "90s") == "1m30s"
    assert validate("captcha_fail", "ban 1d") == "ban 1d"
    assert validate("link_policy", "ALL") == "all"
    assert validate("digest_time", "9:5") == "09:05"
    for key, bad in [("captcha", "maybe"), ("flood_count", "-1"), ("judge_threshold", "2"),
                     ("captcha_time", "soon"), ("captcha_fail", "explode"), ("link_policy", "some"),
                     ("digest_time", "25:00"), ("nope", "1")]:
        with pytest.raises(ValueError):
            validate(key, bad)


@pytest.mark.asyncio
async def test_strikes_and_expiry():
    store = Store(":memory:")
    await store.open()
    assert await store.add_strike(1, 5, "a", timedelta(days=30), None) == 1
    assert await store.add_strike(1, 5, "b", timedelta(seconds=-1), None) == 1  # already expired
    assert await store.active_strikes(1, 5) == 1
    assert await store.purge_expired_strikes() == 1
    assert await store.clear_strikes(1, 5) == 1
    await store.close()


@pytest.mark.asyncio
async def test_audit_and_timed_actions():
    store = Store(":memory:")
    await store.open()
    aid = await store.add_audit(chat_id=1, user_id=5, username="u", message_id=9, text_hash="h",
                                text_excerpt="t", source="rule", reason="r", detail={"x": 1},
                                action="ban", duration_s=60, expires_at=utcnow() - timedelta(seconds=1),
                                mode="enforce")
    row = await store.get_audit(aid)
    assert row and row.detail == {"x": 1} and row.action == "ban"
    await store.schedule_lift(1, 5, "ban", utcnow() - timedelta(seconds=1), aid)
    due = await store.due_lifts()
    assert len(due) == 1
    await store.mark_lift_done(int(due[0]["id"]))
    assert await store.due_lifts() == []
    stats = await store.stats(1, utcnow() - timedelta(hours=1))
    assert stats["total"] == 1 and stats["by_action"] == {"ban": 1}
    await store.close()


@pytest.mark.asyncio
async def test_domains_and_rules_scope():
    store = Store(":memory:")
    await store.open()
    await store.set_domain(0, "global.com", "block", None)
    await store.set_domain(7, "mine.com", "allow", None)
    d = await store.domains(7)
    assert d == {"allow": {"mine.com"}, "block": {"global.com"}}
    await store.set_domain(7, "mine.com", "block", None)  # flips allow to block
    d = await store.domains(7)
    assert d["allow"] == set() and "mine.com" in d["block"]
    rid = await store.add_rule(0, "x", "ban", None, None, None)
    assert [r["id"] for r in await store.list_rules(7)] == [rid]
    assert not await store.delete_rule(7, rid)  # wrong scope
    assert await store.delete_rule(0, rid)
    await store.close()
