"""Fake jobs and task scams (2026-10-13): the rules find them, the AI judge always decides, and
they are only deleted (no strike) until the same person's job_strike_after-th one."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Message

from zyroguard.judge import JudgeResult
from zyroguard.pipeline import job_offer, process
from zyroguard.rules import Context, MessageView, evaluate, load_rules

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")

SCAMS = [
    "Hi! We are looking for part-time online staff, just like and follow TikTok videos, earn 200-800 USD daily, "
    "no fees, message me for details",
    "Simple job: rate products and hotels on our app and get commission for every task. Takes 30 minutes a day, "
    "paid daily. Interested? Contact our receptionist",
    "Earn extra income by boosting app downloads from home. Flexible hours, instant withdrawal, beginners welcome. "
    "Text me to start",
    "Hello, I'm Sarah, HR from Global Media Agency. We have remote positions open, 1-2 hours per day, salary "
    "$150-$500/day. Add me on WhatsApp +1 415 555 0134",
    "Hiring now! Work from home, daily pay, no experience needed. Contact @HR_Jessica_Job",
    "Need 10 people for online typing and data entry work, $50 per hour, payment every day. Limited slots, DM me now",
    "Complete 3 simple tasks and receive 120 dollars, after that you can unlock VIP tasks with higher commission by "
    "making a small deposit",
    "Amazon is looking for order assistants to help merchants increase sales, earn 3000 to 8000 a week, training "
    "provided, click the link to apply",
    "Hi po! Naghahanap kami ng part time online staff, mag-like at follow lang ng TikTok videos, kita ₱500 hanggang "
    "₱3000 araw-araw, walang bayad, message mo ako",
    "Good day! We are hiring online encoders, no experience needed, ₱1,500 per day, payout thru GCash. Chat mo ako "
    "para sa details",
    "Buscamos personas para trabajo desde casa, solo dar like a videos, gana 200 dólares diarios, escríbeme",
]
NORMAL = [
    "We're hiring a backend engineer at Acme, full-time, details on our careers page",
    "anyone know good remote developer jobs? I'm looking for part-time work",
    "my part-time job is killing me, so tired today",
    "how do I earn points in the Acme app? is there a daily task list?",
    "I earn about $50 a day from my Fernhub sessions, not bad at all",
    "Fernhub pays $1 per 1M tokens, how do I withdraw my earnings?",
    "I topped up my ShopLink balance but my orders are not showing, can an admin check?",
    "just posted a new video about Claude Code tips, would love it if you like and share it",
    "if anyone needs help with the setup, message me, happy to help",
    "is there a group on WhatsApp too or only this Telegram?",
    "does anyone know if Acme is hiring now? I'd love to apply",
    "please rate the app on the play store if you like it, helps the team a lot",
    "I work flexible hours so I can join the call later",
]
TASK = SCAMS[0]


@pytest.mark.parametrize("text", SCAMS)
def test_job_and_task_scams_reach_the_judge_and_read_as_job_offers(text):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context(link_policy="new"))
    assert v.kind in {"judge", "hard"}, (v.score, v.matched)
    assert job_offer(v.matched), v.matched


@pytest.mark.parametrize("text", NORMAL)
def test_normal_talk_about_jobs_pay_and_tasks_stays_clean(text):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context(link_policy="new"))
    assert v.kind == "clean", (v.score, v.matched)


@pytest.mark.parametrize("text", ["salary $150-$500/day", "only $50 per hour", "₱1,500 per day",
                                  "200-800 USD daily", "earn 3000 to 8000 a week", "gana 200 dólares diarios"])
def test_pay_rates_are_read_with_a_space_or_dash_before_the_amount(text):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context())
    assert "pay_rate" in v.matched, text


def test_a_crypto_pitch_with_job_words_is_not_handled_as_a_job():
    assert job_offer(["job_bait", "pay_rate", "contact_pitch"])
    assert not job_offer(["job_bait", "usdt_commission"])
    assert not job_offer(["pay_rate", "contact_pitch"])            # pay + contact alone is not a job offer
    assert not job_offer(["task_scam", "brand_name"])


async def _regular(app):  # noqa: F811
    for _ in range(8):
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)


async def _judge(app, monkeypatch, spam=True, category="job_scam", confidence=0.92, seen=None):  # noqa: F811
    async def classify(**kw):
        if seen is not None:
            seen.update(kw)
        return JudgeResult(spam, category, confidence, "pay-per-like task offer")
    monkeypatch.setattr(app.judge, "classify", classify)
    app.judge.client = object()          # configured, so job offers go to the judge


async def _past_job_catches(app, n, undone=False):  # noqa: F811
    for i in range(n):
        audit_id = await app.store.add_audit(
            chat_id=CHAT, user_id=SPAMMER, username="spammy", message_id=900 + i, text_hash=None,
            text_excerpt="old job scam", source="judge", reason="job", detail={"job_scam": True},
            action="delete", duration_s=None, expires_at=None, mode="enforce", actor_id=None)
        if undone:
            await app.enforcer.undo(audit_id, ADMIN)


async def test_a_job_scam_the_rules_are_sure_about_still_goes_to_the_judge_and_is_only_deleted(app, monkeypatch):  # noqa: F811
    seen = {}
    await _regular(app)
    await _judge(app, monkeypatch, seen=seen)
    await process(app, msg(TASK))
    assert seen, "the judge was not asked"
    names = app.bot.names()
    assert "delete_message" in names and "restrict_chat_member" not in names and "ban_chat_member" not in names
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.detail["job_scam"] is True and row.detail["job_count"] == 1


async def test_a_real_job_post_the_judge_clears_is_left_alone(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch, spam=False, category="clean", confidence=0.1)
    await process(app, msg(TASK))
    assert "delete_message" not in app.bot.names()


async def test_the_fifth_job_scam_from_one_person_gets_the_strike_ladder(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch)
    await _past_job_catches(app, 4)
    await process(app, msg(TASK))
    assert "restrict_chat_member" in app.bot.names()                         # strike 1 = mute 24h
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1
    assert (await app.store.recent_audit(CHAT, 1))[0].detail["job_count"] == 5


async def test_undone_job_catches_do_not_count_towards_the_ladder(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch)
    await _past_job_catches(app, 4, undone=True)
    assert await app.store.job_catches(CHAT, SPAMMER) == 0
    await process(app, msg(TASK))
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0


@pytest.mark.parametrize("value,muted", [("1", True), ("0", False)])
async def test_job_strike_after_setting(app, monkeypatch, value, muted):  # noqa: F811
    await app.settings.set(CHAT, "job_strike_after", value, ADMIN)
    await _regular(app)
    await _judge(app, monkeypatch)
    await _past_job_catches(app, 6)
    await process(app, msg(TASK))
    assert ("restrict_chat_member" in app.bot.names()) is muted


async def test_the_judge_labeling_any_message_a_job_scam_is_handled_gently(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch, category="job_scam", confidence=0.9)
    await process(app, msg("We are migrating to a new contract address next week, details soon"))
    assert "delete_message" in app.bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0


async def test_other_scams_keep_their_strike(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch, category="crypto_scam", confidence=0.9)
    await process(app, msg("We are migrating to a new contract address next week, details soon"))
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1


async def test_without_a_judge_a_sure_job_scam_is_still_deleted_without_a_strike(app):  # noqa: F811
    await _regular(app)                                                       # fixture judge has no key
    await process(app, msg(TASK))
    assert "delete_message" in app.bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    assert (await app.store.recent_audit(CHAT, 1))[0].source == "rule"


async def test_the_test_command_explains_the_job_policy(app, monkeypatch):  # noqa: F811
    from zyroguard.diag import cmd_test
    replies = []

    async def quiet_reply(self, text, **kw):
        replies.append(text)
        return SimpleNamespace(message_id=72, chat=self.chat)
    monkeypatch.setattr(Message, "reply", quiet_reply)
    await _judge(app, monkeypatch)
    await cmd_test(msg(f"/test {TASK}", user_id=ADMIN), app, CommandObject(prefix="/", command="test", args=TASK))
    out = "\n".join(replies)
    assert "always go to the AI judge" in out and "no strike" in out and "job scam number 5" in out


# ---- job_rules off: a group keeps the rules and handling it had before (2026-10-13) ----

JOB_ONLY_IDS = {r.id for r in RULES.soft if r.only == "jobs"} - {r.id for r in RULES.soft if r.only != "jobs"}


@pytest.mark.parametrize("text", SCAMS + NORMAL)
def test_with_job_rules_off_no_new_rule_is_used(text):
    v = evaluate(MessageView(text=text, is_new_member=True), RULES, Context(job_rules=False))
    assert not (set(v.matched) & JOB_ONLY_IDS), v.matched


def test_job_rules_off_scores_a_task_scam_like_before():
    v = evaluate(MessageView(text=TASK, is_new_member=False), RULES, Context(job_rules=False))
    assert v.kind == "clean" and v.score == 1      # what the rules gave it before the job rules existed


async def test_job_rules_off_skips_the_gentle_handling(app, monkeypatch):  # noqa: F811
    await app.settings.set(CHAT, "job_rules", "off", ADMIN)
    await _regular(app)
    seen = {}
    await _judge(app, monkeypatch, category="job_scam", confidence=0.9, seen=seen)
    await process(app, msg(TASK))
    assert not seen and "delete_message" not in app.bot.names()             # the rules do not flag it
    await process(app, msg("We are migrating to a new contract address next week, details soon", message_id=43))
    assert await app.store.active_strikes(CHAT, SPAMMER) == 1               # judge's job_scam = normal strike
    assert "job_scam" not in (await app.store.recent_audit(CHAT, 1))[0].detail
