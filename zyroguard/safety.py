"""Safety check: explicit, violent, harassing or hateful content, in text or on images.

Runs after the spam pipeline let a message through. Images from non-exempt
members are always checked when the feature is on; text is checked only when
it matches one of the safety_triggers in rules.yaml, so a quiet group costs
nothing. Uses the same Claude client, model and daily cap as the spam judge.
"""

from __future__ import annotations

import logging

from aiogram.types import Message

from .actions import Decision, Target
from .app import App
from .judge import classify_safety
from .rules import fold, text_hash

log = logging.getLogger("zyroguard.safety")

VISUAL_FIELDS = ("photo", "sticker", "video", "animation", "document", "video_note")


async def media_bytes(app: App, m: Message) -> tuple[bytes | None, str]:
    """Best image we can get for the message: the photo, a static sticker, or a video thumbnail."""
    try:
        if m.photo:
            from .pipeline import pick_photo_size
            size = pick_photo_size(m.photo)
            buf = await app.bot.download(size.file_id)
            return (buf.read() if buf else None), "image/jpeg"
        if m.sticker and not (m.sticker.is_animated or m.sticker.is_video):
            buf = await app.bot.download(m.sticker.file_id)
            return (buf.read() if buf else None), "image/webp"
        for obj in (m.sticker, m.video, m.animation, m.document, m.video_note):
            thumb = getattr(obj, "thumbnail", None) if obj is not None else None
            if thumb:
                buf = await app.bot.download(thumb.file_id)
                return (buf.read() if buf else None), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("safety.media", f"{type(e).__name__}: {e}", m.chat.id)
    return None, "image/jpeg"


def has_visual(m: Message) -> bool:
    return any(getattr(m, k, None) for k in VISUAL_FIELDS)


def text_triggers(app: App, text: str) -> bool:
    if not text:
        return False
    folded = fold(text)   # so ❌❌❌ reads as xxx and 🅷🅾🆃 as hot
    return any(p.search(text) or p.search(folded) for p in app.rules.safety_triggers)


_clean_media: dict[tuple[int, str], float] = {}


def _media_uid(m: Message) -> str | None:
    if m.photo:
        return m.photo[-1].file_unique_id
    if m.sticker:
        return m.sticker.file_unique_id
    return None


def _remember_clean(chat_id: int, uid: str) -> None:
    import time
    now = time.monotonic()
    _clean_media[(chat_id, uid)] = now
    if len(_clean_media) > 5000:
        for k, ts in list(_clean_media.items()):
            if now - ts > 86400:
                _clean_media.pop(k, None)


async def check(app: App, m: Message) -> bool:
    """Returns True when the message was removed."""
    user = m.from_user
    if not user or user.id == app.bot_id:
        return False
    cs = await app.settings.get(m.chat.id)
    if not cs.bool("safety") or app.is_paused(m.chat.id):
        return False
    if await app.admins.is_admin(m.chat.id, user.id) or await app.is_exempt(m.chat.id, user.id):
        return False
    from .pipeline import scan_text, thread_of
    text = scan_text(m)   # body plus buttons and forward source
    visual = has_visual(m)
    triggered = text_triggers(app, text)
    if not visual and not triggered:
        return False
    if not await app.ai_budget_ok(m.chat.id):
        return False
    img, mime = (await media_bytes(app, m)) if visual else (None, "image/jpeg")
    if visual and img is None and not triggered:
        return False  # animated sticker without a thumbnail, nothing to look at
    uid = _media_uid(m)
    if uid and not triggered and (m.chat.id, uid) in _clean_media:
        return False   # this exact picture or sticker was already judged clean here
    res = await classify_safety(app.judge, chat_id=m.chat.id, text=text, image_bytes=img, image_mime=mime,
                                daily_cap=cs.int("safety_daily_cap"))
    if res.error or res.skipped or not res.spam or res.confidence < cs.float("judge_threshold"):
        if uid and res.error is None and res.skipped is None:
            _remember_clean(m.chat.id, uid)
        return False
    if res.category == "scam":
        # a scam carried by an image is spam: same punishment ladder as the spam judge
        source, action, dur = "judge", *cs.action("judge_action")
    else:
        source, action, dur = "safety", *cs.action("safety_action")
    if action == "none":
        return False
    shown = text or ("[image]" if img else "[media]")
    target = Target(m.chat.id, user.id, user.username, user.full_name, m.message_id, shown,
                    text_hash(text) if text else None, m.chat.title, thread_of(m))
    decision = Decision(source, f"{res.category}: {res.reason}", action, dur, delete_message=True,
                        add_strike=(action == "delete"),
                        detail={"safety": {"category": res.category, "confidence": res.confidence,
                                           "reason": res.reason, "image": img is not None}})
    await app.enforcer.apply(target, decision, cs)
    return True
