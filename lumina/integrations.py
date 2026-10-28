"""Lumina's family of apps: brain (memory), OpenCue (agents) and AI WorkStudio (the workspace that holds them).

Lumina talks to each one the way they already talk to each other, with nothing new to install:
- brain: its dashboard API on 127.0.0.1:8765 to search (the same search its MCP tool uses), and its MCP server
  (`uv run --directory ~/.brain python server.py`, the one WorkStudio starts) to write a meeting into the vault.
- OpenCue: its agents are read from opencue.db, and a message reaches one through `opencue mcp-serve` and the
  `send_to_agent` tool (what WorkStudio's chat does).
- AI WorkStudio: it detects Lumina on its port, reads /api/status for its notices and serves `lumina mcp` in its
  hub; Lumina only reports whether WorkStudio is installed and opens it.
Everything stays on this computer: these are local programs, reached on 127.0.0.1 or through a child process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from .config import LOGS

log = logging.getLogger("lumina.integrations")

BRAIN_URL = "http://127.0.0.1:8765"
HOME = Path.home()


class IntegrationError(RuntimeError):
    pass


# ---------- helpers -----------------------------------------------------------------------------------------------

def _which(name: str, *extra: Path) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for p in (*extra, HOME / ".local" / "bin" / name, Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name):
        if p.exists():
            return str(p)
    return None


def _mac_app(name: str) -> Path | None:
    for base in (Path("/Applications"), HOME / "Applications"):
        if (base / name).exists():
            return base / name
    return None


def mcp_call(command: str, args: list[str], tool: str, arguments: dict, timeout: float = 60,
             cwd: str | None = None, env_extra: dict[str, str] | None = None) -> str:
    """Start an MCP server over stdio, call one tool and return its text. Raises IntegrationError."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    env = get_default_environment()
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), str(HOME / ".local" / "bin"), "/opt/homebrew/bin",
                                   "/usr/local/bin"])
    env.update(env_extra or {})

    async def run() -> str:
        with open(LOGS / "integrations-mcp.log", "a", encoding="utf-8") as errlog:
            params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), 60)
                    res = await asyncio.wait_for(session.call_tool(tool, arguments), timeout)
        text = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", None) or [] if getattr(c, "text", ""))
        if getattr(res, "isError", None) or getattr(res, "is_error", False):
            raise IntegrationError(text or f"{tool} failed")
        return text

    try:
        return asyncio.run(run())
    except IntegrationError:
        raise
    except TimeoutError as e:
        raise IntegrationError(f"{tool} took too long") from e
    except Exception as e:  # noqa: BLE001 - a missing program, a server that crashed...
        log.warning("mcp call %s %s failed: %s", command, tool, e)
        raise IntegrationError(str(e) or e.__class__.__name__) from e


def slug(text: str, limit: int = 48) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].strip("-") or "meeting"


# ---------- brain -------------------------------------------------------------------------------------------------

def brain_home() -> Path:
    return Path(os.environ.get("BRAIN_HOME", HOME / ".brain"))


def brain_status() -> dict[str, Any]:
    home = brain_home()
    installed = (home / "server.py").exists() or bool(_which("brain"))
    version, running = "", False
    try:
        r = httpx.get(f"{BRAIN_URL}/api/version", timeout=0.8)
        if r.status_code == 200 and "current" in r.text:
            running, version = True, str(r.json().get("current", ""))
    except (httpx.HTTPError, ValueError):
        pass
    if not version and (home / "VERSION").exists():
        version = (home / "VERSION").read_text(encoding="utf-8").strip()
    return {"id": "brain", "installed": installed or running, "running": running, "version": version,
            "url": f"{BRAIN_URL}/", "can_write": (home / "server.py").exists() and bool(_which("uv"))}


def brain_search(query: str, k: int = 5) -> list[dict]:
    """brain's hybrid search over the vault and saved pages, through its dashboard (only while brain is open)."""
    try:
        r = httpx.post(f"{BRAIN_URL}/api/search", json={"query": query, "top_k": k},
                       headers={"X-Brain": "1"}, timeout=8)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("brain search unavailable: %s", e)
        return []
    if not data.get("ok"):
        return []
    out = []
    for h in data.get("hits", []):
        text = str(h.get("text", "")).strip()
        if text:
            out.append({"title": h.get("path") or h.get("url") or "brain", "text": text,
                        "path": h.get("path", ""), "url": h.get("url", "")})
    return out


def meeting_note(m: dict, segments_text: str = "") -> tuple[str, str]:
    """The vault path and Markdown of a meeting, following brain's FORMAT.md: an independent file in knowledge/,
    type Reference, tagged so it joins the user's other notes on the same topics."""
    when = time.localtime(m.get("started") or m.get("created") or time.time())
    day = time.strftime("%Y-%m-%d", when)
    title = (m.get("title") or "Meeting").strip()
    s = m.get("summary") or {}
    path = m.get("brain_path") or f"knowledge/lumina-{day}-{slug(title)}.md"
    name = Path(path).stem
    overview = (s.get("overview") or "").strip()
    desc = re.sub(r"\s+", " ", overview)[:220] or f"Meeting recorded with Lumina on {day}"
    tags = ["lumina", "meeting", *[slug(t, 32) for t in m.get("tags") or []]]
    fm = ["---", "type: Reference", f"name: {name}", f"title: {json.dumps(title, ensure_ascii=False)}",
          f"description: {json.dumps(desc, ensure_ascii=False)}", f"tags: [{', '.join(dict.fromkeys(tags))}]",
          "source: lumina", f"lumina_meeting: {m['id']}", f"date: {day}", "---", ""]
    body = [f"# {title}", "", f"{time.strftime('%Y-%m-%d %H:%M', when)} · {round((m.get('duration') or 0) / 60)} min"
            " · recorded with Lumina", ""]
    if s.get("text"):
        body += ["## Summary", "", s["text"].strip(), ""]
    elif overview:
        body += ["## Summary", "", overview, ""]
    for key, label in (("decisions", "Decisions"), ("action_items", "Action items"),
                       ("open_questions", "Open questions"), ("topics", "Topics")):
        if s.get(key):
            body += [f"## {label}", "", *[f"- {x}" for x in s[key]], ""]
    notes = [b for b in m.get("enhanced") or [] if b.get("text")]
    if notes:
        body += ["## Notes", ""]
        for b in notes:
            body += [f"### {b.get('cue', '')}", "", b["text"].strip(), ""]
    if (m.get("cues") or "").strip():
        body += ["## My shorthand", "", m["cues"].strip(), ""]
    if segments_text:
        body += ["## Transcript", "", segments_text.strip(), ""]
    return path, "\n".join(fm + body).rstrip() + "\n"


def brain_write(path: str, content: str) -> str:
    """Write a note through brain's own MCP server (history, metadata and indexing stay brain's job)."""
    home = brain_home()
    uv = _which("uv")
    if not (home / "server.py").exists():
        raise IntegrationError("brain is not installed (~/.brain). Install it from https://lautaro005.github.io/brain/")
    if not uv:
        raise IntegrationError("uv was not found; brain needs it to run.")
    out = mcp_call(uv, ["run", "--directory", str(home), "python", "server.py"], "write_file",
                   {"path": path, "content": content}, timeout=90, cwd=str(home))
    if not out.startswith("OK"):
        raise IntegrationError(out or "brain did not save the note")
    return path


# ---------- OpenCue -----------------------------------------------------------------------------------------------

def opencue_db() -> Path:
    if os.environ.get("OPENCUE_HOME"):
        return Path(os.environ["OPENCUE_HOME"]) / "opencue.db"
    system = platform.system()
    if system == "Darwin":
        return HOME / "Library" / "Application Support" / "opencue" / "opencue.db"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", HOME)) / "opencue" / "data" / "opencue.db"
    return Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "opencue" / "opencue.db"


def opencue_agents() -> list[dict]:
    db = opencue_db()
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            rows = con.execute("SELECT id, name FROM agents ORDER BY name").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.info("cannot read OpenCue agents: %s", e)
        return []
    return [{"id": str(r[0]), "name": str(r[1])} for r in rows if r[1]]


def opencue_running() -> bool:
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5).stdout.lower()
            return "opencue" in out
        out = subprocess.run(["pgrep", "-if", "opencue"], capture_output=True, text=True, timeout=5).stdout
        return any(line.strip() for line in out.splitlines() if line.strip() != str(os.getpid()))
    except (OSError, subprocess.SubprocessError):
        return False


def opencue_status() -> dict[str, Any]:
    cli = _which("opencue")
    agents = opencue_agents()
    installed = bool(cli or _mac_app("OpenCue.app") or opencue_db().exists())
    return {"id": "opencue", "installed": installed, "running": installed and opencue_running(), "agents": agents,
            "can_send": bool(cli) and len(agents) > 0}


def _start_opencue() -> None:
    if opencue_running():
        return
    app = _mac_app("OpenCue.app")
    try:
        if app:
            subprocess.Popen(["open", "-g", str(app)])
        elif _which("opencue"):
            subprocess.Popen([_which("opencue")], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        time.sleep(4)
    except OSError as e:
        log.warning("cannot start OpenCue: %s", e)


def opencue_send(agent: str, message: str) -> str:
    """Send a message to an OpenCue agent and return its reply. OpenCue delivers a message from another agent,
    so any other agent works as the sender (as in WorkStudio)."""
    cli = _which("opencue")
    if not cli:
        raise IntegrationError("OpenCue is not installed. Install it from https://lautaro005.github.io/opencue/")
    agents = opencue_agents()
    target = next((a for a in agents if agent in (a["id"], a["name"])), None)
    if not target:
        raise IntegrationError(f"There is no agent called {agent} in OpenCue.")
    sender = next((a for a in agents if a["id"] != target["id"]), target)
    _start_opencue()
    text = mcp_call(cli, ["mcp-serve", "--agent", sender["id"]], "send_to_agent",
                    {"agent": target["name"], "message": message}, timeout=1800)
    text = text.strip()
    prefix = f"[{target['name']}]:"
    return text[len(prefix):].strip() if text.startswith(prefix) else text


def meeting_message(m: dict, note: str = "") -> str:
    s = m.get("summary") or {}
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("started") or m.get("created") or time.time()))
    lines = [f"From Lumina (meeting notes): \"{m.get('title') or 'Meeting'}\", {when}."]
    if note.strip():
        lines += ["", note.strip()]
    if s.get("text"):
        lines += ["", s["text"].strip()]
    elif s.get("overview"):
        lines += ["", "Summary: " + s["overview"]]
    for key, label in (("decisions", "Decisions"), ("action_items", "Action items"), ("open_questions", "Open questions")):
        if s.get(key):
            lines += ["", f"{label}:", *[f"- {x}" for x in s[key]]]
    if not s and (m.get("cues") or "").strip():
        lines += ["", "Notes:", m["cues"].strip()]
    lines += ["", "The full meeting is available with Lumina's MCP tools (get_meeting) if you have them."]
    return "\n".join(lines)


# ---------- AI WorkStudio -----------------------------------------------------------------------------------------

def workstudio_status() -> dict[str, Any]:
    app = _mac_app("AI WorkStudio.app")
    cli = _which("workstudio")
    home = Path(os.environ.get("WORKSTUDIO_HOME", HOME / ".workstudio"))
    hub = False
    try:
        cfg = json.loads((home / "mcp" / "claude.json").read_text(encoding="utf-8"))
        hub = "workstudio" in (cfg.get("mcpServers") or {})
    except (OSError, ValueError, AttributeError):
        pass
    try:  # WorkStudio keeps {app: on} and serves every app that is not switched off
        apps = json.loads((home / "settings.json").read_text(encoding="utf-8")).get("mcp_apps") or {}
        if isinstance(apps, dict) and apps.get("lumina") is False:
            hub = False
    except (OSError, ValueError, AttributeError):
        pass
    return {"id": "workstudio", "installed": bool(app or cli or home.exists()), "hub": hub}


def open_app(app_id: str) -> None:
    """Open brain, OpenCue or AI WorkStudio in its own window."""
    system = platform.system()
    if app_id == "brain":
        target = _mac_app("brain.app") if system == "Darwin" else None
        cmd = ["open", str(target)] if target else ([_which("brain"), "--no-browser"] if _which("brain") else None)
        if not brain_status()["running"] and cmd:
            subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        _open_url(f"{BRAIN_URL}/")
        return
    if app_id == "opencue":
        app = _mac_app("OpenCue.app")
        if app:
            subprocess.Popen(["open", str(app)])
        elif _which("opencue"):
            subprocess.Popen([_which("opencue")], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        else:
            raise IntegrationError("OpenCue is not installed.")
        return
    if app_id == "workstudio":
        app = _mac_app("AI WorkStudio.app")
        if app:
            subprocess.Popen(["open", str(app)])
        elif _which("workstudio"):
            subprocess.Popen([_which("workstudio")], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            raise IntegrationError("AI WorkStudio is not installed.")
        return
    raise IntegrationError(f"unknown app {app_id}")


def _open_url(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def status() -> dict[str, Any]:
    return {"brain": brain_status(), "opencue": opencue_status(), "workstudio": workstudio_status()}
