"""Support mode knowledge base: a group's own FAQ and product files.

An admin sends .txt, .md or .pdf files in a private chat (/zyromod). The file is read in
memory and never written to disk. Its text is cut into small sections and each section gets
a meaning vector from the same on-server model the spam check uses, so nothing extra is
loaded and nothing leaves the server at this step.

A question is matched two ways: by meaning (works across languages and rewording) and by
keywords (catches exact product names and numbers the small model can miss). Only the best
few sections go to Claude, which answers from them or says it cannot.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import io
import logging
import math
import os
import re
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger("zyroguard.kb")

KINDS = {".txt": "txt", ".md": "md", ".markdown": "md", ".pdf": "pdf"}
MAX_GROUP_BYTES = 10 * 1024 * 1024      # all of a group's files together
MAX_GROUP_CHARS = 1_500_000   # about 600 dense pages. A 10 MB text-only PDF can hold 9M characters,
                              # which would take about 40 minutes of the bot's one CPU to embed, so reading stops here
TELEGRAM_MAX_BYTES = 20 * 1024 * 1024   # bots cannot download bigger files
CHUNK_CHARS = 500        # the model reads about 128 tokens, so sections stay short
MAX_HEADING = 160
TOP_K = 8
CONTEXT_CHARS = 9000     # excerpts sent to Claude per question
SMALL_KB_CHARS = 16000   # a knowledge base this small (about 4k tokens) is sent whole, so the search cannot miss
# Calibrated with the real model on a 16-question shop FAQ:
# a question scores only 0.20-0.65 against the section that answers it (median 0.46, also in Tagalog,
# Spanish, Korean, Japanese, Indonesian), on-topic questions the FAQ does not answer 0.25-0.34, off-topic
# chat 0.19 and below. So the gate only drops clearly unrelated questions and Claude decides the rest:
# a needless Claude call costs about 2 cents, a wrong "can't answer" leaves a member without help.
MATCH = 0.22             # best meaning score below this (and no keyword hit): can't-answer, Claude not asked
KEYWORD_MATCH = 0.60     # share of the question's rare words found in one section
HINT_MATCH = 0.33        # an untagged question needs this to get the /ask pointer: chatter stays untouched
KEYWORD_WEIGHT = 0.3
CACHE_GROUPS = 2         # searchable groups kept in memory; the rest load from the database


class ReadError(ValueError):
    """A problem with an uploaded file, worded for the admin."""


# Reading

def clean_name(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip() or "file.txt"
    base = re.sub(r"[\x00-\x1f<>]", "", base)
    return base[:80]


def kind_of(name: str) -> str | None:
    return KINDS.get(os.path.splitext(name.lower())[1])


TOO_MUCH = ("This file has more text than I can hold for one group (about 600 pages in total). Remove a file "
            "you no longer need, or send only the parts members ask about.")


def read_file(name: str, data: bytes, max_chars: int = MAX_GROUP_CHARS) -> tuple[str, int | None]:
    """(text, pages). Raises ReadError with a plain explanation. Reading stops as soon as the text
    passes max_chars, so a huge file never sits in memory whole."""
    kind = kind_of(name)
    if kind is None:
        raise ReadError("I can read .txt, .md and .pdf files. Save Word or Google Docs files as PDF first.")
    if not data:
        raise ReadError("This file is empty.")
    if kind == "pdf":
        return _read_pdf(data, max_chars)
    text = _read_text(data)
    if len(text) > max_chars:
        raise ReadError(TOO_MUCH)
    return text, None


def _read_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise ReadError("This file does not look like plain text. Send it as .txt, .md or .pdf.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return tidy(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return tidy(data.decode("latin-1"))


def _read_pdf(data: bytes, max_chars: int = MAX_GROUP_CHARS) -> tuple[str, int]:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:  # noqa: BLE001
                ok = 0
            if not ok:
                raise ReadError("This PDF is password protected. Save a copy without the password and send that.")
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                pages.append("")
            total += len(pages[-1])
            if total > max_chars * 1.1:   # tidy() trims a little, so stop just past the limit
                raise ReadError(TOO_MUCH)
    except ReadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ReadError("I could not open this PDF, it may be damaged. Save or export it again and resend it.") from e
    text = tidy(_clean_pdf(pages))
    if len(text) > max_chars:
        raise ReadError(TOO_MUCH)
    letters = sum(ch.isalnum() for ch in text)
    if letters < max(20, 15 * len(pages)):   # scans have next to no text, a real FAQ has hundreds per page
        raise ReadError("This PDF has almost no text I can read, it looks like scanned pages or pictures. "
                        "Send a text version: a .txt or .md file, or a PDF exported from a document.")
    return text, len(pages)


_PAGE_NO = re.compile(r"(?i)^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$|^[-–—]\s*\d{1,4}\s*[-–—]$")
_PDF_BULLET = re.compile(r"^[•●▪◦‣∙■□➢►▸✓✔○◆◇]\s*")
_INLINE_BULLET = re.compile(r"\s+[•●▪◦‣■➢►▸]\s+")
PDF_EDGE = 2   # lines at the top and bottom of a page that may be a running header or footer


def _clean_pdf(pages: list[str]) -> str:
    """Page text without running headers, footers and page numbers, with words that were broken
    across lines joined again and bullet glyphs turned into list items."""
    split = [[x.strip() for x in p.split("\n")] for p in pages]

    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.lower())

    def edges(lines: list[str]) -> set[int]:
        filled = [i for i, x in enumerate(lines) if x]
        return set(filled[:PDF_EDGE] + filled[-PDF_EDGE:])

    repeated: set[str] = set()
    if len(split) >= 4:
        seen: Counter[str] = Counter()
        for lines in split:
            seen.update({key(lines[i]) for i in edges(lines) if len(lines[i]) <= 100})
        repeated = {k for k, n in seen.items() if n >= 0.6 * len(split)}
    out = []
    for lines in split:
        edge = edges(lines)
        kept = []
        for i, line in enumerate(lines):
            if i in edge and (_PAGE_NO.match(line) or key(line) in repeated):
                continue
            kept.append(_PDF_BULLET.sub("• ", _INLINE_BULLET.sub("\n• ", line)))
        out.append("\n".join(kept))
    return re.sub(r"(\w)-\n(?=[a-z])", r"\1", "\n\n".join(out))


def tidy(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"[  ]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_SECRETS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"), "an API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "a Telegram bot token"),
    (re.compile(r"(?im)^\W*(?:password|passwd|pwd|passcode)\s*[:=]\s*\S{4,}\s*$"), "a password"),
    (re.compile(r"(?i)\b(?:seed|recovery|mnemonic|secret)\s+phrase\s*[:=]\s*\S+"), "a recovery phrase"),
]


def secret_in(text: str) -> str | None:
    """Anything in these files can be quoted to any member, so obvious secrets are refused."""
    for pattern, what in _SECRETS:
        if pattern.search(text):
            return what
    return None


# Cutting into sections

READER_VERSION = 2       # part of each file's fingerprint, so after a reader change the same file can be sent again
HEADING_LEVELS = 3       # a section title keeps at most this many levels of its heading path

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_BOLD_LINE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_QUESTION_LINE = re.compile(r"^(?:q(?:uestion)?\s*\d*\s*[:.)-]\s*)?(.{3,%d}[?？؟])\s*$" % MAX_HEADING, re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)")
_RULE = re.compile(r"^([-*_])(?:\s*\1){2,}$")
_SETEXT = re.compile(r"^(?:={3,}|-{3,})$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
_QUOTE = re.compile(r"^(?:>\s?)+")
_BULLET = re.compile(r"^(?:[-*+•●▪◦‣∙■□➢►▸✓✔○◆◇]\s+)(?:\[[ xX]\]\s+)?")
_NUMBERED = re.compile(r"^\d{1,3}[.)]\s+")
_GITBOOK = re.compile(r"\{%-?\s*(\w[\w-]*)([^%]*)-?%\}")
_HTML_TAG = re.compile(r"</?(?:a|abbr|b|blockquote|br|code|dd|details|div|dl|dt|em|figcaption|figure|h[1-6]|hr|i|"
                       r"iframe|img|kbd|li|mark|ol|p|picture|pre|small|source|span|strong|sub|summary|sup|table|"
                       r"tbody|td|th|thead|tr|u|ul|video)\b[^>]*>", re.I)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<((?:https?://|mailto:)[^>\s]+)>")
_FOOTNOTE = re.compile(r"\[\^[^\]]+\]")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"~~(.+?)~~")
_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")   # any escaped ASCII punctuation, like \$10 or 1\.
_LEAD_SYMBOLS = re.compile(r"^[^\w\"'(\[`$€£¥@/.]+")
_COLUMNS = re.compile(r"^([\w./-]+(?: [\w./-]+){0,2})\s{2,}(\S.*)$")
_TRAIL_COMMENT = re.compile(r"^(.*?\S)\s+#\s+(.+)$")


def _link(m: re.Match) -> str:
    text, url = m.group(1).strip(), m.group(2).strip()
    low = url.lower()
    if low.startswith(("http://", "https://", "mailto:", "tel:")):
        bare = url.split(":", 1)[1] if low.startswith(("mailto:", "tel:")) else url
        return bare if text.strip("`") in (url, bare) else f"{text} ({bare})"
    return text   # a link to another page of the same docs: the words are enough


def _prose(s: str) -> str:
    s = _HTML_TAG.sub("", s)
    s = _BOLD.sub(r"\2", s)
    s = _ITALIC.sub(lambda m: m.group(1) or m.group(2), s)
    s = _STRIKE.sub(r"\1", s).replace("**", "")
    return html.unescape(_ESCAPED.sub(r"\1", s))


def _inline(s: str) -> str:
    """One line of markdown as plain text. Links keep their address, code keeps its backticks."""
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = _IMAGE.sub(lambda m: m.group(1), s)
    s = _MD_LINK.sub(_link, s)
    s = _REF_LINK.sub(r"\1", s)
    s = _AUTOLINK.sub(r"\1", s)
    s = _FOOTNOTE.sub("", s)
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(s):
        out.append(_prose(s[pos:m.start()]))
        out.append(f"`{m.group(2).strip()}`")
        pos = m.end()
    out.append(_prose(s[pos:]))
    return " ".join("".join(out).split())


def _title(s: str) -> str:
    t = _LEAD_SYMBOLS.sub("", _inline(s).replace("`", "")).rstrip(":").strip()
    return re.sub(r"^\d{1,2}[.)]\s+(?=\D)", "", t)   # "1. Getting started": the number only helps the page


def _list_mark(line: str) -> str:
    return _BULLET.sub("• ", line) if _BULLET.match(line) else line


def _code_line(line: str, plain: bool) -> str:
    """A line of a code block: comments become words, commands stay copyable in backticks."""
    s = " ".join(line.split())
    if not s:
        return ""
    if s == "#" or s.startswith("# "):
        return s.lstrip("#").strip()
    if s.startswith("$ "):
        s = s[2:]
    if plain and s.endswith(":") and len(s.split()) <= 3:
        return s   # a label inside sample output, like "commands:"
    if "`" in s:
        return s
    m = _TRAIL_COMMENT.match(s)
    if m:
        return f"`{m.group(1)}` ({m.group(2)})"
    m = _COLUMNS.match(line.strip())
    if plain and m:   # "  status    show what is recorded": a command and what it does
        return f"`{m.group(1)}`: {' '.join(m.group(2).split())}"
    return f"`{s}`"


def _cells(line: str) -> list[str]:
    row = line.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return [_inline(c.replace("\\|", "|")) for c in re.split(r"(?<!\\)\|", row)]


def _table_row(head: list[str], cells: list[str]) -> str:
    pairs = [(h, c) for h, c in zip(head + [""] * len(cells), cells) if c]
    if not any(h for h in head):
        return "; ".join(c for _, c in pairs)
    return "; ".join(f"{h}: {c}" if h else c for h, c in pairs)


def _front_matter(text: str) -> tuple[str, str]:
    """YAML front matter is dropped, except a description, which says what the page is about."""
    m = re.match(r"---\n(.*?)\n---[ ]*(?:\n|$)", text, re.S)
    if not m or len(m.group(1)) > 3000:
        return text, ""
    about: list[str] = []
    grab = False
    for line in m.group(1).split("\n"):
        k = re.match(r"([\w-]+):\s*(.*)$", line)
        if k and not line.startswith(" "):
            grab = k.group(1).lower() in {"description", "summary"}
            v = k.group(2).strip()
            if grab and v and v not in {">", ">-", ">+", "|", "|-", "|+"}:
                about.append(v.strip("'\""))
        elif grab and line.strip():
            about.append(line.strip())
    return text[m.end():], " ".join(" ".join(about).split())


def _md_paragraphs(text: str) -> list[tuple[str | None, str]]:
    text, about = _front_matter(text)
    paras: list[tuple[str | None, str]] = [(None, about)] if about else []
    trail: list[str] = []          # markdown heading path
    heading: str | None = None
    lines: list[str] = []
    raw_lines = text.split("\n")
    fence: str | None = None
    plain_code = False

    def flush() -> None:
        body = "\n".join(x for x in lines if x).strip()
        if body:
            paras.append((heading, body))
        lines.clear()

    def new_heading(level: int, title: str) -> None:
        """level 1-6 for # headings, 7 for a bold line or tab title under the current path."""
        nonlocal heading
        flush()
        title = _title(title)
        if not title:
            return
        if level <= 6:
            trail[:] = (trail + [""] * 6)[:level - 1] + [title]   # a skipped level stays empty
            path = [t for t in trail if t]
        else:
            path = [t for t in trail if t] + [title]
        heading = " > ".join(path[-HEADING_LEVELS:])[:MAX_HEADING]

    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        i += 1
        if fence:
            if raw.strip().startswith(fence) and not raw.strip().strip(fence[0]):
                fence = None
            else:
                lines.append(_code_line(raw, plain_code))
            continue
        f = _FENCE.match(raw)
        if f:
            fence, plain_code = f.group(1), f.group(2).lower() in {"", "text", "txt", "console", "plaintext"}
            continue
        line = _QUOTE.sub("", raw.strip()).strip()
        if not line:
            flush()
            continue
        tag = _GITBOOK.fullmatch(line)
        if tag:
            name, args = tag.group(1).lower(), tag.group(2)
            url = re.search(r'url="(https?://[^"]+)"', args)
            title = re.search(r'title="([^"]+)"', args)
            if name == "tab" and title:
                new_heading(7, title.group(1))
            elif name == "embed" and url:
                lines.append(f"Link: {url.group(1)}")
            elif name in {"endtab", "endtabs"}:
                flush()
                heading = " > ".join([t for t in trail if t][-HEADING_LEVELS:])[:MAX_HEADING] or None
            else:
                flush()
            continue
        line = _GITBOOK.sub("", line).strip()
        m = _MD_HEADING.match(line)
        if m:
            new_heading(len(m.group(1)), m.group(2))
            continue
        if (_SETEXT.match(line) and len(lines) == 1 and len(lines[0]) <= 100
                and not lines[0].startswith("• ") and not lines[0].endswith(".")):
            title = lines.pop()
            new_heading(1 if line.startswith("=") else 2, title)
            continue
        if _RULE.match(line):
            flush()
            continue
        if "|" in line and i < len(raw_lines) and _TABLE_SEP.match(raw_lines[i].strip()):
            flush()
            head = _cells(line)
            i += 1
            while i < len(raw_lines) and "|" in raw_lines[i] and raw_lines[i].strip():
                lines.append(_table_row(head, _cells(raw_lines[i])))
                i += 1
            flush()
            continue
        b = _MD_BOLD_LINE.match(line)
        if (b and len(b.group(1)) <= 100 and "**" not in b.group(1)
                and not b.group(1).rstrip().endswith((".", "!", ",", ";"))):
            new_heading(7, b.group(1))
            continue
        text_line = _inline(line)
        if not text_line:
            continue
        if not _BULLET.match(line) and not _NUMBERED.match(line) and _QUESTION_LINE.match(text_line):
            new_heading(7, _QUESTION_LINE.match(text_line).group(1))
            continue
        lines.append(_list_mark(text_line) if _BULLET.match(line) else text_line)
    flush()
    return paras


def _heading_of(line: str, kind: str) -> str | None:
    """Lines that start a new section in plain text and PDFs: FAQ questions and short ALL CAPS titles."""
    m = _QUESTION_LINE.match(line)
    if m:
        return m.group(1).strip()
    letters = [c for c in line if c.isalpha()]
    if 3 <= len(letters) and len(line) <= 60 and line.upper() == line and not line.endswith((".", ",")):
        return line.strip().title()
    if kind == "txt" and len(line) <= 60 and line.endswith(":") and not _BULLET.match(line):
        return line[:-1].strip()
    return None


def _looks_like_title(line: str, before: list[str], typical: float) -> bool:
    """PDFs have no heading marks. A short line that starts with a capital, ends without
    punctuation and follows a finished sentence is almost always a title."""
    if before and not before[-1].endswith((".", "!", "?", ":")):
        return False
    words = line.split()
    return (len(line) <= min(60, typical * 0.6) and 1 <= len(words) <= 8 and line[0].isupper()
            and not line.endswith((".", ",", ";", ":", "!", "?")) and not _BULLET.match(line)
            and not _NUMBERED.match(line) and sum(c.isalpha() for c in line) >= 3)


def _join_pdf(lines: list[str]) -> str:
    """A PDF breaks lines where the page ends, so lines are joined back into sentences,
    but list items and steps keep their own line."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif line.startswith("• ") or _NUMBERED.match(line):
            out += "\n" + line
        else:
            out += " " + line
    return out.strip()


def _plain_paragraphs(text: str, kind: str) -> list[tuple[str | None, str]]:
    paras: list[tuple[str | None, str]] = []
    heading: str | None = None
    lines: list[str] = []
    long_lines = sorted(len(x.strip()) for x in text.split("\n") if len(x.strip()) >= 20)
    typical = long_lines[len(long_lines) // 2] if long_lines else 80

    def flush() -> None:
        body = _join_pdf(lines) if kind == "pdf" else "\n".join(lines).strip()
        if body:
            paras.append((heading, body))
        lines.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        h = _heading_of(line, kind)
        if h is None and kind == "pdf" and _looks_like_title(line, lines, typical):
            h = line
        if h:
            flush()
            heading = h[:MAX_HEADING]
            continue
        lines.append(_list_mark(line))
    flush()
    return paras


def sections(text: str, kind: str) -> list[tuple[str | None, str]]:
    """(heading, body) pairs, each body at most CHUNK_CHARS long, in reading order."""
    return _pack(_md_paragraphs(text) if kind == "md" else _plain_paragraphs(text, kind))


def _split_long(body: str) -> list[str]:
    """Cut at line ends first (steps, list items and commands stay whole), then at sentence ends."""
    if len(body) <= CHUNK_CHARS:
        return [body]
    pieces: list[str] = []
    for line in body.split("\n"):
        for sentence in ([line] if len(line) <= CHUNK_CHARS else _SENTENCE_END.split(line)):
            while len(sentence) > CHUNK_CHARS:
                cut = sentence.rfind(" ", 0, CHUNK_CHARS)
                cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
                pieces.append((sentence[:cut].strip(), " "))
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append((sentence, " "))
        if pieces:
            pieces[-1] = (pieces[-1][0], "\n")
    out: list[str] = []
    sep = "\n"
    for p, after in pieces:
        if out and len(out[-1]) + 1 + len(p) <= CHUNK_CHARS:
            out[-1] = f"{out[-1]}{sep}{p}"
        else:
            out.append(p)
        sep = after
    return out


def _pack(paras: list[tuple[str | None, str]]) -> list[tuple[str | None, str]]:
    """Join short paragraphs under the same heading, split long ones at line and sentence ends."""
    out: list[tuple[str | None, str]] = []
    for heading, body in paras:
        for piece in _split_long(body):
            if out and out[-1][0] == heading and len(out[-1][1]) + 2 + len(piece) <= CHUNK_CHARS:
                out[-1] = (heading, f"{out[-1][1]}\n{piece}")
            else:
                out.append((heading, piece))
    return out


def embed_text(heading: str | None, body: str) -> str:
    return f"{heading}\n{body}" if heading else body


# Search

_WORD = re.compile(r"\w+")
STOP = set("""a an the and or but if of to in on at by for from with about into over is are was were be been am
do does did doing have has had i me my we our you your he she it its they them their this that these those what
which who whom whose when where why how can could should would will shall may might must not no yes so than then
there here just also please any some all get got let us ok okay hi hello hey thanks thank pls plz
ang ng sa mga na ba po ko mo ako ikaw siya kami tayo sila ito iyan iyon ano paano saan kailan bakit sino may
meron wala lang din rin pa nga naman kasi pero at o kung""".split())


def terms(text: str) -> list[str]:
    words = [w for w in _WORD.findall(text.lower()) if len(w) >= 2 and w not in STOP]
    return list(dict.fromkeys(words))[:12]


@dataclass
class Index:
    ids: np.ndarray
    doc_ids: np.ndarray
    seqs: np.ndarray
    names: list[str]
    headings: list[str | None]
    texts: list[str]
    lowered: list[str]
    vecs: np.ndarray
    chars: int

    def __len__(self) -> int:
        return len(self.texts)


@dataclass
class Hit:
    row: int
    score: float
    meaning: float
    keyword: float


def keyword_scores(index: Index, query: str) -> np.ndarray:
    """Share of the question's words (weighted by how rare they are) found in each section."""
    n = len(index)
    found = []
    for t in terms(query):
        pat = re.compile(rf"(?<!\w){re.escape(t)}(?!\w)")
        hit = np.fromiter((t in low and bool(pat.search(low)) for low in index.lowered), dtype=bool, count=n)
        found.append((t, hit))
    if not found or not n:
        return np.zeros(n, dtype=np.float32)
    weights = [math.log(1 + n / (1 + int(h.sum()))) for _, h in found]
    total = sum(weights)
    out = np.zeros(n, dtype=np.float32)
    for w, (_, h) in zip(weights, found):
        out += h.astype(np.float32) * (w / total)
    return out


def rank(index: Index, qvec: np.ndarray, query: str, k: int = TOP_K) -> list[Hit]:
    if not len(index):
        return []
    meaning = index.vecs @ qvec
    keyword = keyword_scores(index, query)
    score = meaning + KEYWORD_WEIGHT * keyword
    top = np.argsort(-score)[:k]
    return [Hit(int(i), float(score[i]), float(meaning[i]), float(keyword[i])) for i in top]


def relevant(hits: list[Hit], floor: float = MATCH) -> bool:
    if not hits:
        return False
    best_meaning = max(h.meaning for h in hits)
    best_keyword = max(h.keyword for h in hits)
    return best_meaning >= floor or best_keyword >= KEYWORD_MATCH


@dataclass
class Excerpt:
    name: str
    text: str


def excerpts(index: Index, hits: list[Hit], budget: int = CONTEXT_CHARS) -> list[Excerpt]:
    """The best sections plus their neighbours, merged into runs, best run first."""
    first_seen: dict[int, int] = {}
    if index.chars <= SMALL_KB_CHARS:
        rows = list(range(len(index)))
    else:
        chosen: list[int] = []
        used = 0
        for h in hits:
            if h.row not in chosen and used + len(index.texts[h.row]) <= budget:
                chosen.append(h.row)
                used += len(index.texts[h.row])
        for h in hits:
            for r in (h.row - 1, h.row + 1):
                if (0 <= r < len(index) and r not in chosen and index.doc_ids[r] == index.doc_ids[h.row]
                        and used + len(index.texts[r]) <= budget):
                    chosen.append(r)
                    used += len(index.texts[r])
        rows = sorted(chosen)
        first_seen = {r: i for i, r in enumerate(chosen)}
    runs: list[list[int]] = []
    for r in rows:
        if runs and r == runs[-1][-1] + 1 and index.doc_ids[r] == index.doc_ids[runs[-1][-1]]:
            runs[-1].append(r)
        else:
            runs.append([r])
    if first_seen:
        runs.sort(key=lambda run: min(first_seen[r] for r in run))
    out = []
    for run in runs:
        parts, last_heading = [], object()
        for r in run:
            if index.headings[r] and index.headings[r] != last_heading:
                parts.append(f"## {index.headings[r]}")
            last_heading = index.headings[r]
            parts.append(index.texts[r])
        out.append(Excerpt(index.names[run[0]], "\n".join(parts)))
    return out


# The library itself

@dataclass
class Added:
    doc_id: int
    name: str
    sections: int
    pages: int | None
    chars: int
    replaced: bool


class KnowledgeBase:
    def __init__(self, store: Any, library: Any):
        self.store = store
        self.library = library            # similar.Library: the model and its CPU lock
        self._cache: OrderedDict[int, Index] = OrderedDict()
        self._ingest = asyncio.Lock()     # one file at a time on the whole server: memory stays flat

    @property
    def ready(self) -> bool:
        return bool(self.library is not None and self.library.ready)

    @property
    def model(self) -> str:
        return self.library.embedder.model

    def invalidate(self, chat_id: int) -> None:
        self._cache.pop(chat_id, None)

    async def add_file(self, chat_id: int, name: str, data: bytes, by: int | None, progress=None) -> Added:
        """Read, check, cut, embed and store one file. Raises ReadError for anything the admin must fix.
        progress(done, total) is awaited while the sections are embedded (big files take minutes)."""
        if not self.ready:
            raise ReadError("My search model is still starting. Try again in a minute.")
        name = clean_name(name)
        size = len(data)
        async with self._ingest:
            old = await self.store.kb_document_by_name(chat_id, name)
            chars_left = MAX_GROUP_CHARS - (await self.store.kb_chars_used(chat_id) - (int(old["chars"]) if old else 0))
            text, pages = await asyncio.to_thread(read_file, name, data, max(chars_left, 0))
            del data
            what = secret_in(text)
            if what:
                raise ReadError(f"This file seems to contain {what}. Anything in these files can be quoted to any "
                                "member, so take it out and send the file again.")
            sha = hashlib.sha256(f"reader {READER_VERSION}\n{text}".encode("utf-8")).hexdigest()
            same = await self.store.kb_document_by_hash(chat_id, sha)
            if same and same["name"] == name:
                raise ReadError(f"I already have this exact file ({same['name']}). Nothing changed.")
            parts = sections(text, kind_of(name) or "txt")
            if not parts:
                raise ReadError("I found no text in this file.")
            vecs = await self.library.embed_many([embed_text(h, b) for h, b in parts], progress=progress)
            rows = [(i, h, b, vecs[i].astype(np.float32).tobytes()) for i, (h, b) in enumerate(parts)]
            doc_id, replaced = await self.store.kb_add_document(
                chat_id, name, kind_of(name) or "txt", size, len(text), pages, sha, by, rows, self.model,
                reader=READER_VERSION)
        self.invalidate(chat_id)
        log.info("support files: chat %s added %s (%d sections)", chat_id, name, len(parts))
        return Added(doc_id, name, len(parts), pages, len(text), replaced is not None)

    async def index(self, chat_id: int) -> Index | None:
        if chat_id in self._cache:
            self._cache.move_to_end(chat_id)
            return self._cache[chat_id]
        rows = await self.store.kb_chunks(chat_id, self.model)
        if not rows:
            return None
        texts = [r["text"] for r in rows]
        headings = [r["heading"] for r in rows]
        idx = Index(
            ids=np.array([r["id"] for r in rows], dtype=np.int64),
            doc_ids=np.array([r["doc_id"] for r in rows], dtype=np.int64),
            seqs=np.array([r["seq"] for r in rows], dtype=np.int64),
            names=[r["name"] for r in rows], headings=headings, texts=texts,
            lowered=[embed_text(h, t).lower() for h, t in zip(headings, texts)],
            vecs=np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows]),
            chars=sum(len(t) for t in texts),
        )
        self._cache[chat_id] = idx
        while len(self._cache) > CACHE_GROUPS:
            self._cache.popitem(last=False)
        return idx

    async def search(self, chat_id: int, query: str, k: int = TOP_K) -> tuple[Index | None, list[Hit]]:
        if not self.ready:
            return None, []
        idx = await self.index(chat_id)
        if idx is None:
            return None, []
        qvec = await self.library.vector(query)
        return idx, rank(idx, qvec, query, k)
