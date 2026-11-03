"""Lumina's family of apps: brain (memory), OpenCue (agents), OpenBlocks (notes) and AI WorkStudio (the workspace that
holds them).

Lumina talks to each one the way they already talk to each other, with nothing new to install:
- brain: its dashboard API on 127.0.0.1:8765 to search (the same search its MCP tool uses), and its MCP server
  (`uv run --directory ~/.brain python server.py`, the one WorkStudio starts) to write a meeting into the vault.
- OpenCue: its agents are read from opencue.db, and a message reaches one through `opencue mcp-serve` and the
  `send_to_agent` tool (what WorkStudio's chat does).
- OpenBlocks: its own MCP server (`openblocks mcp`, the one WorkStudio and Claude Code use) to search the vault and to
  write a meeting or a chat answer as a Markdown page; its dashboard on 127.0.0.1:47880 only to see if it is open.
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


# ---------- OpenBlocks --------------------------------------------------------------------------------------------

OPENBLOCKS_PORT = int(os.environ.get("OPENBLOCKS_PORT") or 47880)
OPENBLOCKS_URL = f"http://127.0.0.1:{OPENBLOCKS_PORT}"
OPENBLOCKS_FOLDER = "Lumina"  # where Lumina's pages go inside the OpenBlocks vault


def openblocks_cli() -> str | None:
    """The `openblocks` command: on the PATH, or the binary inside OpenBlocks.app (the app answers `mcp` itself)."""
    found = _which("openblocks")
    if found:
        return found
    for base in (Path("/Applications"), HOME / "Applications"):
        binary = base / "OpenBlocks.app" / "Contents" / "MacOS" / "openblocks"
        if binary.exists():
            return str(binary)
    return None


def openblocks_status() -> dict[str, Any]:
    cli = openblocks_cli()
    version, running = "", False
    try:
        r = httpx.get(f"{OPENBLOCKS_URL}/api/status", timeout=0.8)
        if r.status_code == 200 and "openblocks" in r.text.lower():
            running = True
            try:
                version = str(r.json().get("version", ""))
            except (ValueError, AttributeError):
                pass
    except httpx.HTTPError:
        pass
    home = Path(os.environ.get("OPENBLOCKS_HOME", HOME / ".openblocks"))
    installed = bool(cli or _mac_app("OpenBlocks.app") or home.exists() or running)
    return {"id": "openblocks", "installed": installed, "running": running, "version": version,
            "url": f"{OPENBLOCKS_URL}/", "can_write": bool(cli)}


def _ob_server() -> tuple[str, list[str]]:
    from .config import settings

    cli = openblocks_cli()
    if not cli:
        raise IntegrationError("OpenBlocks is not installed. Install it from https://lautaro005.github.io/openblocks/")
    args = ["mcp"]
    vault = str(settings.get("openblocks_vault") or "").strip()
    if vault:
        args += ["--vault", vault]
    return cli, args


def mcp_run(command: str, args: list[str], work, timeout: float = 60) -> Any:  # noqa: ANN001
    """Open one MCP session over stdio and run `work(session)` (a coroutine) in it. Raises IntegrationError."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    env = get_default_environment()
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), str(HOME / ".local" / "bin"), "/opt/homebrew/bin",
                                   "/usr/local/bin"])

    async def run() -> Any:
        with open(LOGS / "integrations-mcp.log", "a", encoding="utf-8") as errlog:
            params = StdioServerParameters(command=command, args=args, env=env)
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), 30)
                    return await asyncio.wait_for(work(session), timeout)

    try:
        return asyncio.run(run())
    except IntegrationError:
        raise
    except TimeoutError as e:
        raise IntegrationError("OpenBlocks took too long to answer") from e
    except Exception as e:  # noqa: BLE001 - a missing program, a server that crashed...
        log.warning("mcp run %s failed: %s", command, e)
        raise IntegrationError(str(e) or e.__class__.__name__) from e


def _result_text(res: Any) -> str:
    text = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", None) or [] if getattr(c, "text", ""))
    if getattr(res, "isError", None) or getattr(res, "is_error", False):
        raise IntegrationError(text or "OpenBlocks refused the request")
    return text


# OpenBlocks names its tools' arguments itself; Lumina reads each tool's schema and fills the ones it has, so a
# renamed argument (content / markdown / text) does not break the link.
_ALIASES = {
    "query": ("query", "q", "text", "search"),
    "limit": ("limit", "k", "top_k", "max_results"),
    "path": ("path", "page", "file", "name", "title"),
    "title": ("title", "name"),
    "content": ("content", "markdown", "text", "body"),
    "folder": ("folder", "dir", "directory", "parent"),
    "mode": ("mode", "op", "operation", "action", "position"),
}


def _ob_args(schema: dict, **wanted: Any) -> dict[str, Any]:
    props = set((schema or {}).get("properties") or {})
    out: dict[str, Any] = {}
    for key, value in wanted.items():
        name = next((a for a in _ALIASES.get(key, (key,)) if a in props and a not in out), None)
        if name:
            out[name] = value
    return out


def _tool_schemas(tools: Any) -> dict[str, dict]:
    # mcp 1.x names the field inputSchema, mcp 2.x input_schema
    return {t.name: (getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {})
            for t in getattr(tools, "tools", [])}


def _ob_hits(text: str) -> list[dict]:
    """The pages a search returned, whatever shape OpenBlocks printed them in (JSON, or one block per page)."""
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out: list[dict] = []
    if isinstance(data, list):
        for h in data:
            if isinstance(h, str):
                out.append({"path": h, "title": Path(h).stem, "text": ""})
            elif isinstance(h, dict):
                path = str(h.get("path") or h.get("file") or h.get("id") or "")
                title = str(h.get("title") or h.get("name") or Path(path).stem or "OpenBlocks")
                body = str(h.get("snippet") or h.get("excerpt") or h.get("text") or h.get("content") or
                           h.get("preview") or "")
                out.append({"path": path, "title": title, "text": body})
        return out
    for block in re.split(r"\n\s*\n|\n(?=[-*] )", text):
        block = block.strip()
        if not block:
            continue
        m = re.search(r"[\w./ \-]+\.md", block)
        path = m.group(0).strip(" -*") if m else ""
        first = re.sub(r"^[-*#\d.\s]+", "", block.splitlines()[0]).strip()
        out.append({"path": path, "title": Path(path).stem if path else first[:80], "text": block})
    return out


def openblocks_search(query: str, k: int = 4) -> list[dict]:
    """Pages of the user's OpenBlocks vault that match, with their text (search, then read the best ones)."""
    try:
        cli, args = _ob_server()
    except IntegrationError:
        return []

    async def work(session) -> list[dict]:  # noqa: ANN001
        schemas = _tool_schemas(await session.list_tools())
        if "search_pages" not in schemas:
            return []
        hits = _ob_hits(_result_text(await session.call_tool(
            "search_pages", _ob_args(schemas["search_pages"], query=query, limit=k))))[:k]
        if "read_page" in schemas:
            for h in hits:
                if h["path"] and len(h["text"]) < 400:
                    try:
                        h["text"] = _result_text(await session.call_tool(
                            "read_page", _ob_args(schemas["read_page"], path=h["path"])))
                    except IntegrationError:
                        pass
        return hits

    try:
        hits = mcp_run(cli, args, work, timeout=30)
    except IntegrationError as e:
        log.info("OpenBlocks search unavailable: %s", e)
        return []
    return [h for h in hits if h["text"].strip()]


def clean_name(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|#\[\]^]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")[:90] or "Untitled"


def openblocks_write(path: str, content: str, body_only: str = "", update: bool = False) -> str:
    """Create the page `path` (Folder/Name.md) in the OpenBlocks vault; with `update`, replace the body of the page
    that is already there (a meeting saved once is kept in step) and create it again if it was deleted."""
    cli, args = _ob_server()

    async def work(session) -> str:  # noqa: ANN001
        schemas = _tool_schemas(await session.list_tools())
        if update and body_only and "edit_page" in schemas:
            try:
                return _result_text(await session.call_tool("edit_page", _ob_args(
                    schemas["edit_page"], path=path, mode="replace", content=body_only)))
            except IntegrationError:
                pass  # the page was deleted or moved: write it again below
        if "create_page" not in schemas:
            raise IntegrationError("this OpenBlocks has no create_page tool; update it with `openblocks update`")
        schema = schemas["create_page"]
        props = set(schema.get("properties") or {})
        folder, _, name = path.rpartition("/")
        name = name.removesuffix(".md")
        wanted: dict[str, Any] = {"content": content}
        if props & set(_ALIASES["folder"]) and props & set(_ALIASES["title"]):
            wanted |= {"title": name, "folder": folder}
        elif props & {"path", "file"}:
            wanted["path"] = path
        else:
            wanted["title"] = f"{folder}/{name}" if folder else name
        return _result_text(await session.call_tool("create_page", _ob_args(schema, **wanted)))

    out = mcp_run(cli, args, work, timeout=60)
    if re.match(r"(?i)\s*(error|failed|cannot|could not)", out):
        raise IntegrationError(out.strip()[:300])
    return path


def openblocks_meeting_note(m: dict, segments_text: str = "") -> tuple[str, str, str]:
    """(path, whole page, body without properties) for a meeting, in the Markdown OpenBlocks reads: properties,
    a callout for the overview, `- [ ]` tasks for the action items, and folds for the long parts."""
    when = time.localtime(m.get("started") or m.get("created") or time.time())
    day = time.strftime("%Y-%m-%d", when)
    title = (m.get("title") or "Meeting").strip()
    s = m.get("summary") or {}
    path = m.get("openblocks_path") or f"{OPENBLOCKS_FOLDER}/{day} {clean_name(title)}.md"
    tags = ["lumina", "meeting", *[slug(t, 32) for t in m.get("tags") or []]]
    fm = ["---", f"title: {json.dumps(title, ensure_ascii=False)}", f"date: {day}",
          f"tags: [{', '.join(dict.fromkeys(tags))}]", "source: lumina", f"lumina_meeting: {m['id']}", "---", ""]
    body = [f"{time.strftime('%Y-%m-%d %H:%M', when)} · {round((m.get('duration') or 0) / 60)} min · "
            "recorded with Lumina", ""]
    overview = (s.get("overview") or "").strip()
    if s.get("text"):
        body += ["## Summary", "", s["text"].strip(), ""]
    elif overview:
        body += ["> [!info] Overview", *[f"> {line}" if line else ">" for line in overview.splitlines()], ""]
    for key, label in (("decisions", "Decisions"), ("action_items", "Action items"),
                       ("open_questions", "Open questions"), ("topics", "Topics")):
        if s.get(key):
            mark = "- [ ] " if key == "action_items" else "- "
            body += [f"## {label}", "", *[f"{mark}{x}" for x in s[key]], ""]
    notes = [b for b in m.get("enhanced") or [] if b.get("text")]
    if notes:
        body += ["## Notes", ""]
        for b in notes:
            body += [f"### {b.get('cue', '')}", "", b["text"].strip(), ""]
    if (m.get("cues") or "").strip():
        body += ["<details><summary>My shorthand</summary>", "", m["cues"].strip(), "", "</details>", ""]
    if segments_text:
        body += ["<details><summary>Transcript</summary>", "", segments_text.strip(), "", "</details>", ""]
    text = "\n".join(body).rstrip() + "\n"
    return path, "\n".join(fm) + "\n" + text, text


def openblocks_chat_note(question: str, answer: str, sources: list[dict] | None = None) -> tuple[str, str]:
    """A chat answer as an OpenBlocks page: the question, the answer and where it came from."""
    day = time.strftime("%Y-%m-%d")
    title = clean_name(question.splitlines()[0] if question.strip() else "Ask Lumina")[:60]
    fm = ["---", f"title: {json.dumps(title, ensure_ascii=False)}", f"date: {day}", "tags: [lumina, chat]",
          "source: lumina", "---", ""]
    body = ["> [!question] " + (question.strip().splitlines()[0] if question.strip() else "Ask Lumina")[:300], "",
            answer.strip(), ""]
    names = [str(s.get("title") or "").strip() for s in sources or [] if s.get("title")]
    if names:
        body += ["## Sources", "", *[f"- {n}" for n in dict.fromkeys(names)][:12], ""]
    path = f"{OPENBLOCKS_FOLDER}/{day} {title}.md"
    return path, "\n".join(fm + body).rstrip() + "\n"


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
    """Open brain, OpenCue, OpenBlocks or AI WorkStudio in its own window."""
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
    if app_id == "openblocks":
        app = _mac_app("OpenBlocks.app")
        if app:
            subprocess.Popen(["open", str(app)])
        elif openblocks_cli() and not openblocks_status()["running"]:
            subprocess.Popen([openblocks_cli()], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        elif openblocks_status()["running"]:
            _open_url(f"{OPENBLOCKS_URL}/")
        else:
            raise IntegrationError("OpenBlocks is not installed.")
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
    return {"brain": brain_status(), "opencue": opencue_status(), "openblocks": openblocks_status(),
            "workstudio": workstudio_status()}
