"""Knowledge base: ChromaDB collections (documents, meetings, notes), indexing queue and folder watcher."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import CHROMA
from .db import db, new_id
from .embed import NotReady, embedder
from .extract import MAX_FILE_BYTES, SUPPORTED, chunk, extract

log = logging.getLogger("lumina.kb")

COLLECTIONS = ("documents", "meetings", "notes")
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store", "target", "dist", ".cache"}


class KnowledgeBase:
    def __init__(self) -> None:
        self._client = None
        self._cols: dict[str, Any] = {}
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict], None]] = []
        self._observer = None
        self._watches: dict[str, Any] = {}
        self._pending: dict[str, float] = {}
        threading.Thread(target=self._worker, daemon=True, name="kb-index").start()
        threading.Thread(target=self._debouncer, daemon=True, name="kb-debounce").start()

    # --- chroma --------------------------------------------------------------------------
    def col(self, name: str):
        with self._lock:
            if self._client is None:
                import chromadb
                from chromadb.config import Settings as CSettings

                self._client = chromadb.PersistentClient(path=str(CHROMA), settings=CSettings(anonymized_telemetry=False))
            if name not in self._cols:
                self._cols[name] = self._client.get_or_create_collection(
                    name, embedding_function=None, metadata={"hnsw:space": "cosine"})
            return self._cols[name]

    def _emit(self, event: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001
                pass

    def counts_warm(self) -> None:
        try:
            for c in COLLECTIONS:
                self.col(c)
        except Exception:  # noqa: BLE001
            log.exception("chroma warm-up failed")

    def counts(self) -> dict[str, int]:
        if self._client is None:  # Chroma is still starting; do not block the caller on it
            return {c: 0 for c in COLLECTIONS}
        out = {}
        for c in COLLECTIONS:
            try:
                out[c] = self.col(c).count()
            except Exception:  # noqa: BLE001
                out[c] = 0
        return out

    # --- sources ---------------------------------------------------------------------------
    def add_path(self, path: str, watch: bool = True, tags: list[str] | None = None) -> dict:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(str(p))
        existing = db.one("SELECT * FROM sources WHERE path=? AND kind IN ('file','folder')", [str(p)])
        if existing:
            self.enqueue(existing["id"])
            return existing
        sid = new_id()
        kind = "folder" if p.is_dir() else "file"
        db.x("INSERT INTO sources(id,kind,path,title,ext,tags,added,watch) VALUES(?,?,?,?,?,?,?,?)",
             [sid, kind, str(p), p.name, p.suffix.lower(), json.dumps(tags or []), time.time(), int(watch)])
        if kind == "folder":
            self._scan_folder(sid, p)
            if watch:
                self._watch(sid, p)
        else:
            self.enqueue(sid)
        return db.one("SELECT * FROM sources WHERE id=?", [sid])  # type: ignore[return-value]

    def _scan_folder(self, folder_id: str, root: Path) -> None:
        known = {s["path"]: s for s in db.q("SELECT * FROM sources WHERE parent=?", [folder_id])}
        seen = set()
        for f in root.rglob("*"):
            if any(part in SKIP_DIRS or part.startswith(".") for part in f.relative_to(root).parts):
                continue
            if not f.is_file() or f.suffix.lower() not in SUPPORTED:
                continue
            seen.add(str(f))
            row = known.get(str(f))
            mtime = f.stat().st_mtime
            if row and abs(row["mtime"] - mtime) < 1 and row["status"] == "ready":
                continue
            if not row:
                sid = new_id()
                folder = db.one("SELECT tags FROM sources WHERE id=?", [folder_id]) or {"tags": []}
                db.x("INSERT INTO sources(id,kind,path,parent,title,ext,tags,added) VALUES(?,?,?,?,?,?,?,?)",
                     [sid, "file", str(f), folder_id, f.name, f.suffix.lower(), json.dumps(folder["tags"]), time.time()])
                self.enqueue(sid)
            else:
                self.enqueue(row["id"])
        for path, row in known.items():
            if path not in seen:
                self.remove(row["id"])
        db.update("sources", folder_id, {"status": "ready", "chunks": len(seen), "mtime": time.time()})
        self._emit({"type": "source", "id": folder_id, "status": "ready"})

    def add_note(self, title: str, text: str, tags: list[str] | None = None, note_id: str | None = None) -> dict:
        sid = note_id or new_id()
        if db.one("SELECT id FROM sources WHERE id=?", [sid]):
            db.update("sources", sid, {"title": title, "text": text, "tags": tags or [], "mtime": time.time(),
                                       "status": "pending"})
        else:
            db.x("INSERT INTO sources(id,kind,title,tags,added,mtime,text) VALUES(?,?,?,?,?,?,?)",
                 [sid, "note", title, json.dumps(tags or []), time.time(), time.time(), text])
        self.enqueue(sid)
        return db.one("SELECT * FROM sources WHERE id=?", [sid])  # type: ignore[return-value]

    def remove(self, sid: str) -> None:
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if not row:
            return
        if row["kind"] == "folder":
            for child in db.q("SELECT id FROM sources WHERE parent=?", [sid]):
                self.remove(child["id"])
            w = self._watches.pop(sid, None)
            if w is not None and self._observer is not None:
                try:
                    self._observer.unschedule(w)
                except Exception:  # noqa: BLE001
                    pass
        coll = "notes" if row["kind"] == "note" else "meetings" if row["kind"] == "meeting" else "documents"
        try:
            self.col(coll).delete(where={"source_id": sid})
        except Exception:  # noqa: BLE001
            pass
        db.x("DELETE FROM sources WHERE id=?", [sid])
        self._emit({"type": "source", "id": sid, "status": "removed"})

    def enqueue(self, sid: str, kind: str = "source") -> None:
        if kind == "source":
            db.update("sources", sid, {"status": "pending", "error": ""})
        self._q.put((kind, sid))

    def reindex_all(self) -> int:
        n = 0
        for s in db.q("SELECT id,kind,path FROM sources WHERE kind!='meeting'"):
            if s["kind"] == "folder":
                self._scan_folder(s["id"], Path(s["path"]))
            else:
                self.enqueue(s["id"])
            n += 1
        for m in db.q("SELECT id FROM meetings WHERE status='done'"):
            self._q.put(("meeting", m["id"]))
        return n

    # --- indexing worker ---------------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            kind, sid = self._q.get()
            try:
                if kind == "meeting":
                    self._index_meeting(sid)
                else:
                    self._index_source(sid)
            except NotReady as e:
                if kind == "source":
                    db.update("sources", sid, {"status": "pending", "error": str(e)})
                    self._emit({"type": "source", "id": sid, "status": "pending", "error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("index failed: %s", sid)
                if kind == "source":
                    db.update("sources", sid, {"status": "error", "error": str(e)[:500]})
                    self._emit({"type": "source", "id": sid, "status": "error", "error": str(e)})

    def _index_source(self, sid: str) -> None:
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if not row or row["kind"] == "folder":
            return
        db.update("sources", sid, {"status": "indexing"})
        self._emit({"type": "source", "id": sid, "status": "indexing"})
        if row["kind"] == "note":
            text, title, coll, mtime, size = row["text"], row["title"], "notes", row["mtime"], len(row["text"])
        else:
            p = Path(row["path"])
            if not p.exists():
                self.remove(sid)
                return
            st = p.stat()
            if st.st_size > MAX_FILE_BYTES:
                db.update("sources", sid, {"status": "skipped", "error": "file larger than 60 MB"})
                self._emit({"type": "source", "id": sid, "status": "skipped"})
                return
            text, title, coll, mtime, size = extract(p), p.name, "documents", st.st_mtime, st.st_size
        pieces = chunk(text, title)
        col = self.col(coll)
        col.delete(where={"source_id": sid})
        if pieces:
            tags = ",".join(row["tags"] or [])
            for start in range(0, len(pieces), 32):
                batch = pieces[start : start + 32]
                vecs = embedder.documents([(title, c) for c in batch])
                col.add(
                    ids=[f"{sid}:{start + i}" for i in range(len(batch))],
                    embeddings=vecs,
                    documents=batch,
                    metadatas=[{"source_id": sid, "title": title, "kind": row["kind"], "path": row["path"] or "",
                                "tags": tags, "chunk": start + i} for i in range(len(batch))],
                )
        db.update("sources", sid, {"status": "ready", "chunks": len(pieces), "mtime": mtime, "size": size,
                                   "error": "" if pieces else "no text found"})
        self._emit({"type": "source", "id": sid, "status": "ready", "chunks": len(pieces)})

    def index_meeting(self, mid: str) -> None:
        self._q.put(("meeting", mid))

    def _index_meeting(self, mid: str) -> None:
        m = db.meeting(mid)
        if not m:
            return
        col = self.col("meetings")
        col.delete(where={"meeting_id": mid})
        title = m["title"] or "Meeting"
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["started"] or m["created"]))
        docs: list[str] = []
        metas: list[dict] = []
        s = m["summary"] or {}
        head = [f"# {title} ({date})"]
        if s.get("overview"):
            head.append(s["overview"])
        for key, label in (("decisions", "Decisions"), ("action_items", "Action items"), ("topics", "Topics")):
            if s.get(key):
                head.append(f"## {label}\n" + "\n".join(f"- {x}" for x in s[key]))
        if m["cues"].strip():
            head.append("## My notes\n" + m["cues"])
        for block in m["enhanced"] or []:
            if block.get("text"):
                head.append(f"## {block.get('cue') or 'Notes'}\n{block['text']}")
        for piece in chunk("\n\n".join(head), title):
            docs.append(piece)
            metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": title, "kind": "notes",
                          "t0": 0.0, "date": date})
        # transcript in ~2-minute windows
        window: list[str] = []
        t0 = None
        words = 0
        for seg in db.segments(mid):
            if t0 is None:
                t0 = seg["t0"]
            mm, ss = divmod(int(seg["t0"]), 60)
            line = f"[{mm:02d}:{ss:02d}] {'You' if seg['source'] == 'you' else 'Others'}: {seg['text']}"
            window.append(line)
            words += len(seg["text"].split())
            if words > 320:
                docs.append(f"{title}, transcript\n" + "\n".join(window))
                metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": title,
                              "kind": "transcript", "t0": float(t0), "date": date})
                window, t0, words = [], None, 0
        if window:
            docs.append(f"{title}, transcript\n" + "\n".join(window))
            metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": title, "kind": "transcript",
                          "t0": float(t0 or 0), "date": date})
        for start in range(0, len(docs), 32):
            batch = docs[start : start + 32]
            vecs = embedder.documents([(title, d) for d in batch])
            col.add(ids=[f"m:{mid}:{start + i}" for i in range(len(batch))], embeddings=vecs, documents=batch,
                    metadatas=metas[start : start + 32])
        db.update("meetings", mid, {"indexed": time.time()})
        self._emit({"type": "meeting_indexed", "id": mid, "chunks": len(docs)})

    def remove_meeting(self, mid: str) -> None:
        try:
            self.col("meetings").delete(where={"meeting_id": mid})
        except Exception:  # noqa: BLE001
            pass

    # --- search ------------------------------------------------------------------------------
    def search(self, query: str, k: int = 8, collections: tuple[str, ...] = COLLECTIONS,
               meeting_id: str | None = None, min_score: float = 0.0) -> list[dict]:
        if not query.strip():
            return []
        vec = embedder.query(query)
        hits: list[dict] = []
        for name in collections:
            col = self.col(name)
            if col.count() == 0:
                continue
            where = {"meeting_id": meeting_id} if meeting_id and name == "meetings" else None
            res = col.query(query_embeddings=[vec], n_results=min(k, col.count()), where=where)
            for i, doc in enumerate(res["documents"][0]):
                meta = res["metadatas"][0][i] or {}
                score = 1 - float(res["distances"][0][i])
                if score < min_score:
                    continue
                hits.append({"id": res["ids"][0][i], "collection": name, "text": doc, "score": round(score, 4), **meta})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:k]

    # --- watcher -----------------------------------------------------------------------------
    def start_watching(self) -> None:
        for f in db.q("SELECT * FROM sources WHERE kind='folder' AND watch=1"):
            p = Path(f["path"])
            if p.exists():
                self._watch(f["id"], p)
                threading.Thread(target=self._scan_folder, args=(f["id"], p), daemon=True).start()

    def _watch(self, folder_id: str, root: Path) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return
        if self._observer is None:
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
        kb = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: N802
                if event.is_directory:
                    return
                kb._pending[folder_id] = time.time()

        self._watches[folder_id] = self._observer.schedule(Handler(), str(root), recursive=True)

    def _debouncer(self) -> None:
        while True:
            time.sleep(1.5)
            now = time.time()
            for fid, t in list(self._pending.items()):
                if now - t > 2.0:
                    self._pending.pop(fid, None)
                    row = db.one("SELECT * FROM sources WHERE id=?", [fid])
                    if row and Path(row["path"]).exists():
                        try:
                            self._scan_folder(fid, Path(row["path"]))
                        except Exception:  # noqa: BLE001
                            log.exception("rescan failed")


kb = KnowledgeBase()
