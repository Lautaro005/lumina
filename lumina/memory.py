"""Keeps Lumina's memory small: models that nobody is using are unloaded, and they load again by themselves.

Everything here is a cache. Unloading never loses data; the next request pays a few seconds to load the
model again. Turn it off in Settings → General ("Free memory when idle").
"""

from __future__ import annotations

import ctypes
import gc
import logging
import platform
import threading
import time

from .config import settings

log = logging.getLogger("lumina.memory")

# seconds without use before each model is let go
IDLE = {"embeddings": 300, "stt": 300, "llm": 900}
EVERY = 30


def trim() -> None:
    """Gives freed memory back to the OS (Python and the allocator keep it otherwise)."""
    gc.collect()
    if platform.system() == "Linux":
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def sweep(now: float | None = None) -> list[str]:
    """Unloads what has been idle for too long. Returns what it freed."""
    from . import llm
    from .embed import embedder
    from .kb import kb
    from .session import sessions
    from .stt import engine

    now = now or time.time()
    freed: list[str] = []
    recording = sessions.state() is not None
    indexing = kb.progress()["active"]
    if embedder.loaded and not indexing and now - embedder.used > IDLE["embeddings"]:
        embedder.unload()
        freed.append("embeddings")
    if engine.model_id and not recording and now - engine.used > IDLE["stt"]:
        engine.unload()
        freed.append("stt")
    if llm.builtin.running() and now - llm.builtin.used > IDLE["llm"]:
        llm.builtin.stop()
        freed.append("llm")
    if freed:
        log.info("freed memory: %s", ", ".join(freed))
        trim()
    return freed


def start() -> None:
    def loop() -> None:
        while True:
            time.sleep(EVERY)
            if not settings.get("memory_saver"):
                continue
            try:
                sweep()
            except Exception:  # noqa: BLE001
                log.exception("memory sweep failed")

    threading.Thread(target=loop, daemon=True, name="memory-saver").start()
