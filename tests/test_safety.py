"""Safety check: triggers, parsing, and the end-to-end removal through the enforcer."""
import pytest

from zyroguard.judge import JudgeResult, parse_safety
from zyroguard.safety import check, has_visual, text_triggers

from .test_pipeline_e2e import ADMIN, CHAT, app, msg  # noqa: F401  (fixture reuse)


def test_parse_safety():
    r = parse_safety('{"unsafe": true, "category": "hate", "confidence": 0.93, "reason": "slur"}', False)
    assert r.spam and r.category == "hate" and r.confidence == 0.93
    clean = parse_safety('{"unsafe": false, "category": "clean", "confidence": 0.2, "reason": "ok"}', True)
    assert not clean.spam
    # only a real refusal about an image is unsafe; prose or a cut-off reply is an error, nobody is punished
    assert parse_safety("", True, stop_reason="refusal").spam
    assert parse_safety("I can't help with that.", True).error
    assert parse_safety("I can't help with that.", False).error
    assert parse_safety('{"unsafe": tr', True, stop_reason="max_tokens").error


def test_triggers(app):  # noqa: F811
    assert text_triggers(app, "just kys already")
    assert text_triggers(app, "You are a worthless whore")
    assert not text_triggers(app, "what time is the meetup?")
    assert not has_visual(msg("hello"))
    assert has_visual(msg("", photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]))


async def test_text_hit_is_removed_and_muted(app, monkeypatch):  # noqa: F811
    async def fake_classify(judge, **kw):
        assert kw["image_bytes"] is None and "kys" in kw["text"]
        return JudgeResult(True, "harassment", 0.95, "tells someone to kill themselves")
    monkeypatch.setattr("zyroguard.safety.classify_safety", fake_classify)
    removed = await check(app, msg("kys loser"))
    assert removed
    names = app.bot.names()
    assert "delete_message" in names and "restrict_chat_member" in names  # deleted, and a 1-day mute
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.source == "safety" and row.action == "mute" and row.duration_s == 86400
    assert row.detail["safety"]["category"] == "harassment"


async def test_clean_text_and_admins_are_skipped(app, monkeypatch):  # noqa: F811
    called = []

    async def fake_classify(judge, **kw):
        called.append(1)
        return JudgeResult(True, "hate", 0.99, "x")
    monkeypatch.setattr("zyroguard.safety.classify_safety", fake_classify)
    assert not await check(app, msg("what time is the meetup?"))      # no trigger, no call
    assert not await check(app, msg("kys", user_id=ADMIN))             # admin, no call
    await app.settings.set(CHAT, "safety", "off", ADMIN)
    assert not await check(app, msg("kys"))                             # switched off
    assert called == []


async def test_low_confidence_does_nothing(app, monkeypatch):  # noqa: F811
    async def fake_classify(judge, **kw):
        return JudgeResult(True, "harassment", 0.5, "maybe")
    monkeypatch.setattr("zyroguard.safety.classify_safety", fake_classify)
    assert not await check(app, msg("kys"))
    assert "delete_message" not in app.bot.names()


async def test_photo_goes_to_checker(app, monkeypatch):  # noqa: F811
    seen = {}

    async def fake_media(a, m):
        return b"jpegbytes", "image/jpeg"

    async def fake_classify(judge, **kw):
        seen.update(kw)
        return JudgeResult(True, "explicit", 0.97, "nudity")
    monkeypatch.setattr("zyroguard.safety.media_bytes", fake_media)
    monkeypatch.setattr("zyroguard.safety.classify_safety", fake_classify)
    m = msg("", photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}])
    assert await check(app, m)
    assert seen["image_bytes"] == b"jpegbytes"
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.text_excerpt == "[image]" and row.detail["safety"]["image"] is True


@pytest.mark.parametrize("text", ["kys", "I will find you and hurt you"])
def test_more_triggers(app, text):  # noqa: F811
    assert text_triggers(app, text)


def test_settings_defaults(app):  # noqa: F811
    from zyroguard.settings import SPECS
    assert SPECS["safety"].default == "on" and SPECS["safety_action"].default == "mute 1d"


async def test_scam_on_image_uses_spam_ladder(app, monkeypatch):  # noqa: F811
    async def fake_media(a, m):
        return b"jpeg", "image/jpeg"

    async def fake_classify(judge, **kw):
        return JudgeResult(True, "scam", 0.96, "USDT commission pitch written on the image")
    monkeypatch.setattr("zyroguard.safety.media_bytes", fake_media)
    monkeypatch.setattr("zyroguard.safety.classify_safety", fake_classify)
    m = msg("", photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}])
    assert await check(app, m)
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.source == "judge" and row.action == "mute" and row.duration_s == 86400  # strike 1 is a 24h mute
    assert await app.store.active_strikes(CHAT, 555) == 1
