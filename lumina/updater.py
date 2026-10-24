"""Finds out whether a newer Lumina exists and updates this install from inside the app.

The check reads the version on the main branch (the same code `lumina update` pulls). Updating runs
`lumina update` in a detached script, because the update replaces the files the server is running from;
when it finishes, the script restarts Lumina (the desktop app or the server) on the new version.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .config import DATA, LOGS, settings

log = logging.getLogger("lumina.update")

REPO = os.environ.get("LUMINA_GH_REPO", "Lautaro005/lumina")
RAW = f"https://raw.githubusercontent.com/{REPO}/main/lumina/__init__.py"
RELEASES = f"https://github.com/{REPO}/releases"
STATUS = DATA / "update-status.json"
LOG = LOGS / "update.log"
CHECK_EVERY = 6 * 3600


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:4]) or (0,)


def newer(latest: str, current: str = __version__) -> bool:
    return parse(latest) > parse(current)


def home() -> Path | None:
    p = Path(os.environ.get("LUMINA_HOME") or Path.home() / ".lumina")
    return p if (p / "lumina.sh").exists() or (p / "lumina.ps1").exists() else None


class Updater:
    def __init__(self) -> None:
        self.latest = ""
        self.checked = 0.0
        self.error = ""
        self._lock = threading.Lock()

    def check(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if force or time.time() - self.checked > CHECK_EVERY:
                try:
                    r = httpx.get(RAW, timeout=8, follow_redirects=True)
                    r.raise_for_status()
                    m = re.search(r'__version__\s*=\s*"([^"]+)"', r.text)
                    if not m:
                        raise ValueError("no version in the file")
                    self.latest, self.error = m.group(1), ""
                except (httpx.HTTPError, ValueError) as e:
                    self.error = f"Could not look for updates: {e}"
                self.checked = time.time()
        return self.state()

    def status_file(self) -> dict[str, Any]:
        try:
            return json.loads(STATUS.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def state(self) -> dict[str, Any]:
        st = self.status_file()
        running = st.get("state") == "running" and time.time() - st.get("at", 0) < 1800
        return {
            "current": __version__, "latest": self.latest or __version__,
            "available": bool(self.latest) and newer(self.latest), "checked": self.checked, "error": self.error,
            "state": "running" if running else ("failed" if st.get("state") == "failed" and
                                                time.time() - st.get("at", 0) < 3600 else "idle"),
            "can_update": home() is not None, "app": os.environ.get("LUMINA_APP") == "1",
            "releases": RELEASES, "log": self.tail() if running or st.get("state") == "failed" else "",
        }

    def tail(self, n: int = 6) -> str:
        try:
            lines = [ln for ln in LOG.read_text("utf-8", "replace").splitlines() if ln.strip()]
        except OSError:
            return ""
        return "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines[-n:])

    def apply(self, port: int) -> dict[str, Any]:
        root = home()
        if root is None:
            raise RuntimeError("This copy of Lumina was not installed with the installer, so it cannot update itself. "
                               "Run `lumina update` in a terminal.")
        if self.state()["state"] == "running":
            return self.state()
        app_pid = os.getppid() if os.environ.get("LUMINA_APP") == "1" else 0
        STATUS.write_text(json.dumps({"state": "running", "at": time.time(), "from": __version__}), "utf-8")
        LOG.write_text("", "utf-8")
        if platform.system() == "Windows":
            self._spawn_windows(root, port, app_pid)
        else:
            self._spawn_unix(root, port, app_pid)
        return self.state()

    def _spawn_unix(self, root: Path, port: int, app_pid: int) -> None:
        q = shlex.quote
        script = f"""#!/bin/sh
exec >>{q(str(LOG))} 2>&1
export LUMINA_HOME={q(str(root))} LUMINA_DATA={q(str(DATA))}
cd {q(str(root))}
sleep 1
if sh {q(str(root / "lumina.sh"))} update; then
  quit() {{
    curl -s -m 5 -X POST -H 'X-Lumina: 1' -H 'Content-Type: application/json' -d '{{}}' \
      http://127.0.0.1:{port}/api/quit >/dev/null 2>&1
  }}
  echo "Restarting Lumina"
  rm -f {q(str(STATUS))}
  if [ {app_pid} -gt 0 ]; then
    kill -TERM {app_pid} 2>/dev/null
    quit
    sleep 2
    nohup sh {q(str(root / "lumina.sh"))} app >/dev/null 2>&1 &
  else
    quit
    sleep 2
    nohup sh {q(str(root / "lumina.sh"))} serve --port {port} >/dev/null 2>&1 &
  fi
else
  echo "The update failed. Run lumina update in a terminal to see why."
  printf '{{"state":"failed","at":%s}}' "$(date +%s)" > {q(str(STATUS))}
fi
"""
        path = DATA / "update-run.sh"
        path.write_text(script, "utf-8")
        subprocess.Popen(["/bin/sh", str(path)], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(root))

    def _spawn_windows(self, root: Path, port: int, app_pid: int) -> None:
        ps = root / "lumina.ps1"
        script = f"""$ErrorActionPreference = 'Continue'
$env:LUMINA_HOME = '{root}'; $env:LUMINA_DATA = '{DATA}'
Start-Transcript -Path '{LOG}' -Append | Out-Null
Start-Sleep -Seconds 1
& powershell -NoProfile -ExecutionPolicy Bypass -File '{ps}' update
if ($LASTEXITCODE -eq 0) {{
  Write-Host 'Restarting Lumina'
  Remove-Item '{STATUS}' -ErrorAction SilentlyContinue
  if ({app_pid} -gt 0) {{ Stop-Process -Id {app_pid} -Force -ErrorAction SilentlyContinue }}
  try {{
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:{port}/api/quit -Headers @{{'X-Lumina'='1'}} `
      -ContentType 'application/json' -Body '{{}}' -TimeoutSec 5 | Out-Null
  }} catch {{}}
  Start-Sleep -Seconds 2
  if ({app_pid} -gt 0) {{ & powershell -NoProfile -ExecutionPolicy Bypass -File '{ps}' app }}
  else {{
    Start-Process powershell -WindowStyle Hidden `
      -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{ps}','serve','--port','{port}'
  }}
}} else {{
  Write-Host 'The update failed. Run lumina update in a terminal to see why.'
  Set-Content -Path '{STATUS}' -Value ('{{"state":"failed","at":' + [int][double]::Parse((Get-Date -UFormat %s)) + '}}')
}}
"""
        path = DATA / "update-run.ps1"
        path.write_text(script, "utf-8")
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x0800_0000 | 0x0000_0200)  # no window, new process group

    def start_background(self) -> None:
        def loop() -> None:
            time.sleep(15)
            while True:
                if settings.get("update_check"):
                    try:
                        self.check(force=True)
                    except Exception:  # noqa: BLE001
                        log.exception("update check failed")
                time.sleep(CHECK_EVERY)

        threading.Thread(target=loop, daemon=True, name="update-check").start()


updater = Updater()
