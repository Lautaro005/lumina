"""Knowledge base: ChromaDB collections (documents, meetings, notes), the indexing pipeline and the folder watcher.

Indexing is a two-stage pipeline. Two reader threads extract and chunk the next files while one worker embeds the
current one, so the model never waits for a PDF to be read. Everything that touches Chroma or the sources table
happens under one lock, together with a check that the source still exists: removing a source while it is being
indexed can never leave its passages behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CHROMA, UPLOADS, settings
from .db import db, new_id
from .embed import NotReady, embedder
from .extract import MAX_FILE_BYTES, SUPPORTED, chunk, extract

log = logging.getLogger("lumina.kb")

COLLECTIONS = ("documents", "meetings", "notes")
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store", "target", "dist", ".cache"}
ADD_BATCH = 256  # passages per Chroma write


@dataclass
class Job:
    kind: str  # source | meeting
    sid: str
    force: bool = False
    title: str = ""
    coll: str = ""
    texts: list[str] = field(default_factory=list)
    metas: list[dict] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    mtime: float = 0.0
    size: int = 0
    digest: str = ""
    outcome: str = "index"  # index | gone | skipped | unchanged
    note: str = ""


class KnowledgeBase:
    def __init__(self) -> None:
        self._client = None
        self._cols: dict[str, Any] = {}
        self._q: queue.Queue[tuple[str, str, bool]] = queue.Queue()
        self._ready: queue.Queue[Future] = queue.Queue(maxsize=3)  # files read and chunked, waiting for the model
        self._lock = threading.Lock()  # Chroma client and collection cache
        self._wlock = threading.RLock()  # every write to Chroma + the sources table (see the module docstring)
        self.listeners: list[Callable[[dict], None]] = []
        self._observer = None
        self._watches: dict[str, Any] = {}
        self._pending: dict[str, float] = {}
        self._queued: set[tuple[str, str]] = set()  # waiting in _q, so a burst of file events queues a file once
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb-read")
        # progress of the current batch of work (the bar in the UI)
        self._plock = threading.Lock()
        self._total = 0
        self._done = 0
        self._reading: OrderedDict[str, str] = OrderedDict()
        self._cur: dict[str, Any] | None = None
        self._rate_chunks = 0
        self._rate_secs = 0.0
        self._last_progress = 0.0
        threading.Thread(target=self._feeder, daemon=True, name="kb-feed").start()
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

    def reset_collection(self, name: str) -> None:
        """Throws a whole collection away and creates it again empty."""
        self.col(name)
        with self._lock:
            try:
                self._client.delete_collection(name)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                log.exception("could not delete collection %s", name)
            self._cols.pop(name, None)
        self.col(name)

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
            self.purge_orphans()
        except Exception:  # noqa: BLE001
            log.exception("chroma warm-up failed")
        self._emit_counts()

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

    def _emit_counts(self) -> None:
        self._emit({"type": "counts", **self.counts()})

    def purge_orphans(self) -> int:
        """Deletes passages whose source no longer exists (left by older versions or an interrupted removal)."""
        valid = {r["id"] for r in db.q("SELECT id FROM sources")}
        valid |= {f"meeting:{r['id']}" for r in db.q("SELECT id FROM meetings")}
        removed = 0
        for name in COLLECTIONS:
            col = self.col(name)
            stale: list[str] = []
            offset = 0
            while True:
                page = col.get(include=["metadatas"], limit=2000, offset=offset)
                ids = page.get("ids") or []
                if not ids:
                    break
                for i, meta in zip(ids, page.get("metadatas") or [], strict=False):
                    if (meta or {}).get("source_id") not in valid:
                        stale.append(i)
                offset += len(ids)
            with self._wlock:
                for s in range(0, len(stale), 500):
                    col.delete(ids=stale[s : s + 500])
            removed += len(stale)
        if removed:
            log.info("removed %d orphaned passages", removed)
        return removed

    # --- sources ---------------------------------------------------------------------------
    def add_path(self, path: str, watch: bool = True, tags: list[str] | None = None) -> dict:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(str(p))
        existing = db.one("SELECT * FROM sources WHERE path=? AND kind IN ('file','folder')", [str(p)])
        if existing:
            if existing["kind"] == "folder":
                self._scan_folder(existing["id"], p)
            else:
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
                folder = db.one("SELECT tags FROM sources WHERE id=?", [folder_id])
                if folder is None:  # the folder was removed while it was being scanned
                    return
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
        """Deletes a source, the files Lumina copied for it and every passage it produced."""
        with self._wlock:
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
                log.exception("could not delete the passages of %s", sid)
            db.x("DELETE FROM sources WHERE id=?", [sid])
            self._forget_upload(row.get("path") or "")
        self._emit({"type": "source", "id": sid, "status": "removed"})
        self._emit_counts()

    @staticmethod
    def _forget_upload(path: str) -> None:
        """Files added with the picker or by dropping are copied into the uploads folder; drop the copy too."""
        if not path:
            return
        try:
            p = Path(path)
            if p.is_file() and UPLOADS.resolve() in p.resolve().parents:
                p.unlink()
        except OSError:
            pass

    def clear_sources(self) -> int:
        """Removes every file, folder and note and all of their passages."""
        with self._wlock:
            rows = db.q("SELECT id,path FROM sources")
            if self._observer is not None:
                for w in list(self._watches.values()):
                    try:
                        self._observer.unschedule(w)
                    except Exception:  # noqa: BLE001
                        pass
            self._watches.clear()
            db.x("DELETE FROM sources")
            self.reset_collection("documents")
            self.reset_collection("notes")
            for r in rows:
                self._forget_upload(r.get("path") or "")
        self._emit({"type": "source", "id": "*", "status": "removed"})
        self._emit_counts()
        return len(rows)

    def clear_meetings(self) -> int:
        """Removes every meeting (transcripts, notes, assistance cards) and its passages."""
        with self._wlock:
            n = len(db.q("SELECT id FROM meetings"))
            db.x("DELETE FROM meetings")
            self.reset_collection("meetings")
        self._emit({"type": "meeting_indexed", "id": "*", "chunks": 0})
        self._emit_counts()
        return n

    @staticmethod
    def clear_old_uploads(min_age: float = 600.0) -> int:
        """Imported recordings stay in the uploads folder after they were transcribed; delete the ones that no
        source points at (and that are not being worked on right now)."""
        used = {r["path"] for r in db.q("SELECT path FROM sources WHERE path!=''")}
        n = 0
        for p in UPLOADS.glob("*"):
            try:
                if p.is_file() and str(p) not in used and time.time() - p.stat().st_mtime > min_age:
                    p.unlink()
                    n += 1
            except OSError:
                pass
        return n

    def enqueue(self, sid: str, kind: str = "source", force: bool = False) -> None:
        key = (kind, sid)
        with self._plock:
            if key in self._queued:
                return
            self._queued.add(key)
            self._track_new()
        if kind == "source":
            db.update("sources", sid, {"status": "pending", "error": ""})
        self._q.put((kind, sid, force))
        self._progress(force=True)
        if not embedder.loaded:  # load the model while the first file is still being read
            threading.Thread(target=self._warm, daemon=True, name="kb-warm").start()

    @staticmethod
    def _warm() -> None:
        try:
            embedder.warm()
        except Exception:  # noqa: BLE001  (not downloaded yet: the job reports it)
            pass

    def reindex_all(self, force: bool = False) -> int:
        n = 0
        for s in db.q("SELECT id,kind,path FROM sources WHERE kind!='meeting'"):
            if s["kind"] == "folder":
                if force:
                    for c in db.q("SELECT id FROM sources WHERE parent=?", [s["id"]]):
                        self.enqueue(c["id"], force=True)
                self._scan_folder(s["id"], Path(s["path"]))
            else:
                self.enqueue(s["id"], force=force)
            n += 1
        for m in db.q("SELECT id FROM meetings WHERE status='done'"):
            self.enqueue(m["id"], "meeting", force)
        return n

    # --- progress ----------------------------------------------------------------------------
    def _track_new(self) -> None:
        """Called with _plock held when work is added: a new batch starts when the previous one was finished."""
        if self._total and self._done >= self._total and not self._cur:
            self._total = self._done = 0
            self._rate_chunks, self._rate_secs = 0, 0.0
        self._total += 1

    def progress(self) -> dict[str, Any]:
        """What the progress bar shows: files done / total, the file in hand, a rough time left."""
        with self._plock:
            total, done = self._total, self._done
            cur = dict(self._cur) if self._cur else None
            if cur is None and self._reading:
                sid, title = next(iter(self._reading.items()))
                cur = {"id": sid, "title": title, "phase": "reading", "done": 0, "total": 0}
            active = total > 0 and done < total
            share = (cur["done"] / cur["total"]) if cur and cur["total"] else 0.0
            fraction = min(1.0, (done + share) / total) if total else 0.0
            eta = None
            if active and self._rate_secs > 1 and self._rate_chunks > 20:
                per_chunk = self._rate_secs / self._rate_chunks
                avg = self._rate_chunks / max(1, done) if done else (cur["total"] if cur and cur["total"] else 30)
                left = (cur["total"] - cur["done"] if cur and cur["total"] else 0) + max(0, total - done - 1) * avg
                eta = int(left * per_chunk)
            return {"active": active, "total": total, "done": done, "fraction": round(fraction, 4),
                    "current": cur, "eta": eta}

    def _progress(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_progress < 0.25:
            return
        self._last_progress = now
        self._emit({"type": "progress", **self.progress()})

    def _finish_item(self) -> None:
        with self._plock:
            self._done += 1
            self._cur = None
            finished = self._done >= self._total and self._q.empty() and self._ready.empty()
        self._progress(force=True)
        if finished:
            self._emit_counts()

    # --- indexing pipeline ---------------------------------------------------------------------
    def _feeder(self) -> None:
        """Takes queued work, starts reading it on the pool and hands the futures to the worker in order."""
        while True:
            kind, sid, force = self._q.get()
            with self._plock:
                self._queued.discard((kind, sid))
            fut = self._pool.submit(self._prepare, kind, sid, force)
            self._ready.put(fut)  # blocks while three files are already waiting: bounded memory

    def _worker(self) -> None:
        while True:
            job: Job | None = None
            try:
                job = self._ready.get().result()
                self._run(job)
            except NotReady as e:
                if job is not None and job.kind == "source":
                    db.update("sources", job.sid, {"status": "pending", "error": str(e)})
                    self._emit({"type": "source", "id": job.sid, "status": "pending", "error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("index failed: %s", job.sid if job else "?")
                if job is not None and job.kind == "source":
                    db.update("sources", job.sid, {"status": "error", "error": str(e)[:500]})
                    self._emit({"type": "source", "id": job.sid, "status": "error", "error": str(e)})
            finally:
                self._finish_item()

    def _prepare(self, kind: str, sid: str, force: bool) -> Job:
        """Reads and chunks one source. Runs on a reader thread; touches no collection."""
        job = Job(kind, sid, force)
        try:
            return self._read(job)
        except Exception as e:  # noqa: BLE001  (damaged file, unsupported format...)
            log.warning("could not read %s: %s", sid, e)
            job.outcome, job.note = "error", str(e)[:500] or e.__class__.__name__
            return job
        finally:
            self._reading_drop(sid)

    def _read(self, job: Job) -> Job:
        kind, sid, force = job.kind, job.sid, job.force
        if kind == "meeting":
            m = db.meeting(sid)
            if not m:
                job.outcome = "gone"
                return job
            title = m["title"] or "Meeting"
            self._reading_add(sid, title)
            self._prepare_meeting(job, m)
            return job
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if not row or row["kind"] == "folder":
            job.outcome = "gone"
            return job
        title = row["title"] or sid
        self._reading_add(sid, title)
        job.title, job.coll = title, "documents"
        if row["kind"] == "note":
            job.coll, text, job.mtime, job.size = "notes", row["text"], row["mtime"], len(row["text"])
        else:
            p = Path(row["path"])
            if not p.exists():
                job.outcome = "gone"
                return job
            st = p.stat()
            job.title = p.name
            if st.st_size > MAX_FILE_BYTES:
                job.outcome, job.note = "skipped", "file larger than 60 MB"
                return job
            job.mtime, job.size = st.st_mtime, st.st_size
            text = extract(p)
        tags = ",".join(row["tags"] or [])
        dim = settings.get("embedding_dim")
        job.digest = hashlib.sha1(f"{dim}|{tags}|{job.title}|{text}".encode(errors="replace")).hexdigest()
        if not force and row.get("hash") == job.digest and row["status"] != "error" and self._has_passages(
                job.coll, sid):
            job.outcome = "unchanged"
            return job
        job.texts = chunk(text, job.title)
        job.ids = [f"{sid}:{i}" for i in range(len(job.texts))]
        job.metas = [{"source_id": sid, "title": job.title, "kind": row["kind"], "path": row["path"] or "",
                      "tags": tags, "chunk": i} for i in range(len(job.texts))]
        return job

    def _reading_add(self, sid: str, title: str) -> None:
        with self._plock:
            self._reading[sid] = title
        self._progress(force=True)

    def _reading_drop(self, sid: str) -> None:
        with self._plock:
            self._reading.pop(sid, None)

    def _has_passages(self, coll: str, sid: str) -> bool:
        try:
            return bool(self.col(coll).get(where={"source_id": sid}, limit=1, include=[]).get("ids"))
        except Exception:  # noqa: BLE001
            return False

    def _run(self, job: Job) -> None:
        if job.kind == "meeting":
            return self._run_meeting(job)
        sid = job.sid
        if job.outcome == "gone":
            with self._wlock:
                row = db.one("SELECT path FROM sources WHERE id=?", [sid])
                if row:  # the file vanished from disk: forget it like the watcher would
                    self.remove(sid)
            return
        if job.outcome == "error":
            db.update("sources", sid, {"status": "error", "error": job.note})
            self._emit({"type": "source", "id": sid, "status": "error", "error": job.note})
            return
        if job.outcome == "skipped":
            db.update("sources", sid, {"status": "skipped", "error": job.note})
            self._emit({"type": "source", "id": sid, "status": "skipped"})
            return
        if job.outcome == "unchanged":
            with self._wlock:
                if db.one("SELECT id FROM sources WHERE id=?", [sid]):
                    db.update("sources", sid, {"status": "ready", "error": "", "mtime": job.mtime or time.time()})
            self._emit({"type": "source", "id": sid, "status": "ready"})
            return
        db.update("sources", sid, {"status": "indexing"})
        self._emit({"type": "source", "id": sid, "status": "indexing"})
        vecs = self._embed(job)
        with self._wlock:
            if not db.one("SELECT id FROM sources WHERE id=?", [sid]):
                return  # removed while it was being embedded: write nothing
            col = self.col(job.coll)
            col.delete(where={"source_id": sid})
            for s in range(0, len(job.texts), ADD_BATCH):
                e = s + ADD_BATCH
                col.add(ids=job.ids[s:e], embeddings=vecs[s:e], documents=job.texts[s:e], metadatas=job.metas[s:e])
            db.update("sources", sid, {"status": "ready", "chunks": len(job.texts), "mtime": job.mtime,
                                       "size": job.size, "hash": job.digest,
                                       "error": "" if job.texts else "no text found"})
        self._emit({"type": "source", "id": sid, "status": "ready", "chunks": len(job.texts)})
        self._emit_counts()

    def _embed(self, job: Job) -> list[list[float]]:
        """Embeds the passages of a job. Identical passages (repeated slides, boilerplate) are embedded once."""
        n = len(job.texts)
        with self._plock:
            self._cur = {"id": job.sid, "title": job.title, "phase": "embedding", "done": 0, "total": n}
        self._progress(force=True)
        if not n:
            return []
        unique: dict[str, int] = {}
        order: list[int] = []
        for t in job.texts:
            order.append(unique.setdefault(t, len(unique)))
        items = [(job.title, t) for t in unique]
        started = time.time()
        last = [0]

        def tick(done: int, total: int) -> None:
            share = done / total
            with self._plock:
                if self._cur:
                    self._cur["done"] = int(share * n)
            last[0] = done
            self._progress()

        vecs = embedder.documents(items, tick)
        with self._plock:
            self._rate_chunks += len(items)
            self._rate_secs += time.time() - started
            if self._cur:
                self._cur["done"] = n
        return [vecs[i] for i in order]

    # --- meetings ----------------------------------------------------------------------------------
    def index_meeting(self, mid: str) -> None:
        self.enqueue(mid, "meeting", True)

    def _prepare_meeting(self, job: Job, m: dict) -> None:
        mid = m["id"]
        job.title = m["title"] or "Meeting"
        job.coll = "meetings"
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["started"] or m["created"]))
        s = m["summary"] or {}
        head = [f"# {job.title} ({date})"]
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
        for piece in chunk("\n\n".join(head), job.title):
            job.texts.append(piece)
            job.metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": job.title, "kind": "notes",
                              "t0": 0.0, "date": date})
        # transcript in ~2-minute windows
        window: list[str] = []
        t0 = None
        words = 0
        for seg in db.segments(mid):
            if t0 is None:
                t0 = seg["t0"]
            mm, ss = divmod(int(seg["t0"]), 60)
            window.append(f"[{mm:02d}:{ss:02d}] {'You' if seg['source'] == 'you' else 'Others'}: {seg['text']}")
            words += len(seg["text"].split())
            if words > 320:
                job.texts.append(f"{job.title}, transcript\n" + "\n".join(window))
                job.metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": job.title,
                                  "kind": "transcript", "t0": float(t0), "date": date})
                window, t0, words = [], None, 0
        if window:
            job.texts.append(f"{job.title}, transcript\n" + "\n".join(window))
            job.metas.append({"meeting_id": mid, "source_id": f"meeting:{mid}", "title": job.title,
                              "kind": "transcript", "t0": float(t0 or 0), "date": date})
        job.ids = [f"m:{mid}:{i}" for i in range(len(job.texts))]

    def _run_meeting(self, job: Job) -> None:
        mid = job.sid
        if job.outcome == "gone":
            return
        vecs = self._embed(job)
        with self._wlock:
            if not db.meeting(mid):
                return
            col = self.col("meetings")
            col.delete(where={"meeting_id": mid})
            for s in range(0, len(job.texts), ADD_BATCH):
                e = s + ADD_BATCH
                col.add(ids=job.ids[s:e], embeddings=vecs[s:e], documents=job.texts[s:e], metadatas=job.metas[s:e])
            db.update("meetings", mid, {"indexed": time.time()})
        self._emit({"type": "meeting_indexed", "id": mid, "chunks": len(job.texts)})
        self._emit_counts()

    def remove_meeting(self, mid: str) -> None:
        with self._wlock:
            try:
                self.col("meetings").delete(where={"meeting_id": mid})
            except Exception:  # noqa: BLE001
                pass
        self._emit_counts()

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
        # files that were waiting when Lumina was closed
        for s in db.q("SELECT id FROM sources WHERE kind IN ('file','note') AND status IN ('pending','indexing')"):
            self.enqueue(s["id"])

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
