"""Robustness fixes: captcha races, lifts, admin cache, migration, validation, long replies, config."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from tests.test_pipeline_e2e import ADMIN, CHAT, msg
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from zyroguard.captcha import fail_captcha, handle_join
from zyroguard.commands import split_message
from zyroguard.config import load_config
from zyroguard.main import on_migrate
from zyroguard.scheduler import _lift_state
from zyroguard.settings import validate


async def test_fail_captcha_without_a_row_does_nothing(app):
    await fail_captcha(app, CHAT, 8080)
    assert "ban_chat_member" not in app.bot.names()


async def test_double_join_starts_one_captcha(app):
    import asyncio
    cs = await app.settings.get(CHAT)
    user = SimpleNamespace(id=8081, full_name="Twice", username="twice", is_bot=False)
    await asyncio.gather(handle_join(app, CHAT, "Test group", user, cs),
                         handle_join(app, CHAT, "Test group", user, cs))
    assert app.bot.names().count("send_message") == 1


async def test_admin_lookup_failure_is_not_cached_for_long(app, monkeypatch):
    real = app.bot.__getattr__
    calls = {"n": 0}

    def broken(name):
        if name == "get_chat_administrators":
            async def fail(*a, **kw):
                calls["n"] += 1
                raise TelegramBadRequest(method=SimpleNamespace(), message="chat not found")
            return fail
        return real(name)
    monkeypatch.setattr(type(app.bot), "__getattr__", lambda self, name: broken(name))
    assert await app.admins.admins(-1005550123) == {}
    assert await app.admins.admins(-1005550123) == {}     # within 30 s: cached empty result
    assert calls["n"] == 1
    entry = app.admins._admins[-1005550123]
    assert entry[0] < __import__("time").monotonic() - app.admins.TTL + 60   # expires soon, not in 5 minutes


async def test_migration_keeps_everything(app):
    new_id = -1009990001
    await app.settings.set(CHAT, "captcha", "off", ADMIN)
    await app.store.add_strike(CHAT, 5, "x", timedelta(days=1), None)
    m = Message.model_validate({
        "message_id": 1, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": CHAT, "type": "group", "title": "Test group"},
        "migrate_to_chat_id": new_id,
    })
    await on_migrate(m, app)
    assert new_id in app.allowed_chats and CHAT not in app.allowed_chats
    assert (await app.settings.get(new_id)).raw("captcha") == "off"
    assert await app.store.active_strikes(new_id, 5) == 1
    assert await app.store.get_chat(CHAT) is None


def test_captcha_fail_must_remove_the_person():
    assert validate("captcha_fail", "kick") == "kick"
    assert validate("captcha_fail", "ban 1d")
    with pytest.raises(ValueError):
        validate("captcha_fail", "mute 1h")
    with pytest.raises(ValueError):
        validate("captcha_fail", "warn")


async def test_lift_only_what_we_placed(app, monkeypatch):
    exp = datetime.now(timezone.utc)

    def member(status, until):
        return SimpleNamespace(status=status, until_date=until)
    real = app.bot.__getattr__
    state = {}
    monkeypatch.setattr(type(app.bot), "__getattr__",
                        lambda self, name: (lambda *a, **kw: _ret(state["m"])) if name == "get_chat_member" else real(name))

    async def _ret(v):
        return v
    state["m"] = member("kicked", exp)
    assert await _lift_state(app, CHAT, 5, "ban", exp) == "ours"
    state["m"] = member("kicked", None)                       # admin made it permanent
    assert await _lift_state(app, CHAT, 5, "ban", exp) == "changed"
    state["m"] = member("left", None)                         # Telegram already ended the ban
    assert await _lift_state(app, CHAT, 5, "ban", exp) == "ended"
    state["m"] = member("kicked", exp + timedelta(days=3))    # a different ban
    assert await _lift_state(app, CHAT, 5, "ban", exp) == "changed"
    state["m"] = member("member", None)                       # Telegram already ended the mute
    assert await _lift_state(app, CHAT, 5, "mute", exp) == "ended"
    state["m"] = member("restricted", exp)
    assert await _lift_state(app, CHAT, 5, "mute", exp) == "ours"
    state["m"] = member("kicked", None)                       # an admin banned them meanwhile
    assert await _lift_state(app, CHAT, 5, "mute", exp) == "changed"


def test_long_replies_are_split_on_lines():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    parts = split_message(text)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert "\n".join(parts).replace("\n", "") == text.replace("\n", "")
    assert split_message("short") == ["short"]


def test_config_errors_are_readable(tmp_path):
    base = {"BOT_TOKEN": "1:a", "DB_PATH": str(tmp_path / "x.db")}
    with pytest.raises(RuntimeError, match="OWNER_IDS"):
        load_config({**base, "OWNER_IDS": "abc"})
    with pytest.raises(RuntimeError, match="JUDGE_EFFORT"):
        load_config({**base, "JUDGE_EFFORT": "ultra"})
    with pytest.raises(RuntimeError, match="HTTP_PORT"):
        load_config({**base, "HTTP_PORT": "80x"})
    assert load_config({**base, "JUDGE_EFFORT": " High "}).judge_effort == "high"


async def test_reloadrules_reports_a_broken_file(app, tmp_path, monkeypatch):
    from zyroguard.owner import cmd_reloadrules
    replies = []

    async def fake_reply(self, text, **kw):
        replies.append(text)
    monkeypatch.setattr(Message, "reply", fake_reply)
    bad = tmp_path / "rules.yaml"
    bad.write_text("hard:\n  - id: x\n    pattern: '('\n", encoding="utf-8")
    object.__setattr__(app.config, "rules_path", bad)
    await cmd_reloadrules(msg("/reloadrules", user_id=ADMIN), app)
    assert replies and "not reloaded" in replies[-1]
