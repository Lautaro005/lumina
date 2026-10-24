"""Text generation through one OpenAI-compatible client.

Providers: the built-in Gemma 4 (llama.cpp's llama-server, started on demand), Ollama, LM Studio,
or any OpenAI-compatible server the user points at.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from .catalog import CATALOG, installed
from .config import BUILTIN_LLM_PORT, LOGS, settings

log = logging.getLogger("lumina.llm")


class LLMError(RuntimeError):
    pass


def _free_port(start: int) -> int:
    import socket

    for port in range(start, start + 50):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _find_exe(root: Path, name: str) -> Path | None:
    exe = name + (".exe" if platform.system() == "Windows" else "")
    for p in root.rglob(exe):
        if p.is_file():
            return p
    return None


class BuiltinServer:
    """Starts llama-server with the selected Gemma 4 GGUF and keeps it running."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.model_id = ""
        self._lock = threading.Lock()
        self.port = BUILTIN_LLM_PORT
        self.used = time.time()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None

    def ensure(self, model_id: str) -> str:
        with self._lock:
            if self.running() and self.model_id == model_id:
                return self.url
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            if not installed("llama-server"):
                raise LLMError("The built-in engine (llama.cpp) is not downloaded yet. Open Settings → Models.")
            if not installed(model_id):
                raise LLMError(f"{CATALOG[model_id].label} is not downloaded yet. Open Settings → Models.")
            exe = _find_exe(CATALOG["llama-server"].path, "llama-server")
            if not exe:
                raise LLMError("llama-server was not found in the downloaded package")
            if platform.system() != "Windows":
                exe.chmod(0o755)
                if platform.system() == "Darwin":
                    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(exe.parent)], capture_output=True)
            gguf = next(CATALOG[model_id].path.glob("*.gguf"))
            self.port = _free_port(BUILTIN_LLM_PORT)
            args = [str(exe), "-m", str(gguf), "--host", "127.0.0.1", "--port", str(self.port),
                    "-c", "16384", "--jinja", "-ngl", "99", "--no-webui", "--reasoning-budget", "0"]
            threads = max(2, (os.cpu_count() or 4) - 2)
            args += ["-t", str(threads)]
            env = dict(os.environ)
            if platform.system() == "Linux":
                env["LD_LIBRARY_PATH"] = f"{exe.parent}:{env.get('LD_LIBRARY_PATH', '')}"
            logf = open(LOGS / "llama-server.log", "ab")
            flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0  # type: ignore[attr-defined]
            self.proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, cwd=str(exe.parent), env=env,
                                         creationflags=flags)
            self.model_id = model_id
            deadline = time.time() + 180
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise LLMError("llama-server stopped while loading the model; see logs/llama-server.log")
                try:
                    r = httpx.get(self.url + "/health", timeout=2)
                    if r.status_code == 200:
                        return self.url
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            raise LLMError("The built-in model took too long to load")


builtin = BuiltinServer()


def _available(p: str) -> bool:
    if p == "builtin":
        return installed("llama-server") and installed(settings.get("builtin_model"))
    if p in ("ollama", "lmstudio"):
        return bool(list_models(p))
    return p == "openai" and bool(settings.get("openai_url"))


def effective_provider() -> str:
    """The chosen provider, or the first one that works when it is not available
    (e.g. LM Studio was chosen but is closed and Gemma 4 is downloaded)."""
    chosen = settings.get("llm_provider")
    if _available(chosen):
        return chosen
    for p in ("builtin", "ollama", "lmstudio"):
        if p != chosen and _available(p):
            log.info("%s is not available; using %s", chosen, p)
            return p
    return chosen


def provider_config(provider: str | None = None) -> tuple[str, str, dict[str, str]]:
    """(base_url_with_v1, model, headers) for the active provider."""
    p = provider or effective_provider()
    if p == "builtin":
        mid = settings.get("builtin_model")
        url = builtin.ensure(mid)
        return url + "/v1", mid, {}
    if p == "ollama":
        url = settings.get("ollama_url").rstrip("/")
        model = settings.get("ollama_model") or _first_model("ollama")
        return url + "/v1", model, {}
    if p == "lmstudio":
        url = settings.get("lmstudio_url").rstrip("/")
        model = settings.get("lmstudio_model") or _first_model("lmstudio")
        return url + "/v1", model, {}
    if p == "openai":
        url = settings.get("openai_url").rstrip("/")
        if not url:
            raise LLMError("Set the server address in Settings → Models")
        if not url.endswith("/v1"):
            url += "/v1"
        headers = {"Authorization": f"Bearer {settings.get('openai_key')}"} if settings.get("openai_key") else {}
        return url, settings.get("openai_model"), headers
    raise LLMError(f"unknown provider {p}")


def list_models(provider: str) -> list[str]:
    try:
        if provider == "ollama":
            r = httpx.get(settings.get("ollama_url").rstrip("/") + "/api/tags", timeout=3)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", []) if not m["name"].endswith(":cloud")
                    and "embed" not in m["name"]]
        if provider == "lmstudio":
            r = httpx.get(settings.get("lmstudio_url").rstrip("/") + "/v1/models", timeout=3)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", []) if "embed" not in m["id"]]
        if provider == "openai":
            url = settings.get("openai_url").rstrip("/")
            if not url:
                return []
            if not url.endswith("/v1"):
                url += "/v1"
            headers = {"Authorization": f"Bearer {settings.get('openai_key')}"} if settings.get("openai_key") else {}
            r = httpx.get(url + "/models", headers=headers, timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        if provider == "builtin":
            return [i.id for i in CATALOG.values() if i.kind == "llm" and installed(i.id)]
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return []


def _first_model(provider: str) -> str:
    models = list_models(provider)
    if not models:
        name = "Ollama" if provider == "ollama" else "LM Studio"
        raise LLMError(f"{name} is not running or has no models. Start it, or pick another engine in Settings.")
    return models[0]


def detect() -> dict[str, Any]:
    """Which local servers are reachable right now."""
    out: dict[str, Any] = {}
    for name, url, path in (("ollama", settings.get("ollama_url"), "/api/version"),
                            ("lmstudio", settings.get("lmstudio_url"), "/v1/models")):
        try:
            r = httpx.get(url.rstrip("/") + path, timeout=0.6)
            out[name] = r.status_code == 200
        except httpx.HTTPError:
            out[name] = False
    out["builtin"] = installed("llama-server") and any(installed(i) for i in ("gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b"))
    out["builtin_running"] = builtin.running()
    out["effective"] = effective_provider()
    return out


def ready() -> tuple[bool, str]:
    p = effective_provider()
    if p == "builtin":
        if not installed("llama-server") or not installed(settings.get("builtin_model")):
            return False, "Download Gemma 4 in Settings → Models, or connect Ollama or LM Studio."
        return True, ""
    if p in ("ollama", "lmstudio") and not list_models(p):
        return False, f"{'Ollama' if p == 'ollama' else 'LM Studio'} is not reachable or has no models."
    if p == "openai" and not settings.get("openai_url"):
        return False, "Set the server address in Settings → Models."
    return True, ""


def stream(messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int = 1500,
           json_mode: bool = False, provider: str | None = None) -> Iterator[str]:
    base, model, headers = provider_config(provider)
    builtin.used = time.time()
    body: dict[str, Any] = {
        "model": model, "messages": messages, "stream": True, "max_tokens": max_tokens,
        "temperature": settings.get("temperature") if temperature is None else temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        with httpx.stream("POST", base + "/chat/completions", json=body, headers=headers,
                          timeout=httpx.Timeout(20, read=300)) as r:
            if r.status_code >= 400:
                r.read()
                if json_mode and r.status_code in (400, 422):
                    # some servers do not support response_format; retry without it
                    yield from stream(messages, temperature, max_tokens, False, provider)
                    return
                raise LLMError(f"model server answered {r.status_code}: {r.text[:300]}")
            in_think = False
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                piece = delta.get("content") or ""
                if not piece:
                    continue
                # hide <think> blocks from reasoning models served without a reasoning parser
                if "<think>" in piece:
                    in_think = True
                    piece = piece.split("<think>")[0]
                if in_think:
                    if "</think>" in piece:
                        in_think = False
                        piece = piece.split("</think>", 1)[1]
                    else:
                        continue
                if piece:
                    builtin.used = time.time()
                    yield piece
    except httpx.ConnectError as e:
        raise LLMError("Could not reach the model server. Is it running?") from e
    except httpx.ReadTimeout as e:
        raise LLMError("The model took too long to answer.") from e


def complete(messages: list[dict[str, str]], **kw: Any) -> str:
    return "".join(stream(messages, **kw)).strip()


def complete_json(messages: list[dict[str, str]], **kw: Any) -> Any:
    """Ask for JSON. Small local models sometimes wrap it in prose, stop halfway or ignore JSON mode,
    so a bad answer is repaired when possible and otherwise asked for again, with more room."""
    last = ""
    for attempt in range(3):
        opts = dict(kw)
        if attempt:
            opts["json_mode"] = attempt == 1  # the last try goes without the JSON grammar
            opts["temperature"] = 0.0
            opts["max_tokens"] = int(kw.get("max_tokens", 1500) * (1 + attempt))
        opts.setdefault("json_mode", True)
        last = complete(messages, **opts)
        try:
            return parse_json(last)
        except LLMError:
            log.warning("attempt %d: model answer was not JSON: %.300r", attempt + 1, last)
    raise LLMError("The model did not return valid JSON") from None


def _repair(t: str) -> str:
    """Close a JSON text that was cut off: an open string, then every open bracket."""
    stack: list[str] = []
    in_str = esc = False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    out = t + ('"' if in_str else "")
    out = re.sub(r",\s*$", "", out.rstrip())
    out = re.sub(r'"[^"\\]*"\s*:\s*$', "", out).rstrip().rstrip(",")  # a key without its value
    return out + "".join(reversed(stack))


def parse_json(text: str) -> Any:
    t = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)(```|$)", t, flags=re.S | re.I)
    if fence:
        t = fence.group(1).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        raise LLMError("The model did not return valid JSON")
    body = t[min(starts):]
    candidates = [body]
    last = max(body.rfind("}"), body.rfind("]"))
    if last > 0:
        candidates.append(body[: last + 1])
    candidates.append(_repair(body))
    for c in candidates:
        for fixed in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(fixed, strict=False)
            except json.JSONDecodeError:
                continue
    raise LLMError("The model did not return valid JSON")


def shutdown() -> None:
    builtin.stop()


__all__ = ["stream", "complete", "complete_json", "detect", "list_models", "ready", "LLMError", "builtin"]
