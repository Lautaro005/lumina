"""`lumina` command line.

  lumina serve [--port N] [--open]   start Lumina on 127.0.0.1 (the UI, API and MCP over HTTP)
  lumina mcp                         MCP server over stdio, for Claude Desktop, Cursor, Claude Code...
  lumina models [download ID]        list or download models
  lumina doctor                      check the install
  lumina version
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from . import __version__


def _setup_logging(stderr: bool = True) -> None:
    from .config import LOGS

    handlers: list[logging.Handler] = [logging.FileHandler(LOGS / "lumina.log", encoding="utf-8")]
    if stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=os.environ.get("LUMINA_LOG", "INFO"), handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    for noisy in ("httpx", "chromadb", "watchdog", "uvicorn.access", "huggingface_hub", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _lumina_running(port: int) -> bool:
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=1.5)
        return r.status_code == 200 and "version" in r.json()
    except Exception:  # noqa: BLE001
        return False


def serve(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}/"
    if not _port_free(port):
        if _lumina_running(port):
            print(f"Lumina is already running at {url}")
            if open_browser:
                webbrowser.open(url)
            return
        sys.exit(f"Port {port} is busy. Try: lumina serve --port {port + 1}")
    _setup_logging()
    import uvicorn

    from .server import create_app

    app = create_app(port)
    if open_browser:
        def opener() -> None:
            for _ in range(60):
                if _lumina_running(port):
                    webbrowser.open(url)
                    return
                time.sleep(0.5)
        threading.Thread(target=opener, daemon=True).start()
    print(f"Lumina {__version__} → {url}  (Ctrl+C to stop)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", ws_ping_interval=20)


def doctor() -> int:
    from .audio import build_helper, list_inputs
    from .catalog import CATALOG, installed
    from .config import DATA, settings, system_info
    from .llm import detect

    info = system_info()
    print(f"Lumina {__version__} · {info['os']} {info['arch']} · Python {info['python']}")
    print(f"Data folder: {DATA}")
    ok = True
    print("\nModels:")
    for i in CATALOG.values():
        print(f"  {'✓' if installed(i.id) else '·'} {i.label}  ({i.size / 1e6:.0f} MB)")
    inputs = list_inputs()
    print(f"\nMicrophones: {len(inputs)} found")
    for d in inputs:
        print(f"  {'*' if d['default'] else ' '} {d['name']}")
    if not inputs:
        ok = False
    if info["os"] == "Darwin":
        helper = build_helper()
        print("System audio helper:", "ready" if helper else "unavailable (xcode-select --install; see logs/audiotap-build.log)")
        if helper:
            from .audio import permissions

            p = permissions() or {}
            names = {0: "not asked yet", 1: "restricted", 2: "refused", 3: "allowed"}
            print(f"  Microphone permission: {names.get(p.get('microphone'), 'unknown')}")
            print(f"  Screen & System Audio Recording permission: {'allowed' if p.get('screen') else 'not allowed (yet)'}")
    d = detect()
    print(f"\nGenerators: built-in {'ready' if d['builtin'] else 'not downloaded'} · "
          f"Ollama {'running' if d['ollama'] else 'off'} · LM Studio {'running' if d['lmstudio'] else 'off'}")
    print(f"Active: {settings.get('llm_provider')}")
    return 0 if ok else 1


def models(args: list[str]) -> int:
    from .catalog import CATALOG, installed
    from .downloads import downloads

    if args and args[0] == "download":
        ids = args[1:]
        for item_id in ids:
            if item_id not in CATALOG:
                print(f"unknown model: {item_id}")
                return 2
            downloads.start(item_id)
        while True:
            st = downloads.status()
            line = "  ".join(f"{k} {v['done'] / max(v['total'], 1) * 100:5.1f}%" for k, v in st.items())
            print("\r" + line, end="", flush=True)
            if all(v["state"] in ("done", "error", "cancelled") for v in st.values()):
                print()
                bad = [f"{k}: {v['error']}" for k, v in st.items() if v["state"] != "done"]
                for b in bad:
                    print("error", b)
                return 1 if bad else 0
            time.sleep(0.5)
    for i in CATALOG.values():
        print(f"{'✓' if installed(i.id) else ' '} {i.id:<22} {i.size / 1e6:>8.0f} MB  {i.label}")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lumina", description="Local-first meeting notes and knowledge base.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start Lumina")
    s.add_argument("--port", type=int, default=int(os.environ.get("LUMINA_PORT", 0) or 0))
    s.add_argument("--open", action="store_true", help="open the browser")
    sub.add_parser("mcp", help="MCP server over stdio")
    m = sub.add_parser("models", help="list or download models")
    m.add_argument("rest", nargs="*")
    sub.add_parser("doctor", help="check the install")
    sub.add_parser("version")
    a = p.parse_args(argv)
    if a.cmd == "version":
        print(__version__)
    elif a.cmd == "mcp":
        _setup_logging(stderr=False)
        from .mcp_server import run_stdio

        run_stdio()
    elif a.cmd == "models":
        sys.exit(models(a.rest))
    elif a.cmd == "doctor":
        sys.exit(doctor())
    else:
        from .config import DEFAULT_PORT

        port = getattr(a, "port", 0) or DEFAULT_PORT
        serve(port, getattr(a, "open", False) if a.cmd == "serve" else True)


if __name__ == "__main__":
    main()
