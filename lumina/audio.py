"""Audio capture: the microphone ("you") and the computer's own output ("others").

- Microphone: PortAudio through sounddevice (on macOS the helper below, which owns the permission).
- System audio: macOS uses the bundled helper (lumina-audiotap, compiled on first use with the Xcode
  command line tools: a Core Audio process tap on macOS 14.2+, ScreenCaptureKit before); Windows
  (WASAPI loopback) and Linux (PulseAudio/PipeWire monitor) use the soundcard library.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import BIN, LOGS

log = logging.getLogger("lumina.audio")
SR = 16000
HELPER_SRC = Path(__file__).parent / "native" / "audiotap.swift"
# The helper lives in a tiny app bundle of its own (outside Lumina.app, which every update replaces) and runs as
# its own responsible program, so the two macOS permissions belong to it and survive updates.
HELPER_APP = BIN / "LuminaAudio.app"
HELPER = HELPER_APP / "Contents" / "MacOS" / "lumina-audiotap"
HELPER_SHA = BIN / "lumina-audiotap.sha"  # the source the helper was built from
OLD_HELPER = BIN / "lumina-audiotap"  # 0.1.4 - 0.1.5
HELPER_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>app.lumina.audiotap</string>
<key>CFBundleName</key><string>Lumina</string>
<key>CFBundleDisplayName</key><string>Lumina</string>
<key>CFBundleExecutable</key><string>lumina-audiotap</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundleShortVersionString</key><string>1</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key>
<string>Lumina transcribes your meetings on this Mac. The audio never leaves the computer.</string>
<key>NSAudioCaptureUsageDescription</key>
<string>Lumina records the other people in your calls (Zoom, Meet, Teams) to transcribe them on this Mac.</string>
<key>NSScreenCaptureUsageDescription</key>
<string>Lumina uses this only to hear your computer's audio during meetings. Nothing is recorded from the screen.</string>
</dict></plist>
"""
ICON = Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src-tauri" / "icons" / "icon.icns"
NOT_GRANTED = {4: "denied", 5: "not_asked"}  # helper exit codes when a permission is missing


class PermissionMissing(PermissionError):
    """A macOS permission was not given. Recording never asks for it; the walkthrough or Settings does."""

    def __init__(self, kind: str, state: str):
        self.kind, self.state = kind, state
        what = "Microphone" if kind == "microphone" else "System audio"
        if state == "denied":
            msg = (f"{what} access is off for Lumina. Turn it on in System Settings → Privacy & Security, "
                   "or in Lumina's Settings → Permissions.")
        else:
            msg = f"Lumina has not been given {what.lower()} access yet. Allow it in Settings → Permissions."
        super().__init__(msg)


def resample(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR or x.size == 0:
        return x.astype(np.float32)
    if sr % SR == 0:  # 48k / 32k: average then decimate (cheap low-pass)
        f = sr // SR
        n = (x.size // f) * f
        return x[:n].reshape(-1, f).mean(axis=1).astype(np.float32)
    n = int(round(x.size * SR / sr))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)


def list_inputs() -> list[dict]:
    if platform.system() == "Darwin" and HELPER_SRC.exists():
        # on a Mac the microphone goes through the helper, so list its devices (ids are the devices' unique ids)
        found = helper_devices()
        if found:
            return [{"id": d["id"], "name": d.get("name", ""), "default": bool(d.get("default")),
                     "loopback": any(k in str(d.get("name", "")).lower() for k in ("blackhole", "loopback", "soundflower"))}
                    for d in found]
    try:
        import sounddevice as sd

        default = sd.default.device[0] if sd.default.device else None
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out.append({"id": i, "name": d["name"], "default": i == default,
                            "loopback": any(k in d["name"].lower() for k in ("blackhole", "loopback", "soundflower", "monitor"))})
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("cannot list audio devices: %s", e)
        return []


class Mic:
    """The microphone. On a Mac it goes through the signed helper that owns the permission, and recording never
    shows a permission window (the walkthrough asks once). Elsewhere, or on a Mac without the Xcode tools to build
    the helper, it is PortAudio through sounddevice."""

    def __init__(self, on_audio: Callable[[np.ndarray], None], device: int | str | None = None):
        self.on_audio = on_audio
        self.device = device
        self.stream = None
        self.proc: subprocess.Popen | None = None
        self.running = False

    def start(self) -> None:
        if platform.system() == "Darwin":
            helper = build_helper()
            if helper:
                self._start_helper(helper)
                return
        self._start_portaudio()

    def _helper_device(self) -> str | None:
        """The helper's id for the chosen microphone (older settings stored a PortAudio number)."""
        dev = self.device
        if dev in (None, ""):
            return None
        if isinstance(dev, str) and not dev.isdigit():
            return dev
        try:
            import sounddevice as sd

            name = sd.query_devices(int(dev))["name"]
        except Exception:  # noqa: BLE001
            return None
        for d in helper_devices():
            if d["name"] == name:
                return d["id"]
        return None

    def _start_helper(self, helper: Path) -> None:
        err = open(LOGS / "audiotap.log", "ab")
        dev = self._helper_device()
        proc = subprocess.Popen([str(helper), "mic", *([dev] if dev else [])], stdout=subprocess.PIPE, stderr=err,
                                stdin=subprocess.PIPE)
        first = threading.Event()
        self.proc, self.running = proc, True

        def read() -> None:
            assert proc.stdout
            while self.running:
                data = proc.stdout.read(6400)  # 200 ms of 16 kHz float32
                if not data:
                    break
                first.set()
                self.on_audio(np.frombuffer(data[: len(data) // 4 * 4], np.float32).copy())

        threading.Thread(target=read, daemon=True, name="mic-helper").start()
        deadline = time.time() + 10
        while time.time() < deadline and not first.is_set() and proc.poll() is None:
            time.sleep(0.05)
        if first.is_set():
            return
        code = proc.poll()
        self.stop()
        if code in NOT_GRANTED:
            raise PermissionMissing("microphone", NOT_GRANTED[code])
        # no PortAudio fallback here: it would make macOS ask for the microphone again, for another program
        raise RuntimeError(f"The microphone did not start (helper exit {code}). See logs/audiotap.log.")

    def _start_portaudio(self) -> None:
        import sounddevice as sd

        device = int(self.device) if isinstance(self.device, (int, str)) and str(self.device).isdigit() else None
        info = sd.query_devices(device, "input")
        sr = int(info["default_samplerate"]) or 48000

        def cb(indata, frames, t, status):  # noqa: ANN001
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self.on_audio(resample(mono.copy(), sr))

        self.stream = sd.InputStream(device=device, channels=1, samplerate=sr, dtype="float32",
                                     blocksize=int(sr * 0.05), callback=cb)
        self.stream.start()

    def stop(self) -> None:
        self.running = False
        if self.proc is not None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            self.proc = None
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


def _helper_sha() -> str:
    return hashlib.sha256(HELPER_SRC.read_bytes()).hexdigest()


def build_helper() -> Path | None:
    """Compile the macOS helper once; returns its path or None.

    macOS ties the microphone and system audio permissions to the program's signature. A rebuilt helper is a new
    program for macOS, which then asks again, so it is rebuilt only when its source really changed (not when an
    update merely touches the file)."""
    if platform.system() != "Darwin":
        return None
    sha_file = HELPER_SHA
    try:
        if HELPER.exists() and sha_file.exists() and sha_file.read_text().strip() == _helper_sha():
            return HELPER
    except OSError:
        pass
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    contents = HELPER_APP / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(exist_ok=True)
    plist = contents / "Info.plist"
    plist.write_text(HELPER_PLIST)
    if ICON.exists():
        shutil.copyfile(ICON, contents / "Resources" / "icon.icns")
    try:
        r = subprocess.run([swiftc, "-O", "-o", str(HELPER), str(HELPER_SRC), "-framework", "ScreenCaptureKit",
                            "-framework", "CoreMedia", "-framework", "AVFoundation", "-framework", "CoreAudio",
                            "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                            "-Xlinker", str(plist)],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("cannot build audio helper: %s", e)
        return None
    if r.returncode != 0:
        log.warning("audio helper build failed: %s", r.stderr[-2000:])
        try:
            (LOGS / "audiotap-build.log").write_text(r.stderr)
        except OSError:
            pass
        return None
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", "app.lumina.audiotap",
                    str(HELPER_APP)], capture_output=True)
    for old in (OLD_HELPER, BIN / "lumina-audiotap-Info.plist"):
        old.unlink(missing_ok=True)
    try:
        sha_file.write_text(_helper_sha())
    except OSError:
        pass
    return HELPER


def helper_devices() -> list[dict]:
    """The Mac's microphones as the helper sees them (id = the device's unique id)."""
    helper = build_helper()
    if not helper:
        return []
    try:
        out = subprocess.run([str(helper), "devices"], capture_output=True, text=True, timeout=10).stdout
        return [d for d in json.loads(out) if isinstance(d, dict) and "id" in d]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def permissions() -> dict | None:
    """The macOS microphone and system audio permissions, without asking (None elsewhere or without the helper).
    Values: granted, denied, not_asked, restricted, unknown."""
    helper = build_helper()
    if not helper:
        return None
    try:
        out = subprocess.run([str(helper), "status"], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL).stdout
        return json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def request_permission(kind: str) -> dict | None:
    """Show the macOS permission window for "microphone" or "system_audio" (only if it was never answered) and
    return the permissions afterwards. Waits for the person's answer."""
    helper = build_helper()
    if not helper:
        return None
    try:
        out = subprocess.run([str(helper), "request", "mic" if kind == "microphone" else "audio"],
                             capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL).stdout
        return json.loads(out.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return permissions()


class SystemAudio:
    def __init__(self, on_audio: Callable[[np.ndarray], None], on_error: Callable[[str], None]):
        self.on_audio = on_audio
        self.on_error = on_error
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self) -> bool:
        self.running = True
        sysname = platform.system()
        if sysname == "Darwin":
            helper = build_helper()
            if not helper:
                self.on_error("System audio needs the Xcode command line tools (xcode-select --install).")
                return False
            err = open(LOGS / "audiotap.log", "ab")
            self.proc = subprocess.Popen([str(helper)], stdout=subprocess.PIPE, stderr=err, stdin=subprocess.PIPE)
            time.sleep(0.3)
            code = self.proc.poll()
            if code in NOT_GRANTED:  # the permission was never given: no window now, only a notice
                self.proc = None
                self.on_error("permission")
                return False
            self.thread = threading.Thread(target=self._read_helper, daemon=True, name="sysaudio")
        else:
            self.thread = threading.Thread(target=self._read_soundcard, daemon=True, name="sysaudio")
        self.thread.start()
        return True

    def _read_helper(self) -> None:
        assert self.proc and self.proc.stdout
        proc = self.proc
        chunk = SR // 10 * 4  # 100 ms of float32 at 16 kHz
        while self.running:
            data = proc.stdout.read(chunk)
            if not data:
                break
            self.on_audio(np.frombuffer(data[: len(data) // 4 * 4], np.float32).copy())
        if self.running:
            try:
                code = proc.wait(2)
            except subprocess.TimeoutExpired:
                code = None
            self.on_error("permission" if code in NOT_GRANTED else
                          f"System audio stopped (helper exit {code}). See logs/audiotap.log.")

    def _read_soundcard(self) -> None:
        try:
            import soundcard as sc

            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            with mic.recorder(samplerate=SR, channels=1, blocksize=SR // 10) as rec:
                while self.running:
                    x = rec.record(numframes=SR // 10)
                    mono = x[:, 0] if x.ndim > 1 else x
                    self.on_audio(mono.astype(np.float32))
        except Exception as e:  # noqa: BLE001
            log.exception("system audio failed")
            if self.running:
                self.on_error(f"System audio is not available: {e}")

    def stop(self) -> None:
        self.running = False
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            self.proc = None


def load_audio_file(path: Path) -> np.ndarray:
    """Decode any audio/video file to 16 kHz mono float32 with PyAV."""
    import av

    out: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)
