"""Read-only health check of the live bot. Run inside the container:

    docker exec -i zyroguard python - < tools/health_check.py

Opens the database read-only and prints: integrity, recent errors, actions per day,
AI usage and failures, and any background job that is overdue (captchas, forward
checks, timed lifts, expiring bot messages). Sends nothing to Telegram, prints no secrets.
"""
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

from zyroguard.config import load_config

cfg = load_config()
db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
now = datetime.now(timezone.utc)
iso = lambda dt: dt.isoformat(timespec="seconds")  # noqa: E731
NOW, WEEK = iso(now), iso(now - timedelta(days=7))
q = lambda sql, *a: list(db.execute(sql, a))  # noqa: E731

print("DB", q("PRAGMA integrity_check")[0][0], f"{os.path.getsize(cfg.db_path) / 1e6:.1f} MB")
titles = {cid: t for cid, t in q("SELECT chat_id, title FROM chats")}
print("\nCHATS")
for cid, title, active, owner in q("SELECT chat_id, title, active, owner_user_id FROM chats ORDER BY added_at"):
    mode = q("SELECT value FROM chat_settings WHERE chat_id=? AND key='mode'", cid)
    n = len(q("SELECT 1 FROM chat_settings WHERE chat_id=?", cid))
    print(f"  {title!r:32} active={active} mode={mode[0][0] if mode else cfg.default_mode + ' (default)'} "
          f"settings={n} permit_owner={'yes' if owner else 'no'}")

print("\nERRORS last 7 days (grouped)")
errs = Counter((w, (m or "")[:140].replace("\n", " ")) for w, m in q("SELECT where_, message FROM errors WHERE ts>=?", WEEK))
for (w, m), n in errs.most_common(25):
    print(f"  {n:4}x {w}: {m}")
print("  total", sum(errs.values()), "· all time", q("SELECT COUNT(*) FROM errors")[0][0],
      "· last", q("SELECT MAX(ts) FROM errors")[0][0])

print("\nACTIONS per day last 7 days (automatic, by source)")
per = {}
for ts, src, mode in q("SELECT ts, source, mode FROM audit WHERE ts>=? AND source!='admin'", WEEK):
    per.setdefault(ts[:10], Counter())[f"{src}{'(shadow)' if mode == 'shadow' else ''}"] += 1
for d in sorted(per):
    print(f"  {d} {sum(per[d].values()):3}  {dict(per[d])}")
print("  last action", q("SELECT MAX(ts) FROM audit")[0][0], "· undone last 7d",
      q("SELECT COUNT(*) FROM audit WHERE ts>=? AND undone_at IS NOT NULL", WEEK)[0][0],
      "· admin manual last 7d", q("SELECT COUNT(*) FROM audit WHERE ts>=? AND source='admin'", WEEK)[0][0])
lang = q("SELECT COUNT(*) FROM audit WHERE ts>=? AND detail LIKE '%language_not_allowed%'", WEEK)[0][0]
fwd = Counter(a for (a,) in q("SELECT action FROM audit WHERE ts>=? AND source='forward'", WEEK))
print(f"  language deletes 7d: {lang} · forward-check outcomes 7d: {dict(fwd)}")

print("\nAI last 7 days")
v = Counter()
for (verdict,) in q("SELECT verdict FROM judge_usage WHERE ts>=?", WEEK):
    v[(verdict or "none").split(":")[0] if not (verdict or "").startswith("safety") else "safety"] += 1
tok = q("SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM judge_usage WHERE ts>=?", WEEK)[0]
print(f"  calls {tok[0]} · tokens in {tok[1] or 0} out {tok[2] or 0} · by verdict {dict(v)}")
print("  model", cfg.judge_model, "· effort", cfg.judge_effort)

print("\nBACKGROUND JOBS (overdue = should already have been handled)")
late = iso(now - timedelta(minutes=10))
print("  captchas overdue >10m:", q("SELECT COUNT(*) FROM pending_captcha WHERE deadline<?", late)[0][0],
      "· open:", q("SELECT COUNT(*) FROM pending_captcha")[0][0])
print("  forward checks overdue >10m:", q("SELECT COUNT(*) FROM pending_forward WHERE deadline<?", late)[0][0],
      "· open:", q("SELECT COUNT(*) FROM pending_forward")[0][0])
print("  timed lifts overdue >10m:", q("SELECT COUNT(*) FROM timed_actions WHERE done=0 AND expires_at<?", late)[0][0],
      "· open:", q("SELECT COUNT(*) FROM timed_actions WHERE done=0")[0][0])
print("  expiring bot messages overdue >10m:", q("SELECT COUNT(*) FROM expiring_messages WHERE delete_at<?", late)[0][0],
      "· open:", q("SELECT COUNT(*) FROM expiring_messages")[0][0])

print("\nMEANING MATCH")
tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
if "examples" in tables:
    ex = Counter((("shared" if c == 0 else "group"), lab, src)
                 for c, lab, src in q("SELECT chat_id, label, source FROM examples WHERE active=1"))
    print("  examples:", dict(ex) or "none yet")
    for cid, mode in q("SELECT chat_id, value FROM chat_settings WHERE key='similarity'"):
        print(f"  {titles.get(cid, cid)!r}: similarity {mode}")
    rows = q("SELECT ts, chat_id, spam_sim, ham_sim, example_id, applied, kind_with, kind_without, excerpt "
             "FROM similarity_log WHERE ts>=? ORDER BY id", WEEK)
    changed = [r for r in rows if r[6] != r[7]]
    print(f"  close matches 7d: {len(rows)} · would change the verdict: {len(changed)}")
    for ts, cid, s, h, exid, applied, kw, ko, excerpt in rows[-15:]:
        print(f"    {ts[5:16]} {str(titles.get(cid, cid))[:12]:12} spam {s:.2f} ok {h:.2f} #{exid} "
              f"{'ON' if applied else 'shadow'} {ko}->{kw}  {(excerpt or '')[:70]!r}")
else:
    print("  not deployed yet")

print("\nPERMITS", q("SELECT COUNT(*) FROM permits")[0][0], "· CAS cache", q("SELECT COUNT(*) FROM cas_cache")[0][0],
      "· custom rules", q("SELECT COUNT(*) FROM rules WHERE enabled=1")[0][0],
      "· strikes active", q("SELECT COUNT(*) FROM strikes")[0][0])
print("CONFIG default_mode", cfg.default_mode, "· allowed chats", len(getattr(cfg, "allowed_chat_ids", []) or []),
      "· owners", len(getattr(cfg, "owner_ids", []) or []))
db.close()
