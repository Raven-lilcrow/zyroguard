# Support-mode threshold check with the real model. Read-only, touches nothing live.
# It is appended to zyroguard/kb.py and piped into a throwaway container of the bot image:
#   cat zyroguard/kb.py tools/support_calibrate.py | ssh root@HOST "docker run --rm -i --memory 1g --cpus 1 \
#     --network none zyroguard-zyroguard python -"
# Prints the best meaning score per question so MATCH and HINT_MATCH can be set from data.

import resource
import time

from fastembed import TextEmbedding

FAQ = """# Lumen Coffee Help Center

## Orders and shipping
### Where do you ship?
We ship anywhere in the Philippines. Metro Manila orders arrive in 1 to 2 working days, provincial orders in 3 to 7 working days.
### How much is shipping?
Shipping is free for orders of 1,500 pesos or more. Below that it is 99 pesos in Metro Manila and 180 pesos elsewhere.
### How do I track my order?
You get an SMS and an email with a tracking link as soon as your order ships. You can also open My Orders in your account.
### Can I change my delivery address after ordering?
Yes, if the order has not shipped yet. Message us on Messenger or email hello@lumencoffee.ph with your order number.

## Subscriptions
### How does the coffee subscription work?
Choose a blend, a grind and how often you want it (every 2 or 4 weeks). We roast and ship it automatically.
### How do I pause or cancel my subscription?
Open My Subscriptions in your account and tap Pause or Cancel. Changes made 3 days before the next roast date apply to the next box.
### Do subscribers get a discount?
Subscribers get 10% off every box and free shipping on all subscription orders.

## Payments
### What payment methods do you accept?
GCash, Maya, credit and debit cards (Visa, Mastercard), and cash on delivery in Metro Manila.
### Is cash on delivery available outside Metro Manila?
No. Outside Metro Manila please pay online with GCash, Maya or a card.

## Returns and refunds
### Can I return coffee?
Unopened bags can be returned within 14 days for a refund. Opened bags cannot be returned unless they arrived damaged.
### My order arrived damaged, what do I do?
Send a photo of the damaged item and your order number to hello@lumencoffee.ph within 48 hours. We send a replacement for free.
### How long do refunds take?
Refunds to GCash and Maya take 1 to 3 days. Card refunds take 7 to 14 banking days.

## Products
### Which blend is best for espresso?
Our Midnight Blend is roasted dark for espresso and milk drinks. For a fruitier shot try the Ethiopia Yirgacheffe single origin.
### Do you sell decaf?
Yes, the Swiss Water Decaf comes in whole bean and ground.
### How should I store my coffee?
Keep the bag closed in a cool, dark place. Do not put it in the fridge. Use it within 4 weeks of the roast date.

## Wholesale
### Do you supply cafes and offices?
Yes. Email wholesale@lumencoffee.ph for wholesale prices, minimum orders and free brewing training for your staff.
"""

QUESTIONS = [
    # answerable, reworded or translated: (question, should match)
    ("how long does delivery take to cebu", True),
    ("is shipping free?", True),
    ("where can i see where my package is", True),
    ("can I update my address, I already ordered", True),
    ("how do i stop my monthly coffee box", True),
    ("is there a discount if I subscribe", True),
    ("do you accept gcash", True),
    ("can i pay cash if i live in davao", True),
    ("can i get my money back for coffee i didn't open", True),
    ("the bag was torn when it arrived", True),
    ("when will i get my refund on my card", True),
    ("what coffee should i use for my espresso machine", True),
    ("do you have coffee without caffeine", True),
    ("should i keep beans in the refrigerator", True),
    ("do you sell to restaurants", True),
    ("magkano ang shipping fee?", True),
    ("paano i-cancel ang subscription ko", True),
    ("pwede ba COD sa probinsya?", True),
    ("¿hacen envíos a todo el país?", True),
    ("apakah bisa bayar pakai kartu kredit?", True),
    ("배송은 얼마나 걸리나요?", True),
    ("返品はできますか？", True),
    # on topic but not in the files: should end in the can't-answer message either way
    ("do you have a physical store in Makati?", False),
    ("are your beans organic certified?", False),
    ("can I buy a gift card?", False),
    ("what grinder do you recommend?", False),
    # off topic
    ("who won the game last night?", False),
    ("what is 2 + 2", False),
    ("anyone here from QC?", False),
    ("how do I mute someone in this group?", False),
    ("is bitcoin going up this week?", False),
    ("gm everyone, how is your day?", False),
]


def main() -> None:
    t0 = time.time()
    model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", threads=1)
    load_s = time.time() - t0
    parts = sections(tidy(FAQ), "md")
    texts = [embed_text(h, b) for h, b in parts]
    import numpy as np

    def embed(xs):
        v = np.array(list(model.embed(xs)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    t1 = time.time()
    vecs = embed(texts)
    per_section_ms = (time.time() - t1) * 1000 / len(texts)
    idx = Index(ids=np.arange(len(parts)), doc_ids=np.zeros(len(parts), dtype=np.int64),
                seqs=np.arange(len(parts)), names=["faq.md"] * len(parts), headings=[h for h, _ in parts],
                texts=[b for _, b in parts], lowered=[t.lower() for t in texts], vecs=vecs,
                chars=sum(len(b) for _, b in parts))
    print(f"model load {load_s:.1f}s, {len(parts)} sections, {per_section_ms:.0f} ms per section")
    rows = []
    for q, should in QUESTIONS:
        hits = rank(idx, embed([q])[0], q, k=3)
        top = hits[0]
        rows.append((should, top.meaning, top.keyword, q, (idx.headings[top.row] or "").split(" > ")[-1]))
    for should, m, k, q, head in sorted(rows, key=lambda r: -r[1]):
        print(f"{'ANSWERABLE' if should else 'not in faq'}  meaning {m:.2f}  keyword {k:.2f}  {q[:45]:45}  -> {head[:45]}")
    ans = [r[1] for r in rows if r[0]]
    other = [r[1] for r in rows if not r[0]]
    print(f"answerable: min {min(ans):.2f} median {sorted(ans)[len(ans) // 2]:.2f}")
    print(f"not in faq: max {max(other):.2f} median {sorted(other)[len(other) // 2]:.2f}")
    for floor in (0.30, 0.35, 0.40, 0.45, 0.50):
        missed = sum(1 for r in rows if r[0] and not (r[1] >= floor or r[2] >= KEYWORD_MATCH))
        sent = sum(1 for r in rows if not r[0] and (r[1] >= floor or r[2] >= KEYWORD_MATCH))
        print(f"MATCH {floor:.2f}: answerable cut off {missed}/{len(ans)}, unanswerable sent to Claude {sent}/{len(other)}")
    # throughput on a bigger file: 400 sections
    big = (texts * 20)[:400]
    t2 = time.time()
    for i in range(0, len(big), 16):
        embed(big[i:i + 16])
    print(f"400 sections in batches of 16: {time.time() - t2:.1f}s")
    print(f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")


main()
