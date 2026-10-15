"""Read-only production check. Run inside the live container:

    docker exec -i zyroguard python - < tools/prod_check.py

Prints, per active group: Telegram rights, mode and key settings, and what the
deployed rules decide for a fixed battery of messages under that group's real
settings. Opens the database read-only, sends no messages, prints no secrets.
"""
import asyncio
import sqlite3
from pathlib import Path

from aiogram import Bot

from zyroguard.config import load_config
from zyroguard.judge import Judge
from zyroguard.rules import Context, MessageView, compile_custom, evaluate, load_rules
from zyroguard.settings import SPECS

FORWARD_EXTRA = "[forwarded from Leak 🔞]\n[button 👉 OPEN HERE 👈 https://example-leak.site/join]"
BATTERY = [
    # label, text, kwargs, expected verdict for (new member, regular)
    ("EN usdt scam", "We are looking for USDT holders, 10% to 25% commission per transaction, contact us @royal_desk", {}, ("hard", "hard")),
    ("RU usdt scam", "Ищем держателей USDT, комиссия от 10% до 25% за каждую сделку, пишите в личку @royal_desk", {}, ("hard", "hard")),
    ("ZH usdt scam", "我们正在寻找USDT持有者，每笔交易10%到25%佣金，请私信联系 @royal_desk", {}, ("hard", "hard")),
    ("ID usdt scam", "Kami mencari pemegang USDT, komisi 10% sampai 25% per transaksi, hubungi kami @royal_desk", {}, ("hard", "hard")),
    ("VI usdt scam", "Chúng tôi tìm người giữ USDT, hoa hồng 10% đến 25% mỗi giao dịch, liên hệ @royal_desk", {}, ("hard", "hard")),
    ("adult forward", "H O T ❌❌❌\nROUGH 😈\nFIRST TIME 🌸\nBACKSTAGE 🎭\nSPA 🫧\nHOMEMADE 📹",
     {"forwarded": True, "forwarded_from_channel": True, "extra": FORWARD_EXTRA}, ("hard", "hard")),
    ("seed phrase", "Please share your seed phrase to sync your wallet", {}, ("hard", "hard")),
    ("link", "check this out https://random-site.xyz/offer", {}, ("hard", "clean")),
    ("EN chat", "hey guys, does anyone know how to set up the API key?", {}, ("clean", "clean")),
    ("crypto talk", "did you see the bitcoin price today? wild", {}, ("clean", "clean")),
    ("dev talk", "found a memory leak in the worker, rough day", {}, ("clean", "clean")),
    ("RU chat", "привет всем, как дела? кто-нибудь пробовал новый релиз?", {}, (None, None)),
    ("ZH chat", "大家好，今天天气不错，有人用过这个工具吗", {}, (None, None)),
    ("Taglish", "guys paano po mag setup ng api key, anyone?", {}, (None, None)),
    ("github link", "here's the repo https://github.com/aiogram/aiogram", {}, ("clean", "clean")),
    ("CA migration", "🚨 TOKEN UPGRADE NOTICE\n\nWe are upgrading to a new token with a clean CA. The old tokens "
     "will be burned as part of the transition. DM me your wallet address to receive the new tokens 1:1.", {}, ("hard", "hard")),
    ("CA question", "is the CA on the website the right one?", {}, ("clean", "clean")),
]
NAMES = [("BadmintonFan", None, False), ("Lakers Supporter", None, False), ("JohnAdmin", None, True),
         ("Crypto", "crypto_support", True), ("Zyr0Guard", None, True)]


def lookalike(name: str) -> str:
    """The brand spelled the way scammers do: a capital I swapped for a small L."""
    for a, b in (("I", "l"), ("i", "1"), ("o", "0"), ("l", "I"), ("e", "3")):
        if a in name:
            return name.replace(a, b, 1)
    return name


def settings_for(db, chat_id, default_mode):
    raw = {r[0]: r[1] for r in db.execute("SELECT key, value FROM chat_settings WHERE chat_id=?", (chat_id,))}
    def get(k):
        return raw.get(k, default_mode if k == "mode" else SPECS[k].default)
    return get


def context_for(db, chat_id, get, bot_username):
    doms = {"allow": set(), "block": set()}
    for d, kind in db.execute("SELECT domain, kind FROM domains WHERE chat_id IN (0, ?)", (chat_id,)):
        doms[kind].add(d)
    db.row_factory = sqlite3.Row
    rows = list(db.execute("SELECT * FROM rules WHERE chat_id IN (0, ?) AND enabled=1 ORDER BY chat_id, id", (chat_id,)))
    db.row_factory = None
    return Context(link_policy=get("link_policy"), media_unverified=get("media_unverified"),
                   flood_count=int(get("flood_count")), repeat_count=int(get("repeat_count")),
                   allow_domains=doms["allow"], block_domains=doms["block"], custom_rules=compile_custom(rows),
                   bot_username=bot_username,
                   allowed_languages={c for c in get("languages").split(",") if c},
                   language_scope=get("language_scope"),
                   protected_names=[n.strip() for n in get("protected_names").split(",") if n.strip()])


async def main():
    cfg = load_config()
    rules = load_rules(Path(cfg.rules_path))
    db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    bot = Bot(cfg.bot_token)
    problems = 0
    try:
        me = await bot.get_me()
        print(f"BOT @{me.username} id {me.id} · reads all group messages: {me.can_read_all_group_messages}")
        print(f"RULES {len(rules.hard)} hard, {len(rules.soft)} soft, {len(rules.safety_triggers)} safety triggers")
        ok, why = await Judge(None, cfg.anthropic_api_key, cfg.judge_model, cfg.judge_effort).ping()
        print(f"AI JUDGE {'OK' if ok else 'PROBLEM'}: {why}")
        problems += not ok
        chats = list(db.execute("SELECT chat_id, title, owner_user_id FROM chats WHERE active=1 ORDER BY added_at"))
        print(f"GROUPS {len(chats)} active\n")
        for chat_id, title, owner in chats:
            get = settings_for(db, chat_id, cfg.default_mode)
            try:
                chat = await bot.get_chat(chat_id)
                forum = bool(getattr(chat, "is_forum", False))
                m = await bot.get_chat_member(chat_id, me.id)
                rights = (f"status {m.status}, delete {getattr(m, 'can_delete_messages', None)}, "
                          f"restrict {getattr(m, 'can_restrict_members', None)}, invite {getattr(m, 'can_invite_users', None)}")
                count = await bot.get_chat_member_count(chat_id)
            except Exception as e:  # noqa: BLE001
                forum, rights, count = None, f"CANNOT READ ({type(e).__name__}: {e})", "?"
                problems += 1
            print(f"== {title} ({chat_id}) · {count} members · topics: {forum}")
            print(f"   rights: {rights}")
            print(f"   mode {get('mode')} · captcha {get('captcha')} ({get('captcha_mode')}) · forward check "
                  f"{get('forward_captcha')} {get('forward_captcha_time')} · judge {get('judge')} · safety {get('safety')} · "
                  f"languages '{get('languages') or 'all'}' ({get('language_scope')}) · strike1 {get('strike1')}")
            ctx = context_for(db, chat_id, get, me.username or "")
            print(f"   custom rules {len(ctx.custom_rules)} · allow domains {len(ctx.allow_domains)} · block domains {len(ctx.block_domains)}")
            print(f"   protected names: {get('protected_names') or 'none'} · impersonation action {get('impersonation_action')}")
            for label, text, kw, expect in BATTERY:
                if label == "link" and get("link_policy") == "all":
                    expect = ("clean", "clean")   # this group allows links on purpose
                got = []
                for new in (True, False):
                    v = evaluate(MessageView(text=text, is_new_member=new, **kw), rules, ctx)
                    got.append(v.kind if v.action != "delete" or "language_not_allowed" not in v.matched else "lang-delete")
                bad = any(e is not None and g != e and not (e == "hard" and g == "judge") for g, e in zip(got, expect))
                problems += bad
                print(f"   {'FAIL' if bad else 'ok  '} {label:14} new={got[0]:11} regular={got[1]}")
            names = NAMES + [(lookalike(n), None, True) for n in ctx.protected_names] + [
                (f"{n} fan", None, False) for n in ctx.protected_names]
            for name, user, should in names:
                v = evaluate(MessageView(text="hello everyone", sender_name=name, sender_username=user,
                                         is_new_member=False), rules, ctx)
                hit = bool({"impersonation", "brand_lookalike"} & set(v.matched))
                problems += hit != should
                print(f"   {'FAIL' if hit != should else 'ok  '} name {name!r}{' @' + user if user else ''}: "
                      f"{'flagged' if hit else 'fine'}")
            print()
        print(f"RESULT: {problems} problem(s)")
    finally:
        await bot.session.close()
        db.close()


asyncio.run(main())
