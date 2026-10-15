"""The per-message pipeline: exemptions, rules, judge, action."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from .actions import Decision, Target
from .app import App
from .rules import MessageView, Verdict, evaluate, text_hash
from .settings import ChatSettings
from .similar import LEARN_CONFIDENCE
from .store import from_iso, utcnow

log = logging.getLogger("zyroguard.pipeline")
router = Router(name="pipeline")
GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP}
# newcomer restrictions that are policy, not spam: delete only
HOUSE_RULES = {"media_new_member", "link_new_member", "bot_mention", "forward_channel", "via_bot",
               "tg_invite", "link_allowlist", "repeat_wave", "forward_new_member"}
# Judge categories that skip the strike ladder when the judge is this sure (impersonation_action)
SEVERE_CATEGORY = re.compile(r"imperson|phish|wallet|seed|drain", re.IGNORECASE)
SEVERE_CONFIDENCE = 0.95
# Soft rules that, when the score passes the hard threshold, mean a fake official notice
SEVERE_RULES = {"contract_migration", "old_token_burn"}
# Fake jobs and task scams: a hit on one of these, with nothing else but pay, money and contact
# signals next to it, is a job offer. Those always go to the AI judge (a real job post must not be
# removed by word rules alone) and are handled gently, see _job_decision.
JOB_RULES = {"job_bait", "task_scam", "easy_money", "ru_job_bait", "es_pt_job"}
JOB_COMPANIONS = {"pay_rate", "money_terms", "contact_pitch", "contact_pitch_more", "whatsapp_pivot", "whatsapp_more",
                  "pm_me", "dm_me_earn", "urgency",
                  "emoji_arrows", "telegram_contact", "long_pitch", "open_here", "tg_invite_soft",
                  "forward_channel_soft", "foreign_language_risk", "similar_spam", "ru_contact", "tl_money",
                  "tl_contact", "id_money", "id_contact", "vi_money", "vi_contact", "zh_money", "zh_contact",
                  "es_pt_money", "es_pt_contact", "ar_money", "ar_contact"}
JOB_REASON = "looks like a fake job or task offer"
# Account phishing ("your account will be deleted, verify here", "send me the login code"):
# members warning each other quote these, so phishing-only hits also always go to the AI judge,
# which bans when it is 95% sure (SEVERE_CATEGORY) and clears a quoted warning.
PHISH_RULES = {"code_request", "account_threat", "phish_deadline", "verify_link", "brand_domain", "kyc_selfie",
               "support_impersonation", "tl_account_threat"}
PHISH_COMPANIONS = {"fake_support", "contact_pitch", "contact_pitch_more", "whatsapp_pivot", "whatsapp_more", "pm_me",
                    "urgency", "telegram_contact", "open_here", "tg_invite_soft", "foreign_language_risk",
                    "similar_spam", "long_pitch", "emoji_arrows", "tl_contact", "id_contact", "vi_contact",
                    "zh_contact", "es_pt_contact", "ar_contact", "ru_contact"}


def job_offer(matched: list[str]) -> bool:
    m = set(matched)
    return bool(m & JOB_RULES) and m <= JOB_RULES | JOB_COMPANIONS


def phishing_offer(matched: list[str]) -> bool:
    m = set(matched)
    return bool(m & PHISH_RULES) and m <= PHISH_RULES | PHISH_COMPANIONS


async def _job_decision(app: App, m: Message, cs: ChatSettings, source: str, reason: str,
                        detail: dict[str, Any]) -> Decision:
    """Delete only, no strike, until this person's job_strike_after-th job scam here; from then on
    the normal strike ladder (decided 2026-10-13: just delete them, after 5 the ladder applies)."""
    limit = cs.int("job_strike_after")
    nth = await app.store.job_catches(m.chat.id, m.from_user.id) + 1
    detail = {**detail, "job_scam": True, "job_count": nth}
    if limit > 0 and nth >= limit:
        return Decision(source, f"{reason} (number {nth} from this person)", "delete", None, add_strike=True,
                        detail=detail)
    return Decision(source, reason, "delete", None, add_strike=False, detail=detail)
_last_outage_notice: dict[int, float] = {}    # chat_id -> monotonic time of the last "judge down" report
_cap_notice_day: dict[int, str] = {}          # chat_id -> day the "cap reached" line was logged


def media_type(m: Message) -> str | None:
    for kind in ("photo", "video", "document", "sticker", "animation", "audio", "voice", "video_note"):
        if getattr(m, kind, None):
            return kind
    return None


def message_text(m: Message) -> str:
    return m.text or m.caption or ""


def real_reply(m: Message) -> Message | None:
    """The message this one really replies to. In a forum group Telegram marks every
    message in a topic as a reply to the topic's first (service) message, so that
    automatic "reply" is ignored: otherwise /ban would hit whoever opened the topic."""
    r = m.reply_to_message
    if r is None:
        return None
    if r.forum_topic_created is not None:
        return None
    if m.is_topic_message and m.message_thread_id is not None and r.message_id == m.message_thread_id:
        return None
    return r


def thread_of(m: Message) -> int | None:
    """Forum topic id, so the bot's own posts about this message land in the same topic."""
    return m.message_thread_id if m.is_topic_message else None


def _entity_urls(m: Message) -> str:
    """Hidden hrefs in text_link entities, appended so the URL rules see them."""
    urls = []
    for ent in (m.entities or []) + (m.caption_entities or []):
        if ent.type == "text_link" and ent.url:
            urls.append(ent.url)
    return " ".join(urls)


def forward_source(m: Message) -> str:
    """Name of whoever a forward came from: channel title/@username, user or hidden name."""
    o = m.forward_origin
    if o is None:
        return ""
    chat = getattr(o, "chat", None) or getattr(o, "sender_chat", None)
    if chat is not None:
        return " ".join(x for x in (chat.title, f"@{chat.username}" if chat.username else None) if x)
    user = getattr(o, "sender_user", None)
    if user is not None:
        return " ".join(x for x in (user.full_name, f"@{user.username}" if user.username else None) if x)
    return getattr(o, "sender_user_name", "") or ""


def extra_text(m: Message) -> str:
    """What members see besides the message body: inline button labels and URLs, and the
    name of the forward source. Spam hides its link and its 🔞 there."""
    parts = []
    src = forward_source(m)
    if src:
        parts.append(f"[forwarded from {src}]")
    rows = m.reply_markup.inline_keyboard if m.reply_markup and getattr(m.reply_markup, "inline_keyboard", None) else []
    for row in rows:
        for b in row:
            parts.append(f"[button {b.text} {b.url or ''}]".replace(" ]", "]"))
    return "\n".join(parts)


def scan_text(m: Message) -> str:
    """Body, hidden links, buttons and forward source together: everything a check should read."""
    return "\n".join(x for x in (message_text(m), _entity_urls(m), extra_text(m)) if x)


async def build_view(app: App, m: Message, cs: ChatSettings, member: Any, is_admin: bool) -> MessageView:
    text = message_text(m)
    hidden = _entity_urls(m)
    if hidden:
        text = f"{text} {hidden}"
    now = utcnow()
    restrict = cs.seconds("restrict_window") or 0
    joined = from_iso(member["joined_at"]) if member and member["joined_at"] else None
    within_window = bool(joined and restrict and now - joined < timedelta(seconds=restrict))
    # also people whose join I never saw (they were here before me): until they have written
    # a few messages here they are treated as new, so a sleeper account cannot skip the newcomer rules
    few_messages = bool(member and int(member["msg_count"]) <= cs.int("new_member_msgs"))
    is_new = within_window or few_messages
    age = cs.seconds("new_account_age") or 0
    account_new = bool(joined and age and now - joined < timedelta(seconds=age))
    user = m.from_user
    return MessageView(
        text=text,
        has_inline_buttons=bool(m.reply_markup and m.reply_markup.inline_keyboard),
        forwarded_from_channel=bool(m.forward_origin and getattr(m.forward_origin, "type", "") == "channel"),
        forwarded=m.forward_origin is not None,
        via_bot=m.via_bot is not None,
        media_type=media_type(m),
        sender_name=user.full_name if user else "",
        sender_username=user.username if user else None,
        sender_is_admin=is_admin,
        is_new_member=is_new,
        is_verified=bool(member and member["verified_at"]),
        account_is_new=account_new,
        is_edit=m.edit_date is not None,
        caption_only=bool(m.caption and not m.text),
        extra=extra_text(m),
    )


def pick_photo_size(sizes):
    """The size closest to 800px wide: big enough to read text on screenshots, cheap enough to send."""
    return min(sizes, key=lambda s: abs((s.width or 0) - 800))


async def photo_bytes(app: App, m: Message) -> tuple[bytes | None, str]:
    if not m.photo:
        return None, "image/jpeg"
    try:
        size = pick_photo_size(m.photo)
        buf = await app.bot.download(size.file_id)
        return (buf.read() if buf else None), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        await app.store.log_error("photo", f"{type(e).__name__}: {e}", m.chat.id)
        return None, "image/jpeg"


async def decide(app: App, m: Message, cs: ChatSettings, view: MessageView, verdict: Verdict) -> Decision | None:
    """Translate a verdict into a Decision, calling the judge when needed."""
    jobs_on = cs.bool("job_rules")       # off: this group keeps the pre-2026-10-13 handling
    job = jobs_on and job_offer(verdict.matched)
    to_judge = verdict.kind == "judge"
    if verdict.kind == "hard" and job and verdict.score < 100:
        if not (cs.bool("judge") and app.judge.configured):
            return await _job_decision(app, m, cs, "rule", JOB_REASON, {"matched": verdict.matched, **verdict.detail})
        to_judge = True      # however many job words it has, the AI judge decides
    elif (verdict.kind == "hard" and verdict.score < 100 and cs.bool("phishing_rules")
          and phishing_offer(verdict.matched) and cs.bool("judge") and app.judge.configured):
        to_judge = True      # a member quoting a phishing message to warn others must not be removed
    if verdict.kind == "hard" and not to_judge:
        if "brand_lookalike" in verdict.matched or (
                SEVERE_RULES & set(verdict.matched) and verdict.score < 100):
            # a lookalike admin name, or a scored fake "the contract is moving" notice: no warning mute
            action, dur = cs.action("impersonation_action")
            return Decision("rule", verdict.reason, action, dur, add_strike=False,
                            detail={"matched": verdict.matched, **verdict.detail})
        if verdict.action:
            return Decision("rule", verdict.reason, verdict.action, verdict.duration,
                            add_strike=False, detail={"matched": verdict.matched, **verdict.detail})
        if "flood" in verdict.matched:
            action, dur = cs.action("flood_action")
            app.flood.reset_user(m.chat.id, m.from_user.id)
            return Decision("flood", verdict.reason, action, dur, add_strike=False,
                            detail={"matched": verdict.matched})
        if set(verdict.matched) <= HOUSE_RULES:
            # a house rule for newcomers (no links / media / bot mentions yet): just remove it,
            # no strike and no "new account" escalation, they may simply not know the rule
            return Decision("rule", verdict.reason, "delete", None, add_strike=False,
                            detail={"matched": verdict.matched, **verdict.detail})
        if view.account_is_new:
            action, dur = cs.action("new_account_action")
            return Decision("rule", verdict.reason + " (new account)", action, dur, add_strike=False,
                            detail={"matched": verdict.matched, **verdict.detail})
        return Decision("rule", verdict.reason, "delete", None,
                        detail={"matched": verdict.matched, **verdict.detail})
    if not to_judge:
        return None

    if not await app.ai_budget_ok(m.chat.id):
        return None  # this customer's daily AI budget is used up; rules still apply
    img, mime = (await photo_bytes(app, m)) if m.photo else (None, "image/jpeg")
    facts = {
        "new_member": view.is_new_member, "verified": view.is_verified,
        "messages_so_far": (await app.store.get_member(m.chat.id, m.from_user.id) or {"msg_count": 0})["msg_count"],
        "has_media": view.media_type or "none", "edited": view.is_edit, "forwarded": view.forwarded,
        "links": verdict.detail.get("domains", [])[:5],
        "username": view.sender_username or "none", "display_name": view.sender_name[:60],
        "language": verdict.detail.get("language", "unknown"),
    }
    sim = verdict.detail.get("similar") or {}
    if sim.get("applied") and sim.get("points", 0) > 0:
        facts["similar_to_known_spam"] = sim.get("spam")
    text = f"{view.text}\n{view.extra}".strip()
    examples = await _judge_examples(app, cs, m.chat.id, text)
    res = await app.judge.classify(
        chat_id=m.chat.id, text=text, sender_facts=facts, matched_rules=verdict.matched,
        image_bytes=img, image_mime=mime, daily_cap=cs.int("judge_daily_cap"), enabled=cs.bool("judge"),
        group_context=cs.str("judge_context"), examples=examples,
    )
    detail = {"matched": verdict.matched, "score": verdict.score, "judge": {
        "spam": res.spam, "category": res.category, "confidence": res.confidence,
        "reason": res.reason, "error": res.error, "skipped": res.skipped}}
    if examples:
        detail["judge"]["examples"] = [[e["id"], e["label"], e["similarity"]] for e in examples]
    if res.error:
        import time
        last = _last_outage_notice.get(m.chat.id, 0.0)
        if time.monotonic() - last < 3600:
            return None   # already reported this hour; do not flood the reports
        _last_outage_notice[m.chat.id] = time.monotonic()
        return Decision("judge", "the AI judge did not answer, so I left this message alone. Please take a look", "flag", None,
                        delete_message=False, add_strike=False, detail=detail)
    if res.skipped:
        if res.skipped == "cap":
            today = utcnow().strftime("%Y-%m-%d")
            if _cap_notice_day.get(m.chat.id) != today:
                _cap_notice_day[m.chat.id] = today
                log.warning("judge daily cap reached in %s (%s)", m.chat.title, m.chat.id)
        return None
    if res.spam and res.confidence >= SEVERE_CONFIDENCE and SEVERE_CATEGORY.search(res.category):
        # fake admins and wallet thieves do not get a warning mute first
        action, dur = cs.action("impersonation_action")
        return Decision("judge", f"{res.category}, {int(res.confidence * 100)}% sure: {res.reason}", action, dur,
                        add_strike=False, detail=detail)
    if res.spam and res.confidence >= cs.float("judge_threshold"):
        reason = f"{res.category}, {int(res.confidence * 100)}% sure: {res.reason}"
        if job or (jobs_on and res.category == "job_scam"):
            return await _job_decision(app, m, cs, "judge", reason, detail)
        action, dur = cs.action("judge_action")
        return Decision("judge", reason, action, dur, add_strike=(action == "delete"), detail=detail)
    return None


async def process(app: App, m: Message) -> bool:
    """Run the spam checks. Returns True when the message was left alone (clean, exempt, paused)."""
    chat_id = m.chat.id
    if not app.chat_allowed(chat_id):
        return False
    user = m.from_user
    app.last_update_at = utcnow()
    st = app.stat(chat_id)
    st.last_update_at = app.last_update_at
    if not (m.text and m.text.startswith("/")):
        st.plain_messages_seen += 1
    if user is None or user.id == app.bot_id:
        return False
    # anonymous admins and linked-channel posts arrive with sender_chat
    if m.sender_chat is not None and (m.sender_chat.id == chat_id or m.is_automatic_forward):
        return False
    if m.sender_chat is not None:
        return await _process_as_channel(app, m)
    cs = await app.settings.get(chat_id)
    is_admin = await app.admins.is_admin(chat_id, user.id)
    member = await app.store.touch_member(chat_id, user.id, user.username, user.full_name,
                                          count_message=not m.edit_date)
    if is_admin or await app.is_exempt(chat_id, user.id):
        return True
    if app.is_paused(chat_id):
        return True

    text = message_text(m)
    extra = extra_text(m)
    target = Target(chat_id, user.id, user.username, user.full_name, m.message_id,
                    f"{text}\n{extra}".strip() if extra else text, text_hash(text) if text else None, m.chat.title,
                    thread_of(m))

    pending = await app.store.captcha_get(chat_id, user.id)
    if pending and cs.bool("strict_unverified"):
        if cs.enforce:
            await app.enforcer.delete(chat_id, m.message_id)
        return False

    view = await build_view(app, m, cs, member, is_admin)
    if not m.edit_date:
        view.flood_hits = app.flood.hit_user(chat_id, user.id, cs.seconds("flood_window") or 10,
                                             album_id=m.media_group_id)
        if text and len(text) >= 20:
            view.repeat_users = app.flood.hit_text(chat_id, target.text_hash, user.id,
                                                   app.rules.repeat_window_minutes * 60)
    ctx = await app.rule_context(cs)
    verdict = await _evaluate_with_meaning(app, cs, view, ctx, m)
    if ctx.slow_rules:
        await app.drop_slow_rules(cs, ctx)
    if verdict.kind == "clean":
        return True
    decision = await decide(app, m, cs, view, verdict)
    if decision is None:
        return True
    outcome = await app.enforcer.apply(target, decision, cs)
    await _learn_from_catch(app, cs, target, decision, outcome)
    return False


def confident_catch(decision: Decision, hard_threshold: int) -> bool:
    """Sure enough to become a spam example: the judge at LEARN_CONFIDENCE or more, or a rule hit
    whose score passes the hard threshold without the meaning match's own points (else the
    library would teach itself), or one of the unmistakable hard rules. Name checks, house rules
    and language deletes say nothing about the text."""
    d = decision.detail
    matched = set(d.get("matched") or [])
    if decision.source == "judge":
        j = d.get("judge") or {}
        return bool(j.get("spam")) and float(j.get("confidence") or 0) >= LEARN_CONFIDENCE
    if decision.source != "rule" or not matched or matched <= HOUSE_RULES:
        return False
    if matched & {"brand_lookalike", "language_not_allowed"}:
        return False
    if matched & {"seed_phrase_request", "adult_marker"}:
        return True
    sim = d.get("similar") or {}
    own = int(d.get("score") or 0) - (int(sim.get("points") or 0) if sim.get("applied") else 0)
    return hard_threshold <= own < 100


async def _learn_from_catch(app: App, cs: ChatSettings, target: Target, decision: Decision, outcome) -> None:
    """Enforced, confident catches are held as spam examples for this group (similar.Library.hold)."""
    lib = app.similar
    if (lib is None or not lib.ready or outcome.shadow or not outcome.audit_id or not target.text
            or cs.choice("similarity") == "off" or not cs.bool("similarity_learn")
            or not confident_catch(decision, app.rules.hard_threshold)):
        return
    try:
        ex_id = await lib.hold(target.chat_id, target.text, outcome.audit_id)
    except Exception as e:  # noqa: BLE001
        log.exception("learning from a catch failed")
        await app.store.log_error("similar.hold", f"{type(e).__name__}: {e}", target.chat_id)
        return
    if ex_id:
        log.info("held example #%s from action %s in %s", ex_id, outcome.audit_id, target.chat_id)


async def _judge_examples(app: App, cs: ChatSettings, chat_id: int, text: str) -> list[dict[str, Any]]:
    """The closest labeled examples, for the judge to weigh as precedent."""
    lib = app.similar
    if (lib is None or not lib.ready or cs.choice("similarity") == "off" or not cs.bool("judge_examples")
            or not cs.bool("judge")):
        return []
    try:
        return await lib.neighbors(chat_id, text)
    except Exception as e:  # noqa: BLE001
        log.exception("looking up examples for the judge failed")
        await app.store.log_error("similar.neighbors", f"{type(e).__name__}: {e}", chat_id)
        return []


async def _evaluate_with_meaning(app: App, cs: ChatSettings, view: MessageView, ctx, m: Message) -> Verdict:
    """The rules, plus the meaning match when it is on. In shadow it is recorded, not counted."""
    mode = cs.choice("similarity")
    lib = app.similar
    match = None
    if mode != "off" and lib is not None and lib.ready and not view.is_edit:
        try:
            match = await lib.match(m.chat.id, f"{view.text}\n{view.extra}".strip())
        except Exception as e:  # noqa: BLE001
            log.exception("meaning match failed")
            await app.store.log_error("similar.match", f"{type(e).__name__}: {e}", m.chat.id)
    if match is None or not match.notable:
        verdict = evaluate(view, app.rules, ctx)
        if match is not None:
            verdict.detail["similar"] = match.as_detail()
        return verdict
    without = evaluate(view, app.rules, ctx)
    view.similar_points = match.points
    with_it = evaluate(view, app.rules, ctx)
    applied = mode == "on"
    await app.store.similarity_log_add(m.chat.id, m.from_user.id if m.from_user else None, match.spam, match.ham,
                                       match.spam_id if match.points > 0 else match.ham_id, applied,
                                       with_it.kind, without.kind, view.text)
    verdict = with_it if applied else without
    if not applied:
        view.similar_points = 0
    verdict.detail["similar"] = {**match.as_detail(), "applied": applied}
    return verdict


async def _process_as_channel(app: App, m: Message) -> bool:
    """A member posting under a channel identity ("send as channel"). There is no user to
    mute, so a hit means: delete, and ban that channel from posting here."""
    chat_id = m.chat.id
    sc = m.sender_chat
    cs = await app.settings.get(chat_id)
    if app.is_paused(chat_id):
        return True
    text = message_text(m)
    hidden = _entity_urls(m)
    view = MessageView(text=f"{text} {hidden}".strip(), has_inline_buttons=m.reply_markup is not None,
                       forwarded_from_channel=bool(m.forward_origin), media_type=media_type(m),
                       sender_name=sc.title or "", sender_username=sc.username, is_new_member=True,
                       is_edit=bool(m.edit_date), extra=extra_text(m))
    ctx = await app.rule_context(cs)
    verdict = evaluate(view, app.rules, ctx)
    if ctx.slow_rules:
        await app.drop_slow_rules(cs, ctx)
    if verdict.kind != "hard":
        return True
    target = Target(chat_id, None, sc.username, f"channel: {sc.title or sc.id}", m.message_id, text,
                    text_hash(text) if text else None, m.chat.title, thread_of(m))
    action = "ban" if verdict.matched and verdict.matched[0] not in HOUSE_RULES and verdict.action != "delete" else "delete"
    outcome = await app.enforcer.apply(target, Decision("rule", verdict.reason + " (posted as a channel)", action, None,
                                                         add_strike=False, detail={"matched": verdict.matched,
                                                                                   "sender_chat": sc.id}), cs)
    if cs.enforce and action == "ban":
        try:
            await app.bot.ban_chat_sender_chat(chat_id, sc.id)
        except Exception as e:  # noqa: BLE001
            await app.store.log_error("ban_sender_chat", f"{type(e).__name__}: {e}", chat_id)
    _ = outcome
    return False


async def _handle(message: Message, app: App, edited: bool) -> None:
    try:
        clean = await process(app, message)
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline failed")
        await app.store.log_error("pipeline.edit" if edited else "pipeline", f"{type(e).__name__}: {e}", message.chat.id)
        return
    if not clean:
        return
    from .safety import check as safety_check
    try:
        if await safety_check(app, message):
            return
    except Exception as e:  # noqa: BLE001
        log.exception("safety failed")
        await app.store.log_error("safety", f"{type(e).__name__}: {e}", message.chat.id)
    if edited:
        return
    if message.forward_origin is not None:
        from .forwards import challenge
        try:
            if await challenge(app, message):
                return
        except Exception as e:  # noqa: BLE001
            log.exception("forward check failed")
            await app.store.log_error("forward", f"{type(e).__name__}: {e}", message.chat.id)
    from .support import after_clean
    try:
        if await after_clean(app, message):
            return
    except Exception as e:  # noqa: BLE001
        log.exception("support failed")
        await app.store.log_error("support", f"{type(e).__name__}: {e}", message.chat.id)
    from .assistant import answer, question_text, wants_answer
    if wants_answer(app, message):
        try:
            await answer(app, message, question_text(app, message))
        except Exception as e:  # noqa: BLE001
            log.exception("assistant failed")
            await app.store.log_error("assistant", f"{type(e).__name__}: {e}", message.chat.id)


@router.message(F.chat.type.in_(GROUPS))
async def on_message(message: Message, app: App) -> None:
    await _handle(message, app, edited=False)


@router.edited_message(F.chat.type.in_(GROUPS))
async def on_edit(message: Message, app: App) -> None:
    await _handle(message, app, edited=True)   # spam rules and the safety check re-run on edits
