"""Numbers for the ZyroGuard case study. Read-only, anonymised. Run inside the live container:

    docker exec -i zyroguard python - < tools/case_study_stats.py > case-study/stats.json

Opens the database read-only and asks Telegram only for member counts. Output has no
group names, chat ids, user ids, usernames or message text: groups become "Group A, B..."
by size, reasons become rule ids and judge categories.
"""
import asyncio
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from zyroguard.config import load_config

INTERNAL = {"test", "log"}   # groups whose title contains these are the owner's own test/log chats


def day(ts: str) -> str:
    return ts[:10]


async def main():
    cfg = load_config()
    db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    bot = Bot(cfg.bot_token)
    chats = list(db.execute("SELECT chat_id, title, added_at FROM chats WHERE active=1"))
    sizes, forums = {}, {}
    try:
        for c in chats:
            try:
                sizes[c["chat_id"]] = await bot.get_chat_member_count(c["chat_id"])
                forums[c["chat_id"]] = bool(getattr(await bot.get_chat(c["chat_id"]), "is_forum", False))
            except Exception:  # noqa: BLE001
                sizes[c["chat_id"]] = None
    finally:
        await bot.session.close()

    real = [c for c in chats if not any(w in (c["title"] or "").lower() for w in INTERNAL)]
    real.sort(key=lambda c: -(sizes.get(c["chat_id"]) or 0))
    label = {c["chat_id"]: f"Group {chr(65 + i)}" for i, c in enumerate(real)}

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "groups": [], "overall": {}}
    totals = Counter()
    daily = Counter()
    rule_ids = Counter()
    judge_cats = Counter()
    for c in real:
        cid = c["chat_id"]
        mode_row = db.execute("SELECT value, updated_at FROM chat_settings WHERE chat_id=? AND key='mode'", (cid,)).fetchone()
        enforce_since = mode_row["updated_at"] if mode_row and mode_row["value"] == "enforce" else None
        rows = list(db.execute("SELECT * FROM audit WHERE chat_id=? ORDER BY id", (cid,)))
        g = {"label": label[cid], "members": sizes.get(cid), "topics": forums.get(cid, False),
             "bot_added": day(c["added_at"]), "mode": mode_row["value"] if mode_row else cfg.default_mode,
             "enforce_since": day(enforce_since) if enforce_since else None}
        by_mode = Counter(r["mode"] for r in rows)
        auto = [r for r in rows if r["source"] != "admin"]
        enforced = [r for r in auto if r["mode"] == "enforce"]
        g["actions_total"] = len(rows)
        g["automatic_actions"] = len(auto)
        g["shadow_would_have"] = by_mode.get("shadow", 0)
        g["enforced"] = len(enforced)
        g["admin_manual"] = len(rows) - len(auto)
        g["undone_of_enforced"] = sum(1 for r in enforced if r["undone_at"])
        g["by_source"] = dict(Counter(r["source"] for r in auto).most_common())
        g["by_action"] = dict(Counter(r["action"] for r in enforced).most_common())
        if enforce_since:
            end = (datetime.fromisoformat(enforce_since) + timedelta(days=7)).isoformat()
            wk = [r for r in enforced if enforce_since <= r["ts"] < end]
            g["first_enforce_week"] = {"actions": len(wk), "by_source": dict(Counter(r["source"] for r in wk).most_common())}
        m = db.execute("SELECT COUNT(*) n, SUM(msg_count) msgs, SUM(joined_at IS NOT NULL) joins, "
                       "SUM(joined_at IS NOT NULL AND verified_at IS NOT NULL) verified FROM members WHERE chat_id=?",
                       (cid,)).fetchone()
        g["members_seen"] = m["n"]
        g["messages_screened"] = m["msgs"] or 0
        g["joins_seen"] = m["joins"] or 0
        g["joins_verified"] = m["verified"] or 0
        g["captcha_failed"] = sum(1 for r in auto if r["source"] == "captcha")
        g["known_spammer_blocked"] = sum(1 for r in auto if r["source"] == "cas")
        ai = db.execute("SELECT COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o FROM judge_usage "
                        "WHERE chat_id=? AND (verdict IS NULL OR verdict != 'assistant')", (cid,)).fetchone()
        g["ai_checks"] = ai["n"]
        g["ai_tokens"] = {"input": ai["i"] or 0, "output": ai["o"] or 0}
        g["ai_questions_answered"] = db.execute(
            "SELECT COUNT(*) FROM judge_usage WHERE chat_id=? AND verdict='assistant'", (cid,)).fetchone()[0]
        for r in auto:
            daily[day(r["ts"])] += 1
            try:
                d = json.loads(r["detail"] or "{}")
            except json.JSONDecodeError:
                d = {}
            for rid in d.get("matched") or []:
                rule_ids[rid if not rid.startswith("custom_") else "custom rule"] += 1
            if (d.get("judge") or {}).get("category"):
                judge_cats[d["judge"]["category"]] += 1
            if (d.get("safety") or {}).get("category"):
                judge_cats["safety:" + d["safety"]["category"]] += 1
        for k in ("actions_total", "automatic_actions", "shadow_would_have", "enforced", "admin_manual",
                  "undone_of_enforced", "messages_screened", "joins_seen", "joins_verified", "captcha_failed",
                  "known_spammer_blocked", "ai_checks", "ai_questions_answered"):
            totals[k] += g[k] or 0
        totals["ai_input_tokens"] += g["ai_tokens"]["input"]
        totals["ai_output_tokens"] += g["ai_tokens"]["output"]
        out["groups"].append(g)

    verdicts = Counter()
    for (v,) in db.execute("SELECT verdict FROM judge_usage WHERE chat_id IN (%s) AND verdict IS NOT NULL AND verdict != 'assistant'"
                           % ",".join(str(c["chat_id"]) for c in real) or "0"):
        if v == "error":
            verdicts["error"] += 1
        else:
            parts = v.split(":")
            cat = parts[1] if parts[0] == "safety" else parts[0]
            verdicts["clean" if cat == "clean" else "flagged"] += 1
    out["overall"] = {**totals, "members_protected": sum(sizes.get(c["chat_id"]) or 0 for c in real),
                      "groups": len(real), "first_day": min(daily) if daily else None,
                      "last_day": max(daily) if daily else None,
                      "busiest_days": dict(sorted(daily.items(), key=lambda kv: -kv[1])[:5]),
                      "daily_automatic_actions": dict(sorted(daily.items())),
                      "top_rule_ids": dict(rule_ids.most_common(15)),
                      "ai_categories_on_actions": dict(judge_cats.most_common()),
                      "ai_check_outcomes": dict(verdicts)}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    db.close()


asyncio.run(main())
