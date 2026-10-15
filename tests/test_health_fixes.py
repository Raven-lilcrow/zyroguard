"""Fixes from the 2026-10-12 health check: lookalike brand names, fake contract-migration notices,
severe judge verdicts skip the strike ladder, adult menu words need context, lift notices."""
from pathlib import Path

import pytest
from aiogram.types import User

from zyroguard.captcha import handle_join
from zyroguard.judge import JudgeResult
from zyroguard.pipeline import process
from zyroguard.rules import Context, MessageView, brand_in_name, evaluate, load_rules
from zyroguard.settings import validate

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")
BRANDS = ["AcmeAI"]


@pytest.mark.parametrize("name", [
    "AcmeAl",                           # small L for the capital I
    "AcmeAl | Community Discussion",
    "Acm3AI", "Acme Al", "ACM3 AI",
    "Аcme AI",                          # Cyrillic А
    "ᴀᴄᴍᴇᴀɪ",                           # small caps
])
def test_lookalike_brand_names_are_caught(name):
    assert brand_in_name(name, None, BRANDS) == ("AcmeAI", True), name


@pytest.mark.parametrize("name,username", [
    ("AcmeAI fan", None), ("Acme AI", None), ("Ana", "acmeai_lover"), ("acmeai", None),
])
def test_the_real_spelling_is_allowed_but_noted(name, username):
    assert brand_in_name(name, username, BRANDS) == ("AcmeAI", False)


@pytest.mark.parametrize("name", ["Ana Reyes", "Acme Aide", "Acmeist", "BadmintonFan"])
def test_unrelated_names_are_ignored(name):
    assert brand_in_name(name, None, BRANDS) is None


def test_a_longer_word_containing_a_short_brand_is_not_a_lookalike():
    assert brand_in_name("Lumoira Trader", None, ["Lumo1"]) is None
    assert brand_in_name("LumoI support", None, ["Lumo1"]) == ("Lumo1", True)


def test_bot_name_is_protected_everywhere():
    v = evaluate(MessageView(text="hello", sender_name="Zyr0Guard", is_new_member=False), RULES, Context())
    assert v.kind == "hard" and "brand_lookalike" in v.matched


def test_lookalike_name_on_any_message_is_hard_but_real_name_is_only_weight():
    ctx = Context(protected_names=BRANDS)
    v = evaluate(MessageView(text="gm everyone", sender_name="AcmeAl", is_new_member=False), RULES, ctx)
    assert v.kind == "hard" and "brand_lookalike" in v.matched
    v = evaluate(MessageView(text="gm everyone", sender_name="AcmeAI fan", is_new_member=True), RULES, ctx)
    assert v.kind == "clean" and "brand_name" in v.matched


def test_protected_names_setting_validation():
    assert validate("protected_names", "AcmeAI, Acme AI ,AcmeAI") == "AcmeAI, Acme AI"
    assert validate("protected_names", "none") == ""
    with pytest.raises(ValueError):
        validate("protected_names", "AI")


MIGRATION = [
    "🚨 Important Announcement 🚨\n\nHey everyone!\nWe’ll be redeploying our Contract Address (CA) in the next "
    "28 hours to facilitate auditing and improve security. All holders will receive the new V2 tokens through "
    "an airdrop. Please PM me to register your wallet before the snapshot. 👉 Limited slots 👈",
    "🚨 TOKEN UPGRADE NOTICE\n\nWe are upgrading to a new token with a clean CA. The old tokens will be burned as "
    "part of the transition. DM me your wallet address to receive the new tokens 1:1.",
    "🚨 إعلان مهم 🚨\n\nمرحبا بالجميع!\n\nسنقوم بإعادة نشر عنوان العقد الخاص بنا (CA) في ال 28 ساعة القادمة "
    "لتسهيل التدقيق. راسلني للتسجيل في الإصدار الثاني USDT",
]


@pytest.mark.parametrize("text", MIGRATION)
def test_fake_contract_migration_notices_score_high(text):
    for new in (True, False):
        v = evaluate(MessageView(text=text, is_new_member=new), RULES, Context())
        assert v.kind in {"judge", "hard"}, (new, v)


@pytest.mark.parametrize("text", [
    "is the CA on the website the right one?",
    "when will the token upgrade to v2 happen? excited",
    "can someone DM me the docs link? thanks",
])
def test_normal_questions_about_the_contract_stay_clean(text):
    assert evaluate(MessageView(text=text, is_new_member=False), RULES, Context()).kind == "clean"


def test_everyday_words_from_the_adult_menu_are_ignored_alone():
    for text in ["found a memory leak in the worker, rough day", "first time here, amateur trader",
                 "the casting call for the hackathon video is open"]:
        assert evaluate(MessageView(text=text, is_new_member=False), RULES, Context()).kind == "clean", text
    # next to adult emoji they still count
    v = evaluate(MessageView(text="ROUGH 😈 FIRST TIME 🥵 BACKSTAGE 💦", is_new_member=False), RULES, Context())
    assert "adult_menu" in v.matched and v.kind in {"judge", "hard"}


async def test_lookalike_message_is_banned_without_a_strike(app):  # noqa: F811
    await app.settings.set(CHAT, "protected_names", "AcmeAI", ADMIN)
    m = msg("gm, contract news soon")
    m = m.model_copy(update={"from_user": User(id=SPAMMER, is_bot=False, first_name="AcmeAl", last_name="| Community")})
    await process(app, m)
    assert "ban_chat_member" in app.bot.names() and "delete_message" in app.bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.action == "ban" and "lookalike" in row.reason


async def test_lookalike_name_is_removed_at_join(app):  # noqa: F811
    await app.settings.set(CHAT, "protected_names", "AcmeAI", ADMIN)
    cs = await app.settings.get(CHAT)
    await handle_join(app, CHAT, "Test group", User(id=SPAMMER, is_bot=False, first_name="AcmeAl Support"), cs)
    assert "ban_chat_member" in app.bot.names()
    assert await app.store.captcha_get(CHAT, SPAMMER) is None


async def test_real_brand_name_at_join_still_gets_the_normal_captcha(app):  # noqa: F811
    await app.settings.set(CHAT, "protected_names", "AcmeAI", ADMIN)
    cs = await app.settings.get(CHAT)
    await handle_join(app, CHAT, "Test group", User(id=SPAMMER, is_bot=False, first_name="AcmeAI fan"), cs)
    assert "ban_chat_member" not in app.bot.names()
    assert await app.store.captcha_get(CHAT, SPAMMER) is not None


async def test_a_fake_migration_notice_caught_by_rules_is_banned(app):  # noqa: F811
    await process(app, msg(MIGRATION[1]))
    assert "ban_chat_member" in app.bot.names()
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0


@pytest.mark.parametrize("category,conf,banned", [
    ("impersonation", 0.98, True), ("phishing", 0.96, True), ("impersonation", 0.90, False),
    ("crypto_scam", 0.99, False),
])
async def test_sure_impersonation_from_the_judge_skips_the_ladder(app, monkeypatch, category, conf, banned):  # noqa: F811
    async def classify(**kw):
        return JudgeResult(True, category, conf, "fake admin notice")
    monkeypatch.setattr(app.judge, "classify", classify)
    for _ in range(8):   # a regular, so only the judge decides
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)
    await process(app, msg("We are migrating to a new contract address next week, details soon"))
    names = app.bot.names()
    assert ("ban_chat_member" in names) is banned, names
    if banned:
        assert await app.store.active_strikes(CHAT, SPAMMER) == 0
