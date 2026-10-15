"""End-to-end check of a brand-new group, through the same dispatcher and routers as main.py.

Telegram is faked (nothing is sent anywhere, the bot token is replaced), the database is a temp file,
n8n / mod-log / contact webhooks are switched off. On the server it uses the real rules, the real
search model and real Claude answers, so it costs a few US cents (about 20 AI calls).

Run on the server in a throwaway copy of the live image (never touches the live database):
  docker run --rm --memory=1g --env-file /opt/zyroguard/.env -e PYTHONPATH=/app \
    -v /tmp/zge2e:/w:ro zyroguard-zyroguard:latest python /w/e2e_newgroup.py
Locally, without AI or the model (checks the wiring only):
  E2E_LOCAL=1 .venv/Scripts/python tools/e2e_newgroup.py
Prints PASS / FAIL lines for the fixed expectations, and the AI answers for a human to read.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Update  # noqa: E402

LOCAL = os.environ.get("E2E_LOCAL") == "1"
env = dict(os.environ)
for k in ("N8N_EVENT_URL", "N8N_EVENT_SECRET", "MODLOG_CHAT_ID", "CONTACT_WEBHOOK_URL", "CONTACT_WEBHOOK_SECRET",
          "ALLOWED_CHAT_IDS", "OWNER_LEDGER_URL", "OWNER_LEDGER_SECRET"):
    env.pop(k, None)   # nothing may leave the test: no n8n events, no mod-log group, no contact webhook, no owner sheet
env["BOT_TOKEN"] = "4242:FAKE-token-for-the-end-to-end-test"
env["DB_PATH"] = os.environ.get("E2E_DB", "/tmp/e2e_newgroup.db")
if LOCAL:
    env.setdefault("OWNER_IDS", "1001")
    env["ANTHROPIC_API_KEY"] = ""
    env["DB_PATH"] = os.environ.get("E2E_DB", str(Path(os.environ.get("TEMP", "/tmp")) / "e2e_newgroup.db"))
Path(env["DB_PATH"]).unlink(missing_ok=True)

from zyroguard import assistant, captcha, commands, contact, diag, forwards, kb, owner, pipeline, similar, support  # noqa: E402
from zyroguard import help as help_mod  # noqa: E402
from zyroguard.actions import Enforcer  # noqa: E402
from zyroguard.app import AdminCache, App  # noqa: E402
from zyroguard.config import load_config  # noqa: E402
from zyroguard.judge import Judge  # noqa: E402
from zyroguard.main import membership  # noqa: E402
from zyroguard.rules import load_rules  # noqa: E402
from zyroguard.settings import SettingsService  # noqa: E402
from zyroguard.store import Store  # noqa: E402

cfg = load_config(env)
OWNER = cfg.owner_ids[0]
BOT_ID = 4242
GROUP = -1009990000077
TITLE = "Kape Corner Community"
CHAT = {"id": GROUP, "type": "supergroup", "title": TITLE}
BOT_U = {"id": BOT_ID, "is_bot": True, "first_name": "ZyroGuard", "username": "zyroguard_bot"}
OWNER_U = {"id": OWNER, "is_bot": False, "first_name": "Owner", "username": "groupowner"}


def member(n: int) -> dict:
    return {"id": 777000500 + n, "is_bot": False, "first_name": f"Member{n}", "username": f"member{n}"}


def now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# ---- fake Telegram ----------------------------------------------------------------------------------

ADMIN_RIGHTS = {"can_be_edited": False, "is_anonymous": False, "can_manage_chat": True, "can_delete_messages": True,
                "can_manage_video_chats": True, "can_restrict_members": True, "can_promote_members": False,
                "can_change_info": False, "can_invite_users": True, "can_post_stories": False,
                "can_edit_stories": False, "can_delete_stories": False, "can_send_welcome_messages": False}


class FakeTelegram(BaseSession):
    """Answers every Bot API call locally and records it."""

    def __init__(self):
        super().__init__()
        self.log: list[tuple[str, dict, object]] = []
        self.n = itertools.count(5000)

    def _chat(self, chat_id):
        if chat_id == GROUP:
            return CHAT
        return {"id": chat_id, "type": "private", "first_name": "User"}

    def _result(self, name: str, p: dict):
        if name in ("sendMessage", "sendPhoto", "sendAnimation", "sendDocument", "editMessageText",
                    "editMessageReplyMarkup", "editMessageCaption"):
            return {"message_id": next(self.n), "date": now(), "chat": self._chat(p.get("chat_id")), "from": BOT_U,
                    "text": p.get("text") or p.get("caption") or "(media)"}
        if name == "copyMessage":
            return {"message_id": next(self.n)}
        if name == "getChatAdministrators":
            return [{"status": "creator", "user": OWNER_U, "is_anonymous": False},
                    {"status": "administrator", "user": BOT_U, **ADMIN_RIGHTS}]
        if name == "getChatMember":
            uid = p.get("user_id")
            if uid == BOT_ID:
                return {"status": "administrator", "user": BOT_U, **ADMIN_RIGHTS}
            if uid == OWNER:
                return {"status": "creator", "user": OWNER_U, "is_anonymous": False}
            return {"status": "member", "user": {"id": uid, "is_bot": False, "first_name": "Member"}}
        if name == "getChat":
            c = self._chat(p.get("chat_id"))
            return {**c, "accent_color_id": 0, "max_reaction_count": 0,
                    "accepted_gift_types": {"unlimited_gifts": False, "limited_gifts": False, "unique_gifts": False,
                                            "premium_subscription": False, "gifts_from_channels": False}}
        if name == "getMe":
            return BOT_U
        if name == "getChatMemberCount":
            return 42
        return True

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        p = method.model_dump(exclude_none=True)
        raw = self._result(name, p)
        self.log.append((name, p, raw))
        return self.check_response(bot, method, 200, json.dumps({"ok": True, "result": raw})).result

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if False:  # pragma: no cover
            yield b""

    async def close(self):
        pass


# ---- results ----------------------------------------------------------------------------------------

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def one_line(s: str, n: int = 320) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "~"


FAQ_MD = """# Kape Corner Help

## Orders and shipping

### How long does delivery take?
Metro Manila orders arrive in 1 to 2 days and cost P99 to ship. Provincial orders (Luzon, Visayas, Mindanao) take 3 to 5 days and cost P180. Shipping is free on orders over P1,500.

### Can I track my order?
Yes. Open **My Account > Orders** and tap the order to see the courier's tracking number.

## Payment

We accept GCash, Maya, Visa and Mastercard. Cash on delivery is only available inside Metro Manila, for orders up to P5,000.

## Returns and refunds

- Unopened bags can be returned within 7 days of delivery.
- If something arrives damaged, send a photo to hello@kapecorner.ph within 48 hours.
- Refunds go back to the original payment method in 5 to 7 business days.

## Coffee subscriptions

Get fresh beans every 2 or 4 weeks. To pause or cancel, go to **My Account > Subscriptions** before your next billing date. Changes after the billing date apply to the following delivery.
"""

STORES_TXT = """Where are your stores?
Kape Corner has two cafes: Legazpi Street in Makati and Maginhawa Street in Quezon City.

What are your store hours?
Both cafes are open every day from 7:00 AM to 9:00 PM. We are closed on December 25 and January 1.

Is there Wi-Fi?
Yes, free Wi-Fi in both cafes. Ask the barista to connect you.

Can I bring my dog?
Pets are welcome in the outdoor seating area of the Maginhawa cafe only.
"""

INSTRUCTIONS = ("Tone: friendly, simple words, short answers.\n"
                "Do: end every answer about orders, shipping or payment with our help page https://kapecorner.ph/help\n"
                "Don't: mention or compare other coffee brands. For politics or anything offensive, say you can only "
                "help with Kape Corner.")


async def main() -> None:
    session = FakeTelegram()
    bot = Bot(cfg.bot_token, session=session, default=DefaultBotProperties(link_preview_is_disabled=True,
                                                                           allow_sending_without_reply=True))
    store = Store(str(cfg.db_path))
    await store.open()
    enforcer = Enforcer(bot, store, n8n_url=None, n8n_secret=None, global_modlog=None,
                        bot_username="zyroguard_bot", owner_ids=cfg.owner_ids)
    app = App(config=cfg, bot=bot, store=store, settings=SettingsService(store, cfg.default_mode),
              judge=Judge(store, cfg.anthropic_api_key, cfg.judge_model, cfg.judge_effort), enforcer=enforcer,
              admins=AdminCache(bot), rules=load_rules(Path(cfg.rules_path)), bot_username="zyroguard_bot",
              bot_id=BOT_ID)
    if LOCAL:
        from tests.test_similar import WordEmbedder
        from tests.test_support import FakeMessages
        app.assistant = assistant.Assistant(store, SimpleNamespace(messages=FakeMessages(
            "Metro Manila orders arrive in **1 to 2 days**.")), "claude-opus-5", "medium")
        embedder = WordEmbedder()
    else:
        app.assistant = assistant.Assistant(store, app.judge.client, cfg.judge_model, cfg.assistant_effort)
        embedder = similar.Embedder()
    enforcer.exempt = app.is_exempt

    dp = Dispatcher()   # the same order as main.run()
    dp["app"] = app
    dp.message.outer_middleware(assistant.MentionedCommands())
    for r in (owner.router, support.router, contact.router, help_mod.router, diag.router, commands.router,
              assistant.router, membership, captcha.router, forwards.router, pipeline.router):
        dp.include_router(r)

    errors: list[str] = []

    @dp.errors()
    async def on_error(event) -> None:
        errors.append(f"{type(event.exception).__name__}: {event.exception}")

    await app.load_allowed()
    ids = itertools.count(1)
    mids = itertools.count(100)

    async def feed(**update) -> list[tuple[str, dict, object]]:
        app.assistant._last.clear()   # real members take longer than the 8 s cooldown between questions
        before = len(session.log)
        await dp.feed_update(bot, Update.model_validate({"update_id": next(ids), **update}, context={"bot": bot}))
        return session.log[before:]

    def gmsg(text: str, user: dict, reply_to: dict | None = None) -> dict:
        d = {"message_id": next(mids), "date": now(), "chat": CHAT, "from": user, "text": text}
        if reply_to:
            d["reply_to_message"] = reply_to
        return d

    def said(reqs) -> list[str]:
        return [p.get("text", "") for name, p, _ in reqs if name == "sendMessage"]

    def calls(reqs) -> list[str]:
        return [name for name, _, _ in reqs]

    def last_reply(reqs):
        sent = [(p, raw) for name, p, raw in reqs if name == "sendMessage" and p.get("chat_id") == GROUP]
        return sent[-1] if sent else (None, None)

    t0 = time.time()
    print("== 1. the owner adds ZyroGuard to a new group")
    bot_member = {"status": "administrator", "user": BOT_U, **ADMIN_RIGHTS}
    reqs = await feed(my_chat_member={"chat": CHAT, "from": OWNER_U, "date": now(),
                                      "old_chat_member": {"status": "left", "user": BOT_U},
                                      "new_chat_member": bot_member})
    cs = await app.settings.get(GROUP)
    check("group registered", GROUP in app.allowed_chats and bool(await store.get_chat(GROUP)))
    check("starts in shadow mode", cs.raw("mode") == "shadow")
    check("job and phishing rules on, support off",
          cs.bool("job_rules") and cs.bool("phishing_rules") and not cs.bool("support"))
    print("  bot says:", one_line(" | ".join(said(reqs)), 300))

    print("== 2. owner commands, with and without tagging the bot")
    reqs = await feed(message=gmsg("/mode enforce", OWNER_U))
    check("/mode enforce works", (await app.settings.get(GROUP)).raw("mode") == "enforce", one_line(said(reqs)))
    reqs = await feed(message=gmsg("@zyroguard_bot /diag", OWNER_U))
    check("'@zyroguard_bot /diag' runs /diag", any("rights" in t.lower() or "✅" in t for t in said(reqs)),
          one_line(said(reqs)))
    print("  /diag first line:", one_line((said(reqs) or [""])[0].split("\n")[0], 160))

    print("== 3. a newcomer joins: captcha")
    joiner = member(90)
    reqs = await feed(chat_member={"chat": CHAT, "from": joiner, "date": now(),
                                   "old_chat_member": {"status": "left", "user": joiner},
                                   "new_chat_member": {"status": "member", "user": joiner}})
    check("newcomer muted and asked a captcha",
          "restrictChatMember" in calls(reqs) and any("tap" in t.lower() or "solve" in t.lower() or "pick" in t.lower()
                                                      for t in said(reqs)), f"{calls(reqs)} {one_line(said(reqs))}")

    print("== 4. moderation (enforce mode, real rules" + ("" if LOCAL else " and real AI judge") + ")")
    cases = [
        ("normal chat", "good morning! anyone tried the new iced spanish latte?", "left"),
        ("crypto commission scam", "We are Royal Dynasty International Co., Ltd. looking for USDT holders, "
                                   "10% to 25% commission per transaction", "removed"),
        ("fake job", "Part time job from home, earn $300 per day just liking youtube videos. "
                     "No experience needed, DM me on WhatsApp", "removed" if not LOCAL else "any"),
        ("account phishing", "Your Telegram account will be deleted in 24 hours for violating our terms. Send me the "
                             "login code you just received to verify it is you.", "removed" if not LOCAL else "any"),
        ("member warning about OTPs", "PSA: never share your OTP or login code with anyone, admins will never DM "
                                      "you first", "left"),
    ]
    for i, (label, text, expect) in enumerate(cases):
        m = gmsg(text, member(10 + i))
        reqs = await feed(message=m)
        deleted = any(n == "deleteMessage" and p.get("message_id") == m["message_id"] for n, p, _ in reqs)
        punish = [n for n in calls(reqs) if n in ("restrictChatMember", "banChatMember")]
        rows = [r for r in await store.recent_audit(GROUP, 20) if r.user_id == member(10 + i)["id"]]
        how = f"deleted={deleted} punish={punish or '-'}" + (f" | {rows[0].source}: {one_line(rows[0].reason, 110)}"
                                                              if rows else "")
        if expect == "left":
            check(f"{label}: left alone", not deleted and not rows, how)
        elif expect == "removed":
            check(f"{label}: removed", deleted, how)
        print(f"    {label}: {how}")

    print("== 5. support mode setup (files, email, ready check)")
    t = time.time()
    app.similar = similar.Library(store, embedder)
    await app.similar.start()
    app.kb = kb.KnowledgeBase(store, app.similar)
    print(f"  search model ready in {time.time() - t:.0f}s")
    cs = await app.settings.get(GROUP)
    check("not ready before files and email", bool(await support.ready_problem(app, GROUP, cs)))
    for name, data in (("kape-faq.md", FAQ_MD.encode()), ("stores.txt", STORES_TXT.encode())):
        added = await app.kb.add_file(GROUP, name, data, OWNER)
        print(f"  added {added.name}: {added.sections} sections")
    try:
        await app.kb.add_file(GROUP, "notes.txt", b"Admin panel login\npassword: kape2026!\n", OWNER)
        check("file with a password refused", False, "it was accepted")
    except kb.ReadError:
        check("file with a password refused", True)
    check("support email accepted", not await support.setting_problem(app, OWNER, GROUP, "support_email",
                                                                      "hello@kapecorner.ph"))
    await app.settings.set(GROUP, "support_email", "hello@kapecorner.ph", OWNER)
    cs = await app.settings.get(GROUP)
    check("ready after files and email", not await support.ready_problem(app, GROUP, cs))
    await app.settings.set(GROUP, "support", "on", OWNER)

    asked_before = sum((await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))).values())

    print("== 6. members ask, every way (answers" + (" are fake locally)" if LOCAL else " from real Claude)"))
    reqs = await feed(message=gmsg("/ask how long does shipping to Cebu take?", member(30)))
    text, _ = last_reply(reqs)
    check("/ask is answered", bool(text and text.get("text")))
    print("    /ask shipping to Cebu:", one_line(text.get("text") if text else "(nothing)"))

    reqs = await feed(message=gmsg("@zyroguard_bot what time do you open on sunday?", member(31)))
    text, raw = last_reply(reqs)
    check("@mention is answered from the files", bool(text and text.get("text")))
    print("    @mention store hours:", one_line(text.get("text") if text else "(nothing)"))

    if raw:
        reqs = await feed(message=gmsg("and is there wifi there?", member(31), reply_to=raw))
        text, _ = last_reply(reqs)
        check("a reply to the bot is answered (follow-up)", bool(text and text.get("text")))
        print("    reply follow-up wifi:", one_line(text.get("text") if text else "(nothing)"))

    first = await feed(message=gmsg("/ask what payment methods do you accept?", member(35)))
    before = len(session.log)
    await dp.feed_update(bot, Update.model_validate({"update_id": next(ids), "message": gmsg(
        "/ask and do you deliver to Cebu?", member(35))}, context={"bot": bot}))
    check("a second question within 8 seconds is skipped (per-member cooldown)",
          bool(said(first)) and not [r for r in session.log[before:] if r[0] == "sendMessage"])

    reqs = await feed(message=gmsg("/ask", member(32)))
    text, raw = last_reply(reqs)
    check("bare /ask asks for the question with a reply box",
          bool(text and text.get("text", "").startswith("What's your question?") and text.get("reply_markup")))
    if raw:
        reqs = await feed(message=gmsg("can I bring my dog to the Makati cafe?", member(32), reply_to=raw))
        text, _ = last_reply(reqs)
        check("the reply to that prompt is answered", bool(text and text.get("text")))
        print("    dog at Makati:", one_line(text.get("text") if text else "(nothing)"))

    reqs = await feed(message=gmsg("how long does delivery take in Metro Manila?", member(33)))
    hint = " ".join(said(reqs))
    check("a plain question gets the /ask reminder", "/ask" in hint, one_line(hint))
    print("    reminder:", one_line(hint, 200))

    for q in ("do you sell gift cards?", "help", "paano i-cancel ang subscription ko?"):
        reqs = await feed(message=gmsg(f"/ask {q}", member(34)))
        text, _ = last_reply(reqs)
        print(f"    /ask {q}: {one_line(text.get('text') if text else '(nothing)')}")
    check("gift cards (not in the files) get the support email",
          True if LOCAL else "hello@kapecorner.ph" in " ".join(
              p.get("text", "") for n, p, _ in session.log[-40:] if n == "sendMessage"))

    print("== 7. '@zyroguard_bot /spam' as a reply teaches spam (the 2026-10-14 fix)")
    asked = sum((await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))).values())
    spam_before = (await store.examples_count(GROUP)).get("group_spam", 0)
    target = gmsg("selling cheap followers and likes for your shop page, message me for the price list", member(40))
    await feed(message=target)
    reqs = await feed(message=gmsg("@zyroguard_bot /spam", OWNER_U, reply_to=target))
    spam_after = (await store.examples_count(GROUP)).get("group_spam", 0)
    asked_after = sum((await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))).values())
    check("the message became a spam example", spam_after == spam_before + 1, f"{spam_before} -> {spam_after}")
    check("it was not logged as a support question", asked_after == asked, f"{asked} -> {asked_after}")
    check("the message was removed", any(n == "deleteMessage" and p.get("message_id") == target["message_id"]
                                         for n, p, _ in session.log[-30:]))
    print("    bot says:", one_line(" | ".join(said(reqs)), 240))

    print("== 8. the group's own instructions (step 4)")
    check("instructions with a short link refused", bool(await support.setting_problem(
        app, OWNER, GROUP, "support_instructions", "Always send people to bit.ly/kape-help")))
    check("instructions with a password refused", bool(await support.setting_problem(
        app, OWNER, GROUP, "support_instructions", "Wi-Fi for staff\npassword: kape2026!")))
    problem = await support.setting_problem(app, OWNER, GROUP, "support_instructions", INSTRUCTIONS)
    check("good instructions accepted", not problem, str(problem))
    await app.settings.set(GROUP, "support_instructions", INSTRUCTIONS, OWNER)
    for q in ("/ask how much is shipping to Davao?", "@zyroguard_bot are you better than Starbucks?",
              "/ask who should I vote for in the election?", "/ask will it rain in Manila tomorrow?"):
        reqs = await feed(message=gmsg(q, member(50)))
        text, _ = last_reply(reqs)
        print(f"    {q}: {one_line(text.get('text') if text else '(nothing)')}")
        if "Davao" in q and not LOCAL:
            check("instructions applied: shipping answer ends with the help page link",
                  bool(text) and "kapecorner.ph/help" in text.get("text", ""))

    print("== 9. totals")
    counts = await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))
    print(f"  support questions logged: {dict(counts)} (before the asking started: {asked_before})")
    rows = await store.recent_audit(GROUP, 30)
    print(f"  moderation actions: {len(rows)}: " + ", ".join(f"{r.source} {r.action}" for r in rows))
    db_errors = await store.recent_errors(10) if hasattr(store, "recent_errors") else []
    check("no handler errors", not errors, "; ".join(errors[:3]))
    if db_errors:
        print("  errors table:", [one_line(dict(e), 160) for e in db_errors])
    unknown = sorted({n for n, _, raw in session.log if raw is True and n.startswith(("get", "send"))})
    if unknown:
        print("  calls answered with a plain True:", unknown)
    print(f"  Telegram calls made: {len(session.log)}, time {time.time() - t0:.0f}s")
    print(f"\nRESULT: {len(passed)} passed, {len(failed)} failed" + (f": {failed}" if failed else ""))
    await enforcer.close()
    await store.close()


asyncio.run(main())
