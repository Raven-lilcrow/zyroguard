"""Support mode (kb.py + support.py): reading files, cutting and searching them, answering only from
them, the can't-answer message, the /ask pointer, and who may change the contacts.
Uses the word-overlap stand-in embedder, so nothing is downloaded."""
import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.types import ChatMemberAdministrator, Message, User

from zyroguard import kb, support
from zyroguard.assistant import Assistant
from zyroguard.settings import validate
from zyroguard.similar import Library
from zyroguard.store import utcnow

from .test_pipeline_e2e import ADMIN, CHAT, app, msg  # noqa: F401
from .test_similar import WordEmbedder

FAQ = """# Acme Help Center

## Plans and pricing
The Starter plan is free for up to 3 users.
The Pro plan costs $12 per user per month, billed yearly.

### How do I cancel my subscription?
Go to Settings > Billing and press Cancel. You keep access until the end of the billing period.

## Shipping
We ship to the Philippines, Singapore and Malaysia. Orders arrive in 3 to 5 working days.

## Contact
Email help@acme.io or open a ticket at https://acme.io/help
"""


def make_pdf(pages: list[str]) -> bytes:
    """A tiny real PDF with one text line per line of each page (Helvetica)."""
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, text in enumerate(pages):
        ops = ["BT /F1 11 Tf 50 750 Td 14 TL"]
        for line in text.split("\n"):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects[4 + 2 * i] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R"
                              f" >> >> /Contents {5 + 2 * i} 0 R >>").encode()
        objects[5 + 2 * i] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for k in sorted(objects):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n".encode() + objects[k] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for k in range(1, size):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---- reading and cutting -------------------------------------------------------------

def test_markdown_sections_keep_the_heading_trail():
    parts = kb.sections(kb.tidy(FAQ), "md")
    heads = [h for h, _ in parts]
    assert "Acme Help Center > Plans and pricing" in heads
    assert "Acme Help Center > Plans and pricing > How do I cancel my subscription?" in heads
    contact = dict(parts)["Acme Help Center > Contact"]
    assert "https://acme.io/help" in contact
    assert all(len(body) <= kb.CHUNK_CHARS for _, body in parts)


def test_plain_text_faq_questions_become_headings():
    text = "FREQUENTLY ASKED QUESTIONS\n\nQ: Where do you ship?\nTo the Philippines.\n\nCan I return an item?\nYes, within 30 days."
    parts = kb.sections(text, "txt")
    assert parts == [("Where do you ship?", "To the Philippines."), ("Can I return an item?", "Yes, within 30 days.")]


def test_long_text_is_split_at_sentences():
    parts = kb.sections("This is one sentence about our product. " * 60, "pdf")
    assert len(parts) > 3 and all(len(b) <= kb.CHUNK_CHARS for _, b in parts)
    assert all(b.endswith(".") for _, b in parts)


def test_read_text_files_and_refuse_other_kinds():
    assert kb.read_file("faq.md", "Caf\u00e9 menu".encode("utf-8")) == ("Caf\u00e9 menu", None)
    assert kb.read_file("faq.txt", "Caf\u00e9".encode("cp1252"))[0] == "Caf\u00e9"
    for name, data, words in [("faq.docx", b"x", "save word"), ("faq.txt", b"", "empty"),
                              ("faq.txt", b"\x00\x01binary", "plain text")]:
        with pytest.raises(kb.ReadError) as e:
            kb.read_file(name, data)
        assert all(w in str(e.value).lower() for w in words.split())


def test_read_pdf_text_and_refuse_scans_and_passwords():
    pdf = make_pdf(["How do I reset my password?\nOpen Settings and tap Reset password.", "Page two text here."])
    text, pages = kb.read_file("guide.pdf", pdf)
    assert pages == 2 and "Reset password" in text
    heads = [h for h, _ in kb.sections(text, "pdf")]
    assert "How do I reset my password?" in heads
    with pytest.raises(kb.ReadError, match="scanned"):
        kb.read_file("scan.pdf", make_pdf(["", ""]))
    with pytest.raises(kb.ReadError, match="could not open"):
        kb.read_file("broken.pdf", b"%PDF-1.4 not really")
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(kb.ReadError, match="password"):
        kb.read_file("locked.pdf", buf.getvalue())


def test_secrets_are_refused_but_normal_help_text_is_not():
    assert kb.secret_in("key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz") == "an API key"
    assert kb.secret_in("password: hunter22") == "a password"
    assert kb.secret_in("-----BEGIN RSA PRIVATE KEY-----") == "a private key"
    assert kb.secret_in("Password: must be at least 8 characters long") is None
    assert kb.secret_in("Reset your password: open Settings") is None


def test_clean_name():
    assert kb.clean_name("C:\\docs\\faq.md") == "faq.md"
    assert kb.clean_name(None) == "file.txt"


# Markdown the way other tools write it (Notion, Google Docs, GitBook, GitHub), so any group's files read cleanly
EXPORTED = """---
title: Help
description: Everything about orders, shipping and returns.
---

# 🛒 Help Center

### Shipping & Delivery

**How long does delivery take?**

Orders inside Metro Manila arrive in *1-2 days*. Provinces take 3 to 5 days.

| Area | Days | Fee |
|---|---|---|
| Metro Manila | 1-2 | ₱99 |

> Free shipping on orders over ₱2,000.

- [ ] Track your order at [our tracking page](https://shop.example.ph/track)
* Returns: see [Returns policy](returns.md#refunds)

<details>
<summary>Can I change my address?</summary>
Yes, within 1 hour of ordering.
</details>

{% hint style="warning" %}
Cash on delivery is **not** available over ₱10,000.
{% endhint %}

{% tabs %}
{% tab title="Android" %}
Open the app and tap **Orders**.
{% endtab %}
{% endtabs %}

```bash
# check your order status
curl https://shop.example.ph/api/order/123   # needs your order number
```

***

Payments
========
Price is \\$10 \\- paid monthly. **Important note.**
"""


def test_markdown_from_any_tool_reads_cleanly():
    parts = kb.sections(kb.tidy(EXPORTED), "md")
    heads = [h for h, _ in parts]
    body = {h: b for h, b in parts}
    assert parts[0] == (None, "Everything about orders, shipping and returns.")       # front matter description only
    # a skipped heading level stays empty, the emoji goes, a bold question line is a sub-heading
    assert "Help Center > Shipping & Delivery > How long does delivery take?" in heads
    first = body["Help Center > Shipping & Delivery > How long does delivery take?"]
    assert "arrive in 1-2 days." in first                                            # italics gone
    assert "Area: Metro Manila; Days: 1-2; Fee: ₱99" in first                          # tables as rows
    assert "\nFree shipping on orders over ₱2,000." in first                          # quote marker gone
    assert "• Track your order at our tracking page (https://shop.example.ph/track)" in first
    assert "• Returns: see Returns policy" in first                                   # links inside the docs: words only
    address = body["Help Center > Shipping & Delivery > Can I change my address?"]
    assert address.startswith("Yes, within 1 hour") and "Cash on delivery is not available" in address
    assert body["Help Center > Shipping & Delivery > Android"] == "Open the app and tap Orders."
    code = body["Help Center > Shipping & Delivery"]
    assert code == ("check your order status\n"
                    "`curl https://shop.example.ph/api/order/123` (needs your order number)")
    assert body["Payments"] == "Price is $10 - paid monthly. Important note."           # escapes and setext title
    every = "\n".join(f"{h}\n{b}" for h, b in parts)
    for junk in ("**", "{%", "<summary", "|---", "```", "---", "\\$"):
        assert junk not in every, junk


def test_code_comments_are_not_headings_and_paths_stay_short():
    md = ("# Guide\n## Setup\n### Install\n#### On a Mac\n```sh\n# One-off: launch it\nrun --fast\n```\n"
          "Then sign in.\n\n**Done.** You are ready.\n")
    parts = kb.sections(md, "md")
    assert parts == [("Setup > Install > On a Mac",
                      "One-off: launch it\n`run --fast`\nThen sign in.\nDone. You are ready.")]
    usage = "## Commands\n```\nusage: tool <command>\n\ncommands:\n  login        sign in\n  proxy run    start it\n```\n"
    assert kb.sections(usage, "md")[0][1] == ("`usage: tool <command>`\ncommands:\n`login`: sign in\n"
                                              "`proxy run`: start it")


def test_long_lists_are_cut_between_items():
    md = "## Steps\n" + "\n".join(f"{i}. Step number {i} of the long guide, with enough words to fill the line."
                                  for i in range(1, 20))
    parts = kb.sections(md, "md")
    assert len(parts) > 1
    for _, body in parts:
        assert all(line.split(". ", 1)[0].isdigit() for line in body.split("\n"))   # no step is cut in half


def test_pdf_headers_footers_and_broken_words_are_cleaned():
    pages = [
        "Acme Store Help Guide\nShipping Options\nWe ship to every province in the Philip-\npines. Delivery takes "
        "three to five working days for most orders.\n- Standard delivery is free over 2,000 pesos\n"
        "- Express delivery costs 150 pesos\nPage 1 of 4",
        "Acme Store Help Guide\nReturns\nYou can return any item within 30 days of delivery as long as it is "
        "unused.\nRefunds go back to the card you paid with within seven working days.\nPage 2 of 4",
        "Acme Store Help Guide\nHow do I track my order?\nOpen the Orders page in the app and tap the order you "
        "want to follow.\nPage 3 of 4",
        "Acme Store Help Guide\nPAYMENT METHODS\nWe accept cards, GCash and Maya.\nPage 4 of 4",
    ]
    text, n = kb.read_file("guide.pdf", make_pdf(pages))
    assert n == 4 and "Acme Store Help Guide" not in text and "Page 2 of 4" not in text
    assert "Philippines" in text
    parts = dict(kb.sections(text, "pdf"))
    assert parts["Shipping Options"].endswith("most orders.\n• Standard delivery is free over 2,000 pesos\n"
                                              "• Express delivery costs 150 pesos")
    assert parts["Returns"].startswith("You can return any item") and "seven working days." in parts["Returns"]
    assert "How do I track my order?" in parts and "Payment Methods" in parts


def test_plain_text_bullets_are_marked():
    parts = kb.sections("Delivery:\n- Metro Manila: 1-2 days\n* Provinces: 3-5 days", "txt")
    assert parts == [("Delivery", "• Metro Manila: 1-2 days\n• Provinces: 3-5 days")]


# ---- the library ------------------------------------------------------------------------------

@pytest.fixture
async def base(app):  # noqa: F811
    library = Library(app.store, WordEmbedder())
    await library.start()
    app.similar = library
    app.kb = kb.KnowledgeBase(app.store, library)
    return app.kb


async def test_add_search_replace_and_delete(app, base):  # noqa: F811
    added = await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    assert added.sections >= 4 and not added.replaced
    index, hits = await base.search(CHAT, "How do I cancel my subscription?")
    assert "cancel" in index.texts[hits[0].row].lower() or "cancel" in (index.headings[hits[0].row] or "").lower()
    assert kb.relevant(hits)
    _, far = await base.search(CHAT, "zebra quantum volcano sandwich")
    assert not kb.relevant(far)
    # the same text again is refused, a new version with the same name replaces the old one
    with pytest.raises(kb.ReadError, match="already have"):
        await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    again = await base.add_file(CHAT, "faq.md", (FAQ + "\n## Refunds\nRefunds take 7 days.").encode(), ADMIN)
    assert again.replaced
    docs = await app.store.kb_documents(CHAT)
    assert len(docs) == 1 and docs[0]["sections"] == again.sections
    # another group sees nothing of it
    assert (await base.search(-100777, "cancel subscription")) == (None, [])
    assert await app.store.kb_delete_document(-100777, docs[0]["id"]) is None
    assert await app.store.kb_delete_document(CHAT, docs[0]["id"]) == "faq.md"
    base.invalidate(CHAT)
    assert (await base.search(CHAT, "cancel subscription")) == (None, [])


async def test_secret_files_and_too_much_text_are_refused(app, base, monkeypatch):  # noqa: F811
    with pytest.raises(kb.ReadError, match="password"):
        await base.add_file(CHAT, "notes.txt", b"Admin login\npassword: hunter22\n", ADMIN)
    monkeypatch.setattr(kb, "MAX_GROUP_CHARS", 100)
    with pytest.raises(kb.ReadError, match="more text"):
        await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    assert await app.store.kb_documents(CHAT) == []


async def test_excerpts_small_base_is_sent_whole(app, base):  # noqa: F811
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    index, hits = await base.search(CHAT, "shipping")
    ex = kb.excerpts(index, hits)
    joined = "\n".join(e.text for e in ex)
    assert "Philippines" in joined and "Pro plan" in joined   # small file: everything goes
    assert ex[0].name == "faq.md"


async def test_excerpts_large_base_takes_best_and_neighbours(app, base, monkeypatch):  # noqa: F811
    monkeypatch.setattr(kb, "SMALL_KB_CHARS", 10)
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    index, hits = await base.search(CHAT, "ship to Singapore Malaysia")
    ex = kb.excerpts(index, hits[:1], budget=600)
    joined = "\n".join(e.text for e in ex)
    assert "Singapore" in joined and len(joined) < 900


# ---- what members see -----------------------------------------------------------------------

class FakeMessages:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


def _assistant(app, text):  # noqa: F811
    a = Assistant(app.store, SimpleNamespace(messages=FakeMessages(text)), "claude-opus-5", "medium")
    app.assistant = a
    return a.client.messages


async def _ready(app, base, **settings):  # noqa: F811
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    await app.settings.set(CHAT, "support_email", "help@acme.io", ADMIN)
    for k, v in settings.items():
        await app.settings.set(CHAT, k, v, ADMIN)
    await app.settings.set(CHAT, "support", "on", ADMIN)
    return await app.settings.get(CHAT)


async def test_unrelated_question_gets_the_fallback_without_asking_claude(app, base):  # noqa: F811
    cs = await _ready(app, base)
    calls = _assistant(app, "should not be used")
    r = await support.resolve(app, CHAT, 5, "@u", "zebra quantum volcano sandwich", cs, "Acme")
    assert r.status == "no_match" and calls.calls == []
    assert "help@acme.io" in r.text and support.DEFAULT_SORRY in r.text


async def test_answer_comes_from_the_files_with_links_checked(app, base):  # noqa: F811
    cs = await _ready(app, base)
    calls = _assistant(app, "Go to Settings > Billing and press Cancel. Details: https://acme.io/help or evil.example.com")
    r = await support.resolve(app, CHAT, 5, "@u", "How do I cancel my subscription?", cs, "Acme")
    assert r.status == "answered"
    assert "https://acme.io/help" in r.text and "evil.example.com" not in r.text and "[link removed]" in r.text
    sent = calls.calls[0]
    assert sent["model"] == "claude-opus-5" and sent["output_config"] == {"effort": "medium"}
    assert "NO_ANSWER" in sent["system"][0]["text"]
    # a small file goes whole into the cached system prompt; the question turn stays small
    assert '<document name="faq.md">' in sent["system"][1]["text"]
    assert sent["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "<document" not in sent["messages"][-1]["content"]
    # the short memory keeps the question, not the excerpts
    remembered = app.assistant.history(CHAT, 5)
    assert "<document" not in remembered[0]["content"] and "cancel" in remembered[0]["content"]
    assert await app.store.assistant_calls_since(CHAT, utcnow() - timedelta(days=1)) == 1


async def test_big_files_send_only_the_best_sections_with_the_question(app, base, monkeypatch):  # noqa: F811
    monkeypatch.setattr(kb, "SMALL_KB_CHARS", 10)
    cs = await _ready(app, base)
    calls = _assistant(app, "Orders arrive in 3 to 5 working days.")
    r = await support.resolve(app, CHAT, 5, "@u", "Do you ship to Singapore and Malaysia?", cs, "Acme")
    assert r.status == "answered"
    sent = calls.calls[0]
    assert len(sent["system"]) == 1
    assert '<document name="faq.md">' in sent["messages"][-1]["content"] and "Singapore" in sent["messages"][-1]["content"]


async def test_no_answer_and_partial_answers(app, base):  # noqa: F811
    cs = await _ready(app, base, support_link="https://acme.io/help", support_link_label="Open a ticket")
    _assistant(app, "NO_ANSWER")
    r = await support.resolve(app, CHAT, 5, "@u", "How do I cancel my subscription?", cs, "Acme")
    assert r.status == "not_in_files" and "help@acme.io" in r.text
    _assistant(app, "The Pro plan is $12 per user per month. I could not find team discounts.\nNEED_TEAM")
    r = await support.resolve(app, CHAT, 6, "@v", "The Pro plan costs how much per user per month, and is there a team discount?", cs, "Acme")
    assert r.status == "partial" and "NEED_TEAM" not in r.text
    assert r.text.endswith("For the rest, email the team at help@acme.io or tap the button below.")
    markup = support.contact_markup(cs)
    assert markup.inline_keyboard[0][0].text == "Open a ticket"
    assert markup.inline_keyboard[0][0].url == "https://acme.io/help"


async def test_answer_records_the_question_and_replies(app, base, monkeypatch):  # noqa: F811
    cs = await _ready(app, base)
    _assistant(app, "NO_ANSWER")
    replies = []

    async def fake_reply(self, text, **kw):
        replies.append((text, kw))
    monkeypatch.setattr(Message, "reply", fake_reply)
    await support.answer(app, msg("/ask do you sell gift cards?"), "do you sell gift cards?", cs)
    rows = await app.store.kb_unanswered(CHAT, utcnow() - timedelta(days=1))
    assert len(rows) == 1 and rows[0]["question"] == "do you sell gift cards?"
    assert "help@acme.io" in replies[-1][0]
    counts = await app.store.kb_question_counts(CHAT, utcnow() - timedelta(days=1))
    assert support.digest_line(counts).startswith("Support questions: 0 answered, 1 I couldn't answer")


def test_fallback_default_and_custom():
    from zyroguard.settings import ChatSettings
    cs = ChatSettings(CHAT, {"support_email": "help@acme.io"}, "shadow")
    assert support.fallback_text(cs) == "I don't have the answer to that one.\nEmail the team at help@acme.io."
    assert support.partial_footer(cs) == "For the rest, email the team at help@acme.io."
    assert support.contact_markup(cs) is None
    cs = ChatSettings(CHAT, {"support_email": "help@acme.io", "support_text": "No idea <sorry>!",
                             "support_link": "https://acme.io/t"}, "shadow")
    assert support.fallback_text(cs) == ("No idea &lt;sorry&gt;!\n"
                                         "Email the team at help@acme.io or tap the button below.")
    assert support.contact_markup(cs).inline_keyboard[0][0].text == "Contact the team"
    cs = ChatSettings(CHAT, {"support_link": "https://acme.io/t"}, "shadow")   # a test before the email is set
    assert support.fallback_text(cs).endswith("\nTap the button below to reach the team.")


def test_parse_reply_and_link_check():
    assert support.parse_reply("NO_ANSWER") == ("none", "")
    assert support.parse_reply(" no_answer. ")[0] == "none"
    assert support.parse_reply("Yes.\nNEED_TEAM") == ("partial", "Yes.")
    assert support.parse_reply("It is $12.") == ("full", "It is $12.")
    known = "visit https://acme.io/help or mail help@acme.io"
    assert support.keep_known_links("See acme.io/help.", known) == "See acme.io/help."
    assert support.keep_known_links("See scam.io now", known) == "See [link removed] now"
    assert support.keep_known_links("It costs 12.99 a month, e.g. yearly", known) == "It costs 12.99 a month, e.g. yearly"
    assert support.keep_known_links("Mail help@acme.io", known) == "Mail help@acme.io"


def test_answers_are_formatted_for_telegram_without_filler():
    raw = ("Great question! Based on the documents, the **minimum** is $10 — paid in `USDC`.\n\nSteps:\n"
           "1) Open Settings → Wallet\n2. Tap Withdraw\n- fast\n* cheap\n\n"
           "Hope this helps! Let me know if you need anything else.")
    assert support.format_answer(raw) == ("The <b>minimum</b> is $10, paid in <code>USDC</code>.\n\nSteps:\n"
                                          "1. Open Settings &gt; Wallet\n2. Tap Withdraw\n• fast\n• cheap")
    assert support.format_answer("You're welcome! Happy to help.") == "You're welcome!"
    assert support.format_answer("Happy to help!") == "Happy to help!"          # never trimmed to nothing
    out = support.format_answer("## Withdrawals\nRun this:\n```\ntool doctor <id>\n```\n"
                                "Use <b>this</b> & *really* fast, see my_var_name and **`tool status`**")
    assert out == ("<b>Withdrawals</b>\nRun this:\n<pre>tool doctor &lt;id&gt;</pre>\n"
                   "Use &lt;b&gt;this&lt;/b&gt; &amp; really fast, see my_var_name and "
                   "<b><code>tool status</code></b>")
    assert support.format_answer("| Plan | Price |\n|---|---|\n| Pro | $12 |") == "Plan, Price\nPro, $12"


async def test_files_can_be_sent_again_after_a_reader_update(app, base, monkeypatch):  # noqa: F811
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    with pytest.raises(kb.ReadError, match="already have"):
        await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    monkeypatch.setattr(kb, "READER_VERSION", kb.READER_VERSION + 1)
    again = await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    assert again.replaced
    assert (await app.store.kb_documents(CHAT))[0]["reader"] == kb.READER_VERSION


async def test_menu_is_plain_and_asks_to_resend_old_files(app, base):  # noqa: F811
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    text, markup = await support.dashboard(app, CHAT)
    assert "• faq.md (1 KB)" in text and "send it again" not in text
    assert "Status: <b>Off</b>" in text and text.endswith("Next: set the support email.")
    assert not any(ch in text for ch in "·→›«") and "sections" not in text
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert {"Add files", "Set email", "Contact button", "Turn on", "/ask reminders: On"} <= set(labels)
    await app.store.db.execute("UPDATE kb_documents SET reader=1")   # a file read before the 2026-10-13 reader
    await app.store.db.commit()
    text, _ = await support.dashboard(app, CHAT)
    assert "• faq.md (1 KB), send it again" in text and "for cleaner answers" in text


def test_question_detection_and_hint():
    assert support.looks_like_question("how much is the pro plan")
    assert support.looks_like_question("Magkano po ang shipping?")
    assert not support.looks_like_question("gm everyone")
    assert not support.looks_like_question("ok?")
    hint = support.hint_text("Ana", "how much is the pro plan? see scam.io")
    assert hint == ("Hi Ana! If you want an answer from me, start your question with /ask. Tap to copy:\n"
                    "<code>/ask how much is the pro plan? see</code>")


# ---- who may change what -------------------------------------------------------------------

def _admin(uid, promote):
    return ChatMemberAdministrator(
        user=User(id=uid, is_bot=False, first_name="a"), can_be_edited=False, is_anonymous=False,
        can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True, can_restrict_members=True,
        can_promote_members=promote, can_change_info=False, can_invite_users=True, can_post_stories=False,
        can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False)


async def test_only_full_admins_change_contacts(app, base, monkeypatch):  # noqa: F811
    async def admins(chat_id, force=False):
        return {222: _admin(222, False), 333: _admin(333, True)}
    monkeypatch.setattr(app.admins, "admins", admins)
    assert not await support.may_edit_contacts(app, 222, CHAT)
    assert await support.may_edit_contacts(app, 333, CHAT)
    assert await support.may_edit_contacts(app, ADMIN, CHAT)        # bot owner
    assert not await support.may_edit_contacts(app, 444, CHAT)      # not an admin at all
    assert await support.setting_problem(app, 222, CHAT, "support_email", "x@y.io") == support.ONLY_FULL_ADMINS
    assert await support.setting_problem(app, 222, CHAT, "support_hint", "off") is None
    assert await support.setting_problem(app, 333, CHAT, "support_email", "x@y.io") is None


async def test_turning_on_needs_files_and_email(app, base):  # noqa: F811
    assert "files first" in await support.setting_problem(app, ADMIN, CHAT, "support", "on")
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    assert "email first" in await support.setting_problem(app, ADMIN, CHAT, "support", "yes")
    await app.settings.set(CHAT, "support_email", "help@acme.io", ADMIN)
    assert await support.setting_problem(app, ADMIN, CHAT, "support", "on") is None
    await app.settings.set(CHAT, "support", "on", ADMIN)
    assert "needed while support is on" in await support.setting_problem(app, ADMIN, CHAT, "support_email", None)
    assert "needed while support is on" in await support.setting_problem(app, ADMIN, CHAT, "support_email", "none")
    assert await support.setting_problem(app, ADMIN, CHAT, "support", "off") is None


async def test_blocked_sites_cannot_be_the_contact_link(app, base):  # noqa: F811
    await app.store.set_domain(CHAT, "badsite.com", "block", ADMIN)
    assert "block list" in await support.setting_problem(app, ADMIN, CHAT, "support_link", "https://help.badsite.com/x")
    assert await support.setting_problem(app, ADMIN, CHAT, "support_link", "https://acme.io/help") is None


async def test_contact_changes_are_reported(app, base):  # noqa: F811
    user = User(id=ADMIN, is_bot=False, first_name="Ana", username="ana")
    await support.report_change(app, CHAT, "support_email", "", "help@acme.io", user)
    sent = [c for c in app.bot.calls if c[0] == "send_message"]
    text = sent[-1][1][1]
    assert "Support contact changed" in text and "help@acme.io" in text and "Changed by @ana" in text
    assert "·" not in text and str(ADMIN) not in text   # no bare ids in reports
    n = len(app.bot.calls)
    await support.report_change(app, CHAT, "support_email", "a@b.io", "a@b.io", user)
    assert len(app.bot.calls) == n   # nothing changed, nothing posted


def test_email_and_link_validation():
    assert validate("support_email", "mailto:Help@Acme.io") == "Help@Acme.io"
    assert validate("support_link", "discord.gg/acme") == "https://discord.gg/acme"
    assert validate("support_link", "none") == ""
    for key, value in [("support_email", "nope"), ("support_link", "bit.ly/abc"), ("support_link", "not a link"),
                       ("support_link_label", "x" * 41), ("support_text", "y" * 501)]:
        with pytest.raises(ValueError):
            validate(key, value)


# ---- the /ask pointer in the group ------------------------------------------------------------

async def test_pointer_to_ask_once_per_person(app, base, monkeypatch):  # noqa: F811
    await _ready(app, base)
    support._hinted.clear()
    support._chat_hinted.clear()
    replies = []

    async def fake_reply(self, text, **kw):
        replies.append(text)
        return SimpleNamespace(message_id=77)
    monkeypatch.setattr(Message, "reply", fake_reply)
    assert await support.after_clean(app, msg("How do I cancel my subscription?", user_id=5001))
    assert "<code>/ask How do I cancel my subscription?</code>" in replies[-1]
    support._chat_hinted.clear()
    assert not await support.after_clean(app, msg("How do I cancel my subscription now?", user_id=5001))
    # small talk and unrelated questions are left alone
    assert not await support.after_clean(app, msg("gm everyone", user_id=5002))
    assert not await support.after_clean(app, msg("is anyone watching the zebra quantum volcano game?", user_id=5003))
    # @mentioning me is not a pointer case: support leaves it to the answer path
    support._chat_hinted.clear()
    n = len(replies)
    assert not await support.after_clean(app, msg("@ZyroGuardBot what does pro cost", user_id=5004))
    assert len(replies) == n
    # support off: nothing happens here, the normal assistant takes over
    await app.settings.set(CHAT, "support", "off", ADMIN)
    assert not await support.after_clean(app, msg("@ZyroGuardBot what does pro cost", user_id=5005))


def _reply_to_bot(text: str, user_id: int = ADMIN):
    from .test_pipeline_e2e import BOT
    bot_msg = {"message_id": 1001, "date": 0, "chat": {"id": CHAT, "type": "supergroup", "title": "Test group"},
               "from": {"id": BOT, "is_bot": True, "first_name": "ZyroGuard"}, "text": "What's your question?"}
    return msg(text, user_id=user_id, message_id=43, reply_to_message=bot_msg)


async def test_mentions_replies_and_bare_ask_are_answered_from_the_files(app, base, monkeypatch):  # noqa: F811
    from aiogram.filters import CommandObject
    from zyroguard.assistant import cmd_ask
    from zyroguard.pipeline import _handle
    cs = await _ready(app, base)
    calls = _assistant(app, "Go to Settings > Billing and press **Cancel**.")
    replies = []

    async def fake_reply(self, text, **kw):
        replies.append((text, kw))
        return SimpleNamespace(message_id=88)
    monkeypatch.setattr(Message, "reply", fake_reply)
    # "@bot /ask ..." is not a Telegram command, but it is answered like one, from the files
    await _handle(msg("@ZyroGuardBot /ask how do I cancel my subscription?", user_id=ADMIN), app, edited=False)
    assert len(calls.calls) == 1 and "NO_ANSWER" in calls.calls[-1]["system"][0]["text"]
    assert calls.calls[-1]["messages"][-1]["content"].endswith("Question from @spammy:\nhow do I cancel my subscription?")
    assert replies[-1][0] == "Go to Settings &gt; Billing and press <b>Cancel</b>."
    # /ask alone (the command menu sends it bare) asks for the question with Telegram's reply box
    app.assistant._last.clear()
    await cmd_ask(msg("/ask", user_id=ADMIN), app, CommandObject(prefix="/", command="ask", args=None))
    text, kw = replies[-1]
    assert text.startswith("What's your question?") and kw["reply_markup"].force_reply
    assert len(calls.calls) == 1
    # the member's reply to that prompt is the question
    await _handle(_reply_to_bot("can I cancel anytime?"), app, edited=False)
    assert len(calls.calls) == 2 and replies[-1][0].startswith("Go to Settings")
    assert cs.bool("support")


def test_custom_message_with_the_email_inside_is_not_repeated():
    from zyroguard.settings import ChatSettings
    custom = "Sorry, I can't help with that.\nEmail: help@acme.io"
    cs = ChatSettings(CHAT, {"support_email": "help@acme.io", "support_text": custom}, "shadow")
    assert support.fallback_text(cs) == custom
    cs = ChatSettings(CHAT, {"support_email": "Help@Acme.io", "support_text": custom,
                             "support_link": "https://acme.io/t"}, "shadow")
    assert support.fallback_text(cs) == custom + "\nTap the button below to reach the team."


async def test_purge_removes_support_rows(app, base):  # noqa: F811
    await base.add_file(CHAT, "faq.md", FAQ.encode(), ADMIN)
    await app.store.kb_question_add(CHAT, 5, "hello?", "no_match", 0.1)
    await app.store.purge_chat(CHAT)
    assert await app.store.kb_documents(CHAT) == []
    assert await app.store.kb_chunks(CHAT, base.model) == []
    assert await app.store.kb_question_counts(CHAT, utcnow() - timedelta(days=1)) == {}


async def test_a_vague_question_gets_a_clarifying_question_about_the_files_only(app, base):  # noqa: F811
    # 2026-10-13: support mode keeps its questions, not just its answers, inside the files
    cs = await _ready(app, base)
    calls = _assistant(app, "Do you mean the Pro plan price or cancelling your subscription?")
    r = await support.resolve(app, CHAT, 5, "@u", "how much is the plan and how do I stop it?", cs, "Acme")
    assert r.status == "answered" and "Pro plan" in r.text and "help@acme.io" not in r.text
    prompt = calls.calls[0]["system"][0]["text"]
    assert "Everything you ask stays inside the documents" in prompt
    assert "Never offer an option, product or topic the documents don't cover" in prompt


def test_learning_features_ship_switched_off():
    from zyroguard.settings import SPECS
    assert SPECS["similarity_learn"].default == "off" and SPECS["judge_examples"].default == "off"
    assert SPECS["support"].default == "off"


# ---- the group's own instructions (/zyromod step 4) ----------------------------------------------

RULES = ("Tone: friendly, simple words.\nDo: end answers with our guide https://docs.acme.io/guide\n"
         "Don't: mention competitors.")


async def test_instructions_reach_the_model_and_their_links_are_kept(app, base):  # noqa: F811
    cs = await _ready(app, base, support_instructions=RULES)
    calls = _assistant(app, "Press Cancel in Settings > Billing. More: https://docs.acme.io/guide or evil.example.com")
    r = await support.resolve(app, CHAT, 5, "@u", "How do I cancel my subscription?", cs, "Acme")
    assert r.status == "answered"
    assert "https://docs.acme.io/guide" in r.text and "evil.example.com" not in r.text
    system = calls.calls[0]["system"]
    assert [("cache_control" in b) for b in system] == [False, False, True]   # prompt, instructions, files (cached)
    assert "<group_instructions>\n" + RULES in system[1]["text"] and "Acme" in system[1]["text"]
    assert "group_instructions" in system[0]["text"]                          # the rules say how to use them
    # with instructions, even a question far from the files goes to the AI, so the group's rules
    # (like "say you can't help with politics") apply instead of the plain can't-answer message
    calls = _assistant(app, "I can't help with that.")
    r = await support.resolve(app, CHAT, 7, "@w", "zebra quantum volcano sandwich", cs, "Acme")
    assert len(calls.calls) == 1 and r.status == "answered" and r.text == "I can't help with that."
    # without instructions there is no instructions block at all
    await app.settings.reset(CHAT, "support_instructions")
    calls = _assistant(app, "Go to Settings.")
    await support.resolve(app, CHAT, 6, "@v", "How do I cancel my subscription?", await app.settings.get(CHAT), "Acme")
    system = calls.calls[0]["system"]
    assert len(system) == 2 and "<group_instructions>" not in system[1]["text"]


async def test_instructions_are_checked_before_saving(app, base, monkeypatch):  # noqa: F811
    async def admins(chat_id, force=False):
        return {222: _admin(222, False)}
    monkeypatch.setattr(app.admins, "admins", admins)
    problem = support.setting_problem
    assert await problem(app, 222, CHAT, "support_instructions", RULES) == support.ONLY_FULL_ADMINS
    assert await problem(app, ADMIN, CHAT, "support_instructions", RULES) is None
    assert "short link" in await problem(app, ADMIN, CHAT, "support_instructions", "Guide: bit.ly/abc")
    await app.store.set_domain(CHAT, "badsite.com", "block", ADMIN)
    assert "block list" in await problem(app, ADMIN, CHAT, "support_instructions", "See https://x.badsite.com/a.")
    assert "API key" in await problem(app, ADMIN, CHAT, "support_instructions", "key sk-ant-api03-abcdefghijklmnopqrstuv")
    with pytest.raises(ValueError, match="1500"):
        validate("support_instructions", "x" * 1501)
    # changes are reported like contact changes, long text shortened
    user = User(id=ADMIN, is_bot=False, first_name="Ana", username="ana")
    await support.report_change(app, CHAT, "support_instructions", "", "y" * 900, user)
    text = [c for c in app.bot.calls if c[0] == "send_message"][-1][1][1]
    assert "Support instructions changed" in text and "y" * 300 + "..." in text and "y" * 301 not in text


class _PrivateChat:
    """The admin's private chat with the bot: records what the bot sends there."""

    def __init__(self):
        from aiogram.enums import ChatType
        self.chat = SimpleNamespace(type=ChatType.PRIVATE, id=ADMIN)
        self.from_user = User(id=ADMIN, is_bot=False, first_name="Ana", username="ana")
        self.sent: list[tuple[str, dict]] = []
        self.text = ""

    async def answer(self, text, **kw):
        self.sent.append((text, kw))
        return SimpleNamespace(message_id=1)

    async def edit_text(self, text, **kw):
        self.sent.append((text, kw))

    async def delete(self):
        pass


def _tap(chat: _PrivateChat, data: str):
    async def answer(text=None, **kw):
        chat.sent.append((f"(popup) {text or ''}", kw))
    return SimpleNamespace(data=data, message=chat, from_user=chat.from_user, answer=answer)


def _buttons(kw) -> list[str]:
    markup = kw.get("reply_markup")
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


async def test_setup_step_4_instructions_can_be_set_skipped_and_removed(app, base, monkeypatch):  # noqa: F811
    import zyroguard.callbacks
    monkeypatch.setattr(zyroguard.callbacks, "genuine", lambda cb: True)
    await _ready(app, base)
    dm = _PrivateChat()
    # step 3 skipped: step 4 comes next, with an example to copy and its own Skip
    await support.cb_support(_tap(dm, f"zm:sk:{CHAT}"), app)
    text, kw = dm.sent[-1]
    assert text.startswith("<b>Step 4 of 4: Instructions for me (optional)</b>")
    assert "<pre>Tone: friendly and natural." in text and _buttons(kw) == [f"zm:skr:{CHAT}"]
    # sending them saves them and finishes the guided setup
    dm.text = RULES
    await support.on_text(dm, app)
    assert any("I'll follow these instructions" in t for t, _ in dm.sent)
    assert any("All set." in t for t, _ in dm.sent)
    assert (await app.settings.get(CHAT)).raw("support_instructions") == RULES
    # the menu shows them and opens them again, with a way to remove them
    text, markup = await support.dashboard(app, CHAT)
    assert f"Instructions: your own ({len(RULES)} characters)" in text
    assert f"zm:r:{CHAT}" in [b.callback_data for row in markup.inline_keyboard for b in row]
    await support.cb_support(_tap(dm, f"zm:r:{CHAT}"), app)
    text, kw = dm.sent[-1]
    assert "Now:\n<pre>Tone: friendly, simple words." in text and _buttons(kw) == [f"zm:rx:{CHAT}"]
    await support.cb_support(_tap(dm, f"zm:rx:{CHAT}"), app)
    assert (await app.settings.get(CHAT)).raw("support_instructions") == ""
    assert "Instructions: none" in (await support.dashboard(app, CHAT))[0]
    # skipping step 4 finishes without instructions
    n = len(dm.sent)
    await support.cb_support(_tap(dm, f"zm:skr:{CHAT}"), app)
    assert any("All set." in t for t, _ in dm.sent[n:])
