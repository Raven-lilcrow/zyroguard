"""Test the meaning match with the real model and the live example library, outside the bot.

Run in a throwaway container from the bot's image, database mounted read-only:

    docker run --rm -i --memory 1500m -v zyroguard_zyroguard-data:/data:ro zyroguard-zyroguard python - < tools/similarity_test.py

Prints, for each test message: the closest spam and normal example, the points the match
would add, and the verdict without and with it. Sends nothing, writes nothing.
"""
import os
import resource
import sqlite3
import time
from pathlib import Path

import numpy as np

from zyroguard.rules import Context, MessageView, evaluate, load_rules
from zyroguard.similar import MODEL, Embedder, Match

SCAMS = [
    "Hiring people who hold USDT, earn up to 25% per deal, message me",
    "Looking for partners with USDT in their wallet, 20% profit on every trade, DM for details",
    "USDT 보유자를 찾습니다. 거래당 10~25% 수수료, DM 주세요",
    "กำลังหาผู้ถือ USDT ค่าคอมมิชชั่น 10-25% ต่อธุรกรรม ทักแชทมาเลย",
    "USDTをお持ちの方募集、1取引あたり10〜25%の手数料、DMください",
    "USDT sahipleri arıyoruz, işlem başına %10-25 komisyon, DM atın",
    "Buscamos personas con USDT, comisión del 10% al 25% por operación, escríbeme",
    "Important: our contract is migrating to V2, message me to register your wallet for the swap",
    "Your wallet needs to be validated, send me a message and I will help you restore it",
    "I help people get back crypto lost to scammers, inbox me for recovery",
]
NORMAL = [
    "hey guys, does anyone know how to set up the API key?",
    "did you see the bitcoin price today? wild",
    "found a memory leak in the worker, rough day",
    "is the CA on the website the right one?",
    "when will the token upgrade to v2 happen? excited",
    "can someone DM me the docs link? thanks",
    "I bought some USDT on binance yesterday, fees were ok",
    "what commission does the exchange charge per trade?",
    "thanks for the airdrop team, received it",
    "the new trading signals feature in the app is cool",
    "where can I find the wallet setup guide?",
    "привет всем, кто-нибудь пробовал новый релиз?",
    "大家好，有人用过这个工具吗",
    "안녕하세요 여러분 반갑습니다, 처음 왔어요",
    "kumusta po, paano mag-register sa platform?",
]


def main():
    db = sqlite3.connect("file:/data/zyroguard.db?mode=ro", uri=True)
    rows = list(db.execute("SELECT id, chat_id, label, vector, text FROM examples WHERE active=1 AND model=?", (MODEL,)))
    print(f"library: {len(rows)} examples")
    vecs = np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
    spam = np.array([r[2] == "spam" for r in rows])
    t = time.time()
    emb = Embedder()
    emb.load()
    print(f"model load {time.time() - t:.1f}s")
    rules = load_rules(Path("/app/zyroguard/rules.yaml"))
    ctx = Context(link_policy="new")
    times = []
    for title, msgs in (("SCAMS (should match)", SCAMS), ("NORMAL (should not)", NORMAL)):
        print(f"\n== {title}")
        for text in msgs:
            t = time.time()
            v = emb.embed([text])[0]
            times.append(time.time() - t)
            sims = vecs @ v
            m = Match()
            if spam.any():
                i = int(np.argmax(np.where(spam, sims, -2)))
                m.spam, m.spam_id = float(sims[i]), rows[i][0]
            if (~spam).any():
                j = int(np.argmax(np.where(~spam, sims, -2)))
                m.ham, m.ham_id = float(sims[j]), rows[j][0]
            view = MessageView(text=text, is_new_member=False)
            without = evaluate(view, rules, ctx).kind
            view.similar_points = m.points
            with_it = evaluate(view, rules, ctx).kind
            flag = "" if without == with_it else "  <- changed"
            print(f"  spam {m.spam:.2f} (#{m.spam_id}) pts {m.points:+d}  {without:5} -> {with_it:5}{flag}  {text[:60]}")
    print(f"\nper message: {1000 * sum(times) / len(times):.0f} ms")
    print(f"peak memory of this test process: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB")
    print("cpu count seen:", os.cpu_count())


main()
