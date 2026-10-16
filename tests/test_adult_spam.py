"""Adult-channel forwards: a word menu spelled with emoji letters, forwarded from an adult channel with an
OPEN HERE button, sent by a member who joined before the bot."""
from datetime import datetime, timezone
from pathlib import Path

from zyroguard.pipeline import extra_text, process
from zyroguard.rules import Context, MessageView, evaluate, fold, load_rules

from .test_pipeline_e2e import CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")
BODY = "H O T ❌❌❌\nROUGH 😈\nFIRST TIME 🌸\nBACKSTAGE 🎭\nSPA 🫧\nHOMEMADE 📹"
FORWARD = {"type": "channel", "chat": {"id": -1005550001, "type": "channel", "title": "Leak 🔞"},
           "message_id": 77, "date": int(datetime.now(timezone.utc).timestamp())}
BUTTON = {"inline_keyboard": [[{"text": "👉 OPEN HERE 👈", "url": "https://example-leak.site/join"}]]}


def old(text: str, **kw) -> MessageView:
    return MessageView(text=text, is_new_member=False, **kw)


def test_emoji_letters_fold():
    assert "xxx" in fold("❌❌❌")
    assert fold("🇭🇴🇹") == "hot" and fold("🅷🅾🆃") == "hot"


def test_the_message_as_seen_is_hard_even_from_an_old_member():
    extra = "[forwarded from Leak 🔞]\n[button 👉 OPEN HERE 👈 https://example-leak.site/join]"
    v = evaluate(old(BODY, forwarded=True, forwarded_from_channel=True, extra=extra), RULES, Context())
    assert v.kind == "hard"


def test_menu_alone_without_marker_or_button_is_still_hard():
    v = evaluate(old(BODY), RULES, Context())
    assert v.kind == "hard", v


def test_menu_forwarded_without_the_x_emoji_is_hard():
    body = "ROUGH 😈\nFIRST TIME 🌸\nBACKSTAGE 🎭\nHOMEMADE 📹"
    v = evaluate(old(body, forwarded=True, forwarded_from_channel=True,
                     extra="[button 👉 OPEN HERE 👈 https://x.site]"), RULES, Context())
    assert v.kind == "hard", v


def test_ordinary_talk_is_not_punished():
    for text in ["rough day at work", "found a memory leak in the worker",
                 "first time trying n8n, the homemade setup works"]:
        v = evaluate(old(text), RULES, Context())
        assert v.kind != "hard", (text, v)
    assert evaluate(old("rough day at work"), RULES, Context()).kind == "clean"


def test_extra_text_reads_button_and_forward_source():
    m = msg(BODY, forward_origin=FORWARD, reply_markup=BUTTON)
    extra = extra_text(m)
    assert "Leak 🔞" in extra and "OPEN HERE" in extra and "example-leak.site" in extra


async def test_pipeline_removes_it_from_a_member_who_joined_before_the_bot(app):  # noqa: F811
    # someone the bot has seen talk plenty, join never recorded
    for _ in range(12):
        await app.store.touch_member(CHAT, SPAMMER, "jendral", "Jendral", count_message=True)
    await process(app, msg(BODY, forward_origin=FORWARD, reply_markup=BUTTON))
    assert "delete_message" in app.bot.names()
    rows = await app.store.recent_audit(CHAT, 1)
    assert rows and rows[0].source == "rule" and "Leak" in (rows[0].text_excerpt or "")


async def test_quiet_member_from_before_the_bot_counts_as_new(app):  # noqa: F811
    # joined before the bot, first message ever: a link from them is a newcomer link
    await process(app, msg("check this https://random-site.xyz/offer"))
    assert "delete_message" in app.bot.names()
