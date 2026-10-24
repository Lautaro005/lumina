"""EmbeddingGemma 2 (text encoder, 8-bit ONNX) running in-process on CPU."""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from .catalog import CATALOG, installed
from .config import settings

MAX_TOKENS = 2048
BATCH_TOKENS = 6144  # padded tokens in one model call
MAX_BATCH = 16


class NotReady(RuntimeError):
    pass


class Embedder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._tok = None

    @property
    def loaded(self) -> bool:
        return self._session is not None

    @property
    def ready(self) -> bool:
        return installed("embeddinggemma-2")

    def _load(self) -> None:
        if self._session is not None:
            return
        if not self.ready:
            raise NotReady("EmbeddingGemma 2 is not downloaded yet")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        path = CATALOG["embeddinggemma-2"].path
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(path / "onnx" / "model_quantized.onnx"), opts,
                                             providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        self._tok = tok

    def unload(self) -> None:
        with self._lock:
            self._session = None
            self._tok = None

    def _infer(self, encs: list) -> np.ndarray:
        """One padded batch through the model. The caller holds the lock."""
        assert self._session is not None
        width = max(len(e.ids) for e in encs)
        ids = np.zeros((len(encs), width), np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(encs):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        empty = np.zeros((0, 512), np.float32)
        out = self._session.run(["sentence_embedding"], {
            "input_ids": ids, "attention_mask": mask,
            "image_features": empty, "video_features": empty, "audio_features": empty,
        })[0]
        dim = int(settings.get("embedding_dim") or 768)
        out = out[:, :dim]  # Matryoshka truncation, then re-normalise
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

    def warm(self) -> None:
        """Loads the model now (the indexer calls this while it is still reading the first file)."""
        with self._lock:
            self._load()

    def embed(self, texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
        """Embeds every text. Texts are sorted by length and packed into batches of at most BATCH_TOKENS padded
        tokens, so short ones do not pay for long ones. The lock is taken per batch: a search from the chat gets
        in between the batches of a large file instead of waiting for all of it."""
        if not texts:
            return []
        with self._lock:
            self._load()
            tok = self._tok
        assert tok is not None
        encs = tok.encode_batch(texts)
        order = sorted(range(len(texts)), key=lambda i: len(encs[i].ids))
        result: list[list[float] | None] = [None] * len(texts)
        done, pos = 0, 0
        while pos < len(order):
            nxt = pos
            while nxt < len(order) and nxt - pos < MAX_BATCH:
                width = len(encs[order[nxt]].ids)  # sorted ascending: the newest one is the widest
                if nxt > pos and width * (nxt - pos + 1) > BATCH_TOKENS:
                    break
                nxt += 1
            idx = order[pos:nxt]
            with self._lock:
                self._load()
                vecs = self._infer([encs[i] for i in idx])
            for i, v in zip(idx, vecs, strict=True):
                result[i] = v.astype(float).tolist()
            pos = nxt
            done += len(idx)
            if progress:
                progress(done, len(texts))
        return result  # type: ignore[return-value]

    def query(self, text: str, task: str = "search result") -> list[float]:
        return self.embed([f"task: {task} | query: {text}"])[0]

    def documents(self, items: list[tuple[str, str]],
                  progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
        """items: (title, text)"""
        return self.embed([f"title: {t or 'none'} | text: {x}" for t, x in items], progress)


embedder = Embedder()
