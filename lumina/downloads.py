"""Background model downloads with progress, resume and cancel."""

from __future__ import annotations

import fnmatch
import logging
import shutil
import tarfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .catalog import CATALOG, Item, installed

log = logging.getLogger("lumina.downloads")

HF = "https://huggingface.co"


class Job:
    def __init__(self, item: Item):
        self.item = item
        self.done = 0
        self.total = item.size
        self.state = "queued"  # queued | running | done | error | cancelled
        self.error = ""
        self.started = time.time()
        self.cancel = threading.Event()

    def public(self) -> dict[str, Any]:
        elapsed = max(time.time() - self.started, 0.001)
        return {
            "id": self.item.id, "state": self.state, "done": self.done, "total": self.total,
            "error": self.error, "speed": self.done / elapsed,
        }


class Downloads:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict], None]] = []

    def status(self) -> dict[str, dict]:
        with self._lock:
            return {k: j.public() for k, j in self.jobs.items()}

    def _emit(self, job: Job) -> None:
        for fn in list(self.listeners):
            try:
                fn(job.public())
            except Exception:  # noqa: BLE001
                pass

    def start(self, item_id: str) -> dict:
        item = CATALOG.get(item_id)
        if not item:
            raise KeyError(item_id)
        with self._lock:
            job = self.jobs.get(item_id)
            if job and job.state in ("queued", "running"):
                return job.public()
            job = Job(item)
            self.jobs[item_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"dl-{item_id}").start()
        return job.public()

    def cancel(self, item_id: str) -> None:
        job = self.jobs.get(item_id)
        if job:
            job.cancel.set()

    def remove(self, item_id: str) -> None:
        self.cancel(item_id)
        item = CATALOG[item_id]
        if item.path.exists():
            shutil.rmtree(item.path, ignore_errors=True)
        self.jobs.pop(item_id, None)

    # --- worker -------------------------------------------------------------------------
    def _plan(self, item: Item, client: httpx.Client) -> list[tuple[str, Path, int]]:
        """(url, destination, size) for every file of an item."""
        if item.repo:
            info = client.get(f"{HF}/api/models/{item.repo}/tree/main", params={"recursive": "true"})
            info.raise_for_status()
            entries = [e for e in info.json() if e.get("type") == "file"]
            pats = [f.lstrip("?") for f in item.files]
            plan = []
            for e in entries:
                name = e["path"]
                if any(fnmatch.fnmatch(name, pat) for pat in pats):
                    size = e.get("lfs", {}).get("size") or e.get("size") or 0
                    plan.append((f"{HF}/{item.repo}/resolve/main/{name}", item.path / name, int(size)))
            have = {x[1].relative_to(item.path).as_posix() for x in plan}
            missing = [p for p in item.files if "*" not in p and not p.startswith("?")
                       and not any(fnmatch.fnmatch(h, p) for h in have)]
            if missing:
                raise RuntimeError(f"files not found in {item.repo}: {', '.join(missing)}")
            return plan
        return [(url, item.path / name, 0) for url, name in item.urls]

    def _run(self, job: Job) -> None:
        item = job.item
        job.state = "running"
        self._emit(job)
        try:
            item.path.mkdir(parents=True, exist_ok=True)
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
                plan = self._plan(item, client)
                if all(s for _, _, s in plan):
                    job.total = sum(s for _, _, s in plan)
                base = 0
                for url, dest, size in plan:
                    got = self._fetch(client, url, dest, size, job, base)
                    base += got
                    job.done = base
            if item.archive:
                for _, name in item.urls:
                    self._extract(item.path / name, item.path)
            (item.path / ".complete").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
            job.state = "done"
            job.done = job.total
        except _Cancelled:
            job.state = "cancelled"
        except Exception as e:  # noqa: BLE001
            log.exception("download failed: %s", item.id)
            job.state = "error"
            job.error = str(e)
        self._emit(job)

    def _fetch(self, client: httpx.Client, url: str, dest: Path, size: int, job: Job, base: int) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and size and dest.stat().st_size == size:
            return size
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with client.stream("GET", url, headers=headers) as r:
            if r.status_code == 416:  # already complete
                part.replace(dest)
                return dest.stat().st_size
            r.raise_for_status()
            if r.status_code != 206:
                have = 0
            total = size or (int(r.headers.get("content-length", 0)) + have)
            if not size and total and job.total < base + total:
                job.total = base + total
            last = 0.0
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if job.cancel.is_set():
                        raise _Cancelled()
                    f.write(chunk)
                    have += len(chunk)
                    job.done = base + have
                    now = time.time()
                    if now - last > 0.5:
                        last = now
                        self._emit(job)
        part.replace(dest)
        return have

    @staticmethod
    def _extract(archive: Path, into: Path) -> None:
        if archive.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive) as t:
                t.extractall(into, filter="data")
        elif archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(into)
        archive.unlink(missing_ok=True)


class _Cancelled(Exception):
    pass


downloads = Downloads()

__all__ = ["downloads", "installed"]
