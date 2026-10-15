"""Support mode (/zyromod): answer member questions from the group's own files.

Setup happens in a private chat: an admin sends /zyromod, picks the group, sends .txt, .md or
.pdf files (10 MB per group), and sets what members get when I cannot answer: a support email
(required) and an optional contact link with its own button text. Every group has its own
files, email and link, so one client never sees another's.

In the group, members ask with /ask, by @mentioning me, or by replying to one of my messages.
The answer comes only from the group's files. When the files do not cover the question, the
member gets the group's can't-answer message with the email and the contact button. Someone
who asks a plain question without tagging me is told how to ask, at most once a day per person.

Who may change what
- any admin of the group (also bot admins, the permit holder, the bot owner): files, on/off,
  hints, test questions, the unanswered list
- only the group's creator, admins who may add admins, the permit holder and the bot owner:
  the email, the contact link and the can't-answer message, because those decide where
  members are sent. Every contact change is posted to the group's reports.
"""

from __future__ import annotations

import html
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from . import kb
from .app import App
from .settings import LINK_SHORTENERS, SPECS, ChatSettings, link_host, validate
from .store import utcnow

log = logging.getLogger("zyroguard.support")
router = Router(name="support")
GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}

SUPPORT_KEYS = {"support", "support_hint", "support_email", "support_link", "support_link_label", "support_text",
                "support_instructions"}
# what members are told and where they are sent: only full admins, every change reported
CONTACT_KEYS = {"support_email", "support_link", "support_link_label", "support_text", "support_instructions"}
STATE_TTL_S = 30 * 60
HINT_USER_S = 24 * 3600     # untagged questions: one pointer to /ask per person per day
HINT_CHAT_S = 60            # and never more than one pointer a minute in a group
UNANSWERED_DAYS = 7
NO_ANSWER = "NO_ANSWER"
NEED_TEAM = "NEED_TEAM"
DEFAULT_SORRY = "I don't have the answer to that one."
ONLY_FULL_ADMINS = ("Only the group's creator, admins who can add new admins, or the person who added me can change "
                    "where members go for help. Ask one of them to do it in /zyromod.")
NOT_ADMIN = "You're not an admin of that group anymore."


# ---- small helpers ------------------------------------------------------------------

def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _size(n: int) -> str:
    return _mb(n) if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


def _who(user: User) -> str:
    return f"@{user.username}" if user.username else html.escape(user.full_name)


async def _title(app: App, chat_id: int) -> str:
    row = await app.store.get_chat(chat_id)
    return (row["title"] if row and row["title"] else "your group")


async def may_edit_contacts(app: App, user_id: int, chat_id: int) -> bool:
    if app.config.is_owner(user_id):
        return True
    if await app.store.chat_owner(chat_id) == user_id:
        return True
    member = (await app.admins.admins(chat_id)).get(user_id)
    if member is None:
        return False
    return getattr(member, "status", "") == "creator" or bool(getattr(member, "can_promote_members", False))


async def ready_problem(app: App, chat_id: int, cs: ChatSettings) -> str | None:
    """Why support mode cannot be turned on yet, or None."""
    if app.kb is None:
        return "Support isn't available right now. Try again later."
    if not await app.store.kb_documents(chat_id):
        return "Add your files first: open /zyromod in our private chat and tap Add files."
    if not cs.raw("support_email").strip():
        return "Set a support email first: open /zyromod in our private chat and tap Set email."
    return None


async def setting_problem(app: App, user_id: int, chat_id: int, key: str, value: str | None) -> str | None:
    """Checks for /set and /unset on support settings. value None means /unset. None = fine."""
    if key not in SUPPORT_KEYS:
        return None
    if key in CONTACT_KEYS and not await may_edit_contacts(app, user_id, chat_id):
        return ONLY_FULL_ADMINS
    cs = await app.settings.get(chat_id)
    if key == "support" and value is not None:
        try:
            on = validate(key, value) == "on"
        except ValueError:
            return None   # the normal validation explains it
        return await ready_problem(app, chat_id, cs) if on else None
    if key == "support_email" and cs.bool("support"):
        try:
            empty = value is None or validate(key, value) == ""
        except ValueError:
            return None
        if empty:
            return ("The support email is needed while support is on. Change it instead, "
                    "or turn support off first: /set support off")
    if key == "support_link" and value:
        try:
            link = validate(key, value)
        except ValueError:
            return None
        host = link_host(link)
        blocked = (await app.store.domains(chat_id))["block"]
        if any(host == d or host.endswith("." + d) for d in blocked):
            return "That link's website is on this group's block list, so I won't send members there."
    if key == "support_instructions" and value:
        what = kb.secret_in(value)
        if what:
            return (f"These instructions seem to contain {what}. Members can see what I repeat from them, "
                    "so take it out and send them again.")
        blocked = (await app.store.domains(chat_id))["block"]
        for found in _LINK.finditer(value):
            raw = found.group(0).rstrip(".,;:!?)]")
            host = link_host(raw if raw.lower().startswith(("http://", "https://")) else "https://" + raw)
            if host in LINK_SHORTENERS:
                return f"{raw} is a short link that hides where it goes. Use the full link instead."
            if any(host == d or host.endswith("." + d) for d in blocked):
                return f"{raw} is on this group's block list, so I won't send members there."
    return None


async def report_change(app: App, chat_id: int, key: str, old: str, new: str, user: User | None) -> None:
    """Contact changes decide where members are sent, so they always show up in the reports."""
    if old == new:
        return
    cs = await app.settings.get(chat_id)
    by = _who(user) if user else "someone"
    what = "instructions" if key == "support_instructions" else "contact"

    def shown(v: str) -> str:
        return html.escape(v[:300] + ("..." if len(v) > 300 else "")) or "none"
    await app.enforcer.notify(cs, (
        f"🛟 <b>Support {what} changed</b> in {html.escape(await _title(app, chat_id))}\n"
        f"{html.escape(SPECS[key].label)}\n"
        f"Before: {shown(old)}\nNow: {shown(new)}\nChanged by {by}"))


# ---- what members see ------------------------------------------------------------------

def contact_markup(cs: ChatSettings) -> InlineKeyboardMarkup | None:
    link = cs.raw("support_link").strip()
    if not link:
        return None
    label = cs.raw("support_link_label").strip() or SPECS["support_link_label"].default
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=link)]])


def _reach(cs: ChatSettings) -> str:
    """How to reach the team, in a few words: by email, by the button, or both."""
    email = html.escape(cs.raw("support_email").strip())
    button = bool(cs.raw("support_link").strip())
    if email and button:
        return f"email the team at {email} or tap the button below."
    if email:
        return f"email the team at {email}."
    if button:
        return "tap the button below to reach the team."
    return "ask one of the group's admins."


def fallback_text(cs: ChatSettings) -> str:
    custom = cs.raw("support_text").strip()
    email = cs.raw("support_email").strip()
    if custom and email and email.lower() in custom.lower():
        # the admin already wrote the email into their message: only the button is left to mention
        tail = "\nTap the button below to reach the team." if cs.raw("support_link").strip() else ""
        return html.escape(custom, quote=False) + tail
    reach = _reach(cs)
    return f"{html.escape(custom, quote=False) if custom else DEFAULT_SORRY}\n{reach[:1].upper()}{reach[1:]}"


def partial_footer(cs: ChatSettings) -> str:
    return f"For the rest, {_reach(cs)}"


async def send_fallback(m: Message, cs: ChatSettings) -> None:
    await m.reply(fallback_text(cs), parse_mode="HTML", reply_markup=contact_markup(cs))


def parse_reply(text: str) -> tuple[str, str]:
    """('none' | 'partial' | 'full', text to show)."""
    t = text.strip()
    if t.upper().startswith(NO_ANSWER) or (NO_ANSWER in t and len(t) < 60):
        return "none", ""
    partial = NEED_TEAM in t
    body = t.replace(NEED_TEAM, "").replace(NO_ANSWER, "").strip()
    if not body:
        return "none", ""
    return ("partial" if partial else "full"), body


_LINK = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+|\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b(?:/[^\s<>\"']*)?")


def keep_known_links(text: str, known: str) -> str:
    """A link in an answer must appear in the group's files or contacts; anything else is removed,
    so the bot can never be talked into posting a link its owner did not give it."""
    blob = known.lower()

    def sub(m: re.Match) -> str:
        raw = m.group(0)
        core = raw.rstrip(".,;:!?)]")
        tail = raw[len(core):]
        bare = re.sub(r"^(?:https?://)?(?:www\.)?", "", core.lower()).rstrip("/")
        return raw if bare and bare in blob else "[link removed]" + tail

    return _LINK.sub(sub, text)


_Q_START = re.compile(r"(?i)^(?:what|how|when|where|why|who|which|is|are|can|could|does|do|did|will|would|should|"
                      r"any|anyone|anybody|paano|ano|saan|kailan|bakit|magkano|pwede|puwede|meron)\b")


def looks_like_question(text: str) -> bool:
    t = " ".join(text.split())
    if len(t) < 10 or len(t) > 500:
        return False
    return t.endswith(("?", "？", "؟")) or bool(_Q_START.match(t))


def hint_text(first_name: str | None, question: str) -> str:
    q = _LINK.sub("", " ".join(question.split())).strip()
    if len(q) > 150:
        q = q[:150].rsplit(" ", 1)[0]
    example = f"/ask {q}" if q else "/ask your question"
    return (f"Hi {html.escape(first_name or 'there')}! If you want an answer from me, start your question "
            f"with /ask. Tap to copy:\n<code>{html.escape(example)}</code>")


_hinted: dict[tuple[int, int], datetime] = {}
_chat_hinted: dict[int, datetime] = {}


def _hint_allowed(chat_id: int, user_id: int, per_user_s: int) -> bool:
    now = utcnow()
    if len(_hinted) > 5000:
        for k, ts in list(_hinted.items()):
            if (now - ts).total_seconds() > HINT_USER_S:
                _hinted.pop(k, None)
    last = _hinted.get((chat_id, user_id))
    if last and (now - last).total_seconds() < per_user_s:
        return False
    last_chat = _chat_hinted.get(chat_id)
    if last_chat and (now - last_chat).total_seconds() < HINT_CHAT_S:
        return False
    _hinted[(chat_id, user_id)] = now
    _chat_hinted[chat_id] = now
    return True


async def _send_hint(app: App, m: Message, cs: ChatSettings, question: str) -> None:
    sent = await m.reply(hint_text(m.from_user.first_name if m.from_user else None, question), parse_mode="HTML")
    await app.enforcer.expire_later(cs, sent)


async def after_clean(app: App, m: Message) -> bool:
    """A group message that passed every check. True when support mode took care of it."""
    if not m.from_user or m.edit_date or m.sender_chat:
        return False
    cs = await app.settings.get(m.chat.id)
    if not cs.bool("support"):
        return False
    text = m.text or m.caption or ""
    if not text or text.startswith("/"):
        return False
    from .assistant import wants_answer
    if wants_answer(app, m):
        return False   # @mentions and replies to me get a real answer from the files (assistant.answer)
    if not cs.bool("support_hint") or not looks_like_question(text) or app.kb is None or not app.kb.ready:
        return False
    if await app.controls(m.from_user.id, m.chat.id):
        return False   # admins asking each other things do not need a pointer
    last = _hinted.get((m.chat.id, m.from_user.id))
    if last and (utcnow() - last).total_seconds() < HINT_USER_S:
        return False
    _, hits = await app.kb.search(m.chat.id, text, k=3)
    if not kb.relevant(hits, kb.HINT_MATCH):
        return False
    if not _hint_allowed(m.chat.id, m.from_user.id, HINT_USER_S):
        return False
    await _send_hint(app, m, cs, text)
    return True


# ---- the answer's look ----------------------------------------------------------------------

_FILLER_START = re.compile(
    r"(?i)^(?:(?:great|good|nice|excellent) question[.!,]*\s*"
    r"|(?:sure|certainly|of course|absolutely)[.!,]+\s+"
    r"|(?:based on|according to|from) (?:the|our|your|this|the group's|this group's) (?:documents?|files?|docs|"
    r"documentation|information|faq|notes|excerpts?)[^,.:\n]*[,:]\s*)")
_FILLER_END = re.compile(
    r"(?i)^(?:i )?(?:hope (?:this|that|it) helps|let me know\b|feel free\b|(?:i'?m |i am )?(?:always )?"
    r"(?:happy|glad) to help|is there anything else|any other questions|anything else\b|don'?t hesitate|"
    r"if you have (?:any )?(?:other|more|further) questions)")
_ARROW = re.compile(r"\s*(?:→|⇒|➔|➜|⟶)\s*|\s+(?:->|=>)\s+")
_BOLD_MD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC_MD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_TICKS = re.compile(r"`([^`\n]+)`")
_BULLET_MD = re.compile(r"^[-*+•·▪●]\s+(.*)$")
_STEP_MD = re.compile(r"^(\d{1,2})[.)]\s+(.*)$")
_HEADER_MD = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")


def _trim_filler(text: str) -> str:
    """Closing lines like "Hope this helps!" go, unless they are the whole answer."""
    lines = text.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        parts = re.split(r"(?<=[.!?])\s+", last)
        while parts and _FILLER_END.match(parts[-1]) and (len(parts) > 1 or any(x.strip() for x in lines[:-1])):
            parts.pop()
        if parts:
            lines[-1] = " ".join(parts)
            break
        lines.pop()
    return "\n".join(lines).strip()


def _prose_html(s: str) -> str:
    s = s.replace(" — ", ", ").replace("—", "-").replace(" – ", ", ").replace("–", "-")
    s = _ARROW.sub(" > ", s)
    s = html.escape(s, quote=False)
    s = _ITALIC_MD.sub(r"\1", s)
    return s.replace("**", "")


def _line_html(s: str) -> str:
    s = _BOLD_MD.sub(lambda m: "\x01" + m.group(1) + "\x02", s)
    out, pos = [], 0
    for m in _TICKS.finditer(s):
        out.append(_prose_html(s[pos:m.start()]))
        code = m.group(1).replace("\x01", "**").replace("\x02", "**")
        out.append(f"<code>{html.escape(code, quote=False)}</code>")
        pos = m.end()
    out.append(_prose_html(s[pos:]))
    return "".join(out).replace("\x01", "<b>").replace("\x02", "</b>")


def format_answer(text: str) -> str:
    """The model's answer as Telegram HTML: **bold**, `code`, steps and bullet lists shown properly,
    filler openers and closers trimmed, no em dashes or arrows."""
    text = text.replace("\r\n", "\n").strip()
    for _ in range(3):   # "Great question! Based on the documents, ..." has two
        trimmed = _FILLER_START.sub("", text, count=1).lstrip()
        if trimmed == text:
            break
        text = trimmed
    if text[:1].islower():
        text = text[:1].upper() + text[1:]
    text = _trim_filler(text)
    out: list[str] = []
    code: list[str] | None = None
    for raw in text.split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if code is None:
                code = []
            else:
                out.append(f"<pre>{html.escape(chr(10).join(code), quote=False)}</pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        s = line.strip()
        if not s:
            out.append("")
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", s) and "-" in s:
            continue   # a table's divider row
        if s.startswith("|") and s.endswith("|"):
            s = ", ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        h = _HEADER_MD.match(s)
        if h:
            out.append(f"<b>{_line_html(h.group(1).replace('**', ''))}</b>")
            continue
        b = _BULLET_MD.match(s)
        if b:
            s = "• " + b.group(1)
        n = _STEP_MD.match(s)
        if n:
            s = f"{n.group(1)}. {n.group(2)}"
        out.append(_line_html(s))
    if code is not None:
        out.append(f"<pre>{html.escape(chr(10).join(code), quote=False)}</pre>")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# ---- answering -----------------------------------------------------------------------------

@dataclass
class Result:
    status: str                    # answered | partial | no_match | not_in_files | unavailable | cap | error
    text: str = ""                 # HTML, ready to send
    best: float | None = None
    sources: list[str] = field(default_factory=list)


def _search_query(app: App, chat_id: int, user_id: int, question: str) -> str:
    """Short follow-ups ("and for teams?") are searched together with the previous question."""
    if len(question.split()) >= 6 or not app.assistant:
        return question
    earlier = [x["content"] for x in app.assistant.history(chat_id, user_id) if x["role"] == "user"]
    if not earlier:
        return question
    prev = earlier[-1].split("\n", 1)[-1]
    return f"{prev} {question}"


async def resolve(app: App, chat_id: int, user_id: int, asker: str, question: str, cs: ChatSettings,
                  group: str) -> Result:
    """Everything except sending: search, ask Claude, check the reply."""
    if app.kb is None or not app.kb.ready or not app.assistant or not app.assistant.configured:
        return Result("unavailable", fallback_text(cs))
    index, hits = await app.kb.search(chat_id, _search_query(app, chat_id, user_id, question))
    best = max((h.meaning for h in hits), default=None)
    if index is None:
        return Result("unavailable", fallback_text(cs))
    if not kb.relevant(hits) and not cs.raw("support_instructions").strip():
        # nothing close in the files: the can't-answer message, no AI call. A group with its own
        # instructions may say how to handle such questions (politics, insults), so the AI sees them all.
        return Result("no_match", fallback_text(cs), best)
    documents = kb.excerpts(index, hits)
    sources = []
    for h in hits:
        heading = index.headings[h.row]
        where = index.names[h.row] + (f", {heading.split(' > ')[-1]}" if heading else "")
        if where not in sources:
            sources.append(where)
    text, problem = await app.assistant.ask_support(
        chat_id, question, asker=asker, group=group, documents=documents, user_id=user_id,
        daily_cap=cs.int("assistant_daily_cap"), whole=index.chars <= kb.SMALL_KB_CHARS,
        instructions=cs.raw("support_instructions"))
    if problem == "cap":
        return Result("cap", CAP_TEXT, best)
    if problem or not text:
        return Result("error", "Sorry, I couldn't answer right now. Try again in a minute.", best)
    status, body = parse_reply(text)
    if status == "none":
        return Result("not_in_files", fallback_text(cs), best, sources)
    known = "\n".join([*(d.text for d in documents), cs.raw("support_link"), cs.raw("support_email"),
                       cs.raw("support_instructions")])   # links the admins wrote into their instructions may be used
    out = format_answer(keep_known_links(body, known))
    if not out:
        return Result("not_in_files", fallback_text(cs), best, sources)
    if status == "partial":
        return Result("partial", out + "\n\n" + partial_footer(cs), best, sources)
    return Result("answered", out, best, sources)


SHOWS_CONTACTS = {"unavailable", "no_match", "not_in_files", "partial"}
CAP_TEXT = "I've answered a lot of questions today and reached the daily limit. Try again tomorrow."


async def answer(app: App, m: Message, question: str, cs: ChatSettings) -> None:
    """/ask, an @mention or a reply to me, in a group with support mode on."""
    if not m.from_user:
        return
    chat_id = m.chat.id
    if not question:
        from .assistant import ask_for_question
        await ask_for_question(m)
        return
    if app.assistant and app.assistant.on_cooldown(chat_id, m.from_user.id):
        return
    if not await app.ai_budget_ok(chat_id):
        await m.reply(CAP_TEXT)
        return
    try:
        await app.bot.send_chat_action(chat_id, "typing")
    except Exception:  # noqa: BLE001
        pass
    asker = f"@{m.from_user.username}" if m.from_user.username else m.from_user.full_name
    r = await resolve(app, chat_id, m.from_user.id, asker, question, cs, m.chat.title or "this group")
    if r.status not in {"cap", "error"}:
        outcome = "answered" if r.status == "partial" else r.status
        await app.store.kb_question_add(chat_id, m.from_user.id, question, outcome, r.best)
    markup = contact_markup(cs) if r.status in SHOWS_CONTACTS else None
    try:
        await m.reply(r.text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        await m.reply(re.sub(r"<[^>]+>", "", r.text)[:3500], reply_markup=markup)


# ---- setup in a private chat (/zyromod) -----------------------------------------------------

@dataclass
class Wizard:
    chat_id: int
    step: str                  # files | email | link | label | text | test
    guided: bool = False
    ts: datetime = field(default_factory=utcnow)


_wizard: dict[int, Wizard] = {}
TEXT_STEPS = {"email", "link", "label", "text", "rules", "test"}


def _state(user_id: int) -> Wizard | None:
    st = _wizard.get(user_id)
    if st and (utcnow() - st.ts).total_seconds() > STATE_TTL_S:
        _wizard.pop(user_id, None)
        return None
    return st


def _set_state(user_id: int, chat_id: int, step: str, guided: bool = False) -> None:
    if len(_wizard) > 1000:
        for uid in [u for u, s in _wizard.items() if (utcnow() - s.ts).total_seconds() > STATE_TTL_S]:
            _wizard.pop(uid, None)
    _wizard[user_id] = Wizard(chat_id, step, guided)


def _day(ts: str) -> str:
    """Oct 13 from a stored timestamp."""
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return ts[:10]
    return f"{d:%b} {d.day}"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _old_reader(d) -> bool:
    return "reader" in d.keys() and int(d["reader"] or 1) < kb.READER_VERSION


async def dashboard(app: App, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    cs = await app.settings.get(chat_id)
    docs = await app.store.kb_documents(chat_id)
    used = sum(int(d["bytes"]) for d in docs)
    on = cs.bool("support")
    email = cs.raw("support_email").strip()
    link = cs.raw("support_link").strip()
    counts = await app.store.kb_question_counts(chat_id, utcnow() - timedelta(days=UNANSWERED_DAYS))
    missed = counts.get("no_match", 0) + counts.get("not_in_files", 0)
    lines = [f"🛟 <b>Support for {html.escape(await _title(app, chat_id))}</b>",
             "Status: <b>On</b>. Members ask me with /ask." if on else
             "Status: <b>Off</b>. Turn it on when your files and email are ready.", "",
             f"<b>Files</b> ({_size(used)} of {_size(kb.MAX_GROUP_BYTES)} used)"]
    stale = False
    for d in docs:
        about = ([f"{d['pages']} pages"] if d["pages"] else []) + [_size(int(d["bytes"]))]
        resend = _old_reader(d)
        stale = stale or resend
        lines.append(f"• {html.escape(d['name'])} ({', '.join(about)})" + (", send it again" if resend else ""))
    if not docs:
        lines.append("No files yet.")
    if stale:
        lines.append("I read files better now. Send the marked ones again (same name) for cleaner answers.")
    lines.append("Members can see anything in these files, so only add what you'd put on a public help page.")
    lines += ["", "<b>When I can't answer</b>",
              f"Email: {html.escape(email)}" if email else "Email: not set yet, needed before you turn on",
              (f"Button: {html.escape(cs.raw('support_link_label'))}, opens {html.escape(link)}" if link
               else "Button: none"),
              f"Message: {'your own' if cs.raw('support_text').strip() else 'the default'}"]
    rules = cs.raw("support_instructions").strip()
    lines += ["", "<b>How I answer</b>",
              f"Instructions: your own ({len(rules)} characters)" if rules else "Instructions: none, I use my defaults"]
    if on or counts:
        lines += ["", f"<b>Last {UNANSWERED_DAYS} days</b>",
                  f"{counts.get('answered', 0)} answered, {missed} I couldn't answer"]
    if not docs:
        nxt = "Start with Add files."
    elif not email:
        nxt = "Next: set the support email."
    elif not on:
        nxt = "Next: tap Test a question to see how I answer, then Turn on."
    elif missed:
        nxt = "Tap Unanswered to see what members asked that your files don't cover."
    else:
        nxt = ""
    if nxt:
        lines += ["", nxt]
    c = chat_id
    rows = [
        [_btn("Add files", f"zm:f:{c}")] + ([_btn("Remove a file", f"zm:rm:{c}")] if docs else []),
        [_btn("Change email" if email else "Set email", f"zm:e:{c}"),
         _btn("Contact button", f"zm:l:{c}")],
        [_btn("Can't-answer message", f"zm:t:{c}"), _btn("Preview", f"zm:p:{c}")],
        [_btn("Instructions for me", f"zm:r:{c}")],
        [_btn("Test a question", f"zm:q:{c}"), _btn("Unanswered", f"zm:u:{c}")],
        [_btn("Turn off" if on else "Turn on", f"zm:{'off' if on else 'on'}:{c}"),
         _btn(f"/ask reminders: {'On' if cs.bool('support_hint') else 'Off'}", f"zm:h:{c}")],
    ]
    if docs:
        rows.append([_btn("Remove all files", f"zm:da:{c}")])
    rows.append([_btn("Close", f"zm:x:{c}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_dashboard(app: App, m: Message, chat_id: int) -> None:
    text, markup = await dashboard(app, chat_id)
    await m.answer(text, parse_mode="HTML", reply_markup=markup)


async def _edit_dashboard(app: App, cb: CallbackQuery, chat_id: int) -> None:
    text, markup = await dashboard(app, chat_id)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest:
        pass


FILES_PROMPT = ("Send your FAQ or product files here as .txt, .md or .pdf, up to 10 MB in total. "
                "Send as many as you like, then tap Done.\n\n"
                "A file with the same name replaces the old one. Members can see anything in these files, "
                "so only send what you'd put on a public help page.")
EMAIL_PROMPT = ("Send the email members should use when I can't answer, like support@yourcompany.com\n\n"
                "Send /cancel to stop.")
LINK_PROMPT = ("Add a button under my can't-answer message. It can open any link: a ticket page, Discord, "
               "your website or a Telegram chat. Send the full link, like https://discord.gg/yourserver\n\n"
               "Send /cancel to stop.")
RULES_EXAMPLE = ("Tone: friendly and natural. Simple words, short and organized answers.\n"
                 "Do: if a question is unclear, ask what they mean instead of guessing. End answers with our "
                 "guide for more details: https://yourcompany.com/help\n"
                 "Don't: mention other products or competitors. If someone brings up politics or uses offensive "
                 "words, say you can only help with questions about our product.")
RULES_PROMPT = ("Tell me how to answer in this group: my tone, what to do and not do, and any links or details "
                "I should always include. Up to 1500 characters.\n\n"
                "Example, tap it to copy, then edit it and send it back:\n<pre>{example}</pre>\n\n"
                "I still answer only from your files and these instructions, and members can see anything "
                "I repeat from them.\n\nSend /cancel to stop.")


async def _prompt_files(m: Message, chat_id: int, guided: bool) -> None:
    head = "<b>Step 1 of 4: Your files</b>\n" if guided else ""
    await m.answer(head + FILES_PROMPT, parse_mode="HTML",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("Done", f"zm:fd:{chat_id}")]]))


async def _prompt_email(m: Message, chat_id: int, guided: bool, cs: ChatSettings) -> None:
    head = "<b>Step 2 of 4: Support email</b>\n" if guided else ""
    rows = []
    if cs.raw("support_email").strip() and not cs.bool("support"):
        rows.append([_btn("Remove email", f"zm:ex:{chat_id}")])
    await m.answer(head + EMAIL_PROMPT, parse_mode="HTML",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


async def _prompt_link(m: Message, chat_id: int, guided: bool, cs: ChatSettings) -> None:
    head = "<b>Step 3 of 4: Contact button (optional)</b>\n" if guided else ""
    rows = []
    if guided:
        rows.append([_btn("Skip", f"zm:sk:{chat_id}")])
    elif cs.raw("support_link").strip():
        rows.append([_btn("Change button text", f"zm:b:{chat_id}"), _btn("Remove button", f"zm:lx:{chat_id}")])
    current = cs.raw("support_link").strip()
    now = f"Now: {html.escape(current)}\n\n" if current and not guided else ""
    await m.answer(head + now + LINK_PROMPT, parse_mode="HTML",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


async def _prompt_rules(m: Message, chat_id: int, guided: bool, cs: ChatSettings) -> None:
    head = "<b>Step 4 of 4: Instructions for me (optional)</b>\n" if guided else ""
    current = cs.raw("support_instructions").strip()
    rows = []
    if guided:
        rows.append([_btn("Skip", f"zm:skr:{chat_id}")])
    elif current:
        rows.append([_btn("Remove instructions", f"zm:rx:{chat_id}")])
    now = f"Now:\n<pre>{html.escape(current, quote=False)}</pre>\n\n" if current and not guided else ""
    await m.answer(head + now + RULES_PROMPT.format(example=html.escape(RULES_EXAMPLE, quote=False)),
                   parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


async def _finish_guided(app: App, m: Message, chat_id: int) -> None:
    cs = await app.settings.get(chat_id)
    await m.answer("<b>All set.</b> When I can't answer, members see this:", parse_mode="HTML")
    await m.answer(fallback_text(cs), parse_mode="HTML", reply_markup=contact_markup(cs))
    await m.answer("Try a question first, or turn support on for the group.", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[_btn("Test a question", f"zm:q:{chat_id}"), _btn("Turn on", f"zm:on:{chat_id}")],
                         [_btn("Open the support menu", f"zm:g:{chat_id}")]]))


async def _open(app: App, m: Message, user_id: int) -> None:
    if app.kb is None:
        await m.answer("Support isn't available right now. Try again later.")
        return
    mine = [c for c in await app.groups_for(user_id) if app.chat_allowed(int(c["chat_id"]))]
    if not mine:
        from .commands import NO_GROUP
        from .contact import contact_button, contact_line
        await m.answer(NO_GROUP + contact_line(app), reply_markup=contact_button(app))
        return
    if len(mine) > 1:
        rows = [[_btn((c["title"] or str(c["chat_id"]))[:40], f"zm:g:{c['chat_id']}")] for c in mine[:20]]
        await m.answer("🛟 <b>Support</b>\nI answer members' questions from your own files. Pick a group:",
                       parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    chat_id = int(mine[0]["chat_id"])
    await _start_or_dashboard(app, m, user_id, chat_id)


async def _start_or_dashboard(app: App, m: Message, user_id: int, chat_id: int) -> None:
    cs = await app.settings.get(chat_id)
    if not await app.store.kb_documents(chat_id) and not cs.raw("support_email").strip():
        _set_state(user_id, chat_id, "files", guided=True)
        await m.answer(f"🛟 <b>Support for {html.escape(await _title(app, chat_id))}</b>\n"
                       "I'll answer members' questions from your files. When your files don't have the "
                       "answer, I'll send members to your team. Setup takes four short steps, the last two are "
                       "optional.", parse_mode="HTML")
        await _prompt_files(m, chat_id, True)
        return
    await _send_dashboard(app, m, chat_id)


@router.message(CommandStart(deep_link=True, magic=F.args == "zyromod"))
async def start_zyromod(m: Message, app: App) -> None:
    if m.chat.type == ChatType.PRIVATE and m.from_user:
        await _open(app, m, m.from_user.id)


@router.message(Command("zyromod"))
async def cmd_zyromod(m: Message, app: App) -> None:
    if not m.from_user:
        return
    if m.chat.type == ChatType.PRIVATE:
        await _open(app, m, m.from_user.id)
        return
    from .commands import require_admin
    if not await require_admin(m, app):
        return
    button = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Set up support privately", url=f"https://t.me/{app.bot_username}?start=zyromod")]])
    sent = await m.reply("Support is set up in our private chat, so members don't see your files or settings. "
                         "Tap the button below.", reply_markup=button)
    cs = await app.settings.get(m.chat.id)
    await app.enforcer.expire_later(cs, sent)


def _has_state(m: Message) -> bool:
    return bool(m.from_user and m.chat.type == ChatType.PRIVATE and _state(m.from_user.id))


@router.message(Command("cancel"), _has_state)
async def cmd_cancel(m: Message, app: App) -> None:
    _wizard.pop(m.from_user.id, None)
    await m.answer("Stopped. Send /zyromod to open the support menu again.")


def _awaiting_text(m: Message) -> bool:
    if not m.from_user or m.chat.type != ChatType.PRIVATE or not m.text or m.text.startswith("/"):
        return False
    st = _state(m.from_user.id)
    return bool(st and st.step in TEXT_STEPS)


async def _save(app: App, m: Message, chat_id: int, key: str, value: str) -> bool:
    """Set one support setting from the wizard, with the same checks as /set. True when saved."""
    problem = await setting_problem(app, m.from_user.id, chat_id, key, value)
    if problem:
        await m.answer(problem)
        return False
    cs = await app.settings.get(chat_id)
    old = cs.raw(key)
    try:
        new = await app.settings.set(chat_id, key, value, m.from_user.id)
    except ValueError as e:
        await m.answer(f"{html.escape(str(e))}\nTry again, or send /cancel.", parse_mode="HTML")
        return False
    if key in CONTACT_KEYS:
        await report_change(app, chat_id, key, old, new, m.from_user)
    return True


@router.message(_awaiting_text)
async def on_text(m: Message, app: App) -> None:
    st = _state(m.from_user.id)
    uid, chat_id = m.from_user.id, st.chat_id
    if not await app.controls(uid, chat_id):
        _wizard.pop(uid, None)
        await m.answer(NOT_ADMIN)
        return
    st.ts = utcnow()
    text = m.text.strip()
    if st.step == "test":
        await _test(app, m, chat_id, text)
        return
    key = {"email": "support_email", "link": "support_link", "label": "support_link_label",
           "text": "support_text", "rules": "support_instructions"}[st.step]
    if not await _save(app, m, chat_id, key, text):
        return
    cs = await app.settings.get(chat_id)
    if st.guided and st.step == "email":
        _set_state(uid, chat_id, "link", guided=True)
        await m.answer(f"<b>Saved.</b> Members will be sent to {html.escape(cs.raw('support_email'))}",
                       parse_mode="HTML")
        await _prompt_link(m, chat_id, True, cs)
        return
    if st.guided and st.step == "link":
        _set_state(uid, chat_id, "rules", guided=True)
        await m.answer(f"<b>Saved.</b> The button opens {html.escape(cs.raw('support_link'))}", parse_mode="HTML")
        await _prompt_rules(m, chat_id, True, cs)
        return
    if st.step == "rules":
        _wizard.pop(uid, None)
        await m.answer("<b>Saved.</b> I'll follow these instructions in my answers here. Tap Test a question "
                       "to see how they sound.", parse_mode="HTML")
        if st.guided:
            await _finish_guided(app, m, chat_id)
        else:
            await _send_dashboard(app, m, chat_id)
        return
    _wizard.pop(uid, None)
    await m.answer(f"<b>Saved.</b> {html.escape(SPECS[key].label)}: {html.escape(cs.raw(key)) or 'none'}",
                   parse_mode="HTML")
    await _send_dashboard(app, m, chat_id)


async def _test(app: App, m: Message, chat_id: int, question: str) -> None:
    cs = await app.settings.get(chat_id)
    try:
        await app.bot.send_chat_action(m.chat.id, "typing")
    except Exception:  # noqa: BLE001
        pass
    r = await resolve(app, chat_id, m.from_user.id, "the group admin (a test)", question, cs, await _title(app, chat_id))
    why = {"answered": "Answered from your files.",
           "partial": "Partly answered. Members also get your team's contact for the rest.",
           "no_match": "Nothing in your files is close to this, so members get your can't-answer message.",
           "not_in_files": "Your files mention this but don't answer it, so members get your can't-answer message.",
           "unavailable": "I can't search your files right now, so members get your can't-answer message.",
           "cap": "The daily question limit is reached.",
           "error": "The AI didn't reply. Try again in a minute."}[r.status]
    try:
        await m.answer(r.text, parse_mode="HTML",
                       reply_markup=contact_markup(cs) if r.status in SHOWS_CONTACTS else None)
    except TelegramBadRequest:
        await m.answer(re.sub(r"<[^>]+>", "", r.text)[:3500])
    src = (f"\nClosest match: {html.escape(r.sources[0])}"
           if r.sources and r.status in {"answered", "partial", "not_in_files"} else "")
    await m.answer(f"<b>Test only</b>, members didn't see this.\n{why}{src}\n\n"
                   "Ask another question, or send /cancel when you're done.", parse_mode="HTML")


@router.message(F.chat.type == ChatType.PRIVATE, F.document)
async def on_document(m: Message, app: App) -> None:
    if not m.from_user or not m.document:
        return
    uid = m.from_user.id
    st = _state(uid)
    if not st or st.step != "files":
        if await app.groups_for(uid):
            await m.answer("To add files for answering questions, send /zyromod and tap Add files.")
        return
    chat_id = st.chat_id
    if not await app.controls(uid, chat_id):
        _wizard.pop(uid, None)
        await m.answer(NOT_ADMIN)
        return
    st.ts = utcnow()
    if app.kb is None:
        await m.answer("Support isn't available right now. Try again later.")
        return
    doc = m.document
    name = kb.clean_name(doc.file_name)
    done = InlineKeyboardMarkup(inline_keyboard=[[_btn("Done", f"zm:fd:{chat_id}")]])
    if kb.kind_of(name) is None:
        await m.answer(f"{html.escape(name)}: I can read .txt, .md and .pdf files. "
                       "Save Word or Google Docs files as PDF first.", reply_markup=done)
        return
    old = await app.store.kb_document_by_name(chat_id, name)
    free = kb.MAX_GROUP_BYTES - await app.store.kb_bytes_used(chat_id) + (int(old["bytes"]) if old else 0)
    size = int(doc.file_size or 0)
    if size > kb.TELEGRAM_MAX_BYTES:
        await m.answer(f"{html.escape(name)} is {_size(size)}. Each group has 10 MB for files, so send a "
                       "smaller file.", reply_markup=done)
        return
    if size > free:
        await m.answer(f"{html.escape(name)} is {_size(size)}, but this group only has {_size(max(free, 0))} left. "
                       "Remove a file first, or send a smaller one.", reply_markup=done)
        return
    progress = await m.answer(f"Reading {html.escape(name)}... Big files take a few minutes.")
    last_edit = [utcnow()]

    async def counting(done: int, total: int) -> None:
        if total < 80 or done == total or (utcnow() - last_edit[0]).total_seconds() < 8:
            return
        last_edit[0] = utcnow()
        try:
            await progress.edit_text(f"Reading {html.escape(name)}... {done * 100 // total}%")
        except TelegramBadRequest:
            pass

    buf = io.BytesIO()
    try:
        await app.bot.download(doc, destination=buf)
        data = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("support.download", f"{type(e).__name__}: {e}", chat_id)
        await progress.edit_text(f"I couldn't download {html.escape(name)}. Send it again.", reply_markup=done)
        return
    finally:
        buf.close()
    if len(data) > free:
        await progress.edit_text(f"{html.escape(name)} is bigger than the space left for this group "
                                 f"({_size(max(free, 0))}).", reply_markup=done)
        return
    try:
        added = await app.kb.add_file(chat_id, name, data, uid, progress=counting)
    except kb.ReadError as e:
        await progress.edit_text(f"{html.escape(name)}: {html.escape(str(e))}", reply_markup=done)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("support file failed")
        await app.store.log_error("support.file", f"{type(e).__name__}: {e}", chat_id)
        await progress.edit_text(f"Something went wrong reading {html.escape(name)}. Try again, or send it as a "
                                 ".txt file.", reply_markup=done)
        return
    finally:
        del data
    pages = f" ({added.pages} pages)" if added.pages else ""
    verb, again = ("Updated", " It replaced the older copy.") if added.replaced else ("Added", "")
    await progress.edit_text(f"<b>{verb} {html.escape(added.name)}</b>{pages}.{again}\n"
                             "Send another file, or tap Done.", parse_mode="HTML", reply_markup=done)


@router.callback_query(F.data.startswith("zm:"))
async def cb_support(cb: CallbackQuery, app: App) -> None:
    from .callbacks import genuine, reject
    if not genuine(cb) or not cb.message or cb.message.chat.type != ChatType.PRIVATE:
        await reject(cb)
        return
    parts = cb.data.split(":")
    action, chat_id = parts[1], int(parts[2])
    extra = parts[3] if len(parts) > 3 else None
    uid = cb.from_user.id
    if not await app.controls(uid, chat_id):
        await cb.answer(NOT_ADMIN, show_alert=True)
        return
    m = cb.message
    cs = await app.settings.get(chat_id)
    if action in {"e", "ex", "l", "lx", "b", "t", "td", "r", "rx"} and not await may_edit_contacts(app, uid, chat_id):
        await cb.answer(ONLY_FULL_ADMINS, show_alert=True)
        return

    if action == "g":
        await cb.answer()
        st = _state(uid)
        if st and st.chat_id != chat_id:
            _wizard.pop(uid, None)
        if not await app.store.kb_documents(chat_id) and not cs.raw("support_email").strip():
            await _start_or_dashboard(app, m, uid, chat_id)   # nothing set up yet: the three steps
        else:
            await _edit_dashboard(app, cb, chat_id)
        return
    if action == "x":
        _wizard.pop(uid, None)
        await cb.answer()
        try:
            await m.delete()
        except TelegramBadRequest:
            pass
        return
    if action == "f":
        _set_state(uid, chat_id, "files")
        await cb.answer()
        await _prompt_files(m, chat_id, False)
        return
    if action == "fd":
        st = _state(uid)
        guided = bool(st and st.guided and st.chat_id == chat_id)
        await cb.answer()
        if guided:
            if not await app.store.kb_documents(chat_id):
                await m.answer("I don't have any files yet. Send at least one .txt, .md or .pdf file first.")
                return
            if not await may_edit_contacts(app, uid, chat_id):
                _wizard.pop(uid, None)
                await m.answer("Files saved. " + ONLY_FULL_ADMINS)
                await _send_dashboard(app, m, chat_id)
                return
            _set_state(uid, chat_id, "email", guided=True)
            await _prompt_email(m, chat_id, True, cs)
            return
        _wizard.pop(uid, None)
        await _send_dashboard(app, m, chat_id)
        return
    if action == "rm":
        docs = await app.store.kb_documents(chat_id)
        rows = [[_btn(f"Remove {d['name']}"[:60], f"zm:rd:{chat_id}:{d['id']}")] for d in docs[:30]]
        rows.append([_btn("Back", f"zm:g:{chat_id}")])
        await cb.answer()
        try:
            await m.edit_text("Which file should I remove? Its text is deleted right away.",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except TelegramBadRequest:
            pass
        return
    if action == "rd" and extra:
        name = await app.store.kb_delete_document(chat_id, int(extra))
        if app.kb:
            app.kb.invalidate(chat_id)
        note = f"Removed {name}." if name else "That file was already gone."
        last = cs.bool("support") and not await app.store.kb_documents(chat_id)
        if last:
            await app.settings.set(chat_id, "support", "off", uid)
            note += " That was the last file, so support is off now."
        await cb.answer(note, show_alert=last)
        await _edit_dashboard(app, cb, chat_id)
        return
    if action == "da":
        await cb.answer()
        try:
            await m.edit_text("Remove <b>all</b> files for this group? Support turns off. Your email and button "
                              "stay.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                  [_btn("Yes, remove all", f"zm:dk:{chat_id}"), _btn("Back", f"zm:g:{chat_id}")]]))
        except TelegramBadRequest:
            pass
        return
    if action == "dk":
        n = await app.store.kb_delete_all(chat_id)
        if app.kb:
            app.kb.invalidate(chat_id)
        await app.settings.set(chat_id, "support", "off", uid)
        await cb.answer(f"Removed {n} file{'' if n == 1 else 's'}. Support is off.", show_alert=True)
        await _edit_dashboard(app, cb, chat_id)
        return
    if action == "e":
        _set_state(uid, chat_id, "email")
        await cb.answer()
        await _prompt_email(m, chat_id, False, cs)
        return
    if action == "ex":
        if cs.bool("support"):
            await cb.answer("The email is needed while support is on. Turn support off first.", show_alert=True)
            return
        old = cs.raw("support_email")
        await app.settings.reset(chat_id, "support_email")
        await report_change(app, chat_id, "support_email", old, "", cb.from_user)
        _wizard.pop(uid, None)
        await cb.answer("Email removed.")
        await _send_dashboard(app, m, chat_id)
        return
    if action == "l":
        _set_state(uid, chat_id, "link")
        await cb.answer()
        await _prompt_link(m, chat_id, False, cs)
        return
    if action == "lx":
        old = cs.raw("support_link")
        await app.settings.reset(chat_id, "support_link")
        await report_change(app, chat_id, "support_link", old, "", cb.from_user)
        _wizard.pop(uid, None)
        await cb.answer("Contact button removed.")
        await _send_dashboard(app, m, chat_id)
        return
    if action == "b":
        _set_state(uid, chat_id, "label")
        await cb.answer()
        await m.answer(f"Now: {html.escape(cs.raw('support_link_label'))}\n"
                       "Send the new button text, up to 40 characters, like Open a ticket.\n\nSend /cancel to stop.",
                       parse_mode="HTML")
        return
    if action == "t":
        _set_state(uid, chat_id, "text")
        await cb.answer()
        rows = [[_btn("Use the default", f"zm:td:{chat_id}")]] if cs.raw("support_text").strip() else []
        await m.answer("Send the message members get when I can't answer, up to 500 characters. "
                       "I add your email under it (unless your message already has it), and the button "
                       "if you set one.\n\n"
                       f"Default: {DEFAULT_SORRY}\n\nSend /cancel to stop.",
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
        return
    if action == "td":
        old = cs.raw("support_text")
        await app.settings.reset(chat_id, "support_text")
        await report_change(app, chat_id, "support_text", old, "", cb.from_user)
        _wizard.pop(uid, None)
        await cb.answer("Back to the default message.")
        await _send_dashboard(app, m, chat_id)
        return
    if action == "sk":   # guided: no contact button, on to the instructions
        _set_state(uid, chat_id, "rules", guided=True)
        await cb.answer()
        await _prompt_rules(m, chat_id, True, cs)
        return
    if action == "skr":  # guided: no instructions, done
        _wizard.pop(uid, None)
        await cb.answer()
        await _finish_guided(app, m, chat_id)
        return
    if action == "r":
        _set_state(uid, chat_id, "rules")
        await cb.answer()
        await _prompt_rules(m, chat_id, False, cs)
        return
    if action == "rx":
        old = cs.raw("support_instructions")
        await app.settings.reset(chat_id, "support_instructions")
        await report_change(app, chat_id, "support_instructions", old, "", cb.from_user)
        _wizard.pop(uid, None)
        await cb.answer("Instructions removed. I use my defaults again.")
        await _send_dashboard(app, m, chat_id)
        return
    if action == "p":
        await cb.answer()
        await m.answer("When I can't answer, members see this:")
        await m.answer(fallback_text(cs), parse_mode="HTML", reply_markup=contact_markup(cs))
        return
    if action == "q":
        if not await app.store.kb_documents(chat_id):
            await cb.answer("Add some files first.", show_alert=True)
            return
        _set_state(uid, chat_id, "test")
        await cb.answer()
        await m.answer("Ask me something a member might ask. Members won't see it.\n\nSend /cancel when you're done.")
        return
    if action == "u":
        rows = await app.store.kb_unanswered(chat_id, utcnow() - timedelta(days=UNANSWERED_DAYS))
        await cb.answer()
        if not rows:
            await m.answer(f"No unanswered questions in the last {UNANSWERED_DAYS} days.")
            return
        lines = [f"<b>Questions I couldn't answer</b>\nLast {UNANSWERED_DAYS} days, newest first", ""]
        for r in rows:
            lines.append(f"{_day(r['ts'])}: {html.escape(r['question'][:200])}")
        lines += ["", "Add these answers to your files, then send the files again. A file with the same name "
                      "replaces the old one."]
        await m.answer("\n".join(lines), parse_mode="HTML")
        return
    if action in {"on", "off"}:
        if action == "on":
            problem = await ready_problem(app, chat_id, cs)
            if problem:
                await cb.answer(problem, show_alert=True)
                return
        await app.settings.set(chat_id, "support", action, uid)
        await cb.answer("Support is on. Members ask with /ask." if action == "on" else "Support is off.")
        await _edit_dashboard(app, cb, chat_id)
        return
    if action == "h":
        await app.settings.set(chat_id, "support_hint", "off" if cs.bool("support_hint") else "on", uid)
        await cb.answer()
        await _edit_dashboard(app, cb, chat_id)
        return
    await cb.answer()


def digest_line(counts: dict[str, int]) -> str | None:
    answered = counts.get("answered", 0)
    missed = counts.get("no_match", 0) + counts.get("not_in_files", 0)
    if not answered and not missed:
        return None
    return f"Support questions: {answered} answered, {missed} I couldn't answer. Details in /zyromod"
