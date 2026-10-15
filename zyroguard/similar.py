"""Meaning match: compare each message with example spam and known-good messages.

A small multilingual model (runs on the server's CPU, nothing leaves the box) turns text
into a vector; messages that mean the same thing get close vectors even when the wording
or the language differs. A close match to known spam only adds to the rule score, so on
its own it can at most send the message to the AI judge. It never bans by itself.

Examples come from four places: a seed set (built-in scams plus past rule and judge hits),
admins (/spam and /notspam as a reply), /undo (an undone action becomes a known-good
example, so the same mistake is not repeated), and confident catches (hold): a message the
judge was very sure about is held for a day and only counts if nobody undid the action.

The closest examples are also shown to the AI judge (neighbors), so it follows the group's
earlier decisions, above all the /notspam and /undo corrections.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np

from .store import AuditRow, Store, utcnow

log = logging.getLogger("zyroguard.similar")

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
STRONG = 0.80      # this close to known spam adds 4: the AI judge looks at it even with no other signal
CLOSE = 0.70       # adds 2: counts only together with other signs
GOOD = 0.85        # this close to a known-good message (and closer than to any spam) takes 2 off
SAME = 0.97        # practically the same text: do not store it twice
MIN_CHARS = 15     # shorter messages ("gm", "ok lol") match everything a little, skip them
MAX_CHARS = 1000
LEARN_CONFIDENCE = 0.95   # the judge must be this sure before a catch is learned
HOLD = timedelta(hours=24)  # a learned catch waits this long, so an /undo can still stop it
LEARN_PER_DAY = 20         # per group: a spam wave should not flood the library
NEIGHBORS = 3              # examples shown to the judge
NEIGHBOR_FLOOR = 0.60      # below this an example says nothing about the message

# Scams seen in real groups, in the languages they come in. Shared by every group.
SEED_SPAM = [
    "We are looking for USDT holders, 10% to 25% commission per transaction, contact us",
    "Ищем держателей USDT, комиссия от 10% до 25% за каждую сделку, пишите в личку",
    "我们正在寻找USDT持有者，每笔交易10%到25%佣金，请私信联系",
    "Kami mencari pemegang USDT, komisi 10% sampai 25% per transaksi, hubungi kami",
    "Chúng tôi tìm người giữ USDT, hoa hồng 10% đến 25% mỗi giao dịch, liên hệ",
    "We'll be redeploying our Contract Address (CA) in the next 28 hours. All holders will receive "
    "the new tokens through an airdrop. PM me to register your wallet.",
    "TOKEN UPGRADE NOTICE: we are upgrading to a new token with a clean CA. The old tokens will be "
    "burned. DM me your wallet address to receive the new tokens.",
    "Please share your seed phrase to sync your wallet and claim the reward",
    "Your wallet has been flagged, contact support in DM to verify and restore access",
    "Earn $500 daily from home, no experience needed, message me to start",
    "Join our VIP signals channel, 95% accuracy, guaranteed profit every day",
    "I can recover your lost or stolen crypto, contact me for wallet recovery service",
]


@dataclass
class Match:
    spam: float = 0.0
    spam_id: int | None = None
    ham: float = 0.0
    ham_id: int | None = None

    @property
    def points(self) -> int:
        if self.ham >= GOOD and self.ham > self.spam:
            return -2
        if self.spam >= STRONG:
            return 4
        if self.spam >= CLOSE:
            return 2
        return 0

    @property
    def notable(self) -> bool:
        return self.points != 0

    def as_detail(self) -> dict[str, Any]:
        return {"spam": round(self.spam, 3), "spam_id": self.spam_id, "ham": round(self.ham, 3),
                "ham_id": self.ham_id, "points": self.points}


class Embedder:
    """fastembed + ONNX on one CPU thread. Loaded once, in a worker thread."""

    def __init__(self, model: str = MODEL, cache_dir: str | None = None):
        self.model = model
        self.cache_dir = cache_dir or os.environ.get("FASTEMBED_CACHE_PATH")
        self._m = None

    def load(self) -> None:
        from fastembed import TextEmbedding
        self._m = TextEmbedding(self.model, threads=1, cache_dir=self.cache_dir)

    def embed(self, texts: list[str]) -> np.ndarray:
        assert self._m is not None, "load() first"
        v = np.array(list(self._m.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def _clip(text: str) -> str:
    return " ".join(text.split())[:MAX_CHARS]


def usable(text: str) -> bool:
    return len(_clip(text)) >= MIN_CHARS and not text.startswith("[")


class Library:
    def __init__(self, store: Store, embedder: Embedder | Any):
        self.store = store
        self.embedder = embedder
        self.ready = False
        self.problem: str | None = None
        self._lock = asyncio.Lock()          # one embedding at a time: the bot has one CPU
        self._ids = np.zeros(0, dtype=np.int64)
        self._chats = np.zeros(0, dtype=np.int64)
        self._spam = np.zeros(0, dtype=bool)
        self._vecs = np.zeros((0, 0), dtype=np.float32)
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()   # the rules and the judge ask about the same text

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Load the model and the examples. Any failure leaves the bot running without it."""
        try:
            await asyncio.to_thread(self.embedder.load)
            if await self.store.examples_empty():
                n = await self.seed()
                log.info("meaning match: seeded %d examples", n)
            await self._reload()
            self.ready = True
            log.info("meaning match ready: %d examples (%s)", len(self._ids), self.embedder.model)
        except Exception as e:  # noqa: BLE001
            self.problem = f"{type(e).__name__}: {e}"[:200]
            log.exception("meaning match unavailable")
            await self.store.log_error("similar.start", self.problem)

    async def _reload(self) -> None:
        rows = await self.store.examples_active(self.embedder.model)
        self._ids = np.array([r["id"] for r in rows], dtype=np.int64)
        self._chats = np.array([r["chat_id"] for r in rows], dtype=np.int64)
        self._spam = np.array([r["label"] == "spam" for r in rows], dtype=bool)
        self._vecs = (np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
                      if rows else np.zeros((0, 0), dtype=np.float32))

    async def _vector(self, text: str) -> np.ndarray:
        key = _clip(text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        async with self._lock:
            vec = (await asyncio.to_thread(self.embedder.embed, [key]))[0]
        self._cache[key] = vec
        if len(self._cache) > 32:
            self._cache.popitem(last=False)
        return vec

    async def vector(self, text: str) -> np.ndarray:
        """One text, for other features (support mode). Shares the cache and the CPU lock."""
        return await self._vector(text)

    async def embed_many(self, texts: list[str], batch: int = 8, progress=None) -> np.ndarray:
        """Many texts in small batches (about 0.6 s each), so spam checks waiting on the lock are not
        held up for long. progress(done, total) is awaited after each batch when given."""
        out = []
        for i in range(0, len(texts), batch):
            async with self._lock:
                out.append(await asyncio.to_thread(self.embedder.embed, texts[i:i + batch]))
            if progress:
                await progress(min(i + batch, len(texts)), len(texts))
        return np.concatenate(out) if out else np.zeros((0, 0), dtype=np.float32)

    def _scope(self, chat_id: int) -> np.ndarray:
        return (self._chats == 0) | (self._chats == chat_id)

    def _nearest(self, chat_id: int, vec: np.ndarray) -> Match:
        m = Match()
        if not len(self._ids):
            return m
        scope = self._scope(chat_id)
        sims = self._vecs @ vec
        for spam in (True, False):
            mask = scope & (self._spam == spam)
            if mask.any():
                i = int(np.argmax(np.where(mask, sims, -2.0)))
                if spam:
                    m.spam, m.spam_id = float(sims[i]), int(self._ids[i])
                else:
                    m.ham, m.ham_id = float(sims[i]), int(self._ids[i])
        return m

    # ---- use ---------------------------------------------------------------

    async def match(self, chat_id: int, text: str) -> Match | None:
        if not self.ready or not usable(text):
            return None
        return self._nearest(chat_id, await self._vector(text))

    async def add(self, chat_id: int, label: str, text: str, source: str, by: int | None,
                  audit_id: int | None = None) -> tuple[int | None, bool]:
        """(example id, is_new). An example that is practically already there is not stored twice.
        audit_id ties it to an action, so undoing that action switches the example off again."""
        if not self.ready or not usable(text):
            return None, False
        vec = await self._vector(text)
        m = self._nearest(chat_id, vec)
        same_id, same = (m.spam_id, m.spam) if label == "spam" else (m.ham_id, m.ham)
        if same_id is not None and same >= SAME:
            return same_id, False
        new_id = await self.store.example_add(chat_id, label, _clip(text), vec.tobytes(), self.embedder.model, source, by,
                                              audit_id=audit_id)
        await self._reload()
        return new_id, True

    async def neighbors(self, chat_id: int, text: str, k: int = NEIGHBORS,
                        floor: float = NEIGHBOR_FLOOR) -> list[dict[str, Any]]:
        """The closest labeled examples (spam or normal) for the AI judge, closest first."""
        if not self.ready or not usable(text) or not len(self._ids):
            return []
        sims = np.where(self._scope(chat_id), self._vecs @ await self._vector(text), -2.0)
        order = [int(i) for i in np.argsort(-sims)[:k] if sims[i] >= floor]
        rows = await self.store.examples_text([int(self._ids[i]) for i in order])
        out = []
        for i in order:
            r = rows.get(int(self._ids[i]))
            if r is not None:
                out.append({"id": int(r["id"]), "label": r["label"], "similarity": round(float(sims[i]), 3),
                            "text": r["text"]})
        return out

    async def hold(self, chat_id: int, text: str, audit_id: int) -> int | None:
        """A confident catch: store it as a spam example for this group that starts counting after
        HOLD, unless the action is undone first. None when it is not worth keeping."""
        if not self.ready or not usable(text):
            return None
        if await self.store.auto_examples_since(chat_id, utcnow() - timedelta(days=1)) >= LEARN_PER_DAY:
            return None
        vec = await self._vector(text)
        m = self._nearest(chat_id, vec)
        if m.spam_id is not None and m.spam >= SAME:
            return None                                   # already known
        for r in await self.store.examples_held(chat_id, self.embedder.model):
            if float(np.frombuffer(r["vector"], dtype=np.float32) @ vec) >= SAME:
                return None                               # the same wave, already waiting
        return await self.store.example_add(chat_id, "spam", _clip(text), vec.tobytes(), self.embedder.model, "auto",
                                            None, held_until=utcnow() + HOLD, audit_id=audit_id)

    async def promote_due(self) -> int:
        """Scheduler hook: held examples whose day is over start counting, unless the action they
        came from was undone or the library learned the same text meanwhile."""
        if not self.ready:
            return 0
        promoted = 0
        for r in await self.store.examples_due(self.embedder.model):
            row = await self.store.get_audit(r["audit_id"]) if r["audit_id"] else None
            vec = np.frombuffer(r["vector"], dtype=np.float32)
            m = self._nearest(r["chat_id"], vec)
            if row is None or row.undone_at is not None or (m.spam_id is not None and m.spam >= SAME):
                await self.store.example_deactivate(r["id"])
                continue
            await self.store.example_promote(r["id"])
            promoted += 1
        if promoted:
            await self._reload()
        return promoted

    async def forget(self, example_id: int) -> bool:
        row = await self.store.example_get(example_id)
        if row is None or not (row["active"] or row["held_until"]):
            return False
        await self.store.example_deactivate(example_id)
        await self._reload()
        return True

    async def learn_from_undo(self, row: AuditRow, by: int | None) -> int | None:
        """An admin undid an action: the text was fine. Remember it as known-good for that group
        and stop counting any spam example that is the very same text."""
        if await self.store.examples_drop_for_audit(row.id) and self.ready:
            await self._reload()                          # it was learned from this very action
        if row.source not in {"rule", "judge"} or not row.text_excerpt or not self.ready:
            return None
        text = row.text_excerpt
        if not usable(text):
            return None
        vec = await self._vector(text)
        m = self._nearest(row.chat_id, vec)
        if m.spam_id is not None and m.spam >= SAME:
            await self.store.example_deactivate(m.spam_id)
        new_id = await self.store.example_add(row.chat_id, "ham", _clip(text), vec.tobytes(), self.embedder.model, "undo", by)
        await self._reload()
        return new_id

    async def seed(self) -> int:
        """Built-in scams (shared) plus past judge calls and scored rule hits from each group."""
        items: list[tuple[int, str]] = [(0, t) for t in SEED_SPAM]
        for r in await self.store.seed_candidates():
            try:
                d = json.loads(r["detail"] or "{}")
            except json.JSONDecodeError:
                d = {}
            score = d.get("score") or 0
            if r["source"] == "judge" or 10 <= score < 100 or {"seed_phrase_request", "adult_marker"} & set(d.get("matched") or []):
                items.append((0, r["text_excerpt"]))
        seen: set[str] = set()
        texts, chats = [], []
        for chat_id, t in items:
            t = _clip(t)
            if usable(t) and t.lower() not in seen:
                seen.add(t.lower())
                texts.append(t)
                chats.append(chat_id)
        if not texts:
            return 0
        vecs = await asyncio.to_thread(self.embedder.embed, texts)
        for chat_id, t, v in zip(chats, texts, vecs):
            await self.store.example_add(chat_id, "spam", t, v.tobytes(), self.embedder.model, "seed", None)
        return len(texts)
