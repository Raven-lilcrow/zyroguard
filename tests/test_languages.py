"""Languages: detection, the same scam in other languages, and the per-group allowed-languages rule."""
from pathlib import Path

import pytest

from zyroguard.language import detect, parse_languages
from zyroguard.pipeline import process
from zyroguard.rules import Context, MessageView, evaluate, load_rules
from zyroguard.settings import validate

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")

SCAMS = {
    "ru": "Ищем держателей USDT, комиссия от 10% до 25% за каждую сделку, пишите в личку @royal_desk",
    "zh": "我们正在寻找USDT持有者，每笔交易10%到25%佣金，请私信联系 @royal_desk",
    "id": "Kami mencari pemegang USDT, komisi 10% sampai 25% per transaksi, hubungi kami @royal_desk",
    "vi": "Chúng tôi tìm người giữ USDT, hoa hồng 10% đến 25% mỗi giao dịch, liên hệ @royal_desk",
}
CHAT_LINES = {
    "ru": "привет всем, как дела? кто-нибудь пробовал новый релиз?",
    "zh": "大家好，今天天气不错，有人用过这个工具吗",
    "id": "halo semua, apa kabar? saya baru di sini",
    "vi": "xin chào mọi người, hôm nay thế nào?",
    "tl": "kumusta kayo lahat? ako po si Ana",
    "es": "hola a todos, que tal el día?",
    "ja": "こんにちは、みなさん元気ですか",
    "ko": "안녕하세요 여러분 반갑습니다",
    "ar": "مرحبا بالجميع كيف حالكم",
}


@pytest.mark.parametrize("lang,text", list(CHAT_LINES.items()))
def test_detects_the_language(lang, text):
    assert detect(text) == lang


def test_short_or_unclear_text_is_unknown():
    for t in ["ok lol", "gm", "comfy hodl", "🔥🔥🔥", "https://github.com/x/y"]:
        assert detect(t) == "unknown", t
    assert detect("hey guys, does anyone know how to set up the API key?") == "en"


@pytest.mark.parametrize("lang", list(SCAMS))
@pytest.mark.parametrize("new", [True, False])
def test_the_usdt_scam_is_caught_in_every_language(lang, new):
    v = evaluate(MessageView(text=SCAMS[lang], is_new_member=new), RULES, Context())
    assert v.kind == "hard", (lang, v)


@pytest.mark.parametrize("lang,text", list(CHAT_LINES.items()))
def test_normal_chat_in_other_languages_is_left_alone(lang, text):
    for new in (True, False):
        assert evaluate(MessageView(text=text, is_new_member=new), RULES, Context()).kind == "clean", (lang, new)


def test_risky_foreign_message_goes_to_the_judge():
    # nothing the word rules know, but a handle, a phone number and crypto in a Thai message
    t = "สวัสดีครับ ติดต่อ @lucky_trader_88 หรือ +66 812 345 678 สำหรับ USDT"
    v = evaluate(MessageView(text=t, is_new_member=True), RULES, Context())
    assert v.kind in {"judge", "hard"} and "foreign_language_risk" in v.matched


def test_allowed_languages_setting_parses_names_and_codes():
    assert parse_languages("English, tagalog") == "en,tl"
    assert parse_languages("all") == ""
    assert validate("languages", "en zh") == "en,zh"
    with pytest.raises(ValueError):
        validate("languages", "klingon")


def test_other_language_is_delete_only_for_new_members():
    ctx = Context(allowed_languages={"en"})
    v = evaluate(MessageView(text=CHAT_LINES["ru"], is_new_member=True), RULES, ctx)
    assert v.kind == "hard" and v.action == "delete" and "language_not_allowed" in v.matched
    assert evaluate(MessageView(text=CHAT_LINES["ru"], is_new_member=False), RULES, ctx).kind == "clean"
    ctx_all = Context(allowed_languages={"en"}, language_scope="all")
    assert evaluate(MessageView(text=CHAT_LINES["ru"], is_new_member=False), RULES, ctx_all).action == "delete"
    # unknown and allowed languages are never removed for their language
    assert evaluate(MessageView(text="ok lol", is_new_member=True), RULES, ctx).kind == "clean"
    assert evaluate(MessageView(text=CHAT_LINES["tl"], is_new_member=True), RULES,
                    Context(allowed_languages={"en", "tl"})).kind == "clean"


def test_a_scam_in_a_disallowed_language_is_still_punished_as_spam():
    v = evaluate(MessageView(text=SCAMS["ru"], is_new_member=True), RULES, Context(allowed_languages={"en"}))
    assert v.kind == "hard" and v.action is None and "language_not_allowed" not in v.matched


async def test_pipeline_just_deletes_with_no_strike(app):  # noqa: F811
    await app.settings.set(CHAT, "languages", "en", ADMIN)
    await process(app, msg(CHAT_LINES["ru"]))      # first message ever: a new member
    names = app.bot.names()
    assert "delete_message" in names and "restrict_chat_member" not in names and "ban_chat_member" not in names
    assert await app.store.active_strikes(CHAT, SPAMMER) == 0
    row = (await app.store.recent_audit(CHAT, 1))[0]
    assert row.action == "delete" and "Russian" in row.reason


async def test_pipeline_leaves_regulars_alone_by_default(app):  # noqa: F811
    await app.settings.set(CHAT, "languages", "en", ADMIN)
    for _ in range(12):
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)
    await process(app, msg(CHAT_LINES["zh"]))
    assert "delete_message" not in app.bot.names()
