"""The owner's activity record (ledger.py): what gets recorded, owner alerts, the sheet layout,
sending with retries, daily summaries, the month-end snapshot and access that runs out."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiogram.types import ChatMemberUpdated, Message

from tests.test_pipeline_e2e import ADMIN, BOT, CHAT, msg
from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from tests.test_tenants import CUSTOMER, STRANGER, THEIR_GROUP, added_event, dm
from zyroguard import ledger
from zyroguard.commands import cmd_pause, cmd_resume
from zyroguard.main import on_my_chat_member
from zyroguard.owner import cmd_history, cmd_ledger, cmd_permit, cmd_revoke
from zyroguard.scheduler import drop_ended_permits
from zyroguard.store import iso, utcnow

ANSWERS: list = []


async def _quiet_answer(self, *a, **kw):
    ANSWERS.append((a, kw))
    return SimpleNamespace(message_id=1)


@pytest.fixture(autouse=True)
def _setup(monkeypatch, app):
    ANSWERS.clear()
    monkeypatch.setattr(Message, "answer", _quiet_answer)
    monkeypatch.setattr(Message, "reply", _quiet_answer)
    app.bot.admins[THEIR_GROUP] = [CUSTOMER]
    ledger.STATUS.tabs.clear()
    ledger.STATUS.names.clear()


async def events(app, kind: str | None = None) -> list:
    rows = await app.store.owner_events_since(utcnow() - timedelta(days=400), limit=500)
    return [r for r in rows if kind is None or r["kind"] == kind]


def alerts_to_owner(app) -> list[str]:
    return [c[1][1] for c in app.bot.calls if c[0] == "send_message" and c[1] and c[1][0] == ADMIN]


def bot_member(status: str, **rights) -> dict:
    user = {"id": BOT, "is_bot": True, "first_name": "ZyroGuard", "username": "ZyroGuardBot"}
    m = {"status": status, "user": user}
    if status == "administrator":
        m.update({"can_be_edited": False, "is_anonymous": False, "can_manage_chat": True,
                  "can_delete_messages": True, "can_manage_video_chats": True, "can_restrict_members": True,
                  "can_promote_members": False, "can_change_info": False, "can_invite_users": True,
                  "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False,
                  "can_send_welcome_messages": False})
        m.update(rights)
    return m


def member_update(chat_id: int, by: int, old: dict, new: dict) -> ChatMemberUpdated:
    return ChatMemberUpdated.model_validate({
        "chat": {"id": chat_id, "type": "supergroup", "title": "Test group"},
        "from": {"id": by, "is_bot": by == BOT, "first_name": "Ann", "username": "ann"},
        "date": int(datetime.now(timezone.utc).timestamp()),
        "old_chat_member": old, "new_chat_member": new,
    })


def command(text: str, user_id: int = ADMIN) -> tuple[Message, SimpleNamespace]:
    m = dm(text, user_id)
    parts = text.split(maxsplit=1)
    return m, SimpleNamespace(command=parts[0].lstrip("/"), args=parts[1] if len(parts) > 1 else None)


# What gets recorded

async def test_setting_change_is_recorded_with_old_and_new_value(app):
    await app.settings.set(CHAT, "flood_count", "8", ADMIN)
    await app.settings.set(CHAT, "flood_count", "8", ADMIN)          # no change, no second row
    await app.settings.reset(CHAT, "flood_count", ADMIN)
    rows = await events(app)
    assert [r["kind"] for r in rows] == ["setting_reset", "setting"]
    changed = json.loads(rows[1]["detail"])
    assert changed == {"key": "flood_count", "old": None, "new": "8"} and rows[1]["actor_id"] == ADMIN
    row = await ledger.event_row(app, rows[1])
    assert row[2] == "Admin change" and row[3] == "Setting changed" and "(flood_count)" in row[10]
    assert row[11].startswith("default") and row[12] == "8" and row[7] == str(ADMIN)


async def test_support_settings_count_as_zyromod(app):
    await app.settings.set(CHAT, "support_email", "help@acme.io", ADMIN)
    row = await ledger.event_row(app, (await events(app, "setting"))[0])
    assert row[2] == "ZyroMod" and row[12] == "help@acme.io"


async def test_admin_actions_are_recorded_but_bot_actions_are_not(app):
    common = dict(chat_id=CHAT, user_id=555, username="spammy", message_id=1, text_hash=None,
                  text_excerpt="buy cheap followers now", detail=None, duration_s=86400, expires_at=None,
                  mode="enforce")
    await app.store.add_audit(source="rule", reason="crypto pitch", action="mute", **common)
    ref = await app.store.add_audit(source="admin", reason="spamming links", action="ban", actor_id=ADMIN, **common)
    await app.store.mark_undone(ref, ADMIN)
    rows = await events(app)
    assert [r["kind"] for r in rows] == ["undo", "admin_action"]
    action = await ledger.event_row(app, rows[1])
    assert action[2:4] == ["Admin action", "Ban 1d"] and action[9] == "555" and action[14] == f"#{ref}"
    assert action[13] == "Enforce" and action[10] == "spamming links"
    assert all("cheap followers" not in (r["detail"] or "") for r in rows)   # members' messages never copied


async def test_lists_rules_domains_and_files_are_recorded(app):
    await app.store.allow_user(CHAT, 42, ADMIN)
    await app.store.allow_user(CHAT, 42, ADMIN)                       # already trusted: nothing new
    await app.store.disallow_user(CHAT, 42, ADMIN)
    rid = await app.store.add_rule(CHAT, "free crypto", "ban", 604800, None, ADMIN)
    await app.store.delete_rule(CHAT, rid, ADMIN)
    await app.store.set_domain(CHAT, "spam.example", "block", ADMIN)
    doc, _ = await app.store.kb_add_document(CHAT, "faq.md", "md", 10, 100, None, "x", ADMIN,
                                             [(0, None, "hello", b"\0" * 4)], "m")
    await app.store.kb_delete_document(CHAT, doc, ADMIN)
    kinds = [r["kind"] for r in reversed(await events(app))]
    assert kinds == ["trusted_add", "trusted_remove", "rule_add", "rule_delete", "domain_block", "file_add", "file_remove"]
    rule = await ledger.event_row(app, (await events(app, "rule_add"))[0])
    assert "free crypto" in rule[10] and "ban 7d" in rule[10]
    file_row = await ledger.event_row(app, (await events(app, "file_add"))[0])
    assert file_row[2] == "ZyroMod" and file_row[10].startswith("faq.md: 1 sections")


async def test_pause_and_resume_are_recorded(app):
    m = msg("/pause 30m", user_id=ADMIN)
    await cmd_pause(m, app, SimpleNamespace(command="pause", args="30m"))
    await cmd_resume(msg("/resume", user_id=ADMIN), app)
    await cmd_resume(msg("/resume", user_id=ADMIN), app)               # not paused: nothing to record
    assert [r["kind"] for r in await events(app)] == ["resume", "pause"]


# The bot joining and leaving groups

async def test_refused_add_is_recorded_and_the_owner_is_told(app):
    await on_my_chat_member(added_event(THEIR_GROUP, STRANGER), app)
    [row] = await events(app, "bot_refused")
    assert row["actor_id"] == STRANGER and json.loads(row["detail"])["reason"] == "No access"
    [alert] = [a for a in alerts_to_owner(app) if "without access" in a]
    assert str(THEIR_GROUP) in alert and f"/permit {STRANGER}" in alert
    # the bot's own "left" update after leaving is not a removal
    await on_my_chat_member(member_update(THEIR_GROUP, BOT, bot_member("member"), bot_member("left")), app)
    assert not await events(app, "bot_removed")


async def test_add_with_access_records_who_added_it(app):
    await app.store.add_permit(CUSTOMER, "cust", 2, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    [row] = await events(app, "bot_added")
    d = json.loads(row["detail"])
    assert d["how"] == "User access (1 of 2 groups)" and d["rights_ok"] is True
    chat = await app.store.get_chat(THEIR_GROUP)
    assert chat["added_by"] == CUSTOMER and chat["added_by_name"] == "Cust"
    assert any("added to a new group" in a for a in alerts_to_owner(app))


async def test_limit_reached_and_not_admin_are_explained(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, None, None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    app.bot.admins[THEIR_GROUP - 1] = [CUSTOMER]
    await on_my_chat_member(added_event(THEIR_GROUP - 1, CUSTOMER), app)
    await on_my_chat_member(added_event(-1005550999, CUSTOMER), app)     # not their group either
    reasons = [json.loads(r["detail"])["reason"] for r in await events(app, "bot_refused")]
    assert "Group limit reached (1 of 1)" in reasons


async def test_removal_and_lost_rights_are_recorded(app):
    await on_my_chat_member(member_update(CHAT, ADMIN, bot_member("administrator"),
                                          bot_member("administrator", can_restrict_members=False)), app)
    [rights] = await events(app, "bot_rights")
    assert json.loads(rights["detail"])["missing"] == ["Ban users"]
    assert any("lost admin rights" in a and "Ban users" in a for a in alerts_to_owner(app))
    await on_my_chat_member(member_update(CHAT, ADMIN, bot_member("administrator"), bot_member("left")), app)
    [removed] = await events(app, "bot_removed")
    assert removed["actor_id"] == ADMIN


# Access that ends

async def test_ended_access_stops_service_at_once_and_is_recorded_once(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, utcnow() + timedelta(hours=1), None, ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    assert app.chat_allowed(THEIR_GROUP)
    app.access_ends[THEIR_GROUP] = utcnow() - timedelta(seconds=1)       # the hour has passed
    assert not app.chat_allowed(THEIR_GROUP)                              # no wait for the 5-minute check
    await app.store.db.execute("UPDATE permits SET expires_at=? WHERE user_id=?",
                               (iso(utcnow() - timedelta(seconds=1)), CUSTOMER))
    await app.store.db.commit()
    assert await drop_ended_permits(app) == 1
    await drop_ended_permits(app)
    [expired] = await events(app, "access_expired")
    assert json.loads(expired["detail"])["groups"] == ["Customer group"]
    assert len(await events(app, "bot_left")) == 1
    assert sum("Access ended" in a for a in alerts_to_owner(app)) == 1


async def test_permits_that_ended_before_the_record_started_are_not_reported(app):
    await app.store.add_permit(CUSTOMER, "cust", 1, utcnow() - timedelta(days=20), "test", ADMIN)
    await ledger.start(app)
    await drop_ended_permits(app)
    assert not await events(app, "access_expired")


async def test_unknown_groups_are_never_served(app):
    assert not app.chat_allowed(-100777)          # even with ALLOWED_CHAT_IDS set or empty
    object.__setattr__(app.config, "allowed_chat_ids", [])
    assert app.config.multi_group and not app.chat_allowed(-100777) and app.chat_allowed(CHAT)


async def test_permit_and_revoke_commands_are_recorded(app):
    m, c = command(f"/permit {CUSTOMER} 2 30d paid")
    await cmd_permit(m, app, c)
    m, c = command(f"/permit {CUSTOMER} 3 30d paid")
    await cmd_permit(m, app, c)
    m, c = command(f"/revoke {CUSTOMER}")
    await cmd_revoke(m, app, c)
    kinds = [r["kind"] for r in reversed(await events(app))]
    assert kinds == ["access_given", "access_changed", "access_revoked"]
    row = await ledger.event_row(app, (await events(app, "access_changed"))[0])
    assert row[11].startswith("2 group(s)") and row[12].startswith("3 group(s)") and "paid" in row[10]


# Sending to the sheet

async def test_events_go_to_the_month_tab_in_manila_time_and_are_sent_once(app, monkeypatch):
    sent: list = []

    async def fake_post(_app, ops, what):
        sent.append(ops)
        return True
    monkeypatch.setattr(ledger, "post", fake_post)
    object.__setattr__(app.config, "owner_ledger_url", "http://n8n/webhook")
    late = datetime(2026, 10, 31, 16, 30, tzinfo=timezone.utc)            # Nov 1, 00:30 in Manila
    await app.store.owner_event_add("pause", CHAT, ADMIN, ts=late, duration="1 hour")
    await app.store.owner_event_add("resume", CHAT, ADMIN, ts=late - timedelta(hours=1))
    assert await ledger.flush(app) == 2
    ops = sent[0]
    tabs = [o for o in ops if o["path"] == ":batchUpdate"]
    titles = [o["body"]["requests"][0]["addSheet"]["properties"]["title"] for o in tabs]
    assert sorted(titles) == ["Nov 2026", "Oct 2026"]
    nov = next(o for o in tabs if "Nov" in json.dumps(o))
    assert nov["ok_if"] == "already exists"
    assert nov["body"]["requests"][0]["addSheet"]["properties"]["sheetId"] == 202611
    appends = [o for o in ops if ":append" in o["path"]]
    assert any("%27Nov%202026%27" in o["path"] and o["body"]["values"][0][:4] == ["2026-11-01", "00:30", "Admin change", "Filtering paused"]
               for o in appends)
    assert await ledger.flush(app) == 0                                   # nothing left to send


async def test_failed_send_keeps_events_for_the_next_try(app, monkeypatch):
    async def down(_app, ops, what):
        return False
    monkeypatch.setattr(ledger, "post", down)
    object.__setattr__(app.config, "owner_ledger_url", "http://n8n/webhook")
    await app.store.owner_event_add("resume", CHAT, ADMIN)
    assert await ledger.flush(app) == 0
    [row] = await app.store.owner_events_unsent()
    assert row["attempts"] == 1


async def test_post_sends_the_secret_and_needs_an_ok_answer(app):
    seen: dict = {}

    async def hook(request):
        seen["secret"] = request.headers.get("X-ZyroGuard-Ledger-Secret")
        seen["body"] = await request.json()
        return web.json_response({"ok": seen["secret"] == "s3cret"})
    webapp = web.Application()
    webapp.router.add_post("/hook", hook)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        object.__setattr__(app.config, "owner_ledger_url", f"http://127.0.0.1:{port}/hook")
        object.__setattr__(app.config, "owner_ledger_secret", "s3cret")
        assert await ledger.post(app, [ledger.append_op("Oct 2026", [["x"]])], "test")
        assert seen["body"]["ops"][0]["body"]["values"] == [["x"]]
        object.__setattr__(app.config, "owner_ledger_secret", "wrong")
        assert not await ledger.post(app, [ledger.append_op("Oct 2026", [["x"]])], "test")
        assert "webhook answered 200" in (ledger.STATUS.last_error or "")
    finally:
        await ledger.close()
        await runner.cleanup()


# Groups, Access, daily summary, month end

async def test_groups_and_access_rows(app):
    await app.store.add_permit(CUSTOMER, "cust", 2, utcnow() + timedelta(days=10), "paid", ADMIN)
    await on_my_chat_member(added_event(THEIR_GROUP, CUSTOMER), app)
    groups = await ledger.groups_rows(app)
    assert groups[0] == ledger.GROUP_COLS
    by_id = {g[1]: dict(zip(ledger.GROUP_COLS, g)) for g in groups[1:]}
    assert by_id[str(CHAT)]["Allowed by"] == "Allowlist" and by_id[str(CHAT)]["Bot working"] == "OK"
    theirs = by_id[str(THEIR_GROUP)]
    assert theirs["Allowed by"] == "User access" and theirs["Added by ID"] == str(CUSTOMER)
    assert theirs["Days left"] in {"10", "11"} and theirs["Mode"] in {"Report-only", "Enforce"}
    access = await ledger.access_rows(app)
    row = dict(zip(ledger.ACCESS_COLS, access[1]))
    assert row["Status"] == "Active" and row["Groups used"] == "1 of 2" and row["Their groups"] == "Customer group"


async def test_daily_summary_counts_yesterday_once(app):
    now = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)               # 09:00 Manila on Oct 16
    in_day = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)             # 11:00 Manila on Oct 15
    for action, source in (("ban", "judge"), ("kick", "captcha"), ("kick", "captcha")):
        audit_id = await app.store.add_audit(chat_id=CHAT, user_id=9, username=None, message_id=None, text_hash=None,
                                             text_excerpt=None, source=source, reason="r", detail=None, action=action,
                                             duration_s=None, expires_at=None, mode="enforce")
        await app.store.db.execute("UPDATE audit SET ts=? WHERE id=?", (iso(in_day), audit_id))
    await app.store.db.commit()
    assert await ledger.daily_summaries(app, now) == 1
    assert await ledger.daily_summaries(app, now) == 0
    [row] = await events(app, "daily_summary")
    text = json.loads(row["detail"])["text"]
    assert "2 kicked" in text and "1 banned" in text and "captcha 2" in text and "AI judge 1" in text
    sheet_row = await ledger.event_row(app, row)
    assert sheet_row[:4] == ["2026-10-15", "23:59", "Bot", "Daily summary"]


async def test_month_end_snapshot_once_and_never_before_the_record_started(app):
    await app.store.ledger_set("started", datetime(2026, 10, 16, tzinfo=timezone.utc).isoformat())
    early = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)            # 23:00 Manila: too early
    assert not await ledger.month_end(app, early)
    last = datetime(2026, 10, 31, 15, 56, tzinfo=timezone.utc)             # 23:56 Manila
    assert await ledger.month_end(app, last)
    assert not await ledger.month_end(app, last + timedelta(minutes=2))
    [snap] = await events(app, "month_snapshot")
    block = ledger.snapshot_block(json.loads(snap["detail"]))
    assert block[1][0].startswith("Snapshot at the end of Oct 2026") and ledger.GROUP_COLS in block
    assert ledger.month_tab(datetime.fromisoformat(snap["ts"])) == "Oct 2026"


# Owner commands

async def test_history_and_ledger_commands(app):
    await app.settings.set(CHAT, "mode", "shadow", ADMIN)
    m, c = command("/history")
    await cmd_history(m, app, c)
    text = ANSWERS[-1][0][0]
    assert "Setting changed" in text and "Test group" in text
    m, c = command("/ledger")
    await cmd_ledger(m, app, c)
    assert "not connected" in ANSWERS[-1][0][0]
