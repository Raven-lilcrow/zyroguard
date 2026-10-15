"""Which language is a message in? No network, no model, no extra package.

Non-Latin alphabets are told apart by their script (Cyrillic, Chinese, Japanese,
Korean, Arabic ...). Latin-alphabet languages are told apart by their most common
small words. When the answer is not clear (short messages, emoji, mixed Taglish)
the result is "unknown", and nothing is ever deleted for an unknown language.
"""

from __future__ import annotations

import re

NAMES = {
    "en": "English", "id": "Indonesian or Malay", "tl": "Tagalog", "vi": "Vietnamese", "es": "Spanish",
    "pt": "Portuguese", "fr": "French", "de": "German", "tr": "Turkish", "ru": "Russian (Cyrillic)",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "he": "Hebrew", "th": "Thai",
    "hi": "Hindi", "el": "Greek",
}

# what admins may type for each code
ALIASES = {
    "english": "en", "indonesian": "id", "bahasa": "id", "malay": "id", "ms": "id", "tagalog": "tl",
    "filipino": "tl", "fil": "tl", "vietnamese": "vi", "spanish": "es", "portuguese": "pt", "french": "fr",
    "german": "de", "turkish": "tr", "russian": "ru", "cyrillic": "ru", "ukrainian": "ru", "uk": "ru",
    "chinese": "zh", "mandarin": "zh", "japanese": "ja", "korean": "ko", "arabic": "ar", "hebrew": "he",
    "thai": "th", "hindi": "hi", "greek": "el",
}

_STOPWORDS = {
    "en": "the and is are you to of in for it that this with have my we what how not be on can just so but do me "
          "your was will if about they at all there get like any does anyone i'm it's don't know want need there's",
    "id": "yang dan ini itu di ke dari untuk dengan tidak ada saya kami kita anda apa juga bisa akan sudah aku kamu "
          "gak nggak tak lagi mau sama atau karena jadi belum bang kak aja dong deh banget",
    "tl": "ang ng mga sa na ay ako ikaw ka mo ko siya tayo kayo sila hindi oo po opo lang din rin naman ba yung "
          "kasi talaga wala may para kung ito yan dito pa nga sana ganun paano bakit natin namin",
    "es": "el la los las que y en un una es por para con no se lo como pero más muy yo está hola gracias del al "
          "también porque qué",
    "pt": "os as que em um uma é não nao para com por mais muito eu você voce está obrigado oi do da dos das "
          "também porque",
    "fr": "le la les des et est un une je tu il nous vous pas que qui pour dans avec sur ce c'est bonjour merci "
          "du au aussi",
    "de": "der die das und ist ich du nicht ein eine zu mit auf für es wir sie den dem auch wie hallo danke",
    "tr": "ve bir bu için ile ne ben sen var yok çok mi mı değil merhaba nasıl gibi",
}
_WORDS = {lang: set(words.split()) for lang, words in _STOPWORDS.items()}
_VI_CHARS = set("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")
_NOISE = re.compile(r"(?i)https?://\S+|www\.\S+|(?<!\w)@\w+|\[[^\]]*\]|\b\S+\.\S+/\S*")
_LATIN_WORD = re.compile(r"[a-zà-ɏḀ-ỿ']+")


def _script(ch: str) -> str | None:
    o = ord(ch)
    if o < 0x250 or 0x1E00 <= o <= 0x1EFF:
        return "latin"
    if 0x0400 <= o <= 0x052F:
        return "ru"
    if 0x0370 <= o <= 0x03FF:
        return "el"
    if 0x0590 <= o <= 0x05FF:
        return "he"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        return "ar"
    if 0x0900 <= o <= 0x097F:
        return "hi"
    if 0x0E00 <= o <= 0x0E7F:
        return "th"
    if 0x3040 <= o <= 0x30FF:
        return "kana"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
        return "ko"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return "zh"
    return None


def _latin_language(text: str) -> str:
    words = _LATIN_WORD.findall(text)
    if len(words) < 3:
        return "unknown"
    vi_words = sum(1 for w in words if _VI_CHARS & set(w))
    if vi_words >= 2 and vi_words >= 0.2 * len(words):
        return "vi"
    hits = {lang: sum(1 for w in words if w in vocab) for lang, vocab in _WORDS.items()}
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    (best, n1), (_, n2) = ranked[0], ranked[1]
    if n1 >= 2 and n1 >= 2 * n2:
        return best
    if hits["en"] >= 2:
        return "en"    # mixed, with plenty of English in it: read it as English
    return "unknown"


def detect(text: str) -> str:
    """Language code (see NAMES) or 'unknown'."""
    t = _NOISE.sub(" ", text or "").lower()
    counts: dict[str, int] = {}
    for ch in t:
        if ch.isalpha():
            s = _script(ch)
            if s:
                counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values())
    if total < 4:
        return "unknown"
    if counts.get("kana", 0) >= 2:
        return "ja"
    script, n = max(counts.items(), key=lambda kv: kv[1])
    if n < 0.5 * total:
        return "unknown"
    if script == "latin":
        return _latin_language(t)
    return script


def name(code: str) -> str:
    return NAMES.get(code, code)


def parse_languages(raw: str) -> str:
    """'English, tagalog' -> 'en,tl'. Empty, 'all' or 'any' -> '' (every language allowed)."""
    out: list[str] = []
    for part in re.split(r"[\s,;/]+", (raw or "").strip().lower()):
        if not part or part in {"all", "any", "none", "off"}:
            continue
        code = ALIASES.get(part, part)
        if code not in NAMES:
            raise ValueError(f"I do not know the language '{part}'. Use codes like en, tl, id, zh, ru, "
                             f"or names like English, Tagalog. Known: {', '.join(sorted(NAMES))}.")
        if code not in out:
            out.append(code)
    return ",".join(out)
