"""Speech to text: model wrappers (Parakeet, Canary, Whisper) and the streaming segmenter.

Audio arrives as 16 kHz mono float32 per source ("you" = microphone, "others" = system audio).
Silero VAD cuts it into utterances; partial results are re-decoded about once a second while someone
is speaking, and a final result is emitted when they pause.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .catalog import CATALOG, EG2_STT, installed, stt_choice
from .config import settings

log = logging.getLogger("lumina.stt")
SR = 16000


class NotReady(RuntimeError):
    pass


class Engine:
    """Loads one speech model at a time; thread-safe recognize()."""

    def __init__(self) -> None:
        self.model_id = ""
        self._model = None
        self._lock = threading.Lock()
        self.used = time.time()

    def current_choice(self) -> str:
        return stt_choice(settings.get("stt_engine"), settings.get("stt_language"), settings.get("whisper_size"))

    def load(self, model_id: str | None = None) -> str:
        model_id = model_id or self.current_choice()
        with self._lock:
            if self._model is not None and self.model_id == model_id:
                return model_id
            if not installed(model_id):
                raise NotReady(f"{CATALOG[model_id].label} is not downloaded yet")
            path = CATALOG[model_id].path
            self._model = None
            if model_id == EG2_STT:
                from .eg2 import NotReady as Eg2NotReady
                from .eg2 import Recognizer

                try:
                    self._model = Recognizer(path)
                except Eg2NotReady as e:
                    raise NotReady(str(e)) from e
            elif model_id.startswith("whisper"):
                from faster_whisper import WhisperModel

                self._model = WhisperModel(str(path), device="auto", compute_type="int8")
            else:
                import onnx_asr
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.log_severity_level = 3
                name = "nemo-parakeet-tdt-0.6b-v3" if model_id == "parakeet-v3" else "nemo-canary-1b-v2"
                self._model = onnx_asr.load_model(name, str(path), quantization="int8",
                                                  providers=["CPUExecutionProvider"], sess_options=opts)
            self.model_id = model_id
            return model_id

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self.model_id = ""

    @property
    def fast(self) -> bool:
        return self.model_id in ("parakeet-v3", "whisper-tiny", "whisper-base", "canary-1b-v2")

    @property
    def partials_ok(self) -> bool:
        """The EmbeddingGemma 2 reader re-reads a 30 s window per call, too heavy to re-decode every second."""
        return self.model_id != EG2_STT

    def recognize(self, audio: np.ndarray, language: str = "auto") -> tuple[str, str]:
        """Returns (text, detected_or_forced_language)."""
        self.used = time.time()
        if audio.size < SR * 0.3:
            return "", language
        with self._lock:
            if self._model is None:
                raise NotReady("speech model not loaded")
            lang = None if language in ("", "auto") else language
            if self.model_id == EG2_STT:
                return self._model.transcribe(audio), "en"
            if self.model_id.startswith("whisper"):
                segs, info = self._model.transcribe(audio, language=lang, beam_size=1, vad_filter=False,
                                                    condition_on_previous_text=False, without_timestamps=True)
                text = " ".join(s.text.strip() for s in segs).strip()
                return text, info.language or (lang or "")
            if self.model_id == "canary-1b-v2":
                text = self._model.recognize(audio, language=lang or "en")
                return str(text).strip(), lang or "en"
            return str(self._model.recognize(audio)).strip(), lang or ""


engine = Engine()


class Vad:
    """Streaming Silero VAD: 512-sample frames with 64 samples of context."""

    HOP = 512
    CTX = 64

    def __init__(self) -> None:
        import onnxruntime as ort

        if not installed("silero-vad"):
            raise NotReady("Silero VAD is not downloaded yet")
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(CATALOG["silero-vad"].path / "silero_vad.onnx"), opts,
                                         providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), np.float32)
        self.ctx = np.zeros(self.CTX, np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.ctx, frame]).astype(np.float32)[None, :]
        out, self.state = self.sess.run(["output", "stateN"], {"input": x, "state": self.state,
                                                                "sr": np.array([SR], np.int64)})
        self.ctx = frame[-self.CTX:]
        return float(out[0, 0])


@dataclass
class Event:
    kind: str  # partial | final | level
    source: str
    text: str = ""
    t0: float = 0.0
    t1: float = 0.0
    language: str = ""
    level: float = 0.0


class Stream:
    """Segments one audio source into utterances and asks the engine to transcribe them."""

    def __init__(self, source: str, emit: Callable[[Event], None], jobs: queue.Queue, clock: Callable[[], float]):
        self.source = source
        self.emit = emit
        self.jobs = jobs
        self.clock = clock
        self.vad = Vad()
        self.pending = np.zeros(0, np.float32)
        self.utt: list[np.ndarray] = []
        self.utt_t0 = 0.0
        self.speaking = False
        self.silence = 0
        self.last_partial = 0.0
        self.pre: list[np.ndarray] = []  # a little audio before speech starts
        self.level_t = 0.0
        self.seq = 0

    def feed(self, samples: np.ndarray) -> None:
        self.pending = np.concatenate([self.pending, samples.astype(np.float32)])
        now = self.clock()
        if now - self.level_t > 0.1 and samples.size:
            self.level_t = now
            self.emit(Event("level", self.source, level=float(min(1.0, np.sqrt(np.mean(samples ** 2)) * 8))))
        while self.pending.size >= Vad.HOP:
            frame, self.pending = self.pending[: Vad.HOP], self.pending[Vad.HOP:]
            p = self.vad.prob(frame)
            if self.speaking:
                self.utt.append(frame)
                if p < 0.35:
                    self.silence += 1
                else:
                    self.silence = 0
                dur = len(self.utt) * Vad.HOP / SR
                # ~700 ms pause ends the utterance; past 18 s a shorter breath is enough, and 30 s is the cap
                if self.silence * Vad.HOP / SR > 0.7 or (dur > 18 and p < 0.3) or dur > 30:
                    self._finish()
                elif settings.get("partials") and engine.partials_ok and dur > 1.0 and now - self.last_partial > 1.1:
                    self.last_partial = now
                    self._job("partial")
            else:
                self.pre.append(frame)
                self.pre = self.pre[-10:]
                if p > 0.55:
                    self.speaking = True
                    self.silence = 0
                    self.utt = list(self.pre)
                    self.pre = []
                    self.utt_t0 = max(0.0, now - len(self.utt) * Vad.HOP / SR)
                    self.last_partial = now

    def _job(self, kind: str) -> None:
        audio = np.concatenate(self.utt) if self.utt else np.zeros(0, np.float32)
        self.jobs.put((0 if kind == "final" else 1, time.time(), kind, self.source, self.seq, audio, self.utt_t0,
                       self.clock()))

    def _finish(self) -> None:
        # trim trailing silence before decoding
        keep = max(1, len(self.utt) - max(0, self.silence - 6))
        self.utt = self.utt[:keep]
        self._job("final")
        self.seq += 1
        self.speaking = False
        self.utt = []
        self.silence = 0
        self.vad.reset()

    def flush(self) -> None:
        if self.speaking and self.utt:
            self._finish()


class Transcriber:
    """One worker thread decoding utterances from every stream, finals before partials."""

    def __init__(self, emit: Callable[[Event], None], clock: Callable[[], float]):
        self.emit = emit
        self.clock = clock
        self.jobs: queue.PriorityQueue = queue.PriorityQueue()
        self.streams: dict[str, Stream] = {}
        self.running = True
        self.latest_partial: dict[str, int] = {}
        self.thread = threading.Thread(target=self._work, daemon=True, name="stt")
        self.thread.start()

    def stream(self, source: str) -> Stream:
        if source not in self.streams:
            self.streams[source] = Stream(source, self.emit, self.jobs, self.clock)
        return self.streams[source]

    def stop(self) -> None:
        for s in self.streams.values():
            s.flush()
        self.jobs.put((2, time.time(), "stop", "", 0, None, 0, 0))
        self.thread.join(timeout=60)
        self.running = False

    def _work(self) -> None:
        language = settings.get("stt_language") or "auto"
        while True:
            _, _, kind, source, seq, audio, t0, t1 = self.jobs.get()
            if kind == "stop":
                break
            # skip stale partials: a newer utterance or the final for this one is already queued
            if kind == "partial" and self.streams.get(source) and self.streams[source].seq != seq:
                continue
            try:
                text, lang = engine.recognize(audio, language)
            except Exception as e:  # noqa: BLE001
                log.exception("recognize failed")
                self.emit(Event("error", source, text=str(e)))
                continue
            if kind == "final" and not text:
                self.emit(Event("partial", source, text="", t0=t0, t1=t1))
                continue
            self.emit(Event(kind, source, text=text, t0=t0, t1=t1, language=lang))


def transcribe_file(audio: np.ndarray, emit: Callable[[Event], None], language: str = "auto") -> None:
    """Imported recordings: VAD-segment a whole file and transcribe each piece."""
    vad = Vad()
    speaking, start, silence = False, 0, 0
    pieces: list[tuple[int, int]] = []
    for i in range(0, audio.size - Vad.HOP, Vad.HOP):
        p = vad.prob(audio[i : i + Vad.HOP])
        if speaking:
            silence = silence + 1 if p < 0.35 else 0
            long = (i - start) / SR
            if silence * Vad.HOP / SR > 0.6 or (long > 40 and p < 0.3) or long > 120:
                pieces.append((start, i))
                speaking, silence = False, 0
        elif p > 0.55:
            speaking, start = True, max(0, i - 4 * Vad.HOP)
    if speaking:
        pieces.append((start, audio.size))
    for a, b in pieces:
        text, lang = engine.recognize(audio[a:b], language)
        if text:
            emit(Event("final", "file", text=text, t0=a / SR, t1=b / SR, language=lang))
