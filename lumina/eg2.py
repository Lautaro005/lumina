"""Speech to text with the audio tower of EmbeddingGemma 2 (experimental, English only).

EmbeddingGemma 2 is an embedding model, not a speech recogniser. What does work is the community project
``mlboydaisuke/EmbeddingGemma-2-Transcribe-LiteRT``: the model's own audio encoder (frozen, one vector every 40 ms)
plus a small trained "reader" that turns every vector into one of 200,000 English words or a blank (CTC).
Both are LiteRT files that run on the CPU with ``ai-edge-litert``.

Pipeline for one recording:  16 kHz audio -> log-mel (same maths as transformers' ``Gemma4AudioFeatureExtractor``)
-> encoder over 30 s windows (3.84 s of left context, 1 token of right context) -> reader -> words.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger("lumina.eg2")

SR = 16000
N_MELS = 128
FRAME = 320  # 20 ms
HOP = 160  # 10 ms
FFT = 512
MEL_FLOOR = 1e-3
WINDOW_FRAMES = 3000  # 30 s
WINDOW_TOKENS = 750  # one token every 4 mel frames (40 ms)
CONTEXT_TOKENS = 96  # left context re-read by every window after the first
STEP_TOKENS = WINDOW_TOKENS - CONTEXT_TOKENS - 1  # 653
ENCODER_FILE = "eg2_audio_f16safe_fp16w_w30.tflite"
READER_FILE = "reader_w30.tflite"
VOCAB_FILE = "vocab.txt"


class NotReady(RuntimeError):
    pass


# --- log-mel (numpy port of Gemma4AudioFeatureExtractor, defaults of EmbeddingGemma 2) --------------------

def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def _mel_filters() -> np.ndarray:
    """HTK triangular filters, shape (FFT // 2 + 1, N_MELS), no normalisation."""
    n_freq = FFT // 2 + 1
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(SR / 2), N_MELS + 2)
    filt = _mel_to_hz(mel_pts)
    fft_freqs = np.linspace(0, SR // 2, n_freq)
    diff = np.diff(filt)
    slopes = filt[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    return np.maximum(0.0, np.minimum(down, up))


_FILTERS: np.ndarray | None = None
_WINDOW = np.hanning(FRAME + 1)[:-1].astype(np.float32)  # periodic Hann


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(frames, 128) float32 log-mel of 16 kHz mono audio, all frames valid."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = _mel_filters()
    x = np.pad(audio.astype(np.float32), (FRAME // 2, 0))  # semicausal: first frame centred on t = 0
    n = (x.size - (FRAME + 1)) // HOP + 1
    if n <= 0:
        return np.zeros((0, N_MELS), np.float32)
    frames = np.lib.stride_tricks.as_strided(x, (n, FRAME), (x.strides[0] * HOP, x.strides[0]))
    spec = np.abs(np.fft.rfft(frames * _WINDOW, n=FFT, axis=-1))
    mel = spec @ _FILTERS
    return np.log(mel + np.float64(MEL_FLOOR)).astype(np.float32)


# --- windows and decoding -------------------------------------------------------------------------------

def plan_windows(n_frames: int) -> list[tuple[int, int, int | None]]:
    """(first mel frame, first token kept, one past the last token kept or None = through the last valid one).

    Window k starts at token 653 k. Window 0 keeps its tokens 0..748, later ones 96..748, and the window that
    reaches the end of the recording keeps everything valid.
    """
    out: list[tuple[int, int, int | None]] = []
    k = 0
    while True:
        start = k * STEP_TOKENS * 4
        last = start + WINDOW_FRAMES >= n_frames
        keep_from = 0 if k == 0 else CONTEXT_TOKENS
        out.append((start, keep_from, None if last else WINDOW_TOKENS - 1))
        if last:
            return out
        k += 1


def collapse(ids: list[int], blank_from: list[bool], last: int = -1) -> tuple[list[int], int]:
    """CTC rule: drop blanks, merge runs of the same word. Returns (words, last word id seen)."""
    words: list[int] = []
    for i, is_blank in zip(ids, blank_from, strict=True):
        if is_blank:
            last = -1
            continue
        if i != last:
            words.append(i)
        last = i
    return words, last


def _pick(out: dict, name: str, rank: int | None = None):
    if name in out:
        return out[name]
    for k, v in out.items():
        if name in k.lower() and (rank is None or np.ndim(v) == rank):
            return v
    raise KeyError(f"output '{name}' not found in {list(out)}")


class Recognizer:
    """Loads the LiteRT encoder and reader once and turns audio into text."""

    def __init__(self, folder: Path):
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError as e:
            raise NotReady(
                "The EmbeddingGemma 2 speech option needs the 'ai-edge-litert' package, which has no build for this "
                "system (Intel Macs). Pick Parakeet, Canary or Whisper instead."
            ) from e
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        self.encoder = self._runner(Interpreter, folder / ENCODER_FILE, "audio_30s", threads)
        self.reader = self._runner(Interpreter, folder / READER_FILE, "reader_30s", threads)
        self.vocab = [w.strip().lower() for w in (folder / VOCAB_FILE).read_text("utf-8").splitlines()]

    @staticmethod
    def _runner(Interpreter, path: Path, signature: str, threads: int):  # noqa: N803
        it = Interpreter(model_path=str(path), num_threads=threads)
        sigs = it.get_signature_list()
        name = signature if signature in sigs else next(iter(sigs))
        runner = it.get_signature_runner(name)
        runner._keepalive = it  # the runner does not own the interpreter
        return runner

    @staticmethod
    def _cast(runner, name: str, value: np.ndarray) -> np.ndarray:
        dtype = runner.get_input_details().get(name, {}).get("dtype", np.float32)
        return value.astype(dtype)

    def _window(self, mel: np.ndarray, start: int) -> tuple[np.ndarray, np.ndarray]:
        """Encoder + reader for one window: (ids, is_blank) per token, valid tokens only."""
        piece = mel[start:start + WINDOW_FRAMES]
        valid = piece.shape[0]
        buf = np.zeros((1, WINDOW_FRAMES, N_MELS), np.float32)
        mask = np.zeros((1, WINDOW_FRAMES), np.float32)
        buf[0, :valid] = piece
        mask[0, :valid] = 1.0
        enc = self.encoder(mel=self._cast(self.encoder, "mel", buf), mask=self._cast(self.encoder, "mask", mask))
        soft = np.asarray(_pick(enc, "soft", 3))
        tmask = np.asarray(_pick(enc, "mask", 2))
        out = self.reader(soft=self._cast(self.reader, "soft", soft), mask=self._cast(self.reader, "mask", tmask))
        ids = np.asarray(_pick(out, "ids")).reshape(-1)
        best = np.asarray(_pick(out, "best")).reshape(-1)
        blank = np.asarray(_pick(out, "blank")).reshape(-1)
        n_valid = int(np.asarray(tmask).reshape(-1).sum())
        return ids[:n_valid], blank[:n_valid] >= best[:n_valid]

    def transcribe(self, audio: np.ndarray) -> str:
        mel = log_mel(audio)
        if mel.shape[0] < 20:
            return ""
        words: list[int] = []
        last = -1
        for start, keep_from, keep_to in plan_windows(mel.shape[0]):
            ids, is_blank = self._window(mel, start)
            ids, is_blank = ids[keep_from:keep_to], is_blank[keep_from:keep_to]
            got, last = collapse([int(i) for i in ids], [bool(b) for b in is_blank], last)
            words += got
        text = " ".join(self.vocab[i] for i in words if 0 <= i < len(self.vocab))
        return text[:1].upper() + text[1:]
