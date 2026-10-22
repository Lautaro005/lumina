"""Optional read-aloud with Kokoro 82M (ONNX). The UI falls back to the system voice without it."""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

from .catalog import CATALOG, installed

VOICES = {
    "en": ["af_heart", "af_bella", "am_michael", "bf_emma", "bm_george"],
    "es": ["ef_dora", "em_alex", "em_santa"],
    "fr": ["ff_siwis"],
    "it": ["if_sara", "im_nicola"],
    "pt": ["pf_dora", "pm_alex"],
    "ja": ["jf_alpha", "jm_kumo"],
    "zh": ["zf_xiaobei", "zm_yunjian"],
    "hi": ["hf_alpha", "hm_omega"],
}
LANG_CODE = {"en": "en-us", "es": "es", "fr": "fr-fr", "it": "it", "pt": "pt-br", "ja": "ja", "zh": "cmn", "hi": "hi"}

_lock = threading.Lock()
_model = None


def available() -> bool:
    if not installed("kokoro"):
        return False
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def speak(text: str, lang: str = "en", voice: str = "", speed: float = 1.0) -> bytes:
    global _model
    from kokoro_onnx import Kokoro

    lang = lang.split("-")[0].lower()
    if lang not in VOICES:
        lang = "en"
    voice = voice if voice and any(voice in v for v in VOICES.values()) else VOICES[lang][0]
    with _lock:
        if _model is None:
            p = CATALOG["kokoro"].path
            _model = Kokoro(str(p / "kokoro-v1.0.int8.onnx"), str(p / "voices-v1.0.bin"))
        samples, sr = _model.create(text[:4000], voice=voice, speed=speed, lang=LANG_CODE[lang])
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
