"""Read-only review of the meaning-match shadow trial. Run inside the container:

    docker exec -i zyroguard python - < tools/similarity_report.py

Answers "what would happen if I ran /set similarity on?" from the similarity_log: every
match that would have changed a verdict, what the bot actually did to that sender anyway,
which examples do the matching, and how the scores spread. Opens the database read-only,
sends nothing to Telegram, prints no secrets.
"""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from zyroguard.config import load_config
from zyroguard.similar import CLOSE, GOOD, MODEL, STRONG

cfg = load_config()
db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
now = datetime.now(timezone.utc)
q = lambda sql, *a: list(db.execute(sql, a))  # noqa: E731
parse = lambda ts: datetime.fromisoformat(ts.replace("Z", "+00:00"))  # noqa: E731
cut = lambda s, n=90: " ".join((s or "").split())[:n]  # noqa: E731

tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
if not {"examples", "similarity_log"} <= tables:
    print("Meaning match is not deployed on this database yet.")
    raise SystemExit(0)

titles = {cid: t for cid, t in q("SELECT chat_id, title FROM chats")}
name = lambda cid: str(titles.get(cid, cid))[:14]  # noqa: E731
example_text = {i: (lab, t) for i, lab, t in q("SELECT id, label, text FROM examples")}

print("SIMILARITY REPORT (read-only)")
print(f"model {MODEL.split('/')[-1]} · strong {STRONG} · close {CLOSE} · known-good {GOOD}")
print(f"server switch SIMILARITY={'on' if cfg.similarity_enabled else 'off'}")
explicit = {cid: v for cid, v in q("SELECT chat_id, value FROM chat_settings WHERE key='similarity'")}
for cid, title, active in q("SELECT chat_id, title, active FROM chats WHERE active=1 ORDER BY added_at"):
    print(f"  {title!r:32} similarity {explicit.get(cid, 'shadow (default)')}")

print("\nLIBRARY (active examples)")
lib = Counter((("shared" if c == 0 else name(c)), lab, src)
              for c, lab, src in q("SELECT chat_id, label, source FROM examples WHERE active=1"))
for (scope, lab, src), n in sorted(lib.items()):
    print(f"  {scope:14} {lab:4} {src:6} {n}")
cols = {r[1] for r in q("PRAGMA table_info(examples)")}
if "held_until" in cols:
    for scope, n, nxt in q("SELECT chat_id, COUNT(*), MIN(held_until) FROM examples WHERE held_until IS NOT NULL "
                           "GROUP BY chat_id"):
        print(f"  {name(scope):14} spam auto   {n} waiting (first counts at {nxt[:16]})")
    print(f"  inactive (forgotten, undone or replaced): "
          f"{q('SELECT COUNT(*) FROM examples WHERE active=0 AND held_until IS NULL')[0][0]}")
else:
    print(f"  inactive (forgotten or replaced): {q('SELECT COUNT(*) FROM examples WHERE active=0')[0][0]}")
shown = q("SELECT COUNT(*) FROM audit WHERE source='judge' AND detail LIKE '%\"examples\"%'")[0][0]
print(f"  judge actions that were shown examples: {shown}")

rows = q("SELECT id, ts, chat_id, user_id, spam_sim, ham_sim, example_id, applied, kind_with, kind_without, excerpt "
         "FROM similarity_log ORDER BY id")
print("\nMATCHES")
if not rows:
    print("  none logged yet. A match is only logged when it would add or take off points")
    print(f"  (spam >= {CLOSE} or known-good >= {GOOD}), so a quiet log can simply mean no look-alikes came in.")
    db.close()
    raise SystemExit(0)

first = parse(rows[0][1])
hours = (now - first).total_seconds() / 3600
print(f"  window {rows[0][1][:16]} to now ({hours:.0f} h) · {len(rows)} logged · "
      f"{sum(1 for r in rows if r[7])} counted (setting on), {sum(1 for r in rows if not r[7])} shadow")
print("  per group:", dict(Counter(name(r[2]) for r in rows)))
moves = Counter(f"{r[9]}->{r[8]}" for r in rows)
print("  verdict without -> with:", dict(moves))

buckets = Counter()
for r in rows:
    s = r[4]
    if s >= CLOSE:
        buckets["0.90+" if s >= 0.90 else f"{int(s * 20) / 20:.2f}"] += 1
print("  spam score spread:", {k: buckets[k] for k in sorted(buckets)} or "no spam-side matches")

hot = Counter(r[6] for r in rows if r[6] is not None)
print("\n  examples doing the matching:")
for exid, n in hot.most_common(8):
    lab, text = example_text.get(exid, ("?", "(gone)"))
    print(f"    #{exid:<4} {lab:4} {n:3}x  {cut(text, 80)!r}")


def outcome(chat_id, user_id, ts):
    """What the bot or an admin did to this sender from a minute before the match to a day after."""
    if user_id is None:
        return "no sender id"
    t = parse(ts)
    acts = q("SELECT source, action, mode, undone_at, detail FROM audit WHERE chat_id=? AND user_id=? "
             "AND ts>=? AND ts<=? ORDER BY id", chat_id, user_id,
             (t - timedelta(minutes=1)).isoformat(), (t + timedelta(days=1)).isoformat())
    if not acts:
        return "nothing happened to this sender"
    src, action, mode, undone, detail = acts[0]
    extra = " UNDONE" if undone else ""
    try:
        cat = json.loads(detail or "{}").get("category")
    except json.JSONDecodeError:
        cat = None
    return f"{src} {action} ({mode}){f' {cat}' if cat else ''}{extra}" + (f" +{len(acts) - 1} more" if len(acts) > 1 else "")


changed = [r for r in rows if r[8] != r[9]]
raised = [r for r in changed if r[8] != "clean" and (r[9] == "clean" or r[8] == "hard")]
lowered = [r for r in changed if r not in raised]
fates = Counter()

for title, group in (("WOULD RAISE (more suspicion than the rules alone)", raised),
                     ("WOULD LOWER (a known-good example matched)", lowered)):
    print(f"\n{title}: {len(group)}")
    for i, ts, cid, uid, s, h, exid, applied, kw, ko, excerpt in group:
        fate = outcome(cid, uid, ts)
        if group is raised:
            fates["nothing happened" if fate.startswith("nothing") else "actioned anyway"] += 1
        lab, text = example_text.get(exid, ("?", "(gone)"))
        print(f"  {ts[5:16]} {name(cid):14} spam {s:.2f} ok {h:.2f} {ko}->{kw} {'ON' if applied else 'shadow'}")
        print(f"    msg   {cut(excerpt)!r}")
        print(f"    like  #{exid} {lab} {cut(text, 80)!r}")
        print(f"    then  {fate}")

agreed = sum(1 for r in rows if r[8] == r[9] == "hard" and r[4] >= CLOSE)
print("\nREAD")
print(f"  {agreed} matches agreed with a hard rule hit (the rules already removed those)")
if raised:
    print(f"  of {len(raised)} raises: {fates['actioned anyway']} senders were actioned anyway, "
          f"{fates['nothing happened']} were not. The second group is what 'on' would add: read those above.")
print(f"  {len(lowered)} lowered by a known-good match: each one should be a normal message, not a scam")
if hours < 72:
    print(f"  only {hours:.0f} h of data: keep it in shadow until about 3 days have passed")
db.close()
