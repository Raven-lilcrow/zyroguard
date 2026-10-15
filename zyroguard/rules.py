"""Deterministic detection. No network, no LLM, microseconds per message.

``evaluate()`` takes a MessageView (built from an aiogram Message by the
handler) plus the group's settings and lists and returns a Verdict. The
handler decides what to do with it; this module never touches Telegram.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .durations import Duration
from .language import detect as detect_language, name as language_name

_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿­]")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s<>\"']*)?")
_TG_INVITE = re.compile(r"(?i)\b(?:t\.me|telegram\.me|telegram\.dog)/[/\w+?=&%.-]*|tg://(?:join|resolve)[\w?=&%.-]*")
_BOT_MENTION = re.compile(r"(?i)(?<![\w@])@[a-z0-9_]{2,30}bot\b")
_LATIN_WORD = re.compile(r"[A-Za-z]")
# Bare "name.tld" (no scheme, no www) only counts as a link with a real, common TLD,
# so file names like main.py or node.js are not links.
_BARE_TLDS = set("""
com net org io co me app xyz info biz ru uk de fr es it nl pl br in cn jp kr us ca au ch se no fi dk
at be cz tr ua kz vn id ph my sg hk tw ai dev site online store shop tech link click top club live vip
pro cc tv fm gg to ly gl ws su eu asia cash money finance crypto exchange trade market tk ml ga cf gq
zip mov icu buzz rest one world today news email cloud win bid loan men party trade stream download
""".split())

# Confusable map: the common Cyrillic / Greek lookalikes used to dodge filters.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "к": "k",
    "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ɡ": "g", "ո": "n", "ս": "u",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y", "К": "K",
    "В": "B", "Н": "H", "М": "M", "Т": "T", "І": "I",
    "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ν": "v", "τ": "t", "ι": "i", "κ": "k",
})


_SMALL_CAPS = str.maketrans({
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j",
    "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u",
    "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z", "т": "t", "г": "r", "м": "m", "н": "h", "в": "b",
})


# Emoji that spell letters: ❌❌❌ for "xxx", regional indicators 🇭🇴🇹 and the
# boxed letters 🅷🅾🆃 / 🅰 (NFKC leaves all of these alone).
_EMOJI_LETTERS = str.maketrans({
    **{c: "x" for c in "❌✖✕✗✘❎"},
    **{chr(0x1F1E6 + i): chr(ord("a") + i) for i in range(26)},
    **{chr(0x1F170 + i): chr(ord("a") + i) for i in range(26)},
    **{chr(0x1F150 + i): chr(ord("a") + i) for i in range(26)},
})


def fold(text: str) -> str:
    """Lowercase, NFKC-normalise, strip zero-width and direction-control chars, map confusables."""
    t = unicodedata.normalize("NFKC", text)
    t = _ZERO_WIDTH.sub("", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf" and ch not in "︎️")
    t = t.translate(_CONFUSABLES).translate(_SMALL_CAPS).translate(_EMOJI_LETTERS)
    return t.lower()


_SPACED = re.compile(r"\b(?:\w ){3,}\w\b")


def squeeze_spaced(text: str) -> str:
    """'U S D T holders' -> 'USDT holders': letters spaced out to dodge word filters."""
    return _SPACED.sub(lambda m: m.group(0).replace(" ", ""), text)


def text_hash(text: str) -> str:
    return hashlib.sha256(fold(re.sub(r"\s+", " ", text)).strip().encode()).hexdigest()[:24]


def mixed_script(text: str) -> bool:
    """True when a single word mixes Latin with Cyrillic/Greek letters."""
    for word in re.findall(r"\S+", text):
        has_latin = has_other = False
        for ch in word:
            if not ch.isalpha():
                continue
            name = unicodedata.name(ch, "")
            if name.startswith("LATIN"):
                has_latin = True
            elif name.startswith(("CYRILLIC", "GREEK")):
                has_other = True
            if has_latin and has_other:
                return True
    return False


def extract_domains(text: str) -> list[str]:
    out = []
    for m in _URL.finditer(text):
        raw = m.group(0)
        raw = re.sub(r"(?i)^https?://", "", raw)
        raw = re.sub(r"^www\.", "", raw)
        host = raw.split("/")[0].split("?")[0].split(":")[0].lower().strip(".")
        if "." in host and not host.replace(".", "").isdigit() and len(host) <= 253:
            # skip things like "e.g." or "1.5x" that the loose regex catches
            tld = host.rsplit(".", 1)[-1]
            explicit = raw != m.group(0)   # had a scheme or www.
            if len(tld) >= 2 and tld.isalpha() and (explicit or tld in _BARE_TLDS):
                out.append(host)
    return out


def domain_matches(host: str, patterns: set[str]) -> bool:
    for p in patterns:
        p = p.lower().lstrip(".")
        if host == p or host.endswith("." + p):
            return True
    return False


@dataclass
class HardRule:
    id: str
    desc: str
    regex: re.Pattern[str]
    action: str | None = None         # None = strike ladder
    duration: Duration | None = None
    source: str = "yaml"              # yaml | chat | global
    db_id: int | None = None


@dataclass
class SoftRule:
    id: str
    weight: int
    regex: re.Pattern[str] | None
    min_length: int | None = None
    needs: tuple[str, ...] = ()       # only counts when one of these other signals is also present
    only: str | None = None           # "jobs": only where job_rules is on; "no_jobs": only where it is off


@dataclass
class RuleSet:
    hard: list[HardRule]
    soft: list[SoftRule]
    impersonation: list[str]
    join_name: list[re.Pattern[str]]
    bio: list[re.Pattern[str]]
    global_allow_domains: set[str]
    judge_threshold: int
    hard_threshold: int
    repeat_window_minutes: int
    loaded_from: str
    safety_triggers: list[re.Pattern[str]] = field(default_factory=list)
    protected_names: list[str] = field(default_factory=list)   # protected in every group (the bot's own name)


def load_rules(path: Path) -> RuleSet:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    th = data.get("thresholds", {}) or {}
    hard = []
    for item in data.get("hard", []) or []:
        hard.append(HardRule(
            id=str(item["id"]), desc=str(item.get("desc", item["id"])),
            regex=re.compile(item["pattern"], re.IGNORECASE | re.DOTALL),
        ))
    soft = []
    for item in data.get("soft", []) or []:
        pat = item.get("pattern")
        soft.append(SoftRule(
            id=str(item["id"]), weight=int(item.get("weight", 1)),
            regex=re.compile(pat, re.IGNORECASE | re.DOTALL) if pat else None,
            min_length=item.get("min_length"),
            needs=tuple(str(x) for x in item.get("needs", []) or []),
            only=item.get("only"),
        ))
    return RuleSet(
        hard=hard, soft=soft,
        impersonation=[str(x).lower() for x in data.get("impersonation", []) or []],
        join_name=[re.compile(p, re.IGNORECASE) for p in data.get("join_name_patterns", []) or []],
        bio=[re.compile(p, re.IGNORECASE) for p in data.get("bio_patterns", []) or []],
        global_allow_domains={str(d).lower() for d in data.get("global_allow_domains", []) or []},
        judge_threshold=int(th.get("judge", 3)),
        hard_threshold=int(th.get("hard", 8)),
        repeat_window_minutes=int(th.get("repeat_window_minutes", 10)),
        loaded_from=str(path),
        safety_triggers=[re.compile(p, re.IGNORECASE) for p in data.get("safety_triggers", []) or []],
        protected_names=[str(x) for x in data.get("protected_names", []) or []],
    )


MAX_PATTERN_LEN = 200
_NESTED_QUANT = re.compile(r"\([^()]*[+*}|][^()]*\)\s*[+*{]|\([^()]*\)\s*[+*]\s*[+*]")
_BACKREF = re.compile(r"\\[1-9]")


def pattern_problem(pattern: str) -> str | None:
    """Why a custom pattern cannot be accepted, or None. Keeps out patterns that make the
    regex engine crawl (nested repeats, backreferences) since one of those would stall the
    bot for every group."""
    if len(pattern) > MAX_PATTERN_LEN:
        return f"That pattern is too long (max {MAX_PATTERN_LEN} characters)."
    if _BACKREF.search(pattern):
        return "Backreferences like \\1 are not allowed in patterns."
    if _NESTED_QUANT.search(pattern):
        return "That pattern repeats a repeated group, which can stall the bot. Simplify it."
    if pattern.count(".*") + pattern.count(".+") > 2:
        return "Too many .* or .+ in one pattern. Use at most two."
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"That pattern is not valid: {e}."
    return None


def compile_custom(rows: list[Any]) -> list[HardRule]:
    """Build HardRules from DB rows (chat_id 0 = global)."""
    out = []
    for r in rows:
        try:
            regex = re.compile(r["pattern"], re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        dur = Duration(r["duration_s"]) if r["duration_s"] is not None else (
            Duration(None) if r["action"] in {"mute", "ban"} else None
        )
        out.append(HardRule(
            id=f"custom_{r['id']}", desc=r["note"] or r["pattern"], regex=regex,
            action=r["action"], duration=dur,
            source="global" if r["chat_id"] == 0 else "chat", db_id=int(r["id"]),
        ))
    return out


@dataclass
class MessageView:
    """Everything the rules need to know about one message."""
    text: str = ""
    has_inline_buttons: bool = False
    forwarded_from_channel: bool = False
    forwarded: bool = False
    via_bot: bool = False
    media_type: str | None = None      # photo | video | document | sticker | animation | audio | voice | None
    sender_name: str = ""
    sender_username: str | None = None
    sender_is_admin: bool = False
    is_new_member: bool = True         # joined < restrict window or < N messages
    is_verified: bool = False
    account_is_new: bool = False       # first seen < new_account_age
    is_edit: bool = False
    caption_only: bool = False
    repeat_users: int = 0              # distinct users who posted identical text recently
    flood_hits: int = 0                # messages inside the flood window (incl. this one)
    extra: str = ""                    # button labels + URLs and the forward source name, scanned like text
    similar_points: int = 0            # from the meaning match (similar.py): +2/+4 near known spam, -2 near known-good


@dataclass
class Verdict:
    kind: str                          # hard | judge | clean
    score: int = 0
    matched: list[str] = field(default_factory=list)
    reason: str = ""
    action: str | None = None          # override from a custom rule
    duration: Duration | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_hard(self) -> bool:
        return self.kind == "hard"


@dataclass
class Context:
    """Per-group knobs the rules consult."""
    link_policy: str = "new"           # all | new | allowlist
    media_unverified: str = "delete"   # allow | delete
    flood_count: int = 5
    repeat_count: int = 3
    allow_domains: set[str] = field(default_factory=set)
    block_domains: set[str] = field(default_factory=set)
    custom_rules: list[HardRule] = field(default_factory=list)
    bot_username: str = ""             # the guard bot itself; mentioning it is fine
    allowed_languages: set[str] = field(default_factory=set)   # empty = every language allowed
    language_scope: str = "new"        # new | all: who the allowed-languages rule applies to
    protected_names: list[str] = field(default_factory=list)   # brand names only admins may use
    job_rules: bool = True             # fake-job / task-scam rules (rules marked only: jobs / no_jobs)
    phishing_rules: bool = True        # account-phishing rules (only: phishing)


RULE_PACKS = {"jobs": "job_rules", "phishing": "phishing_rules"}   # `only:` value -> Context switch


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@lru_cache(maxsize=256)
def _whole_word(word: str) -> re.Pattern[str]:
    # letters on either side mean it is part of a longer word: badminton, supporter, unofficial
    return re.compile(rf"(?<![a-z]){re.escape(word)}s?(?![a-z])")


def impersonation_word(name: str, username: str | None, words: list[str]) -> str | None:
    """The staff word ('admin', 'support' ...) the name uses as a word of its own, if any.
    CamelCase is split first, so JohnAdmin and SupportTeam count but BadmintonFan does not."""
    blob = fold(_CAMEL.sub(" ", f"{name} {username or ''}"))
    for word in words:
        if _whole_word(word).search(blob):
            return word
    return None


# Letters scammers swap to fake a brand name: AcmeAl (small L), Acm3AI, AcrneAI (rn for m).
_LOOKALIKE = str.maketrans({"1": "i", "l": "i", "|": "i", "!": "i", "0": "o", "3": "e", "4": "a",
                            "@": "a", "5": "s", "$": "s", "7": "t"})
_NAME_TOKEN = re.compile(r"[^\W_]+|[|!@$]")


def _skeleton(s: str) -> str:
    t = fold(s).translate(_LOOKALIKE).replace("rn", "m").replace("vv", "w")
    return re.sub(r"[^a-z]", "", t)


def _exact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s).lower())


def brand_in_name(name: str, username: str | None, brands: list[str]) -> tuple[str, bool] | None:
    """(brand, lookalike) when the name uses a protected brand name, else None.
    lookalike=True means it is spelled with swapped letters (AcmeAl, Acm3AI, Cyrillic А):
    nobody does that by accident. Words are compared whole, alone or joined with their
    neighbours ("Acme Al"), so a longer word that merely contains the brand does not count."""
    found = None
    for blob in (name or "", username or ""):
        tokens = _NAME_TOKEN.findall(_CAMEL.sub(" ", blob))
        for brand in brands:
            want_sk, want_ex = _skeleton(brand), _exact(brand)
            if len(want_sk) < 4:
                continue
            for i in range(len(tokens)):
                for j in range(i + 1, min(i + 4, len(tokens)) + 1):
                    window = "".join(tokens[i:j])
                    if _skeleton(window) != want_sk:
                        continue
                    if _exact(window) != want_ex:
                        return brand, True
                    found = (brand, False)
    return found


_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{5,32}")
_CRYPTO_TOKEN = re.compile(r"(?<![a-z0-9])(usdt|usdc|btc|eth|trx|bnb|sol|crypto|binance|okx|bybit|trc20|erc20)(?![a-z0-9])")
_PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def evaluate(msg: MessageView, rules: RuleSet, ctx: Context) -> Verdict:
    if msg.sender_is_admin:
        return Verdict("clean", reason="admin")
    brand = brand_in_name(msg.sender_name, msg.sender_username, ctx.protected_names + rules.protected_names)

    # the button labels/URLs and the "forwarded from" name are part of what members see
    raw = f"{msg.text}\n{msg.extra}" if msg.extra else msg.text
    folded = fold(raw)
    # original letters, lowercased: the non-English rules need real Cyrillic, which fold() turns into Latin
    plain = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", raw)).lower()
    squeezed = squeeze_spaced(folded)
    if squeezed != folded:
        folded = f"{folded}\n{squeezed}"   # match both spellings
    matched: list[str] = []
    detail: dict[str, Any] = {}

    def hard(reason: str, rule_id: str, action: str | None = None, duration: Duration | None = None) -> Verdict:
        matched.append(rule_id)
        return Verdict("hard", score=100, matched=matched, reason=reason, action=action, duration=duration, detail=detail)

    if brand and brand[1]:
        return hard(f"name imitates '{brand[0]}' with lookalike letters", "brand_lookalike")

    # -- structural signals -------------------------------------------------
    if msg.has_inline_buttons:
        return hard("inline buttons from a non-admin", "inline_buttons")
    if msg.via_bot and msg.is_new_member:
        return hard("message sent via an inline bot by a new member", "via_bot")
    if msg.forwarded_from_channel and msg.is_new_member:
        return hard("forward from a channel by a new member", "forward_channel")
    if msg.forwarded and msg.is_new_member:
        return hard("forwarded message from a new member", "forward_new_member")
    if ctx.flood_count > 0 and msg.flood_hits and msg.flood_hits >= ctx.flood_count:
        return hard(f"flooding: {msg.flood_hits} messages in a few seconds", "flood")

    # -- links ------------------------------------------------------------
    domains = extract_domains(raw)
    detail["domains"] = domains
    tg_invite = bool(_TG_INVITE.search(folded))
    # the same text from several accounts is a wave only when it carries a contact or link
    if (ctx.repeat_count > 0 and msg.repeat_users and msg.repeat_users >= ctx.repeat_count
            and (domains or tg_invite or "@" in raw)):
        return hard(f"the same text from {msg.repeat_users} accounts", "repeat_wave")
    if tg_invite and msg.is_new_member:
        return hard("telegram invite link from a new member", "tg_invite")
    if domains:
        blocked = [d for d in domains if domain_matches(d, ctx.block_domains)]
        if blocked:
            detail["blocked"] = blocked
            return hard(f"blocked domain {blocked[0]}", "block_domain")
        allowed_all = all(
            domain_matches(d, ctx.allow_domains) or domain_matches(d, rules.global_allow_domains)
            for d in domains
        )
        if not allowed_all:
            if ctx.link_policy == "allowlist":
                return hard(f"link to {domains[0]} not on allowlist", "link_allowlist")
            if ctx.link_policy == "new" and msg.is_new_member:
                return hard(f"link from a new member ({domains[0]})", "link_new_member")

    # -- bot mentions -----------------------------------------------------
    if msg.is_new_member:
        own = f"@{ctx.bot_username}".lower() if ctx.bot_username else None
        if any(h.lower() != own for h in _BOT_MENTION.findall(msg.text)):
            return hard("bot mention by a new member", "bot_mention")

    # -- media ------------------------------------------------------------
    if msg.media_type and msg.is_new_member and ctx.media_unverified == "delete":
        return hard(f"{msg.media_type} from a new member", "media_new_member")

    # -- impersonation ----------------------------------------------------
    word = impersonation_word(msg.sender_name, msg.sender_username, rules.impersonation)
    if word:
        return hard(f"name contains '{word}' but sender is not an admin", "impersonation")

    # -- evasion ----------------------------------------------------------
    if _ZERO_WIDTH.search(msg.text) and len(msg.text) > 20:
        matched.append("zero_width")
    if mixed_script(msg.text) and _LATIN_WORD.search(msg.text):
        matched.append("mixed_script")

    # -- custom regex rules (chat + global) --------------------------------
    for rule in ctx.custom_rules:
        if rule.regex.search(folded) or rule.regex.search(raw):
            return hard(f"custom rule: {rule.desc}", rule.id, rule.action, rule.duration)

    # -- yaml hard rules --------------------------------------------------
    for rule in rules.hard:
        if rule.regex.search(folded) or rule.regex.search(plain):
            return hard(rule.desc, rule.id)

    # -- soft scoring -----------------------------------------------------
    score = 0
    gated: list[tuple[SoftRule, int]] = []
    for rule in rules.soft:
        if rule.min_length is not None and len(msg.text) < rule.min_length:
            continue
        if rule.only:
            # rule packs a group can switch off: "jobs" runs only where job_rules is on, "no_jobs" only where it is off
            negate = rule.only.startswith("no_")
            if getattr(ctx, RULE_PACKS.get(rule.only.removeprefix("no_"), ""), True) == negate:
                continue
        if rule.regex is None:
            score += rule.weight
            matched.append(rule.id)
            continue
        hits = max(len(rule.regex.findall(folded)), len(rule.regex.findall(plain)))
        if hits and rule.needs:
            gated.append((rule, hits))
        elif hits:
            score += rule.weight * min(hits, 3)
            matched.append(rule.id)
    for rule, hits in gated:
        # e.g. 'leak' or 'rough' only count as adult words next to adult emoji or an 'open here' link
        if any(n in matched or (n == "forward_channel_soft" and msg.forwarded_from_channel) for n in rule.needs):
            score += rule.weight * min(hits, 3)
            matched.append(rule.id)
    if msg.similar_points:
        score += msg.similar_points
        matched.append("similar_spam" if msg.similar_points > 0 else "similar_ham")
    if brand:
        # the real brand name in a member's name: fine alone, but it adds weight to a pitch
        score += 2
        matched.append("brand_name")
    if "zero_width" in matched:
        score += 2
    if "mixed_script" in matched:
        score += 2
    if msg.is_new_member:
        score += 1
    if msg.account_is_new:
        score += 1
    if tg_invite:
        score += 2
        matched.append("tg_invite_soft")
    if domains and msg.is_new_member:
        score += 1
    if msg.is_edit and (domains or tg_invite):
        score += 3
        matched.append("edit_added_link")
    if msg.forwarded_from_channel:
        # channel reposts are how adult and scam channels spread; alone this only adds weight
        score += 2
        matched.append("forward_channel_soft")
    # -- language -----------------------------------------------------------
    lang = detect_language(msg.text)
    detail["language"] = lang
    if lang not in {"en", "unknown"}:
        # the English word rules cannot read this message, so risk signs count extra and send it to the judge
        signs = sum([bool(domains or tg_invite), bool(_HANDLE.search(raw)), bool(_CRYPTO_TOKEN.search(plain)),
                     bool(_PHONE.search(raw)), len(msg.text) >= 200,
                     sum(1 for ch in msg.text if unicodedata.category(ch) == "So") >= 3])
        if signs:
            score += min(3, 1 + signs)
            matched.append("foreign_language_risk")
    detail["score"] = score

    if score >= rules.hard_threshold:
        return Verdict("hard", score=score, matched=matched, reason="many spam signals at once", detail=detail)
    if (ctx.allowed_languages and lang != "unknown" and lang not in ctx.allowed_languages
            and (ctx.language_scope == "all" or msg.is_new_member)):
        # not spam, just not a language this group uses: delete only, no strike, no mute
        matched.append("language_not_allowed")
        allowed = ", ".join(language_name(c) for c in sorted(ctx.allowed_languages))
        return Verdict("hard", score=score, matched=matched, action="delete", detail=detail,
                       reason=f"written in {language_name(lang)}, this group allows {allowed}")
    if score >= rules.judge_threshold:
        return Verdict("judge", score=score, matched=matched, reason="some spam signals, asked the AI judge", detail=detail)
    return Verdict("clean", score=score, matched=matched, reason="clean", detail=detail)


def check_join_name(name: str, username: str | None, rules: RuleSet) -> str | None:
    blob = f"{name} {username or ''}"
    for pat in rules.join_name:
        if pat.search(blob) or pat.search(fold(blob)):
            return "display name contains a link"
    return None


def check_bio(bio: str | None, rules: RuleSet) -> str | None:
    if not bio:
        return None
    for pat in rules.bio:
        if pat.search(bio) or pat.search(fold(bio)):
            return "bio contains a link or a spam pitch"
    return None
