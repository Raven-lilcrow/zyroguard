"""Account phishing (2026-10-13): fake 'your account will be deleted' notices, fake support, and
requests for login codes / OTP / two-step passwords. Phishing-only hits always go to the AI judge."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Message

from zyroguard.judge import JudgeResult
from zyroguard.pipeline import phishing_offer, process
from zyroguard.rules import Context, MessageView, evaluate, load_rules

from .test_pipeline_e2e import ADMIN, CHAT, SPAMMER, app, msg  # noqa: F401

RULES = load_rules(Path(__file__).resolve().parents[1] / "zyroguard" / "rules.yaml")

PHISHING = [
    "Your Telegram account has been reported for spam and will be deleted in 24 hours. To keep your account, "
    "verify it here: telegram-verify-center.com",
    "Security alert: someone tried to log in to your Telegram account from a new device. If this wasn't you, "
    "confirm your identity at t-me-security.net/login",
    "Hello, this is Telegram Support. Your account is limited due to unusual activity. Please send me the 5-digit "
    "login code you just received to restore access",
    "I accidentally sent my verification code to your number, can you send it back to me please?",
    "Admin here, we are verifying members. Reply with the OTP sent to your phone to stay in the group",
    "To complete the security check, please share your two-step verification password with me",
    "Your Facebook page has violated our community standards and will be permanently disabled. Submit an appeal "
    "within 24 hours: meta-appeal-center.com",
    "We received a copyright complaint about your Instagram post. Fill in the objection form or your account will "
    "be removed: instagram-help-copyright.com",
    "Congratulations, you received a free Telegram Premium gift! Activate it within 12 hours by logging in here: "
    "premium-gift-telegram.org",
    "Na-lock ang iyong GCash account dahil sa kahina-hinalang activity. I-verify agad dito para ma-unlock: "
    "gcash-verify-ph.com",
    "Your bank account has been temporarily locked. Update your details now to avoid permanent suspension: "
    "secure-bank-update.com",
    "Your wallet has been restricted due to a security issue. Reconnect your wallet to restore access before your "
    "funds are frozen",
    "Your account will be banned in 24 hours for violating the rules. Message me privately to appeal and keep your "
    "account",
    "Your package could not be delivered due to an incomplete address. Update your details within 48 hours: "
    "parcel-redelivery-ph.com",
    "Your account needs KYC verification. Send a photo of your ID and a selfie to our support agent "
    "@Verify_Support_Desk to avoid suspension",
]
NORMAL = [
    "my account got suspended on X yesterday for no reason, anyone else?",
    "how do I verify my account on the Acme app? the email never came",
    "reminder: never share your login code or OTP with anyone, admins will never DM you first",
    "careful guys, I got a DM saying my Telegram will be deleted in 24 hours unless I verify, it's a scam, don't click",
    "is this legit? someone says my account is limited and wants the code I received",
    "my Fernhub account locked after too many login attempts, how do I unlock it?",
    "the login code never arrives on my phone, any idea?",
    "my GCash got locked lol, had to call support to fix it",
    "after you sign up, check your email and verify your account, the link expires in 24 hours",
    "support replied to my ticket, my account is restored now, thanks team",
    "our app now sends a 6-digit OTP when you log in, much safer",
    "turn on two-step verification in Telegram settings, it really helps",
    "is Telegram Premium worth it? thinking of buying it",
    "how long does an appeal take on Facebook after a page is disabled?",
    "the official site is telegram.org and help is at support.google.com",
]
META = PHISHING[6]          # rules alone would act (score >= 10): must still go to the judge
PHISH_IDS = {r.id for r in RULES.soft if r.only == "phishing"}


@pytest.mark.parametrize("text", PHISHING)
def test_account_phishing_reaches_the_judge(text):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context(link_policy="new"))
    assert v.kind in {"judge", "hard"}, (v.score, v.matched)
    assert phishing_offer(v.matched), v.matched


@pytest.mark.parametrize("text", NORMAL)
def test_normal_talk_about_accounts_codes_and_support_stays_clean(text):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context(link_policy="new"))
    assert v.kind == "clean", (v.score, v.matched)


@pytest.mark.parametrize("text,hit", [
    ("send me the code you received", True),
    ("please reply with your OTP", True),
    ("share the password with me", True),
    ("never share your login code with anyone", False),
    ("don't send me your password, admins never ask", False),
    ("the app will send the OTP to the user", False),
])
def test_code_requests(text, hit):
    v = evaluate(MessageView(text=text, is_new_member=False), RULES, Context())
    assert ("code_request" in v.matched) is hit, v.matched


def test_phishing_rules_off_uses_none_of_the_new_rules():
    for text in PHISHING + NORMAL:
        v = evaluate(MessageView(text=text, is_new_member=True), RULES, Context(phishing_rules=False))
        assert not set(v.matched) & PHISH_IDS, v.matched


async def _regular(app):  # noqa: F811
    for _ in range(8):
        await app.store.touch_member(CHAT, SPAMMER, "spammy", "Spam", count_message=True)


async def _judge(app, monkeypatch, spam, category, confidence, seen=None):  # noqa: F811
    async def classify(**kw):
        if seen is not None:
            seen.update(kw)
        return JudgeResult(spam, category, confidence, "fake account notice")
    monkeypatch.setattr(app.judge, "classify", classify)
    app.judge.client = object()


async def test_sure_phishing_goes_to_the_judge_and_is_banned(app, monkeypatch):  # noqa: F811
    seen = {}
    await _regular(app)
    await _judge(app, monkeypatch, True, "phishing", 0.97, seen)
    await process(app, msg(META))
    assert seen, "the judge was not asked"
    assert "ban_chat_member" in app.bot.names()                 # impersonation_action, no ladder


async def test_a_quoted_warning_the_judge_clears_is_left_alone(app, monkeypatch):  # noqa: F811
    await _regular(app)
    await _judge(app, monkeypatch, False, "clean", 0.1)
    await process(app, msg(META))
    assert "delete_message" not in app.bot.names()


async def test_without_a_judge_the_rules_still_act(app):  # noqa: F811
    await _regular(app)                                         # fixture judge has no key
    await process(app, msg(META))
    assert "delete_message" in app.bot.names()


async def test_phishing_rules_off_keeps_the_old_behaviour(app, monkeypatch):  # noqa: F811
    await app.settings.set(CHAT, "phishing_rules", "off", ADMIN)
    seen = {}
    await _regular(app)
    await _judge(app, monkeypatch, True, "phishing", 0.97, seen)
    await process(app, msg(PHISHING[2]))                        # code request: clean without the new rules
    assert not seen and "delete_message" not in app.bot.names()


async def test_the_test_command_says_phishing_goes_to_the_judge(app, monkeypatch):  # noqa: F811
    from zyroguard.diag import cmd_test
    replies = []

    async def quiet_reply(self, text, **kw):
        replies.append(text)
        return SimpleNamespace(message_id=72, chat=self.chat)
    monkeypatch.setattr(Message, "reply", quiet_reply)
    await _judge(app, monkeypatch, True, "phishing", 0.97)
    support = PHISHING[2]       # no link (/test plays a new member, whose links are a house rule of their own)
    await cmd_test(msg(f"/test {support}", user_id=ADMIN), app, CommandObject(prefix="/", command="test", args=support))
    assert "looks like account phishing" in "\n".join(replies)
