"""A brand-new group set up the way a client does it, end to end, with real files.

Adds the bot to a new group, sets a log chat, solves a captcha, checks moderation in shadow and enforce
mode (and presses Why, Teach as spam and Undo on the reports), then runs the whole /zyromod setup in a
private chat: files sent as Telegram documents (one unreadable, one with a password), email, contact
button, instructions, Test a question, Turn on. Then members ask in every way, and the admin uses the
dashboard (Unanswered, a custom can't-answer message, Remove a file).

Same dispatcher and routers as main.py, Telegram faked by tools/fake_telegram.py, temp database,
n8n / mod-log / webhooks off. The files come from client-kit/test-group (E2E_FILES to change).
On the server, in a throwaway copy of the live image (about 25 AI calls, a few US cents):
  docker run --rm --memory=1g --env-file /opt/zyroguard/.env -e PYTHONPATH=/app -e E2E_FILES=/w/files \
    -v /tmp/zge2e:/w:ro zyroguard-zyroguard:latest python /w/e2e_full_setup.py
Locally, fake AI and a fake model (wiring only):  E2E_LOCAL=1 CAS_ENABLED=false python tools/e2e_full_setup.py
"""
from __future__ import annotations

import asyncio
import itertools
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.types import Update  # noqa: E402

from fake_telegram import ADMIN_RIGHTS, FakeTelegram, now  # noqa: E402

LOCAL = os.environ.get("E2E_LOCAL") == "1"
FILES = Path(os.environ.get("E2E_FILES", HERE.parent / "client-kit" / "test-group"))
env = dict(os.environ)
for k in ("N8N_EVENT_URL", "N8N_EVENT_SECRET", "MODLOG_CHAT_ID", "CONTACT_WEBHOOK_URL", "CONTACT_WEBHOOK_SECRET",
          "ALLOWED_CHAT_IDS"):
    env.pop(k, None)   # nothing may leave the test
env["BOT_TOKEN"] = "4242:FAKE-token-for-the-end-to-end-test"
env["DB_PATH"] = os.environ.get("E2E_DB", "/tmp/e2e_full_setup.db")
if LOCAL:
    env.setdefault("OWNER_IDS", "1001")
    env["ANTHROPIC_API_KEY"] = ""
    env["DB_PATH"] = os.environ.get("E2E_DB", str(Path(os.environ.get("TEMP", "/tmp")) / "e2e_full_setup.db"))
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
GROUP, LOGCHAT = -1009990000123, -1009990000124
CHAT = {"id": GROUP, "type": "supergroup", "title": "Lakbay Bikes Riders"}
LOG = {"id": LOGCHAT, "type": "supergroup", "title": "Lakbay Bikes admin log"}
DM = {"id": OWNER, "type": "private", "first_name": "Owner"}
BOT_U = {"id": BOT_ID, "is_bot": True, "first_name": "ZyroGuard", "username": "zyroguard_bot"}
OWNER_U = {"id": OWNER, "is_bot": False, "first_name": "Owner", "username": "groupowner"}
EMAIL, LINK = "help@lakbaybikes.ph", "https://lakbaybikes.ph/support"
INSTRUCTIONS = ("Tone: friendly and simple, like a bike shop staff member. Short answers.\n"
                "Do: end answers about repairs, rentals or prices with our help page https://lakbaybikes.ph/help\n"
                "Don't: compare us with other bike shops or brands. For politics or offensive messages, say you can "
                "only help with Lakbay Bikes questions.")
CUSTOM_SORRY = f"Sorry, I don't have that one yet. Our team can help at {EMAIL}"


def member(n: int) -> dict:
    return {"id": 777000600 + n, "is_bot": False, "first_name": f"Rider{n}", "username": f"rider{n}"}


passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({one_line(detail, 300)})" if detail and not ok else ""))


def one_line(s, n: int = 330) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "~"


async def main() -> None:
    session = FakeTelegram({GROUP: CHAT, LOGCHAT: LOG}, OWNER_U, BOT_U)
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
            "A basic tune-up is **P650**.")), "claude-opus-5", "medium")
        embedder = WordEmbedder()
    else:
        app.assistant = assistant.Assistant(store, app.judge.client, cfg.judge_model, cfg.assistant_effort)
        embedder = similar.Embedder()
    enforcer.exempt = app.is_exempt
    t = time.time()
    app.similar = similar.Library(store, embedder)   # main.run() loads the model in the background at start
    await app.similar.start()
    app.kb = kb.KnowledgeBase(store, app.similar)
    print(f"search model ready in {time.time() - t:.0f}s, files from {FILES}")

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
    ids, mids, cbids, fids = itertools.count(1), itertools.count(100), itertools.count(1), itertools.count(1)

    async def feed(**update) -> int:
        app.assistant._last.clear()   # people take longer than the 8 s cooldown between questions
        mark = len(session.log)
        await dp.feed_update(bot, Update.model_validate({"update_id": next(ids), **update}, context={"bot": bot}))
        return mark

    def msg(text: str, user: dict, chat: dict = CHAT, reply_to: dict | None = None) -> dict:
        d = {"message_id": next(mids), "date": now(), "chat": chat, "from": user, "text": text}
        if reply_to:
            d["reply_to_message"] = reply_to
        return d

    async def say(text: str, user: dict, chat: dict = CHAT, reply_to: dict | None = None) -> tuple[int, dict]:
        m = msg(text, user, chat, reply_to)
        return await feed(message=m), m

    async def send_file(name: str, data: bytes) -> int:
        fid = f"file{next(fids)}"
        session.files[fid] = data
        mime = {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf"}.get(Path(name).suffix,
                                                                                           "application/octet-stream")
        return await feed(message={"message_id": next(mids), "date": now(), "chat": DM, "from": OWNER_U,
                                   "document": {"file_id": fid, "file_unique_id": f"{fid}-u", "file_name": name,
                                                "file_size": len(data), "mime_type": mime}})

    async def press(chat_id: int, label: str, user: dict = OWNER_U) -> int | None:
        found = session.button(chat_id, label)
        if not found:
            check(f"button '{label}' is there", False)
            return None
        message, data = found
        return await feed(callback_query={"id": str(next(cbids)), "from": user, "chat_instance": "ci",
                                          "message": message, "data": data})

    def said(mark: int, chat_id: int | None = None) -> str:
        return " | ".join(session.sent_since(mark, chat_id))

    def last_bot(mark: int, chat_id: int) -> dict | None:
        for name, p, raw in reversed(session.log[mark:]):
            if name == "sendMessage" and p.get("chat_id") == chat_id and isinstance(raw, dict):
                return raw
        return None

    async def ask(text: str, user: dict, reply_to: dict | None = None, label: str | None = None) -> tuple[str, dict | None]:
        mark, _ = await say(text, user, reply_to=reply_to)
        raw = last_bot(mark, GROUP)
        answer = raw["text"] if raw else ""
        print(f"    {label or text}: {one_line(answer) or '(nothing)'}")
        return answer, raw

    t0 = time.time()
    print("== 1. the owner adds ZyroGuard to the new group")
    mark = await feed(my_chat_member={"chat": CHAT, "from": OWNER_U, "date": now(),
                                      "old_chat_member": {"status": "left", "user": BOT_U},
                                      "new_chat_member": {"status": "administrator", "user": BOT_U, **ADMIN_RIGHTS}})
    cs = await app.settings.get(GROUP)
    check("group registered, starts in shadow mode", GROUP in app.allowed_chats and cs.raw("mode") == "shadow")
    print("  bot says:", one_line(said(mark, GROUP), 220))
    mark, _ = await say(f"/set modlog_chat_id {LOGCHAT}", OWNER_U)
    check("reports go to the group's own log chat", (await app.settings.get(GROUP)).modlog_chat_id == LOGCHAT,
          said(mark))
    mark, _ = await say("@zyroguard_bot /diag", OWNER_U)
    check("'@zyroguard_bot /diag' answers with the check", "check" in said(mark, GROUP).lower(), said(mark))

    print("== 2. a new member joins and solves the captcha")
    joiner = member(1)
    mark = await feed(chat_member={"chat": CHAT, "from": joiner, "date": now(),
                                   "old_chat_member": {"status": "left", "user": joiner},
                                   "new_chat_member": {"status": "member", "user": joiner}})
    row = await store.captcha_get(GROUP, joiner["id"])
    check("captcha asked", bool(row) and bool(session.button(GROUP, row["answer"] or "")), said(mark))
    if row:
        print("    captcha:", one_line(said(mark, GROUP), 160), "| answer:", row["answer"])
        await press(GROUP, row["answer"], joiner)
        m = await store.get_member(GROUP, joiner["id"])
        check("right answer lets them in", bool(m and m["verified_at"]) and not await store.captcha_get(GROUP, joiner["id"]))

    print("== 3. moderation in shadow mode: report only")
    mark, scam = await say("We are Royal Dynasty International Co., Ltd. looking for USDT holders, 10% to 25% "
                           "commission per transaction", member(2))
    report = said(mark, LOGCHAT)
    check("shadow: message stays, the log chat gets a 'would' report",
          (GROUP, scam["message_id"]) not in session.deleted and "Would" in report, report)

    print("== 4. enforce mode, then real scams" + ("" if LOCAL else " (real AI judge)"))
    mark, _ = await say("@zyroguard_bot /mode enforce", OWNER_U)
    check("'@zyroguard_bot /mode enforce' switches to enforce", (await app.settings.get(GROUP)).raw("mode") == "enforce",
          said(mark))
    cases = [("crypto pitch", member(3), "Royal Dynasty International is looking for USDT holders, earn 10% to 25% "
                                         "commission per transaction, DM us", True),
             ("fake job", member(4), "Part time job from home, earn $300 per day just liking youtube videos. "
                                     "No experience needed, DM me on WhatsApp", True),
             ("phishing", member(5), "Your Telegram account will be deleted in 24 hours for violating our terms. "
                                     "Send me the login code you just received to verify it is you.", True),
             ("OTP warning", member(6), "Reminder: never share your OTP or login code with anyone, the admins "
                                        "will never DM you first", False)]
    reports = {}
    for label, user, text, spam in cases:
        mark, m = await say(text, user)
        gone = (GROUP, m["message_id"]) in session.deleted
        rows = [r for r in await store.recent_audit(GROUP, 30) if r.user_id == user["id"]]
        how = (f"{rows[0].source} {rows[0].action}: {one_line(rows[0].reason, 100)}" if rows else "no action")
        check(f"{label}: {'removed' if spam else 'left alone'}", gone == spam, how)
        print(f"    {label}: {how}")
        reports[label] = said(mark, LOGCHAT)
    print("    report in the log chat:", one_line(reports["phishing"], 260))
    mark = await press(LOGCHAT, "Why")
    if mark is not None:
        print("    Why:", one_line(said(mark), 220))
        check("Why explains the action", len(said(mark)) > 40, said(mark))
    before = (await store.examples_count(GROUP)).get("group_spam", 0)
    found = session.button(LOGCHAT, "Teach as spam")
    if found:
        await press(LOGCHAT, "Teach as spam")
        after = (await store.examples_count(GROUP)).get("group_spam", 0)
        check("Teach as spam saves an example", after == before + 1, f"{before} -> {after}; {session.alerts[-1:]}")
    crypto_user = member(3)["id"]
    crypto_report = next((m for (c, _), m in sorted(session.messages.items()) if c == LOGCHAT
                          and str(crypto_user) in m.get("text", "") and m.get("reply_markup")), None)
    if crypto_report:
        undo = next(b["callback_data"] for row in crypto_report["reply_markup"]["inline_keyboard"] for b in row
                    if b["text"] == "Undo")
        mark = await feed(callback_query={"id": str(next(cbids)), "from": OWNER_U, "chat_instance": "ci",
                                          "message": crypto_report, "data": undo})
        rows = [r for r in await store.recent_audit(GROUP, 30) if r.user_id == crypto_user]
        check("Undo lifts the mute and marks the action undone",
              bool(rows and rows[0].undone_at) and any(n == "restrictChatMember" for n, _, _ in session.log[mark:]),
              f"{session.alerts[-1:]} {said(mark)}")

    print("== 5. support setup in the private chat (/zyromod)")
    mark, _ = await say("/zyromod", OWNER_U)
    check("/zyromod in the group points to the private chat", "private chat" in said(mark, GROUP), said(mark))
    mark, _ = await say("/start zyromod", OWNER_U, chat=DM)
    text = said(mark, OWNER)
    check("setup starts at step 1 of 4", "Step 1 of 4" in text, text)
    mark = await send_file("lakbay-price-list.docx", b"PK\x03\x04 not really a word file")
    check("a Word file is refused with advice", "Save Word" in said(mark), said(mark))
    for name in ("lakbay-bikes-faq.md", "lakbay-bikes-shop-info.txt", "lakbay-bikes-rentals.pdf"):
        mark = await send_file(name, (FILES / name).read_bytes())
        check(f"{name} added", f"Added {name}" in said(mark), said(mark))
        print("    ", one_line(session.sent_since(mark)[-1], 120))
    mark = await send_file("should-be-refused-staff-notes.txt",
                           (FILES / "should-be-refused-staff-notes.txt").read_bytes())
    check("a file with a password is refused", "password" in said(mark).lower(), said(mark))
    docs = await store.kb_documents(GROUP)
    check("3 files stored, all read with the current reader",
          len(docs) == 3 and all(int(d["reader"] or 1) == kb.READER_VERSION for d in docs), str(len(docs)))
    mark = await press(OWNER, "Done")
    check("Done goes to step 2 of 4 (email)", "Step 2 of 4" in said(mark), said(mark))
    mark, _ = await say("help at lakbaybikes", OWNER_U, chat=DM)
    check("a wrong email is refused", not (await app.settings.get(GROUP)).raw("support_email"), said(mark))
    print("    wrong email:", one_line(said(mark), 150))
    mark, _ = await say(EMAIL, OWNER_U, chat=DM)
    check("email saved, step 3 of 4 (button)", "Step 3 of 4" in said(mark), said(mark))
    check("the email change is reported to the log chat", EMAIL in said(mark, LOGCHAT), said(mark, LOGCHAT))
    mark, _ = await say("bit.ly/lakbay-help", OWNER_U, chat=DM)
    check("a short link is refused", not (await app.settings.get(GROUP)).raw("support_link"), said(mark))
    mark, _ = await say(LINK, OWNER_U, chat=DM)
    check("link saved, step 4 of 4 (instructions)", "Step 4 of 4" in said(mark), said(mark))
    mark, _ = await say(INSTRUCTIONS, OWNER_U, chat=DM)
    check("instructions saved and setup finished", "All set" in said(mark), said(mark))
    print("    can't-answer preview:", one_line(session.sent_since(mark)[-2] if len(session.sent_since(mark)) > 1
                                                 else "", 200))
    mark = await press(OWNER, "Test a question")
    mark, _ = await say("how much is a basic tune-up?", OWNER_U, chat=DM)
    test = session.sent_since(mark, OWNER)
    check("Test a question answers privately", len(test) >= 2 and "Test only" in test[-1], " | ".join(test))
    print("    test answer:", one_line(test[0] if test else "", 220))
    mark, _ = await say("/cancel", OWNER_U, chat=DM)
    check("/cancel stops the test", "Stopped" in said(mark), said(mark))
    mark = await press(OWNER, "Turn on")
    check("Turn on switches support on", (await app.settings.get(GROUP)).bool("support"), str(session.alerts[-1:]))

    print("== 6. members ask" + (" (fake answers)" if LOCAL else " (real Claude answers)"))
    a, raw = await ask("/ask how much is a basic tune-up?", member(10))
    check("/ask answered from the files", bool(a))
    a, raw = await ask("@zyroguard_bot are you open on sunday?", member(11))
    check("@mention answered", bool(a))
    if raw:
        a, _ = await ask("can I buy a bike then?", member(11), reply_to=raw, label="reply: can I buy a bike then?")
        check("reply to the bot answered as a follow-up", bool(a))
    mark, _ = await say("/ask", member(12))
    prompt = last_bot(mark, GROUP)
    check("bare /ask asks for the question", bool(prompt and prompt["text"].startswith("What's your question?")))
    if prompt:
        a, _ = await ask("how much is it to rent an e-bike for a day?", member(12), reply_to=prompt,
                         label="reply to the prompt: e-bike rental (from the PDF)")
        check("the reply to the prompt is answered", bool(a))
    await ask("/ask what do I need to rent a bike?", member(13), label="/ask what do I need to rent (PDF)")
    mark, _ = await say("do you fix e-bike batteries?", member(14))
    check("a plain question gets the /ask reminder", "/ask" in said(mark, GROUP), said(mark))
    a, raw = await ask("/ask do you sell skateboards?", member(15))
    buttons = (raw or {}).get("reply_markup", {}).get("inline_keyboard", [])
    check("not in the files: email and the contact button",
          (EMAIL in a and any(b.get("url") == LINK for row in buttons for b in row)) or LOCAL, a)
    await ask("/ask magkano ang shipping papuntang Davao?", member(16))
    await ask("/ask help", member(17))
    a, _ = await ask("/ask how much is a full tune-up?", member(18))
    check("instructions: a price answer ends with the help page", LOCAL or "lakbaybikes.ph/help" in a, a)
    await ask("@zyroguard_bot is Trek better than your bikes?", member(19))
    await ask("/ask who will win the election?", member(20))

    print("== 7. the admin's dashboard")
    mark, _ = await say("/zyromod", OWNER_U, chat=DM)
    dash = said(mark)
    check("dashboard: on, 3 files, own instructions",
          "<b>On</b>" in dash and dash.count("• ") == 3 and "your own" in dash, dash)
    mark = await press(OWNER, "Unanswered")
    check("Unanswered lists the skateboard question", LOCAL or "skateboards" in said(mark), said(mark))
    await press(OWNER, "Can't-answer message")
    mark, _ = await say(CUSTOM_SORRY, OWNER_U, chat=DM)
    a, _ = await ask("/ask do you sell skateboards?", member(21), label="/ask skateboards (own can't-answer message)")
    check("own can't-answer message, email not repeated", LOCAL or (a.count(EMAIL) == 1 and "Sorry, I don't" in a), a)
    await press(OWNER, "Remove a file")
    mark = await press(OWNER, "Remove lakbay-bikes-shop-info.txt")
    check("a file can be removed", len(await store.kb_documents(GROUP)) == 2, str(session.alerts[-1:]))
    await ask("/ask where is your shop?", member(22), label="/ask where is your shop? (file removed)")

    print("== 8. teaching with the bot tagged in front of the command")
    mark, spam = await say("selling cheap followers and likes for your shop page, message me for the price list",
                           member(30))
    before = (await store.examples_count(GROUP)).get("group_spam", 0)
    asked = sum((await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))).values())
    mark, _ = await say("@zyroguard_bot /spam", OWNER_U, reply_to=spam)
    after = (await store.examples_count(GROUP)).get("group_spam", 0)
    asked2 = sum((await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))).values())
    check("'@zyroguard_bot /spam' teaches, not a support question", after == before + 1 and asked2 == asked,
          f"{before}->{after}, questions {asked}->{asked2}")

    print("== 9. totals")
    counts = await store.kb_question_counts(GROUP, datetime(2000, 1, 1, tzinfo=timezone.utc))
    print(f"  support questions logged: {dict(counts)}")
    print(f"  pop-ups and toasts: {[one_line(a, 60) for a in session.alerts]}")
    db_errors = await store.recent_errors(10)
    check("no handler errors", not errors, "; ".join(errors[:3]))
    check("no errors logged", not db_errors, str([dict(e) for e in db_errors][:2]))
    print(f"  Telegram calls: {len(session.log)}, time {time.time() - t0:.0f}s")
    print(f"\nRESULT: {len(passed)} passed, {len(failed)} failed" + (f": {failed}" if failed else ""))
    await enforcer.close()
    await store.close()


asyncio.run(main())
