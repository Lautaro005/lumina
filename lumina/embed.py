"""EmbeddingGemma 2 (text encoder, 8-bit ONNX) running in-process on CPU."""

from __future__ import annotations

import threading

import numpy as np

from .catalog import CATALOG, installed
from .config import settings

MAX_TOKENS = 2048


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

    def _run(self, texts: list[str]) -> np.ndarray:
        assert self._tok is not None and self._session is not None
        enc = self._tok.encode_batch(texts)
        width = max(len(e.ids) for e in enc)
        ids = np.zeros((len(texts), width), np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(enc):
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

    def embed(self, texts: list[str], batch: int = 8) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            self._load()
            # sort by length so batches pad less
            order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
            result: list[list[float] | None] = [None] * len(texts)
            for s in range(0, len(order), batch):
                idx = order[s : s + batch]
                vecs = self._run([texts[i] for i in idx])
                for i, v in zip(idx, vecs, strict=True):
                    result[i] = v.astype(float).tolist()
            return result  # type: ignore[return-value]

    def query(self, text: str, task: str = "search result") -> list[float]:
        return self.embed([f"task: {task} | query: {text}"])[0]

    def documents(self, items: list[tuple[str, str]]) -> list[list[float]]:
        """items: (title, text)"""
        return self.embed([f"title: {t or 'none'} | text: {x}" for t, x in items])


embedder = Embedder()
