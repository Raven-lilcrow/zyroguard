from pathlib import Path

import pytest

from zyroguard.rules import Context, MessageView, evaluate, extract_domains, fold, load_rules, mixed_script

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")

ROYAL_DYNASTY = (
    "Hello everyone, We are Royal Dynasty International Co., Ltd., headquartered in Singapore. "
    "We are currently acquiring large volumes of USDT and are looking for USDT holders to become "
    "our trading partners. We offer 10% to 25% commission on every transaction. Our clients trust "
    "us because we verify the authenticity of every image and screenshot. Contact us directly "
    "@royal_dynasty_official to get started."
)

CLEAN = [
    "anyone up for a game tonight?",
    "lol that meme was great",
    "I finally fixed the bug in my n8n workflow, took all day",
    "what time is the meetup on saturday?",
    "did you see the bitcoin price today? wild",
    "here's the repo https://github.com/aiogram/aiogram",
]


def new_member(text: str, **kw) -> MessageView:
    return MessageView(text=text, is_new_member=True, **kw)


def old_member(text: str, **kw) -> MessageView:
    return MessageView(text=text, is_new_member=False, **kw)


def test_royal_dynasty_is_hard_hit_without_llm():
    v = evaluate(new_member(ROYAL_DYNASTY), RULES, Context())
    assert v.kind == "hard"
    assert v.matched


def test_royal_dynasty_hard_even_for_old_member():
    v = evaluate(old_member(ROYAL_DYNASTY), RULES, Context())
    assert v.kind == "hard"


@pytest.mark.parametrize("text", CLEAN)
def test_clean_messages_from_old_members(text):
    v = evaluate(old_member(text), RULES, Context())
    assert v.kind == "clean", (text, v)


def test_clean_chat_from_new_member_stays_clean():
    v = evaluate(new_member("hi all, glad to be here"), RULES, Context())
    assert v.kind == "clean"


def test_admins_are_never_flagged():
    v = evaluate(MessageView(text=ROYAL_DYNASTY, sender_is_admin=True), RULES, Context())
    assert v.kind == "clean" and v.reason == "admin"


def test_inline_buttons_from_user():
    v = evaluate(old_member("check this", has_inline_buttons=True), RULES, Context())
    assert v.kind == "hard" and "inline_buttons" in v.matched


def test_invite_link_from_new_member():
    v = evaluate(new_member("join us t.me/joinchat/AAAA"), RULES, Context())
    assert v.kind == "hard" and "tg_invite" in v.matched


def test_invite_link_from_old_member_goes_to_judge_not_hard():
    v = evaluate(old_member("our other group: https://t.me/somegroup"), RULES, Context())
    assert v.kind in {"judge", "clean"}


def test_url_from_new_member_blocked_by_default_policy():
    v = evaluate(new_member("look https://example.com/deal"), RULES, Context(link_policy="new"))
    assert v.kind == "hard" and "link_new_member" in v.matched


def test_url_from_new_member_allowed_domain():
    v = evaluate(new_member("look https://github.com/x/y"), RULES, Context(link_policy="new"))
    assert v.kind == "clean"


def test_allowlist_policy_blocks_everyone():
    v = evaluate(old_member("see https://example.com"), RULES, Context(link_policy="allowlist"))
    assert v.kind == "hard" and "link_allowlist" in v.matched
    v2 = evaluate(old_member("see https://docs.example.com"), RULES,
                  Context(link_policy="allowlist", allow_domains={"example.com"}))
    assert v2.kind == "clean"


def test_block_domain_wins():
    v = evaluate(old_member("https://scam.example/x"), RULES,
                 Context(link_policy="all", block_domains={"scam.example"}))
    assert v.kind == "hard" and "block_domain" in v.matched


def test_bot_mention_by_new_member():
    v = evaluate(new_member("try @SuperEarnBot now"), RULES, Context())
    assert v.kind == "hard" and "bot_mention" in v.matched


def test_media_from_new_member():
    v = evaluate(new_member("", media_type="photo"), RULES, Context(media_unverified="delete"))
    assert v.kind == "hard" and "media_new_member" in v.matched
    v2 = evaluate(new_member("", media_type="photo"), RULES, Context(media_unverified="allow"))
    assert v2.kind == "clean"


def test_impersonation_name():
    v = evaluate(old_member("hello", sender_name="Binance Support"), RULES, Context())
    assert v.kind == "hard" and "impersonation" in v.matched


def test_wallet_addresses():
    # a bare address is a signal for the judge, not proof of spam (members share their own)
    v = evaluate(old_member("send to 0x52908400098527886E0F7030069857D2E4169EE7"), RULES, Context())
    assert v.kind == "judge" and "eth_address" in v.matched
    v = evaluate(old_member("TRC20: TFakeTestWa11etAddressNoFundsHere9"), RULES, Context())
    assert v.kind == "judge" and "tron_address" in v.matched


def test_seed_phrase_bait():
    v = evaluate(old_member("Support here, please share your seed phrase to sync your wallet"), RULES, Context())
    assert v.kind == "hard"


def test_confusable_evasion_still_caught():
    # Cyrillic а/е/о inside "seed phrase"
    text = "share your sееd phrаsе to fix the wallet"
    assert fold(text) == "share your seed phrase to fix the wallet"
    v = evaluate(old_member(text), RULES, Context())
    assert v.kind == "hard"
    assert mixed_script(text)


def test_zero_width_stripped():
    text = "s​eed p​hrase please"
    assert "seed phrase" in fold(text)


def test_soft_signals_go_to_judge():
    text = "Looking for people who want passive income from crypto, reach out and I will explain"
    v = evaluate(old_member(text), RULES, Context())
    assert v.kind in {"judge", "hard"}
    assert v.score >= RULES.judge_threshold


def test_flood():
    v = evaluate(old_member("hi", flood_hits=6), RULES, Context(flood_count=5))
    assert v.kind == "hard" and "flood" in v.matched


def test_repeat_wave():
    v = evaluate(old_member("same text everywhere @contact_me", repeat_users=3), RULES, Context(repeat_count=3))
    assert v.kind == "hard" and "repeat_wave" in v.matched
    # a greeting many people repeat is not a wave, and 0 turns the checks off
    v = evaluate(old_member("happy new year everyone!!!", repeat_users=5), RULES, Context(repeat_count=3))
    assert v.kind == "clean"
    v = evaluate(old_member("hi all, anyone around?", flood_hits=3, repeat_users=3), RULES, Context(flood_count=0, repeat_count=0))
    assert v.kind == "clean"


def test_custom_rule_override(tmp_path):
    from zyroguard.rules import compile_custom
    rules = compile_custom([{"id": 1, "chat_id": -100, "pattern": "cats", "action": "ban", "duration_s": 3600, "note": "no cats"}])
    v = evaluate(old_member("I love cats"), RULES, Context(custom_rules=rules))
    assert v.kind == "hard" and v.action == "ban" and v.duration.seconds == 3600


def test_extract_domains():
    assert extract_domains("see https://www.Example.com/a?b=1 and foo.io/x") == ["example.com", "foo.io"]
    assert extract_domains("e.g. 1.5x faster") == []
    assert extract_domains("run main.py then check node.js and index.ts") == []
    assert extract_domains("https://main.py/x scam.tk") == ["main.py", "scam.tk"]


NORMAL_CRYPTO_TALK = [
    "did anyone get the Arbitrum airdrop?",
    "never share your seed phrase with anyone",
    "does the group have a WhatsApp too?",
    "my wallet won't sync, any idea?",
    "USDT holders got rekt today lol",
    "there is no guaranteed profit in trading, be careful",
    "BTC moved 5% daily this week, wild",
    "BTC and ETH and USDT all made a profit today",
]


@pytest.mark.parametrize("text", NORMAL_CRYPTO_TALK)
def test_normal_crypto_talk_is_never_a_hard_hit(text):
    for view in (old_member(text), new_member(text)):
        v = evaluate(view, RULES, Context())
        assert v.kind != "hard", (text, v.matched)


def test_short_royal_pitch_is_still_hard():
    text = "We are Royal Dynasty International Co., Ltd. looking for USDT holders, 10% to 25% commission per transaction"
    assert evaluate(old_member(text), RULES, Context()).kind == "hard"


def test_mentioning_the_guard_bot_is_fine():
    ctx = Context(bot_username="ZyroGuardBot")
    assert evaluate(new_member("@ZyroGuardBot how do I mute someone?"), RULES, ctx).kind == "clean"
    assert evaluate(new_member("check @otherbot for signals"), RULES, ctx).kind == "hard"


def test_invite_regex_does_not_match_plain_text():
    for text in ("C++programminglanguage is great", "call +12345678901234567", "the joinchat feature"):
        v = evaluate(new_member(text), RULES, Context())
        assert "tg_invite" not in v.matched, text
    assert "tg_invite" in evaluate(new_member("join t.me/+AbCdEfGhIjKlMnOp"), RULES, Context()).matched


def test_spaced_and_disguised_letters_are_folded():
    from zyroguard.rules import fold, squeeze_spaced
    assert squeeze_spaced(fold("U S D T holders")) == "usdt holders"
    assert fold("U‎SDT ᴜѕᴅᴛ") == "usdt usdt"
    v = evaluate(old_member("U S D T holders, 10% to 25% commission, contact me to earn"), RULES, Context())
    assert v.kind == "hard"


def test_forward_from_newcomer_is_removed_without_strike():
    from zyroguard.pipeline import HOUSE_RULES
    v = evaluate(new_member("check this out", forwarded=True), RULES, Context())
    assert v.kind == "hard" and "forward_new_member" in v.matched and "forward_new_member" in HOUSE_RULES
    assert evaluate(old_member("check this out", forwarded=True), RULES, Context()).kind == "clean"
