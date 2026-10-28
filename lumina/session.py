"""A live recording: audio → transcript → enhanced notes, assistance cards and, at the end, a summary."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import enhance, llm
from .audio import Mic, PermissionMissing, SystemAudio, load_audio_file
from .config import settings
from .db import db, new_id
from .embed import NotReady as EmbedNotReady
from .hub import hub
from .kb import kb
from .stt import Event, NotReady, Transcriber, engine, transcribe_file

ENHANCE_GAP = 12  # seconds between two enhance runs while recording

log = logging.getLogger("lumina.session")


class LiveSession:
    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        self.t_start = time.time()
        self.offset = 0.0  # resuming adds onto the previous duration
        self.mic: Mic | None = None
        self.sys: SystemAudio | None = None
        self.tr: Transcriber | None = None
        self.paused = False
        self.stop_flag = threading.Event()
        self.last_enhance = 0.0
        self.last_cues = ""
        self.cancel = threading.Event()  # set when the meeting ends: nothing is enhanced after that
        self.segments_since = 0
        self.enhancing = threading.Lock()
        self.last_assist = 0.0
        self.sources: list[str] = []
        self.recent: deque[dict[str, str]] = deque(maxlen=8)  # last sentences, for the notch
        self.speaking: dict[str, str] = {}  # what each source is saying right now (not final yet)

    def clock(self) -> float:
        return self.offset + (time.time() - self.t_start)

    # --- lifecycle -----------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        m = db.meeting(self.meeting_id)
        if not m:
            raise KeyError(self.meeting_id)
        self.offset = float(m.get("duration") or 0)
        model = engine.load()  # raises NotReady with a clear message
        self.tr = Transcriber(self._on_event, self.clock)
        you = self.tr.stream("you")
        mic_error: Exception | None = None
        try:
            dev = settings.get("mic_device")
            self.mic = Mic(lambda x: None if self.paused else you.feed(x), dev if dev not in (None, "") else None)
            self.mic.start()
            self.sources.append("you")
        except PermissionMissing as e:
            # never a macOS window here: the meeting says where to allow it
            mic_error, self.mic = e, None
            hub.publish({"type": "permission", "meeting_id": self.meeting_id, "kind": e.kind, "state": e.state,
                         "text": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("mic failed")
            mic_error, self.mic = e, None
            hub.publish({"type": "warning", "meeting_id": self.meeting_id, "text": f"Microphone unavailable: {e}"})
        if settings.get("system_audio"):
            others = self.tr.stream("others")
            self.sys = SystemAudio(lambda x: None if self.paused else others.feed(x), self._sys_failed)
            if self.sys.start():
                self.sources.append("others")
        if not self.sources:
            self.tr.stop()
            raise RuntimeError(str(mic_error) if isinstance(mic_error, PermissionMissing) else
                               "No audio source could be opened. Check the microphone permission.")
        now = time.time()
        db.update("meetings", self.meeting_id, {"status": "live", "started": m.get("started") or now,
                                                "language": settings.get("stt_language")})
        self.t_start = now
        threading.Thread(target=self._ticker, daemon=True, name="live-ticker").start()
        hub.publish({"type": "live", "meeting_id": self.meeting_id, "state": "recording", "model": model,
                     "sources": self.sources, "offset": self.offset})
        return {"model": model, "sources": self.sources}

    def _sys_failed(self, msg: str) -> None:
        if "others" in self.sources:
            self.sources.remove("others")
        hub.publish({"type": "sysaudio", "meeting_id": self.meeting_id, "ok": False, "text": msg})

    def pause(self, paused: bool) -> None:
        self.paused = paused
        hub.publish({"type": "live", "meeting_id": self.meeting_id, "state": "paused" if paused else "recording",
                     "sources": self.sources, "offset": self.offset})

    def stop(self) -> None:
        self.stop_flag.set()
        self.cancel.set()
        if self.mic:
            self.mic.stop()
        if self.sys:
            self.sys.stop()
        if self.tr:
            self.tr.stop()
        duration = self.clock()
        db.update("meetings", self.meeting_id, {"status": "done", "ended": time.time(), "duration": duration})
        hub.publish({"type": "live", "meeting_id": self.meeting_id, "state": "processing"})
        threading.Thread(target=finish_meeting, args=(self.meeting_id,), daemon=True).start()

    # --- events ------------------------------------------------------------------------
    def _on_event(self, ev: Event) -> None:
        base = {"meeting_id": self.meeting_id, "source": ev.source}
        if ev.kind == "level":
            hub.publish({"type": "level", **base, "level": round(ev.level, 3)})
        elif ev.kind == "partial":
            self.speaking[ev.source] = ev.text
            hub.publish({"type": "partial", **base, "text": ev.text, "t0": ev.t0})
        elif ev.kind == "error":
            hub.publish({"type": "warning", **base, "text": ev.text})
        elif ev.kind == "final":
            self.speaking[ev.source] = ""
            self.recent.append({"source": ev.source, "text": ev.text})
            sid = db.add_segment(self.meeting_id, ev.source, ev.t0, ev.t1, ev.text, ev.language)
            self.segments_since += 1
            hub.publish({"type": "segment", **base, "id": sid, "t0": ev.t0, "t1": ev.t1, "text": ev.text,
                         "language": ev.language})
            if settings.get("live_assistance") and enhance.looks_like_question(ev.text):
                threading.Thread(target=self._assist, args=(ev.text,), daemon=True).start()

    def _ticker(self) -> None:
        while not self.stop_flag.wait(1.0):
            if self.paused:
                continue
            hub.publish({"type": "tick", "meeting_id": self.meeting_id, "elapsed": round(self.clock(), 1)})
            if not settings.get("auto_enhance") or time.time() - self.last_enhance < ENHANCE_GAP:
                continue
            m = db.meeting(self.meeting_id)
            cues = (m or {}).get("cues", "").strip()
            # enhance as the meeting goes: when there is something new in the transcript or in the user's notes
            if cues and (self.segments_since >= 2 or (cues != self.last_cues and db.transcript_text(self.meeting_id).strip())):
                threading.Thread(target=self.enhance_now, daemon=True).start()

    def enhance_now(self) -> None:
        if self.cancel.is_set() or not self.enhancing.acquire(blocking=False):
            return
        try:
            self.last_enhance = time.time()
            self.segments_since = 0
            m = db.meeting(self.meeting_id)
            self.last_cues = (m or {}).get("cues", "").strip()
            run_enhance(self.meeting_id, cancel=self.cancel)
        finally:
            self.enhancing.release()

    def _assist(self, text: str) -> None:
        if time.time() - self.last_assist < 15:
            return
        self.last_assist = time.time()
        question = enhance.extract_question(text)
        try:
            hits = kb.search(question, k=4, min_score=0.42)
        except EmbedNotReady:
            return
        except Exception:  # noqa: BLE001
            log.exception("assist search failed")
            return
        hits = [h for h in hits if h.get("meeting_id") != self.meeting_id]
        if not hits:
            return
        try:
            ok, _ = llm.ready()
            if not ok:
                return
            answer = enhance.assist_answer(question, hits, db.transcript_text(self.meeting_id, max(0, self.clock() - 120)))
        except llm.LLMError as e:
            log.warning("assist failed: %s", e)
            return
        if not answer:
            return
        aid = new_id()
        srcs = [{"title": h.get("title", ""), "path": h.get("path", ""), "meeting_id": h.get("meeting_id", ""),
                 "snippet": h["text"][:240], "score": h["score"]} for h in hits]
        db.x("INSERT INTO assists(id,meeting_id,created,question,answer,sources) VALUES(?,?,?,?,?,?)",
             [aid, self.meeting_id, time.time(), question, answer, json.dumps(srcs, ensure_ascii=False)])
        hub.publish({"type": "assist", "meeting_id": self.meeting_id,
                     "assist": {"id": aid, "question": question, "answer": answer, "sources": srcs,
                                "created": time.time(), "seen": 0}})


def run_enhance(meeting_id: str, cancel: threading.Event | None = None) -> list[dict]:
    """Expands the user's notes with what was said. Runs while recording, and on demand for a finished meeting."""
    m = db.meeting(meeting_id)
    if not m:
        return []
    ok, why = llm.ready()
    if not ok:
        hub.publish({"type": "warning", "meeting_id": meeting_id, "text": why})
        return m["enhanced"] or []
    hub.publish({"type": "enhancing", "meeting_id": meeting_id, "state": "start"})
    try:
        blocks = enhance.enhance(m["cues"], db.transcript_text(meeting_id), m["title"], cancel=cancel)
    except llm.LLMError as e:
        quiet = cancel is not None and cancel.is_set()
        hub.publish({"type": "enhancing", "meeting_id": meeting_id, "state": "idle" if quiet else "error", "text": str(e)})
        return m["enhanced"] or []
    if cancel is not None and cancel.is_set():
        hub.publish({"type": "enhancing", "meeting_id": meeting_id, "state": "idle"})
        return m["enhanced"] or []
    old = {b.get("cue"): b.get("text") for b in (m["enhanced"] or [])}
    now = time.time()
    for b in blocks:
        b["changed"] = now if old.get(b["cue"]) != b["text"] and b["text"] else 0
    db.update("meetings", meeting_id, {"enhanced": blocks})
    hub.publish({"type": "enhanced", "meeting_id": meeting_id, "blocks": blocks})
    return blocks


def run_summary(meeting_id: str, instructions: str = "", preset: str = "") -> bool:
    """The summary and the automatic title. Separate from the enhanced notes: it reads the whole meeting.
    `instructions` (free text) and `preset` (expand, shorten...) say how the user wants it; a preset rewrites the
    summary that is there."""
    m = db.meeting(meeting_id)
    if not m:
        return False
    transcript = db.transcript_text(meeting_id)
    ok, why = llm.ready()
    if not ok:
        hub.publish({"type": "warning", "meeting_id": meeting_id, "text": why})
        return False
    if not transcript.strip():
        hub.publish({"type": "warning", "meeting_id": meeting_id, "text": "There is no transcript to summarize."})
        return False
    try:
        hub.publish({"type": "summarizing", "meeting_id": meeting_id})
        asked = "\n".join(x for x in (enhance.SUMMARY_PRESETS.get(preset, ""), instructions.strip()) if x)
        previous = m["summary"] if preset and m["summary"] else None
        s = enhance.summarize(m["cues"], transcript, m["title"], asked, previous)
        if preset and s.get("request"):
            s["request"] = instructions.strip() or preset
    except llm.LLMError as e:
        hub.publish({"type": "warning", "meeting_id": meeting_id, "text": f"Summary failed: {e}"})
        hub.publish({"type": "summary", "meeting_id": meeting_id, "summary": m["summary"] or {}, "title": None})
        return False
    fields: dict[str, Any] = {"summary": s}
    if not (db.meeting(meeting_id) or m)["title"].strip() and s.get("title"):
        fields["title"] = s["title"]
    db.update("meetings", meeting_id, fields)
    hub.publish({"type": "summary", "meeting_id": meeting_id, "summary": s, "title": fields.get("title")})
    return True


def finish_meeting(meeting_id: str, summary: bool = True, instructions: str = "", preset: str = "",
                   auto_brain: bool = True) -> None:
    """After recording: the summary and automatic title, then index into the knowledge base (and, if the user
    turned it on, save the meeting in brain). The notes are not enhanced here: they ran with the meeting."""
    if not db.meeting(meeting_id):
        return
    if summary:
        run_summary(meeting_id, instructions, preset)
    try:
        kb.index_meeting(meeting_id)
    except Exception:  # noqa: BLE001
        log.exception("index meeting failed")
    hub.publish({"type": "live", "meeting_id": meeting_id, "state": "done"})
    m = db.meeting(meeting_id)
    if auto_brain and m and (settings.get("brain_autosave") or m.get("brain_path")):
        # kept in step: a meeting already in brain is updated there when its summary changes
        save_to_brain(meeting_id, quiet=True)


def save_to_brain(meeting_id: str, quiet: bool = False) -> str | None:
    """Writes the meeting into brain's vault (knowledge/lumina-<date>-<title>.md) and remembers where."""
    from . import integrations

    m = db.meeting(meeting_id)
    if not m:
        return None
    transcript = db.transcript_text(meeting_id) if settings.get("brain_transcript") else ""
    path, content = integrations.meeting_note(m, transcript)
    try:
        integrations.brain_write(path, content)
    except integrations.IntegrationError as e:
        hub.publish({"type": "brain", "meeting_id": meeting_id, "ok": False, "text": str(e), "quiet": quiet})
        return None
    db.update("meetings", meeting_id, {"brain_path": path, "brain_saved": time.time()})
    hub.publish({"type": "brain", "meeting_id": meeting_id, "ok": True, "path": path, "quiet": quiet})
    return path


def import_recording(meeting_id: str, path) -> None:  # noqa: ANN001
    """Transcribe an uploaded audio/video file into a meeting, then summarize it like a finished one.
    There are no notes to enhance in an imported recording: it gets a transcript and a summary."""
    db.update("meetings", meeting_id, {"audio": str(path)})
    transcribe_meeting_audio(meeting_id)


def transcribe_meeting_audio(meeting_id: str) -> None:
    """(Re)transcribes the recording kept for an imported meeting; the old transcript is replaced."""
    m = db.meeting(meeting_id)
    path = (m or {}).get("audio") or ""
    try:
        if not path or not Path(path).exists():
            raise FileNotFoundError("the recording is no longer on disk")
        engine.load()
        audio = load_audio_file(Path(path))
        db.x("DELETE FROM segments WHERE meeting_id=?", [meeting_id])
        db.update("meetings", meeting_id, {"status": "live", "started": time.time(), "duration": audio.size / 16000})
        hub.publish({"type": "live", "meeting_id": meeting_id, "state": "importing"})

        def emit(ev: Event) -> None:
            if ev.kind == "final":
                sid = db.add_segment(meeting_id, "others", ev.t0, ev.t1, ev.text, ev.language)
                hub.publish({"type": "segment", "meeting_id": meeting_id, "source": "others", "id": sid,
                             "t0": ev.t0, "t1": ev.t1, "text": ev.text})

        transcribe_file(audio, emit, settings.get("stt_language") or "auto")
        db.update("meetings", meeting_id, {"status": "done", "ended": time.time()})
        finish_meeting(meeting_id)
    except (NotReady, Exception) as e:  # noqa: BLE001
        log.exception("import failed")
        db.update("meetings", meeting_id, {"status": "done"})
        hub.publish({"type": "warning", "meeting_id": meeting_id, "text": f"Transcription failed: {e}"})
        hub.publish({"type": "live", "meeting_id": meeting_id, "state": "done"})


class Sessions:
    def __init__(self) -> None:
        self.current: LiveSession | None = None
        self.lock = threading.Lock()

    def start(self, meeting_id: str) -> dict:
        with self.lock:
            if self.current and not self.current.stop_flag.is_set():
                if self.current.meeting_id == meeting_id:
                    return {"model": engine.model_id, "sources": self.current.sources}
                self.current.stop()
            s = LiveSession(meeting_id)
            info = s.start()
            self.current = s
            return info

    def stop(self, meeting_id: str) -> None:
        with self.lock:
            if self.current and self.current.meeting_id == meeting_id:
                self.current.stop()
                self.current = None

    def state(self) -> dict | None:
        s = self.current
        if not s or s.stop_flag.is_set():
            return None
        return {"meeting_id": s.meeting_id, "paused": s.paused, "elapsed": s.clock(), "sources": s.sources,
                "model": engine.model_id}


sessions = Sessions()
