"""SQLite persistence. This is the only module that touches the database.

Every table is keyed by chat_id so the bot serves many groups from one file.
Swap the engine here (Postgres later) and nothing else changes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS chats (
    chat_id     INTEGER PRIMARY KEY,
    title       TEXT,
    added_at    TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id     INTEGER NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  INTEGER,
    PRIMARY KEY (chat_id, key)
);

CREATE TABLE IF NOT EXISTS members (
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    first_seen    TEXT NOT NULL,
    joined_at     TEXT,
    verified_at   TEXT,
    msg_count     INTEGER NOT NULL DEFAULT 0,
    last_msg_at   TEXT,
    username      TEXT,
    display_name  TEXT,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS verified_global (
    user_id     INTEGER PRIMARY KEY,
    verified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strikes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    audit_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_strikes_user ON strikes(chat_id, user_id, expires_at);

CREATE TABLE IF NOT EXISTS allow_users (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    added_by  INTEGER,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS bot_admins (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    added_by  INTEGER,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS domains (
    chat_id   INTEGER NOT NULL,       -- 0 = global
    domain    TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- allow | block
    added_by  INTEGER,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, domain, kind)
);

CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,      -- 0 = global (owner-defined)
    pattern    TEXT NOT NULL,
    action     TEXT,                  -- NULL = inherit strike ladder
    duration_s INTEGER,               -- NULL = permanent / n/a
    note       TEXT,
    added_by   INTEGER,
    added_at   TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS timed_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action      TEXT NOT NULL,        -- ban | mute
    expires_at  TEXT NOT NULL,
    audit_id    INTEGER,
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_timed_due ON timed_actions(done, expires_at);

CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    user_id       INTEGER,
    username      TEXT,
    message_id    INTEGER,
    text_hash     TEXT,
    text_excerpt  TEXT,
    source        TEXT NOT NULL,      -- rule | judge | flood | captcha | cas | admin | join
    reason        TEXT NOT NULL,
    detail        TEXT,               -- JSON
    action        TEXT NOT NULL,      -- delete | mute | ban | kick | warn | flag | none
    duration_s    INTEGER,
    expires_at    TEXT,
    mode          TEXT NOT NULL,      -- shadow | enforce
    actor_id      INTEGER,            -- admin who did it (manual actions)
    undone_at     TEXT,
    undone_by     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_chat_ts ON audit(chat_id, ts);

CREATE TABLE IF NOT EXISTS errors (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    chat_id  INTEGER,
    where_   TEXT NOT NULL,
    message  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS judge_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    verdict       TEXT
);
CREATE INDEX IF NOT EXISTS idx_judge_usage_chat_ts ON judge_usage(chat_id, ts);

CREATE TABLE IF NOT EXISTS cas_cache (
    user_id   INTEGER PRIMARY KEY,
    listed    INTEGER NOT NULL,
    offenses  INTEGER,
    checked_at TEXT NOT NULL
);

-- Bot messages in groups that should disappear after a while (notices, command replies, greetings).
CREATE TABLE IF NOT EXISTS expiring_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    delete_at   TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_expiring_due ON expiring_messages(delete_at);

CREATE TABLE IF NOT EXISTS pending_captcha (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    message_id  INTEGER,
    created_at  TEXT NOT NULL,
    deadline    TEXT NOT NULL,
    answer      TEXT,
    PRIMARY KEY (chat_id, user_id)
);

-- Forwards waiting for their sender to pass the forward captcha.
CREATE TABLE IF NOT EXISTS pending_forward (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    prompt_id    INTEGER,
    message_ids  TEXT NOT NULL,       -- JSON list of the forwarded messages
    deadline     TEXT NOT NULL,
    answer       TEXT,
    wrong        INTEGER NOT NULL DEFAULT 0,
    thread_id    INTEGER,
    PRIMARY KEY (chat_id, user_id)
);

-- Who may add the bot to their own groups (multi-tenant). Owners never need one.
CREATE TABLE IF NOT EXISTS permits (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    max_groups   INTEGER NOT NULL DEFAULT 1,
    daily_ai_cap INTEGER NOT NULL DEFAULT 1500,
    expires_at   TEXT,
    note         TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_by   INTEGER,
    created_at   TEXT NOT NULL
);

-- Example messages for the meaning match (similar.py). chat_id 0 = shared by every group.
CREATE TABLE IF NOT EXISTS examples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    label       TEXT NOT NULL,          -- spam | ham
    text        TEXT NOT NULL,
    vector      BLOB NOT NULL,          -- float32, unit length
    model       TEXT NOT NULL,
    source      TEXT NOT NULL,          -- seed | admin | undo | auto
    added_by    INTEGER,
    added_at    TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    audit_id    INTEGER,                -- auto: the action it was learned from (undo drops it)
    held_until  TEXT                    -- auto: inactive until then, NULL once promoted or dropped
);
CREATE INDEX IF NOT EXISTS idx_examples_active ON examples(active, model);

-- Close meaning matches, kept so the shadow trial can be reviewed before matches count.
CREATE TABLE IF NOT EXISTS similarity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER,
    spam_sim    REAL NOT NULL,
    ham_sim     REAL NOT NULL,
    example_id  INTEGER,
    applied     INTEGER NOT NULL,       -- 1 = it changed the score (setting on), 0 = shadow
    kind_with   TEXT NOT NULL,          -- verdict with the match counted
    kind_without TEXT NOT NULL,         -- verdict without it
    excerpt     TEXT
);
CREATE INDEX IF NOT EXISTS idx_similarity_log_ts ON similarity_log(chat_id, ts);

-- Support mode: each group's own FAQ files. Only the text is kept, never the original file.
CREATE TABLE IF NOT EXISTS kb_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- txt | md | pdf
    bytes       INTEGER NOT NULL,       -- size of the uploaded file, counts toward the group's limit
    chars       INTEGER NOT NULL,
    pages       INTEGER,
    sha256      TEXT NOT NULL,
    added_by    INTEGER,
    added_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_chat ON kb_documents(chat_id);

-- Small sections of those files with their meaning vectors (kb.py searches them).
CREATE TABLE IF NOT EXISTS kb_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    doc_id      INTEGER NOT NULL,
    seq         INTEGER NOT NULL,       -- order inside the file, so neighbouring sections can be added
    heading     TEXT,
    text        TEXT NOT NULL,
    vector      BLOB NOT NULL,          -- float32, unit length
    model       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_chat ON kb_chunks(chat_id, doc_id, seq);

-- Every support question and how it went, for the daily summary and the unanswered list.
CREATE TABLE IF NOT EXISTS kb_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER,
    question    TEXT NOT NULL,          -- first 300 characters
    outcome     TEXT NOT NULL,          -- answered | no_match | not_in_files | unavailable
    best        REAL
);
CREATE INDEX IF NOT EXISTS idx_kb_questions_chat ON kb_questions(chat_id, ts);
"""

# tables that hold per-group rows, for /purge
CHAT_TABLES = ("chat_settings", "members", "strikes", "allow_users", "bot_admins", "domains", "rules",
               "timed_actions", "audit", "errors", "judge_usage", "pending_captcha", "pending_forward",
               "expiring_messages", "examples", "similarity_log", "kb_documents", "kb_chunks",
               "kb_questions", "chats")


_TOKEN_IN_TEXT = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class AuditRow:
    id: int
    ts: datetime
    chat_id: int
    user_id: int | None
    username: str | None
    message_id: int | None
    text_excerpt: str | None
    source: str
    reason: str
    detail: dict[str, Any]
    action: str
    duration_s: int | None
    expires_at: datetime | None
    mode: str
    actor_id: int | None
    undone_at: datetime | None


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        await self._db.executescript(SCHEMA)
        # migrations for databases created before multi-tenant support
        cur = await self._db.execute("PRAGMA table_info(chats)")
        cols = {r[1] for r in await cur.fetchall()}
        if "owner_user_id" not in cols:
            await self._db.execute("ALTER TABLE chats ADD COLUMN owner_user_id INTEGER")
        # learning from confident catches (2026-10): examples created before it lack these
        cur = await self._db.execute("PRAGMA table_info(examples)")
        cols = {r[1] for r in await cur.fetchall()}
        for col, kind in (("audit_id", "INTEGER"), ("held_until", "TEXT")):
            if col not in cols:
                await self._db.execute(f"ALTER TABLE examples ADD COLUMN {col} {kind}")
        # support files remember which version of the file reader cut them (1 = before 2026-10-13)
        cur = await self._db.execute("PRAGMA table_info(kb_documents)")
        if "reader" not in {r[1] for r in await cur.fetchall()}:
            await self._db.execute("ALTER TABLE kb_documents ADD COLUMN reader INTEGER NOT NULL DEFAULT 1")
        await self._db.commit()

    # ---- meaning-match examples -------------------------------------------

    async def example_add(self, chat_id: int, label: str, text: str, vector: bytes, model: str,
                          source: str, by: int | None, *, held_until: datetime | None = None,
                          audit_id: int | None = None) -> int:
        """A held example (held_until set) is stored inactive and only counts once promoted."""
        cur = await self.db.execute(
            "INSERT INTO examples(chat_id,label,text,vector,model,source,added_by,added_at,active,audit_id,held_until) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, label, text, vector, model, source, by, iso(utcnow()), int(held_until is None), audit_id,
             iso(held_until)))
        await self.db.commit()
        return int(cur.lastrowid)

    async def examples_active(self, model: str) -> list[sqlite3.Row]:
        cur = await self.db.execute("SELECT id, chat_id, label, vector FROM examples WHERE active=1 AND model=?", (model,))
        return list(await cur.fetchall())

    async def examples_text(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        cur = await self.db.execute(
            f"SELECT id, label, text, source FROM examples WHERE id IN ({','.join('?' * len(ids))})", ids)
        return {int(r["id"]): r for r in await cur.fetchall()}

    async def examples_held(self, chat_id: int, model: str) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT id, vector FROM examples WHERE held_until IS NOT NULL AND chat_id=? AND model=?", (chat_id, model))
        return list(await cur.fetchall())

    async def examples_due(self, model: str) -> list[sqlite3.Row]:
        """Held examples whose waiting time is over."""
        cur = await self.db.execute(
            "SELECT id, chat_id, label, vector, audit_id FROM examples WHERE held_until IS NOT NULL AND held_until<=? "
            "AND model=? ORDER BY id", (iso(utcnow()), model))
        return list(await cur.fetchall())

    async def example_promote(self, example_id: int) -> None:
        await self.db.execute("UPDATE examples SET active=1, held_until=NULL WHERE id=?", (example_id,))
        await self.db.commit()

    async def examples_drop_for_audit(self, audit_id: int) -> int:
        """Switch off whatever was learned from this action, held or already counting."""
        cur = await self.db.execute(
            "UPDATE examples SET active=0, held_until=NULL WHERE audit_id=? AND (active=1 OR held_until IS NOT NULL)",
            (audit_id,))
        await self.db.commit()
        return cur.rowcount

    async def auto_examples_since(self, chat_id: int, since: datetime) -> int:
        cur = await self.db.execute("SELECT COUNT(*) FROM examples WHERE source='auto' AND chat_id=? AND added_at>=?",
                                    (chat_id, iso(since)))
        return int((await cur.fetchone())[0])

    async def examples_recent(self, chat_id: int, limit: int = 10) -> list[sqlite3.Row]:
        """Counting and held examples, newest first (held ones can be /forget-ten before they count)."""
        cur = await self.db.execute(
            "SELECT id, chat_id, label, text, source, added_at, held_until FROM examples "
            "WHERE (active=1 OR held_until IS NOT NULL) AND chat_id IN (0, ?) ORDER BY id DESC LIMIT ?", (chat_id, limit))
        return list(await cur.fetchall())

    async def examples_count(self, chat_id: int) -> dict[str, int]:
        cur = await self.db.execute(
            "SELECT (chat_id = 0) shared, label, COUNT(*) n FROM examples WHERE active=1 AND chat_id IN (0, ?) "
            "GROUP BY shared, label", (chat_id,))
        counts = {f"{'shared' if r['shared'] else 'group'}_{r['label']}": r["n"] for r in await cur.fetchall()}
        cur = await self.db.execute("SELECT COUNT(*) FROM examples WHERE held_until IS NOT NULL AND chat_id=?", (chat_id,))
        counts["held"] = int((await cur.fetchone())[0])
        return counts

    async def example_get(self, example_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute("SELECT * FROM examples WHERE id=?", (example_id,))
        return await cur.fetchone()

    async def example_deactivate(self, example_id: int) -> None:
        await self.db.execute("UPDATE examples SET active=0, held_until=NULL WHERE id=?", (example_id,))
        await self.db.commit()

    async def examples_empty(self) -> bool:
        cur = await self.db.execute("SELECT 1 FROM examples LIMIT 1")
        return await cur.fetchone() is None

    # ---- support mode: a group's own FAQ files ------------------------------

    async def kb_add_document(self, chat_id: int, name: str, kind: str, size: int, chars: int, pages: int | None,
                              sha256: str, by: int | None, chunks: list[tuple[int, str | None, str, bytes]],
                              model: str, reader: int = 1) -> tuple[int, int | None]:
        """Store one file's sections in one go. A file with the same name replaces the old one.
        Returns (new document id, id of the document it replaced)."""
        cur = await self.db.execute("SELECT id FROM kb_documents WHERE chat_id=? AND name=?", (chat_id, name))
        old = await cur.fetchone()
        try:
            if old:
                await self.db.execute("DELETE FROM kb_chunks WHERE doc_id=?", (old["id"],))
                await self.db.execute("DELETE FROM kb_documents WHERE id=?", (old["id"],))
            cur = await self.db.execute(
                "INSERT INTO kb_documents(chat_id,name,kind,bytes,chars,pages,sha256,added_by,added_at,reader) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (chat_id, name, kind, size, chars, pages, sha256, by, iso(utcnow()), reader))
            doc_id = int(cur.lastrowid)
            await self.db.executemany(
                "INSERT INTO kb_chunks(chat_id,doc_id,seq,heading,text,vector,model) VALUES(?,?,?,?,?,?,?)",
                [(chat_id, doc_id, seq, heading, text, vec, model) for seq, heading, text, vec in chunks])
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return doc_id, (int(old["id"]) if old else None)

    async def kb_documents(self, chat_id: int) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT d.*, (SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id=d.id) AS sections "
            "FROM kb_documents d WHERE chat_id=? ORDER BY id", (chat_id,))
        return list(await cur.fetchall())

    async def kb_document_by_name(self, chat_id: int, name: str) -> sqlite3.Row | None:
        cur = await self.db.execute("SELECT * FROM kb_documents WHERE chat_id=? AND name=?", (chat_id, name))
        return await cur.fetchone()

    async def kb_document_by_hash(self, chat_id: int, sha256: str) -> sqlite3.Row | None:
        cur = await self.db.execute("SELECT * FROM kb_documents WHERE chat_id=? AND sha256=?", (chat_id, sha256))
        return await cur.fetchone()

    async def kb_bytes_used(self, chat_id: int) -> int:
        cur = await self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM kb_documents WHERE chat_id=?", (chat_id,))
        return int((await cur.fetchone())[0])

    async def kb_chars_used(self, chat_id: int) -> int:
        cur = await self.db.execute("SELECT COALESCE(SUM(chars),0) FROM kb_documents WHERE chat_id=?", (chat_id,))
        return int((await cur.fetchone())[0])

    async def kb_delete_document(self, chat_id: int, doc_id: int) -> str | None:
        """Returns the removed file's name, or None when it was not this group's."""
        cur = await self.db.execute("SELECT name FROM kb_documents WHERE id=? AND chat_id=?", (doc_id, chat_id))
        row = await cur.fetchone()
        if not row:
            return None
        await self.db.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
        await self.db.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))
        await self.db.commit()
        return str(row["name"])

    async def kb_delete_all(self, chat_id: int) -> int:
        cur = await self.db.execute("DELETE FROM kb_documents WHERE chat_id=?", (chat_id,))
        n = cur.rowcount or 0
        await self.db.execute("DELETE FROM kb_chunks WHERE chat_id=?", (chat_id,))
        await self.db.commit()
        return n

    async def kb_chunks(self, chat_id: int, model: str) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT c.id, c.doc_id, c.seq, c.heading, c.text, c.vector, d.name FROM kb_chunks c "
            "JOIN kb_documents d ON d.id=c.doc_id WHERE c.chat_id=? AND c.model=? ORDER BY c.doc_id, c.seq",
            (chat_id, model))
        return list(await cur.fetchall())

    async def kb_question_add(self, chat_id: int, user_id: int | None, question: str, outcome: str,
                              best: float | None) -> None:
        await self.db.execute(
            "INSERT INTO kb_questions(ts,chat_id,user_id,question,outcome,best) VALUES(?,?,?,?,?,?)",
            (iso(utcnow()), chat_id, user_id, " ".join(question.split())[:300], outcome, best))
        await self.db.commit()

    async def kb_question_counts(self, chat_id: int, since: datetime) -> dict[str, int]:
        cur = await self.db.execute(
            "SELECT outcome, COUNT(*) FROM kb_questions WHERE chat_id=? AND ts>=? GROUP BY outcome",
            (chat_id, iso(since)))
        return {r[0]: int(r[1]) for r in await cur.fetchall()}

    async def kb_unanswered(self, chat_id: int, since: datetime, limit: int = 15) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT * FROM kb_questions WHERE chat_id=? AND ts>=? AND outcome IN ('no_match','not_in_files') "
            "ORDER BY id DESC LIMIT ?", (chat_id, iso(since), limit))
        return list(await cur.fetchall())

    async def similarity_log_add(self, chat_id: int, user_id: int | None, spam_sim: float, ham_sim: float,
                                 example_id: int | None, applied: bool, kind_with: str, kind_without: str,
                                 excerpt: str | None) -> None:
        await self.db.execute(
            "INSERT INTO similarity_log(ts,chat_id,user_id,spam_sim,ham_sim,example_id,applied,kind_with,kind_without,excerpt) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (iso(utcnow()), chat_id, user_id, spam_sim, ham_sim, example_id, int(applied), kind_with, kind_without,
             (excerpt or "")[:300] or None))
        await self.db.commit()

    async def seed_candidates(self) -> list[sqlite3.Row]:
        """Past actions whose text is a good spam example: judge calls and scored rule hits, never undone."""
        cur = await self.db.execute(
            "SELECT chat_id, source, text_excerpt, detail FROM audit WHERE source IN ('rule','judge') "
            "AND mode='enforce' AND undone_at IS NULL AND text_excerpt IS NOT NULL ORDER BY id")
        return list(await cur.fetchall())

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store.open() not called"
        return self._db

    async def writable(self) -> bool:
        try:
            await self.db.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
            await self.db.execute("DROP TABLE _probe")
            await self.db.commit()
            return True
        except Exception:
            return False

    # ---- chats -----------------------------------------------------------

    async def upsert_chat(self, chat_id: int, title: str | None) -> bool:
        """Returns True if the chat is new."""
        cur = await self.db.execute("SELECT 1 FROM chats WHERE chat_id=?", (chat_id,))
        existed = await cur.fetchone() is not None
        await self.db.execute(
            "INSERT INTO chats(chat_id, title, added_at, active) VALUES(?,?,?,1) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, active=1",
            (chat_id, title, iso(utcnow())),
        )
        await self.db.commit()
        return not existed

    async def deactivate_chat(self, chat_id: int) -> None:
        await self.db.execute("UPDATE chats SET active=0 WHERE chat_id=?", (chat_id,))
        await self.db.commit()

    async def list_chats(self, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM chats" + (" WHERE active=1" if active_only else "")
        cur = await self.db.execute(q + " ORDER BY added_at")
        return list(await cur.fetchall())

    async def get_chat(self, chat_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
        return await cur.fetchone()

    async def set_chat_owner(self, chat_id: int, user_id: int | None) -> None:
        await self.db.execute("UPDATE chats SET owner_user_id=? WHERE chat_id=?", (user_id, chat_id))
        await self.db.commit()

    async def chat_owner(self, chat_id: int) -> int | None:
        row = await self.get_chat(chat_id)
        return int(row["owner_user_id"]) if row and row["owner_user_id"] else None

    async def chats_owned_by(self, user_id: int, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM chats WHERE owner_user_id=?" + (" AND active=1" if active_only else "")
        cur = await self.db.execute(q + " ORDER BY added_at", (user_id,))
        return list(await cur.fetchall())

    async def migrate_chat(self, old_id: int, new_id: int) -> None:
        """A basic group became a supergroup: carry every row over to the new id."""
        if await self.get_chat(new_id):
            await self.db.execute("DELETE FROM chats WHERE chat_id=?", (old_id,))
        for table in CHAT_TABLES:
            try:
                await self.db.execute(f"UPDATE OR REPLACE {table} SET chat_id=? WHERE chat_id=?", (new_id, old_id))
            except sqlite3.Error:
                await self.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (old_id,))
        await self.db.commit()

    async def purge_old_rows(self, usage_days: int = 90, audit_days: int = 365) -> int:
        n = 0
        cur = await self.db.execute("DELETE FROM judge_usage WHERE ts < ?", (iso(utcnow() - timedelta(days=usage_days)),))
        n += cur.rowcount or 0
        cur = await self.db.execute("DELETE FROM audit WHERE ts < ?", (iso(utcnow() - timedelta(days=audit_days)),))
        n += cur.rowcount or 0
        cur = await self.db.execute("DELETE FROM kb_questions WHERE ts < ?", (iso(utcnow() - timedelta(days=usage_days)),))
        n += cur.rowcount or 0
        await self.db.commit()
        return n

    async def digest_sent_on(self, chat_id: int) -> str | None:
        cur = await self.db.execute("SELECT value FROM chat_settings WHERE chat_id=? AND key='_digest_day'", (chat_id,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def mark_digest_sent(self, chat_id: int, day: str) -> None:
        await self.set_setting(chat_id, "_digest_day", day, None)

    async def purge_chat(self, chat_id: int) -> int:
        """Delete every row that belongs to one group. Returns rows removed."""
        n = 0
        for table in CHAT_TABLES:
            cur = await self.db.execute(f"DELETE FROM {table} WHERE chat_id=?", (chat_id,))
            n += cur.rowcount or 0
        await self.db.commit()
        return n

    # ---- permits (who may add the bot to their own groups) -----------------

    async def add_permit(self, user_id: int, username: str | None, max_groups: int, expires_at: datetime | None,
                         note: str | None, by: int | None, daily_ai_cap: int = 1500) -> None:
        await self.db.execute(
            "INSERT INTO permits(user_id, username, max_groups, daily_ai_cap, expires_at, note, active, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "max_groups=excluded.max_groups, daily_ai_cap=excluded.daily_ai_cap, expires_at=excluded.expires_at, "
            "note=excluded.note, active=1",
            (user_id, username, max_groups, daily_ai_cap, iso(expires_at) if expires_at else None, note, by, iso(utcnow())),
        )
        await self.db.commit()

    async def revoke_permit(self, user_id: int) -> bool:
        cur = await self.db.execute("UPDATE permits SET active=0 WHERE user_id=? AND active=1", (user_id,))
        await self.db.commit()
        return bool(cur.rowcount)


    async def get_permit(self, user_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute("SELECT * FROM permits WHERE user_id=?", (user_id,))
        return await cur.fetchone()

    async def permit_valid(self, user_id: int) -> tuple[bool, str]:
        """(ok, reason). Checks active flag, expiry and group count."""
        p = await self.get_permit(user_id)
        if not p or not p["active"]:
            return False, "no permit"
        if p["expires_at"] and from_iso(p["expires_at"]) < utcnow():
            return False, "permit expired"
        owned = await self.chats_owned_by(user_id)
        if len(owned) >= int(p["max_groups"]):
            return False, f"already using {len(owned)} of {p['max_groups']} allowed groups"
        return True, "ok"

    async def permit_valid_for_existing(self, user_id: int) -> bool:
        """Is this customer's permit still good for groups they already registered?"""
        p = await self.get_permit(user_id)
        if not p or not p["active"]:
            return False
        if p["expires_at"] and from_iso(p["expires_at"]) < utcnow():
            return False
        return True

    async def list_permits(self) -> list[sqlite3.Row]:
        cur = await self.db.execute("SELECT * FROM permits ORDER BY created_at")
        return list(await cur.fetchall())

    async def ai_calls_for_owner_since(self, user_id: int, since: datetime) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM judge_usage WHERE ts >= ? AND chat_id IN "
            "(SELECT chat_id FROM chats WHERE owner_user_id=?)", (iso(since), user_id))
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ---- settings --------------------------------------------------------

    async def get_settings(self, chat_id: int) -> dict[str, str]:
        cur = await self.db.execute(
            "SELECT key, value FROM chat_settings WHERE chat_id=?", (chat_id,)
        )
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    async def set_setting(self, chat_id: int, key: str, value: str, by: int | None) -> None:
        await self.db.execute(
            "INSERT INTO chat_settings(chat_id,key,value,updated_at,updated_by) VALUES(?,?,?,?,?) "
            "ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (chat_id, key, value, iso(utcnow()), by),
        )
        await self.db.commit()

    async def delete_setting(self, chat_id: int, key: str) -> None:
        await self.db.execute(
            "DELETE FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key)
        )
        await self.db.commit()

    # ---- members ---------------------------------------------------------

    async def touch_member(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        joined: bool = False,
        count_message: bool = False,
    ) -> sqlite3.Row:
        now = iso(utcnow())
        await self.db.execute(
            "INSERT INTO members(chat_id,user_id,first_seen,joined_at,username,display_name,msg_count,last_msg_at) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET "
            "username=excluded.username, display_name=excluded.display_name, "
            "joined_at=COALESCE(CASE WHEN ? THEN excluded.joined_at END, members.joined_at), "
            "msg_count=members.msg_count + ?, "
            "last_msg_at=CASE WHEN ? THEN excluded.last_msg_at ELSE members.last_msg_at END",
            (
                chat_id, user_id, now, now if joined else None, username, display_name,
                1 if count_message else 0, now if count_message else None,
                1 if joined else 0, 1 if count_message else 0, 1 if count_message else 0,
            ),
        )
        await self.db.commit()
        cur = await self.db.execute(
            "SELECT * FROM members WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        row = await cur.fetchone()
        assert row is not None
        return row

    async def get_member(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute(
            "SELECT * FROM members WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone()

    async def clear_verified(self, chat_id: int, user_id: int) -> None:
        """Forget that the user passed the captcha here (captcha_every_join)."""
        await self.db.execute(
            "UPDATE members SET verified_at=NULL WHERE chat_id=? AND user_id=?", (chat_id, user_id),
        )
        await self.db.commit()

    async def mark_verified(self, chat_id: int, user_id: int, everywhere: bool = True) -> None:
        """everywhere=False: verified in this group only (captcha was off), so a captcha-free
        group cannot be used to skip the captcha in groups that trust global verification."""
        now = iso(utcnow())
        await self.db.execute(
            "UPDATE members SET verified_at=? WHERE chat_id=? AND user_id=?",
            (now, chat_id, user_id),
        )
        if everywhere:
            await self.db.execute(
                "INSERT OR IGNORE INTO verified_global(user_id, verified_at) VALUES(?,?)",
                (user_id, now),
            )
        await self.db.commit()

    async def is_verified_anywhere(self, user_id: int) -> bool:
        cur = await self.db.execute(
            "SELECT 1 FROM verified_global WHERE user_id=?", (user_id,)
        )
        return await cur.fetchone() is not None

    async def member_count(self, chat_id: int) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM members WHERE chat_id=?", (chat_id,)
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # ---- strikes ---------------------------------------------------------

    async def add_strike(
        self, chat_id: int, user_id: int, reason: str, ttl: timedelta | None, audit_id: int | None
    ) -> int:
        now = utcnow()
        await self.db.execute(
            "INSERT INTO strikes(chat_id,user_id,reason,created_at,expires_at,audit_id) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, reason, iso(now), iso(now + ttl) if ttl else None, audit_id),
        )
        await self.db.commit()
        return await self.active_strikes(chat_id, user_id)

    async def job_catches(self, chat_id: int, user_id: int) -> int:
        """Fake job / task offers already removed for this person here (enforced, not undone)."""
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE chat_id=? AND user_id=? AND mode='enforce' AND undone_at IS NULL "
            "AND json_extract(detail, '$.job_scam') = 1", (chat_id, user_id))
        return int((await cur.fetchone())[0])

    async def active_strikes(self, chat_id: int, user_id: int) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM strikes WHERE chat_id=? AND user_id=? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (chat_id, user_id, iso(utcnow())),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def last_strike_at(self, chat_id: int, user_id: int) -> datetime | None:
        cur = await self.db.execute(
            "SELECT MAX(created_at) AS t FROM strikes WHERE chat_id=? AND user_id=? AND (expires_at IS NULL OR expires_at > ?)",
            (chat_id, user_id, iso(utcnow())))
        row = await cur.fetchone()
        return from_iso(row["t"]) if row and row["t"] else None

    async def list_strikes(self, chat_id: int, user_id: int) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT * FROM strikes WHERE chat_id=? AND user_id=? "
            "AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at DESC",
            (chat_id, user_id, iso(utcnow())),
        )
        return list(await cur.fetchall())

    async def clear_strikes(self, chat_id: int, user_id: int) -> int:
        cur = await self.db.execute(
            "DELETE FROM strikes WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.db.commit()
        return cur.rowcount

    async def remove_strike_by_audit(self, audit_id: int) -> None:
        await self.db.execute("DELETE FROM strikes WHERE audit_id=?", (audit_id,))
        await self.db.commit()

    async def purge_expired_strikes(self) -> int:
        cur = await self.db.execute(
            "DELETE FROM strikes WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (iso(utcnow()),),
        )
        await self.db.commit()
        return cur.rowcount

    # ---- allow lists -----------------------------------------------------

    async def allow_user(self, chat_id: int, user_id: int, by: int | None) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO allow_users(chat_id,user_id,added_by,added_at) VALUES(?,?,?,?)",
            (chat_id, user_id, by, iso(utcnow())),
        )
        await self.db.commit()

    async def disallow_user(self, chat_id: int, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM allow_users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def is_allowed_user(self, chat_id: int, user_id: int) -> bool:
        cur = await self.db.execute(
            "SELECT 1 FROM allow_users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone() is not None

    async def list_allowed_users(self, chat_id: int) -> list[int]:
        cur = await self.db.execute(
            "SELECT user_id FROM allow_users WHERE chat_id=?", (chat_id,)
        )
        return [int(r["user_id"]) for r in await cur.fetchall()]

    # ---- bot admins (owner-granted, per group) ----------------------------

    async def add_bot_admin(self, chat_id: int, user_id: int, by: int | None) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO bot_admins(chat_id,user_id,added_by,added_at) VALUES(?,?,?,?)",
            (chat_id, user_id, by, iso(utcnow())),
        )
        await self.db.commit()

    async def remove_bot_admin(self, chat_id: int, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM bot_admins WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def is_bot_admin(self, chat_id: int, user_id: int) -> bool:
        cur = await self.db.execute(
            "SELECT 1 FROM bot_admins WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone() is not None

    async def list_bot_admins(self, chat_id: int) -> list[int]:
        cur = await self.db.execute(
            "SELECT user_id FROM bot_admins WHERE chat_id=?", (chat_id,)
        )
        return [int(r["user_id"]) for r in await cur.fetchall()]

    async def set_domain(self, chat_id: int, domain: str, kind: str, by: int | None) -> None:
        other = "block" if kind == "allow" else "allow"
        await self.db.execute(
            "DELETE FROM domains WHERE chat_id=? AND domain=? AND kind=?", (chat_id, domain, other)
        )
        await self.db.execute(
            "INSERT OR IGNORE INTO domains(chat_id,domain,kind,added_by,added_at) VALUES(?,?,?,?,?)",
            (chat_id, domain.lower(), kind, by, iso(utcnow())),
        )
        await self.db.commit()

    async def remove_domain(self, chat_id: int, domain: str) -> bool:
        cur = await self.db.execute(
            "DELETE FROM domains WHERE chat_id=? AND domain=?", (chat_id, domain.lower())
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def domains(self, chat_id: int) -> dict[str, set[str]]:
        cur = await self.db.execute(
            "SELECT domain, kind FROM domains WHERE chat_id IN (0, ?)", (chat_id,)
        )
        out: dict[str, set[str]] = {"allow": set(), "block": set()}
        for r in await cur.fetchall():
            out[r["kind"]].add(r["domain"])
        return out

    # ---- custom rules ----------------------------------------------------

    async def add_rule(
        self, chat_id: int, pattern: str, action: str | None, duration_s: int | None,
        note: str | None, by: int | None,
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO rules(chat_id,pattern,action,duration_s,note,added_by,added_at) VALUES(?,?,?,?,?,?,?)",
            (chat_id, pattern, action, duration_s, note, by, iso(utcnow())),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def delete_rule(self, chat_id: int, rule_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM rules WHERE id=? AND chat_id=?", (rule_id, chat_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def list_rules(self, chat_id: int, include_global: bool = True) -> list[sqlite3.Row]:
        if include_global:
            cur = await self.db.execute(
                "SELECT * FROM rules WHERE chat_id IN (0, ?) AND enabled=1 ORDER BY chat_id, id",
                (chat_id,),
            )
        else:
            cur = await self.db.execute(
                "SELECT * FROM rules WHERE chat_id=? AND enabled=1 ORDER BY id", (chat_id,)
            )
        return list(await cur.fetchall())

    # ---- timed actions ---------------------------------------------------

    async def schedule_lift(
        self, chat_id: int, user_id: int, action: str, expires_at: datetime, audit_id: int | None
    ) -> None:
        await self.db.execute(
            "INSERT INTO timed_actions(chat_id,user_id,action,expires_at,audit_id) VALUES(?,?,?,?,?)",
            (chat_id, user_id, action, iso(expires_at), audit_id),
        )
        await self.db.commit()

    async def due_lifts(self) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT * FROM timed_actions WHERE done=0 AND expires_at <= ? ORDER BY expires_at",
            (iso(utcnow()),),
        )
        return list(await cur.fetchall())

    async def mark_lift_done(self, row_id: int) -> None:
        await self.db.execute("UPDATE timed_actions SET done=1 WHERE id=?", (row_id,))
        await self.db.commit()

    async def cancel_lifts(self, chat_id: int, user_id: int, action: str | None = None) -> None:
        if action:
            await self.db.execute(
                "UPDATE timed_actions SET done=1 WHERE chat_id=? AND user_id=? AND action=? AND done=0",
                (chat_id, user_id, action),
            )
        else:
            await self.db.execute(
                "UPDATE timed_actions SET done=1 WHERE chat_id=? AND user_id=? AND done=0",
                (chat_id, user_id),
            )
        await self.db.commit()

    # ---- audit -----------------------------------------------------------

    async def add_audit(
        self, *, chat_id: int, user_id: int | None, username: str | None, message_id: int | None,
        text_hash: str | None, text_excerpt: str | None, source: str, reason: str,
        detail: dict[str, Any] | None, action: str, duration_s: int | None,
        expires_at: datetime | None, mode: str, actor_id: int | None = None,
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO audit(ts,chat_id,user_id,username,message_id,text_hash,text_excerpt,"
            "source,reason,detail,action,duration_s,expires_at,mode,actor_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                iso(utcnow()), chat_id, user_id, username, message_id, text_hash, text_excerpt,
                source, reason, json.dumps(detail or {}, ensure_ascii=False), action,
                duration_s, iso(expires_at), mode, actor_id,
            ),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    def _row_to_audit(self, r: sqlite3.Row) -> AuditRow:
        try:
            detail = json.loads(r["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return AuditRow(
            id=int(r["id"]), ts=from_iso(r["ts"]) or utcnow(), chat_id=int(r["chat_id"]),
            user_id=r["user_id"], username=r["username"], message_id=r["message_id"],
            text_excerpt=r["text_excerpt"], source=r["source"], reason=r["reason"],
            detail=detail, action=r["action"], duration_s=r["duration_s"],
            expires_at=from_iso(r["expires_at"]), mode=r["mode"], actor_id=r["actor_id"],
            undone_at=from_iso(r["undone_at"]),
        )

    async def get_audit(self, audit_id: int) -> AuditRow | None:
        cur = await self.db.execute("SELECT * FROM audit WHERE id=?", (audit_id,))
        r = await cur.fetchone()
        return self._row_to_audit(r) if r else None

    async def find_audit_by_message(self, chat_id: int, message_id: int) -> AuditRow | None:
        cur = await self.db.execute(
            "SELECT * FROM audit WHERE chat_id=? AND message_id=? ORDER BY id DESC LIMIT 1",
            (chat_id, message_id),
        )
        r = await cur.fetchone()
        return self._row_to_audit(r) if r else None

    async def last_audit_for_user(self, chat_id: int, user_id: int) -> AuditRow | None:
        cur = await self.db.execute(
            "SELECT * FROM audit WHERE chat_id=? AND user_id=? AND source!='admin' ORDER BY id DESC LIMIT 1",
            (chat_id, user_id),
        )
        r = await cur.fetchone()
        return self._row_to_audit(r) if r else None

    async def recent_audit(self, chat_id: int, limit: int = 10) -> list[AuditRow]:
        cur = await self.db.execute(
            "SELECT * FROM audit WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit)
        )
        return [self._row_to_audit(r) for r in await cur.fetchall()]

    async def audit_since(self, chat_id: int | None, since: datetime) -> list[AuditRow]:
        if chat_id is None:
            cur = await self.db.execute(
                "SELECT * FROM audit WHERE ts >= ? ORDER BY id", (iso(since),)
            )
        else:
            cur = await self.db.execute(
                "SELECT * FROM audit WHERE chat_id=? AND ts >= ? ORDER BY id", (chat_id, iso(since))
            )
        return [self._row_to_audit(r) for r in await cur.fetchall()]

    async def mark_undone(self, audit_id: int, by: int | None) -> None:
        await self.db.execute(
            "UPDATE audit SET undone_at=?, undone_by=? WHERE id=?", (iso(utcnow()), by, audit_id)
        )
        await self.db.commit()


    # ---- errors ----------------------------------------------------------

    async def log_error(self, where: str, message: str, chat_id: int | None = None) -> None:
        # Telegram download errors carry the file URL, which contains the bot token
        message = _TOKEN_IN_TEXT.sub("bot<redacted>", message or "")
        await self.db.execute(
            "INSERT INTO errors(ts,chat_id,where_,message) VALUES(?,?,?,?)",
            (iso(utcnow()), chat_id, where, message[:2000]),
        )
        await self.db.execute(
            "DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT 500)"
        )
        await self.db.commit()

    async def recent_errors(self, limit: int = 20) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,)
        )
        return list(await cur.fetchall())

    # ---- judge usage -----------------------------------------------------

    async def add_judge_usage(
        self, chat_id: int, model: str, input_tokens: int, output_tokens: int,
        cache_read: int, cache_write: int, verdict: str | None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO judge_usage(ts,chat_id,model,input_tokens,output_tokens,cache_read,cache_write,verdict) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (iso(utcnow()), chat_id, model, input_tokens, output_tokens, cache_read, cache_write, verdict),
        )
        await self.db.commit()

    async def judge_calls_since(self, chat_id: int, since: datetime, kind: str | None = None) -> int:
        """AI calls for a group in a window. kind: None = spam judge + safety, 'safety' = safety only,
        'judge' = spam judge only."""
        extra = {"safety": "AND verdict LIKE 'safety:%'",
                 "judge": "AND verdict NOT LIKE 'safety:%'"}.get(kind or "", "")
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM judge_usage WHERE chat_id=? AND ts >= ? "
            f"AND (verdict IS NULL OR verdict != 'assistant') {extra}", (chat_id, iso(since))
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def assistant_calls_since(self, chat_id: int, since: datetime) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM judge_usage WHERE chat_id=? AND ts >= ? AND verdict = 'assistant'",
            (chat_id, iso(since))
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def judge_usage_summary(self, since: datetime) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT chat_id, model, CASE WHEN verdict = 'assistant' THEN 'questions' ELSE 'judge' END AS kind, "
            "COUNT(*) AS calls, SUM(input_tokens) AS inp, SUM(output_tokens) AS out, "
            "SUM(cache_read) AS cr, SUM(cache_write) AS cw FROM judge_usage WHERE ts >= ? "
            "GROUP BY chat_id, model, kind",
            (iso(since),),
        )
        return list(await cur.fetchall())

    # ---- CAS cache -------------------------------------------------------

    async def cas_get(self, user_id: int, max_age: timedelta) -> bool | None:
        cur = await self.db.execute(
            "SELECT listed, checked_at FROM cas_cache WHERE user_id=?", (user_id,)
        )
        r = await cur.fetchone()
        if not r:
            return None
        checked = from_iso(r["checked_at"])
        if checked and utcnow() - checked > max_age:
            return None
        return bool(r["listed"])

    async def cas_put(self, user_id: int, listed: bool, offenses: int | None) -> None:
        await self.db.execute(
            "INSERT INTO cas_cache(user_id,listed,offenses,checked_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET listed=excluded.listed, offenses=excluded.offenses, "
            "checked_at=excluded.checked_at",
            (user_id, 1 if listed else 0, offenses, iso(utcnow())),
        )
        await self.db.commit()

    # ---- captcha ---------------------------------------------------------

    async def captcha_put(
        self, chat_id: int, user_id: int, message_id: int | None, deadline: datetime, answer: str | None
    ) -> None:
        await self.db.execute(
            "INSERT INTO pending_captcha(chat_id,user_id,message_id,created_at,deadline,answer) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET message_id=excluded.message_id, "
            "created_at=excluded.created_at, deadline=excluded.deadline, answer=excluded.answer",
            (chat_id, user_id, message_id, iso(utcnow()), iso(deadline), answer),
        )
        await self.db.commit()

    async def captcha_get(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute(
            "SELECT * FROM pending_captcha WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone()

    # ---- expiring messages -----------------------------------------------

    async def expire_message(self, chat_id: int, message_id: int, delete_at: datetime) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO expiring_messages(chat_id,message_id,delete_at) VALUES(?,?,?)",
            (chat_id, message_id, iso(delete_at)),
        )
        await self.db.commit()

    async def due_expiring(self) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT chat_id, message_id FROM expiring_messages WHERE delete_at <= ? ORDER BY delete_at LIMIT 100",
            (iso(utcnow()),),
        )
        return list(await cur.fetchall())

    async def forget_expiring(self, chat_id: int, message_id: int) -> None:
        await self.db.execute(
            "DELETE FROM expiring_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        )
        await self.db.commit()

    async def captcha_delete(self, chat_id: int, user_id: int) -> None:
        await self.db.execute(
            "DELETE FROM pending_captcha WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        await self.db.commit()

    # ---- forward captcha -------------------------------------------------

    async def forward_get(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        cur = await self.db.execute(
            "SELECT * FROM pending_forward WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        return await cur.fetchone()

    async def forward_put(self, chat_id: int, user_id: int, prompt_id: int | None, message_ids: list[int],
                          deadline: datetime, answer: str | None, thread_id: int | None = None) -> None:
        await self.db.execute(
            "INSERT INTO pending_forward(chat_id,user_id,prompt_id,message_ids,deadline,answer,wrong,thread_id) "
            "VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET prompt_id=excluded.prompt_id, "
            "message_ids=excluded.message_ids, deadline=excluded.deadline, answer=excluded.answer, "
            "wrong=excluded.wrong, thread_id=excluded.thread_id",
            (chat_id, user_id, prompt_id, json.dumps(message_ids), iso(deadline), answer, thread_id))
        await self.db.commit()

    async def forward_add_message(self, chat_id: int, user_id: int, message_id: int) -> None:
        row = await self.forward_get(chat_id, user_id)
        if not row:
            return
        ids = json.loads(row["message_ids"] or "[]")
        if message_id not in ids:
            ids.append(message_id)
        await self.db.execute("UPDATE pending_forward SET message_ids=? WHERE chat_id=? AND user_id=?",
                              (json.dumps(ids), chat_id, user_id))
        await self.db.commit()

    async def forward_update(self, chat_id: int, user_id: int, *, answer: str, wrong: int) -> None:
        await self.db.execute("UPDATE pending_forward SET answer=?, wrong=? WHERE chat_id=? AND user_id=?",
                              (answer, wrong, chat_id, user_id))
        await self.db.commit()

    async def forward_delete(self, chat_id: int, user_id: int) -> None:
        await self.db.execute("DELETE FROM pending_forward WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await self.db.commit()

    async def forward_overdue(self) -> list[sqlite3.Row]:
        cur = await self.db.execute("SELECT * FROM pending_forward WHERE deadline <= ?", (iso(utcnow()),))
        return list(await cur.fetchall())

    async def captcha_overdue(self) -> list[sqlite3.Row]:
        cur = await self.db.execute(
            "SELECT * FROM pending_captcha WHERE deadline <= ?", (iso(utcnow()),)
        )
        return list(await cur.fetchall())

    # ---- stats -----------------------------------------------------------

    async def stats(self, chat_id: int | None, since: datetime) -> dict[str, Any]:
        rows = await self.audit_since(chat_id, since)
        by_source: dict[str, int] = {}
        by_action: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        undone = 0
        shadow = 0
        for r in rows:
            by_source[r.source] = by_source.get(r.source, 0) + 1
            by_action[r.action] = by_action.get(r.action, 0) + 1
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
            if r.undone_at:
                undone += 1
            if r.mode == "shadow":
                shadow += 1
        judge = [j for j in await self.judge_usage_summary(since)
                 if j["kind"] != "questions" and (chat_id is None or j["chat_id"] == chat_id)]
        judge_calls = sum(int(j["calls"]) for j in judge)
        judge_in = sum(int(j["inp"] or 0) for j in judge)
        judge_out = sum(int(j["out"] or 0) for j in judge)
        return {
            "since": iso(since),
            "total": len(rows),
            "by_source": by_source,
            "by_action": by_action,
            "top_reasons": sorted(by_reason.items(), key=lambda kv: -kv[1])[:10],
            "undone": undone,
            "shadow": shadow,
            "judge_calls": judge_calls,
            "judge_input_tokens": judge_in,
            "judge_output_tokens": judge_out,
        }
