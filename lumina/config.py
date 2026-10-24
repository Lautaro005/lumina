"""Paths and user settings.

Everything Lumina stores lives in one data folder (default ``~/.lumina/data``, or ``LUMINA_DATA``):
settings.json, the SQLite database, the Chroma index, downloaded models and logs.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import threading
from pathlib import Path
from typing import Any

from . import __version__

VERSION = __version__


def data_dir() -> Path:
    env = os.environ.get("LUMINA_DATA")
    base = Path(env).expanduser() if env else Path.home() / ".lumina" / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA = data_dir()
MODELS = DATA / "models"
BIN = DATA / "bin"
LOGS = DATA / "logs"
CHROMA = DATA / "chroma"
UPLOADS = DATA / "uploads"
for _d in (MODELS, BIN, LOGS, CHROMA, UPLOADS):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA / "lumina.db"
SETTINGS_PATH = DATA / "settings.json"
UI_DIR = Path(__file__).parent / "ui"

DEFAULT_PORT = 47770
BUILTIN_LLM_PORT = 47781

DEFAULTS: dict[str, Any] = {
    "first_run_done": False,
    "ui_language": "auto",  # auto | en | es
    "theme": "system",  # system | light | dark
    # generation
    "llm_provider": "builtin",  # builtin | ollama | lmstudio | openai
    "builtin_model": "gemma-4-e2b",  # gemma-4-e2b | gemma-4-e4b | gemma-4-12b
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "",
    "lmstudio_url": "http://127.0.0.1:1234",
    "lmstudio_model": "",
    "openai_url": "",
    "openai_model": "",
    "openai_key": "",
    "temperature": 0.3,
    # transcription
    "stt_engine": "auto",  # auto | parakeet | canary | whisper | embeddinggemma
    "whisper_size": "small",  # tiny | base | small | medium | large-v3-turbo
    "stt_language": "auto",  # auto or an ISO 639-1 code
    "mic_device": None,
    "system_audio": True,
    "partials": True,
    # meeting helpers
    "auto_enhance": True,
    "enhance_interval": 45,
    "live_assistance": True,
    "notes_view": "columns",  # columns (Foresight style) | rows (Cornell) | tabs
    "update_check": True,  # look for a newer version in the background and offer it in the app
    "memory_saver": True,  # unload models nobody is using, so Lumina stays small
    "notch_live": platform.system() == "Darwin",  # desktop app: the live transcript in the notch while recording
    # knowledge
    "embedding_dim": 768,
    "watched_folders": [],
    # chat
    "web_search": False,
    "search_provider": "duckduckgo",  # duckduckgo | searxng
    "searxng_url": "http://127.0.0.1:8888",
    # speech
    "tts_engine": "system",  # system | kokoro
    "tts_voice": "",
    # mcp
    "mcp_token": "",
}


class Settings:
    """Thread-safe JSON settings with defaults."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        merged = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
        if not merged.get("mcp_token"):
            merged["mcp_token"] = secrets.token_urlsafe(24)
        self._data = merged
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def public(self) -> dict[str, Any]:
        """Settings safe to send to the UI (API keys masked)."""
        d = self.all()
        if d.get("openai_key"):
            d["openai_key"] = "••••" + d["openai_key"][-4:]
        return d

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for k, v in changes.items():
                if k not in DEFAULTS or k == "mcp_token":
                    continue
                if k == "openai_key" and isinstance(v, str) and v.startswith("••••"):
                    continue
                self._data[k] = v
            self._save()
            return dict(self._data)


settings = Settings()


def system_info() -> dict[str, Any]:
    return {
        "app": os.environ.get("LUMINA_APP") == "1",
        "os": platform.system(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "version": VERSION,
        "data": str(DATA),
    }
