"""SQLite store: meetings, transcript segments, assistance cards, chats and knowledge sources."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  started REAL,
  ended REAL,
  status TEXT NOT NULL DEFAULT 'draft',      -- draft | live | done
  language TEXT NOT NULL DEFAULT 'auto',
  tags TEXT NOT NULL DEFAULT '[]',
  cues TEXT NOT NULL DEFAULT '',             -- the user's own shorthand, one cue per line
  enhanced TEXT NOT NULL DEFAULT '[]',       -- [{cue, text}] aligned to cues
  summary TEXT NOT NULL DEFAULT '{}',        -- {overview, decisions[], action_items[], topics[]}
  duration REAL NOT NULL DEFAULT 0,
  indexed REAL
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  source TEXT NOT NULL,                      -- you | others | file
  t0 REAL NOT NULL,
  t1 REAL NOT NULL,
  text TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS seg_meeting ON segments(meeting_id, t0);
CREATE TABLE IF NOT EXISTS assists (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  created REAL NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  sources TEXT NOT NULL DEFAULT '[]',
  seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL,
  meeting_id TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  sources TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS msg_chat ON messages(chat_id, created);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                        -- file | folder | note | meeting
  path TEXT NOT NULL DEFAULT '',
  parent TEXT,                               -- folder id for files found in a watched folder
  title TEXT NOT NULL DEFAULT '',
  ext TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  added REAL NOT NULL,
  mtime REAL NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  chunks INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',    -- pending | indexing | ready | error | skipped
  error TEXT NOT NULL DEFAULT '',
  watch INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '',             -- body for notes
  hash TEXT NOT NULL DEFAULT ''              -- digest of what was indexed, to skip unchanged files
);
CREATE INDEX IF NOT EXISTS src_path ON sources(path);
"""

JSON_COLS = {"tags", "enhanced", "summary", "sources"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class DB:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._local = threading.local()
        self._write = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(sources)")}
            if "hash" not in cols:  # databases from before 0.1.3
                c.execute("ALTER TABLE sources ADD COLUMN hash TEXT NOT NULL DEFAULT ''")

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        return c

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            try:
                d[k] = json.loads(d[k]) if d[k] else ([] if k != "summary" else {})
            except json.JSONDecodeError:
                pass
        return d

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        return [self._row(r) for r in self.conn().execute(sql, tuple(args)).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, args: Iterable[Any] = ()) -> dict | None:
        return self._row(self.conn().execute(sql, tuple(args)).fetchone())

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self._write:
            c = self.conn()
            cur = c.execute(sql, tuple(args))
            c.commit()
            return cur.lastrowid or 0

    def update(self, table: str, id_: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v, ensure_ascii=False) if k in JSON_COLS and not isinstance(v, str) else v
                for k, v in fields.items()]
        self.x(f"UPDATE {table} SET {cols} WHERE id=?", [*vals, id_])

    # --- meetings ----------------------------------------------------------------------
    def create_meeting(self, title: str = "", language: str = "auto", tags: list[str] | None = None) -> dict:
        mid = new_id()
        self.x("INSERT INTO meetings(id,title,created,language,tags) VALUES(?,?,?,?,?)",
               [mid, title, time.time(), language, json.dumps(tags or [])])
        return self.meeting(mid)  # type: ignore[return-value]

    def meeting(self, mid: str) -> dict | None:
        return self.one("SELECT * FROM meetings WHERE id=?", [mid])

    def meetings(self, q: str = "", tag: str = "", since: float = 0, until: float = 0) -> list[dict]:
        sql = "SELECT id,title,created,started,ended,status,language,tags,duration,summary,indexed FROM meetings WHERE 1=1"
        args: list[Any] = []
        if q:
            sql += " AND (title LIKE ? OR cues LIKE ? OR id IN (SELECT meeting_id FROM segments WHERE text LIKE ?))"
            args += [f"%{q}%"] * 3
        if tag:
            sql += " AND tags LIKE ?"
            args.append(f'%"{tag}"%')
        if since:
            sql += " AND created >= ?"
            args.append(since)
        if until:
            sql += " AND created <= ?"
            args.append(until)
        return self.q(sql + " ORDER BY created DESC", args)

    def segments(self, mid: str) -> list[dict]:
        return self.q("SELECT * FROM segments WHERE meeting_id=? ORDER BY t0", [mid])

    def add_segment(self, mid: str, source: str, t0: float, t1: float, text: str, language: str = "") -> int:
        return self.x("INSERT INTO segments(meeting_id,source,t0,t1,text,language) VALUES(?,?,?,?,?,?)",
                      [mid, source, t0, t1, text, language])

    def transcript_text(self, mid: str, since: float = 0, labels: dict[str, str] | None = None) -> str:
        labels = labels or {"you": "You", "others": "Others", "file": "Speaker"}
        lines = []
        for s in self.q("SELECT * FROM segments WHERE meeting_id=? AND t0>=? ORDER BY t0", [mid, since]):
            m, sec = divmod(int(s["t0"]), 60)
            lines.append(f"[{m:02d}:{sec:02d}] {labels.get(s['source'], s['source'])}: {s['text']}")
        return "\n".join(lines)

    def assists(self, mid: str) -> list[dict]:
        return self.q("SELECT * FROM assists WHERE meeting_id=? ORDER BY created DESC", [mid])

    # --- chats -------------------------------------------------------------------------
    def create_chat(self, title: str = "", meeting_id: str | None = None) -> dict:
        cid = new_id()
        now = time.time()
        self.x("INSERT INTO chats(id,title,created,updated,meeting_id) VALUES(?,?,?,?,?)",
               [cid, title, now, now, meeting_id])
        return self.one("SELECT * FROM chats WHERE id=?", [cid])  # type: ignore[return-value]

    def add_message(self, chat_id: str, role: str, content: str, sources: list | None = None) -> dict:
        mid = new_id()
        now = time.time()
        self.x("INSERT INTO messages(id,chat_id,role,content,sources,created) VALUES(?,?,?,?,?,?)",
               [mid, chat_id, role, content, json.dumps(sources or [], ensure_ascii=False), now])
        self.x("UPDATE chats SET updated=? WHERE id=?", [now, chat_id])
        return self.one("SELECT * FROM messages WHERE id=?", [mid])  # type: ignore[return-value]


db = DB()
