"""HTTP API + WebSocket + the web UI, served on 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import __version__, chat, export, llm, tts
from .audio import list_inputs
from .catalog import CATALOG, EU25, installed, languages_for, stt_choice
from .config import DATA, UI_DIR, UPLOADS, settings, system_info
from .db import db, new_id
from .downloads import downloads
from .embed import NotReady, embedder
from .hub import hub
from .kb import kb
from .mcp_server import server as mcp
from .session import import_recording, run_enhance, sessions
from .stt import engine

log = logging.getLogger("lumina.server")


class MeetingIn(BaseModel):
    title: str = ""
    tags: list[str] = []


class SourceIn(BaseModel):
    path: str
    watch: bool = True
    tags: list[str] = []


class NoteIn(BaseModel):
    title: str
    text: str
    tags: list[str] = []



def running_models() -> dict[str, Any]:
    """What is loaded in memory right now (the UI offers a Stop button for each)."""
    return {
        "llm": llm.builtin.running(), "llm_model": llm.builtin.model_id if llm.builtin.running() else "",
        "stt": bool(engine.model_id), "stt_model": engine.model_id,
        "embeddings": embedder.loaded,
    }


def create_app(port: int) -> FastAPI:
    mcp_app = mcp.streamable_http_app(streamable_http_path="/", host="127.0.0.1")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.attach(asyncio.get_running_loop())
        downloads.listeners.append(lambda j: hub.publish({"type": "download", **j}))
        kb.listeners.append(lambda e: hub.publish({"type": "kb", "event": e.get("type", ""),
                                                   **{k: v for k, v in e.items() if k != "type"}}))
        threading.Thread(target=kb.start_watching, daemon=True).start()
        threading.Thread(target=kb.counts_warm, daemon=True).start()
        async with mcp.session_manager.run():
            yield
        if sessions.current:
            sessions.current.stop()
        llm.shutdown()

    app = FastAPI(title="Lumina", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", "tauri://localhost",
                       "http://tauri.localhost", "https://tauri.localhost"}
    if os.environ.get("LUMINA_DEV") == "1":  # the Vite dev server (web/: npm run dev)
        allowed_origins |= {"http://localhost:5178", "http://127.0.0.1:5178"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Only this machine, and only pages served by Lumina itself (blocks DNS rebinding and
        # cross-site requests from websites open in the browser).
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return PlainTextResponse("forbidden host", status_code=403)
        origin = request.headers.get("origin")
        path = request.url.path
        if path.startswith("/mcp"):
            token = settings.get("mcp_token")
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}" and request.query_params.get("token") != token:
                return PlainTextResponse("missing or wrong token", status_code=401)
        elif origin and origin not in allowed_origins:
            return PlainTextResponse("forbidden origin", status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if not path.startswith("/api") and not path.startswith("/mcp"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: data:; "
                "style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self' ws://127.0.0.1:* "
                "ws://localhost:*; frame-ancestors 'self' tauri://localhost http://tauri.localhost")
        return resp

    app.mount("/mcp", mcp_app)
    app.add_middleware(_McpSlash)

    # --- status & settings -------------------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        s = settings.all()
        return {
            "version": __version__,
            "system": system_info(),
            "settings": settings.public(),
            "models": {i.id: installed(i.id) for i in CATALOG.values()},
            "downloads": downloads.status(),
            "llm": llm.detect(),
            "llm_ready": llm.ready()[0],
            "stt_model": stt_choice(s["stt_engine"], s["stt_language"], s["whisper_size"]),
            "stt_loaded": engine.model_id,
            "embeddings_ready": embedder.ready,
            "tts_ready": tts.available(),
            "session": sessions.state(),
            "running": running_models(),
            "kb": kb.counts(),
            "index": kb.progress(),
            "port": port,
        }

    @app.post("/api/running/{name}/stop")
    def stop_running(name: str) -> dict[str, Any]:
        """Frees the memory of a model Lumina loaded. It loads again by itself the next time it is needed."""
        if name == "llm":
            llm.builtin.stop()
        elif name == "stt":
            if sessions.state():
                raise HTTPException(409, "A meeting is being recorded. End it first.")
            engine.unload()
        elif name == "embeddings":
            embedder.unload()
        else:
            raise HTTPException(404, "unknown model")
        return {"running": running_models()}

    @app.post("/api/running/llm/start")
    def start_llm() -> dict[str, Any]:
        """Loads the built-in notes model now, so the first answer is not slow."""
        if llm.effective_provider() != "builtin":
            raise HTTPException(400, "The built-in model is not the one selected in Settings.")
        try:
            llm.builtin.ensure(settings.get("builtin_model"))
        except llm.LLMError as e:
            raise HTTPException(400, str(e)) from e
        return {"running": running_models()}

    @app.post("/api/quit")
    async def quit_app(request: Request) -> dict:
        """The desktop app asks the backend it started to close."""
        if request.headers.get("x-lumina") != "1":
            raise HTTPException(403, "missing header")
        import signal

        def bye() -> None:
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT)

        threading.Thread(target=bye, daemon=True).start()
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return settings.public()

    @app.put("/api/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        changes = await request.json()
        before = settings.all()
        settings.update(changes)
        if ("builtin_model" in changes and changes["builtin_model"] != before["builtin_model"]) or (
                changes.get("llm_provider") and changes["llm_provider"] != "builtin"):
            threading.Thread(target=llm.builtin.stop, daemon=True).start()
        if "embedding_dim" in changes and changes["embedding_dim"] != before["embedding_dim"]:
            threading.Thread(target=kb.reindex_all, kwargs={"force": True}, daemon=True).start()
        return settings.public()

    @app.get("/api/languages")
    def languages(engine_name: str = "") -> dict[str, Any]:
        e = engine_name or settings.get("stt_engine")
        langs = languages_for(e)
        return {"engine": e, "languages": sorted(langs), "eu25": EU25, "auto": e not in ("canary", "embeddinggemma"),
                "forced": e != "parakeet"}

    # --- models ----------------------------------------------------------------------------------
    @app.get("/api/models")
    def models() -> list[dict[str, Any]]:
        jobs = downloads.status()
        return [{**i.public(), "installed": installed(i.id), "job": jobs.get(i.id)} for i in CATALOG.values()]

    @app.post("/api/models/{item_id}/download")
    def download(item_id: str) -> dict[str, Any]:
        if item_id not in CATALOG:
            raise HTTPException(404, "unknown model")
        return downloads.start(item_id)

    @app.post("/api/models/{item_id}/cancel")
    def cancel(item_id: str) -> dict[str, bool]:
        downloads.cancel(item_id)
        return {"ok": True}

    @app.delete("/api/models/{item_id}")
    def remove_model(item_id: str) -> dict[str, bool]:
        if item_id not in CATALOG:
            raise HTTPException(404, "unknown model")
        if engine.model_id == item_id:
            engine.unload()
        if item_id == "embeddinggemma-2":
            embedder.unload()
        if item_id in ("llama-server", llm.builtin.model_id):
            llm.builtin.stop()
        downloads.remove(item_id)
        return {"ok": True}

    @app.get("/api/llm/models")
    def llm_models(provider: str) -> dict[str, Any]:
        return {"provider": provider, "models": llm.list_models(provider)}

    @app.post("/api/llm/test")
    def llm_test() -> dict[str, Any]:
        t = time.time()
        try:
            text = llm.complete([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=20)
            return {"ok": True, "text": text, "seconds": round(time.time() - t, 2)}
        except llm.LLMError as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/audio/devices")
    def devices() -> dict[str, Any]:
        return {"inputs": list_inputs(), "system_audio": platform.system() in ("Darwin", "Windows", "Linux")}

    # --- meetings --------------------------------------------------------------------------------
    @app.get("/api/meetings")
    def list_meetings(q: str = "", tag: str = "", since: float = 0, until: float = 0) -> list[dict]:
        return db.meetings(q, tag, since, until)

    @app.post("/api/meetings")
    def create_meeting(body: MeetingIn) -> dict:
        return db.create_meeting(body.title, settings.get("stt_language"), body.tags)

    @app.get("/api/meetings/{mid}")
    def get_meeting(mid: str) -> dict:
        m = db.meeting(mid)
        if not m:
            raise HTTPException(404, "meeting not found")
        return {**m, "segments": db.segments(mid), "assists": db.assists(mid)}

    @app.patch("/api/meetings/{mid}")
    async def patch_meeting(mid: str, request: Request) -> dict:
        body = await request.json()
        fields = {k: v for k, v in body.items() if k in ("title", "tags", "cues", "enhanced", "summary")}
        db.update("meetings", mid, fields)
        m = db.meeting(mid)
        if m and m["status"] == "done" and any(k in fields for k in ("title", "cues", "enhanced", "summary")):
            kb.index_meeting(mid)
        return m or {}

    @app.delete("/api/meetings/{mid}")
    def delete_meeting(mid: str) -> dict:
        sessions.stop(mid)
        kb.remove_meeting(mid)
        db.x("DELETE FROM meetings WHERE id=?", [mid])
        return {"ok": True}

    @app.post("/api/meetings/{mid}/start")
    def start(mid: str) -> dict:
        try:
            return sessions.start(mid)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(409, str(e)) from e

    @app.post("/api/meetings/{mid}/pause")
    async def pause(mid: str, request: Request) -> dict:
        body = await request.json()
        if sessions.current and sessions.current.meeting_id == mid:
            sessions.current.pause(bool(body.get("paused")))
        return {"ok": True}

    @app.post("/api/meetings/{mid}/stop")
    def stop(mid: str) -> dict:
        sessions.stop(mid)
        return {"ok": True}

    @app.post("/api/meetings/{mid}/enhance")
    def enhance_now(mid: str) -> dict:
        s = sessions.current
        if s and s.meeting_id == mid:
            threading.Thread(target=s.enhance_now, daemon=True).start()
        else:
            threading.Thread(target=run_enhance, args=(mid, True), daemon=True).start()
        return {"ok": True}

    @app.post("/api/meetings/{mid}/summarize")
    def summarize(mid: str) -> dict:
        from .session import finish_meeting

        threading.Thread(target=finish_meeting, args=(mid,), daemon=True).start()
        return {"ok": True}

    @app.post("/api/meetings/{mid}/assists/{aid}/seen")
    def assist_seen(mid: str, aid: str) -> dict:
        db.x("UPDATE assists SET seen=1 WHERE id=? AND meeting_id=?", [aid, mid])
        return {"ok": True}

    @app.post("/api/meetings/import")
    async def import_audio(file: UploadFile = File(...)) -> dict:
        name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "recording").name)[:120]
        dest = UPLOADS / f"{new_id()}_{name}"
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        m = db.create_meeting("", settings.get("stt_language"), ["imported"])
        db.update("meetings", m["id"], {"cues": ""})
        threading.Thread(target=import_recording, args=(m["id"], dest), daemon=True).start()
        return m

    @app.get("/api/meetings/{mid}/export")
    def export_meeting(mid: str, format: str = "md") -> Response:  # noqa: A002
        m = db.meeting(mid)
        if not m:
            raise HTTPException(404, "meeting not found")
        base = re.sub(r"[^\w\- ]", "", m["title"] or "meeting").strip().replace(" ", "-")[:60] or "meeting"
        kinds = {"md": (export.markdown, "text/markdown"), "txt": (export.transcript, "text/plain"),
                 "json": (export.as_json, "application/json"), "srt": (export.srt, "application/x-subrip")}
        if format not in kinds:
            raise HTTPException(400, "unknown format")
        fn, mime = kinds[format]
        return Response(fn(mid), media_type=f"{mime}; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{base}.{format}"'})

    @app.get("/api/tags")
    def tags() -> list[str]:
        out: set[str] = set()
        for m in db.q("SELECT tags FROM meetings"):
            out.update(m["tags"] or [])
        for s in db.q("SELECT tags FROM sources"):
            out.update(s["tags"] or [])
        return sorted(out)

    # --- sources ---------------------------------------------------------------------------------
    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        rows = db.q("SELECT id,kind,path,parent,title,ext,tags,added,mtime,size,chunks,status,error,watch FROM sources "
                    "ORDER BY added DESC")
        return rows

    @app.post("/api/sources")
    def add_source(body: SourceIn) -> dict:
        try:
            return kb.add_path(body.path, body.watch, body.tags)
        except FileNotFoundError as e:
            raise HTTPException(404, f"Not found: {e}") from e

    @app.post("/api/sources/upload")
    async def upload(files: list[UploadFile] = File(...)) -> list[dict]:
        out = []
        for f in files:
            name = re.sub(r"[^\w.\- ]", "_", Path(f.filename or "file").name)[:150]
            dest = UPLOADS / f"{new_id()}_{name}"
            with open(dest, "wb") as fh:
                shutil.copyfileobj(f.file, fh)
            row = kb.add_path(str(dest), False, [])
            db.update("sources", row["id"], {"title": name})
            out.append(db.one("SELECT * FROM sources WHERE id=?", [row["id"]]))
        return out

    @app.post("/api/notes")
    def add_note(body: NoteIn) -> dict:
        return kb.add_note(body.title, body.text, body.tags)

    @app.get("/api/sources/{sid}")
    def get_source(sid: str) -> dict:
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if not row:
            raise HTTPException(404, "not found")
        return row

    @app.patch("/api/sources/{sid}")
    async def patch_source(sid: str, request: Request) -> dict:
        body = await request.json()
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if not row:
            raise HTTPException(404, "not found")
        fields = {k: v for k, v in body.items() if k in ("tags", "title", "text")}
        db.update("sources", sid, fields)
        if row["kind"] == "folder" and "tags" in fields:
            for child in db.q("SELECT id FROM sources WHERE parent=?", [sid]):
                db.update("sources", child["id"], {"tags": fields["tags"]})
                kb.enqueue(child["id"])
        elif row["kind"] != "folder":
            kb.enqueue(sid)
        return db.one("SELECT * FROM sources WHERE id=?", [sid]) or {}

    @app.delete("/api/sources/{sid}")
    def delete_source(sid: str) -> dict:
        kb.remove(sid)
        return {"ok": True}

    @app.post("/api/sources/{sid}/reindex")
    def reindex(sid: str) -> dict:
        row = db.one("SELECT * FROM sources WHERE id=?", [sid])
        if row and row["kind"] == "folder":
            def rescan() -> None:
                for c in db.q("SELECT id FROM sources WHERE parent=?", [sid]):
                    kb.enqueue(c["id"], force=True)
                kb._scan_folder(sid, Path(row["path"]))

            threading.Thread(target=rescan, daemon=True).start()
        elif row:
            kb.enqueue(sid, force=True)
        return {"ok": True}

    @app.post("/api/reindex")
    def reindex_all() -> dict:
        return {"queued": kb.reindex_all(force=True)}

    @app.get("/api/index")
    def index_progress() -> dict:
        return kb.progress()

    # --- delete data -------------------------------------------------------------------------------
    @app.delete("/api/data/{what}")
    def delete_data(what: str) -> dict[str, Any]:
        """Settings → Delete: all chats, all sources, all meetings, or all of them. Settings and downloaded models stay."""
        if what not in ("chats", "sources", "meetings", "all"):
            raise HTTPException(404, "unknown data")
        if what in ("meetings", "all") and sessions.state():
            raise HTTPException(409, "A meeting is being recorded. End it first.")
        out: dict[str, int] = {}
        if what in ("chats", "all"):
            out["chats"] = len(db.q("SELECT id FROM chats"))
            db.x("DELETE FROM chats")
        if what in ("sources", "all"):
            out["sources"] = kb.clear_sources()
        if what in ("meetings", "all"):
            out["meetings"] = kb.clear_meetings()
            kb.clear_old_uploads()
        return {"deleted": out, "kb": kb.counts()}

    # --- the notch (desktop app) -----------------------------------------------------------------------
    @app.get("/api/notch")
    def notch() -> dict[str, Any]:
        """What the desktop app shows in the notch: whether a meeting is being recorded and its latest words."""
        cur = sessions.current
        base: dict[str, Any] = {"enabled": bool(settings.get("notch_live")), "recording": False}
        if not cur or cur.stop_flag.is_set():
            return base
        m = db.meeting(cur.meeting_id) or {}
        return {**base, "recording": True, "paused": cur.paused, "meeting_id": cur.meeting_id,
                "title": m.get("title", ""), "elapsed": round(cur.clock(), 1),
                "lines": list(cur.recent)[-6:], "speaking": dict(cur.speaking)}

    @app.get("/api/search")
    def search(q: str, k: int = 10, scope: str = "all") -> list[dict]:
        cols = ("documents", "meetings", "notes") if scope == "all" else (scope,)
        try:
            return kb.search(q, k=k, collections=cols)
        except NotReady as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/open")
    async def open_path(request: Request) -> dict:
        """Reveal a source file in the system file manager."""
        body = await request.json()
        row = db.one("SELECT path FROM sources WHERE id=?", [body.get("id", "")])
        if not row or not row["path"] or not Path(row["path"]).exists():
            raise HTTPException(404, "not found")
        p = row["path"]
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", p])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", p])
        else:
            subprocess.Popen(["xdg-open", str(Path(p).parent)])
        return {"ok": True}

    @app.post("/api/open-privacy")
    async def open_privacy(request: Request) -> dict:
        """Open the OS privacy pane for the microphone or screen/system audio recording."""
        body = await request.json()
        what = body.get("what", "screen")
        if platform.system() == "Darwin":
            pane = "Privacy_Microphone" if what == "mic" else "Privacy_ScreenCapture"
            subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "ms-settings:privacy-microphone"])
        return {"ok": True}

    @app.post("/api/pick")
    async def pick(request: Request) -> dict:
        """Native file/folder chooser (the UI runs in a browser or webview without one)."""
        body = await request.json()
        folder = body.get("type") == "folder"
        return {"path": await asyncio.to_thread(_native_pick, folder)}

    # --- chat --------------------------------------------------------------------------------------
    @app.get("/api/chats")
    def chats() -> list[dict]:
        return db.q("SELECT * FROM chats ORDER BY updated DESC")

    @app.post("/api/chats")
    async def new_chat(request: Request) -> dict:
        body = await request.json() if (await request.body()) else {}
        return db.create_chat(body.get("title", ""), body.get("meeting_id"))

    @app.get("/api/chats/{cid}")
    def get_chat(cid: str) -> dict:
        c = db.one("SELECT * FROM chats WHERE id=?", [cid])
        if not c:
            raise HTTPException(404, "chat not found")
        return {**c, "messages": db.q("SELECT * FROM messages WHERE chat_id=? ORDER BY created", [cid])}

    @app.delete("/api/chats/{cid}")
    def delete_chat(cid: str) -> dict:
        db.x("DELETE FROM chats WHERE id=?", [cid])
        return {"ok": True}

    @app.post("/api/chats/{cid}/messages")
    async def send(cid: str, request: Request) -> StreamingResponse:
        body = await request.json()
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "empty message")
        web = body.get("web")

        def gen():
            try:
                for ev in chat.answer(cid, text, web):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                log.exception("chat failed")
                yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- speech out --------------------------------------------------------------------------------
    @app.post("/api/tts")
    async def speak(request: Request) -> Response:
        body = await request.json()
        if not tts.available():
            raise HTTPException(409, "Kokoro is not downloaded")
        wav = await asyncio.to_thread(tts.speak, str(body.get("text", "")), body.get("lang", "en"),
                                      settings.get("tts_voice"))
        return Response(wav, media_type="audio/wav")

    @app.get("/api/tts/voices")
    def voices() -> dict:
        return tts.VOICES

    # --- agents ------------------------------------------------------------------------------------
    @app.get("/api/agents")
    def agents() -> dict:
        import sys

        exe = shutil.which("lumina") or str(Path.home() / ".local" / "bin" / "lumina")
        return {
            "stdio": {"command": exe, "args": ["mcp"]},
            "http": {"url": f"http://127.0.0.1:{port}/mcp", "token": settings.get("mcp_token")},
            "python": sys.executable,
            "data": str(DATA),
        }

    # --- websocket -----------------------------------------------------------------------------------
    @app.websocket("/api/live")
    async def live(ws: WebSocket):
        origin = ws.headers.get("origin")
        if ws.headers.get("host", "") not in allowed_hosts or (origin and origin not in allowed_origins):
            await ws.close(code=4403)
            return
        await ws.accept()
        q = hub.subscribe()
        try:
            await ws.send_json({"type": "hello", "session": sessions.state(), "downloads": downloads.status()})
            while True:
                ev = await q.get()
                await ws.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(q)

    # --- UI ------------------------------------------------------------------------------------------
    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (UI_DIR / path).resolve()
        if path and target.is_file() and UI_DIR.resolve() in target.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
            return FileResponse(target, headers=headers)
        index = UI_DIR / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"error": "UI not built. Run: cd web && npm install && npm run build"}, status_code=500)

    return app


class _McpSlash:
    """Agents call /mcp without the trailing slash; serve it the same as /mcp/."""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


def _native_pick(folder: bool) -> str:
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            what = "folder" if folder else "file"
            r = subprocess.run(["osascript", "-e", f'POSIX path of (choose {what} with prompt "Lumina")'],
                               capture_output=True, text=True, timeout=600)
            return r.stdout.strip()
        if sysname == "Windows":
            if folder:
                ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                      "if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}")
            else:
                ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.OpenFileDialog;"
                      "if($d.ShowDialog() -eq 'OK'){$d.FileName}")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                               timeout=600)
            return r.stdout.strip()
        for cmd in (["zenity", "--file-selection"] + (["--directory"] if folder else []),
                    ["kdialog", "--getexistingdirectory" if folder else "--getopenfilename", str(Path.home())]):
            if shutil.which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ""
