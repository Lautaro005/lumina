"""Audio capture: the microphone ("you") and the computer's own output ("others").

- Microphone: PortAudio through sounddevice, on every OS.
- System audio: macOS uses the bundled ScreenCaptureKit helper (lumina-audiotap, compiled on first
  use with the Xcode command line tools); Windows (WASAPI loopback) and Linux (PulseAudio/PipeWire
  monitor) use the soundcard library.
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
HELPER = BIN / "lumina-audiotap"
HELPER_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>app.lumina.audiotap</string>
<key>CFBundleName</key><string>Lumina</string>
<key>NSMicrophoneUsageDescription</key>
<string>Lumina transcribes your meetings on this Mac. The audio never leaves the computer.</string>
<key>NSAudioCaptureUsageDescription</key>
<string>Lumina records the other people in your calls to transcribe them on this Mac.</string>
</dict></plist>
"""


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
    """The microphone. On a Mac the default microphone goes through the same signed helper that records the
    system audio, so macOS asks for the permission once, for one program. Elsewhere (or if the helper is not
    there) it is PortAudio through sounddevice."""

    def __init__(self, on_audio: Callable[[np.ndarray], None], device: int | None = None):
        self.on_audio = on_audio
        self.device = device
        self.stream = None
        self.proc: subprocess.Popen | None = None
        self.running = False

    def start(self) -> None:
        if platform.system() == "Darwin" and self.device is None and self._start_helper():
            return
        self._start_portaudio()

    def _start_helper(self) -> bool:
        helper = build_helper()
        if not helper:
            return False
        err = open(LOGS / "audiotap.log", "ab")
        proc = subprocess.Popen([str(helper), "mic"], stdout=subprocess.PIPE, stderr=err, stdin=subprocess.PIPE)
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
        # the first time macOS shows its permission window; wait for the answer
        deadline = time.time() + 120
        while time.time() < deadline and not first.is_set() and proc.poll() is None:
            time.sleep(0.05)
        if first.is_set():
            return True
        code = proc.poll()
        self.stop()
        if code == 4:
            raise PermissionError("Microphone access is off for Lumina. "
                                  "Turn it on in System Settings → Privacy & Security → Microphone.")
        log.warning("mic helper gave no audio (exit %s); using PortAudio", code)
        return False

    def _start_portaudio(self) -> None:
        import sounddevice as sd

        info = sd.query_devices(self.device, "input")
        sr = int(info["default_samplerate"]) or 48000

        def cb(indata, frames, t, status):  # noqa: ANN001
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self.on_audio(resample(mono.copy(), sr))

        self.stream = sd.InputStream(device=self.device, channels=1, samplerate=sr, dtype="float32",
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

    macOS ties the microphone and screen-recording permissions to the program's signature. A helper that is
    rebuilt gets a new signature and macOS asks again, so it is rebuilt only when its source really changed
    (not when an update merely touches the file) and it is signed with a fixed name."""
    if platform.system() != "Darwin":
        return None
    sha_file = HELPER.with_suffix(".sha")
    try:
        if HELPER.exists() and sha_file.exists() and sha_file.read_text().strip() == _helper_sha():
            return HELPER
    except OSError:
        pass
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    plist = BIN / "lumina-audiotap-Info.plist"
    plist.write_text(HELPER_PLIST)
    try:
        r = subprocess.run([swiftc, "-O", "-o", str(HELPER), str(HELPER_SRC), "-framework", "ScreenCaptureKit",
                            "-framework", "CoreMedia", "-framework", "AVFoundation",
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
                    str(HELPER)], capture_output=True)
    try:
        sha_file.write_text(_helper_sha())
    except OSError:
        pass
    return HELPER


def permissions() -> dict | None:
    """The macOS microphone and screen-recording permissions as the helper sees them (None elsewhere)."""
    helper = build_helper()
    if not helper:
        return None
    try:
        out = subprocess.run([str(helper), "status"], capture_output=True, text=True, timeout=10).stdout
        return json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


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
            self.thread = threading.Thread(target=self._read_helper, daemon=True, name="sysaudio")
        else:
            self.thread = threading.Thread(target=self._read_soundcard, daemon=True, name="sysaudio")
        self.thread.start()
        return True

    def _read_helper(self) -> None:
        assert self.proc and self.proc.stdout
        chunk = 48000 // 10 * 4  # 100 ms of float32 at 48 kHz
        while self.running:
            data = self.proc.stdout.read(chunk)
            if not data:
                break
            x = np.frombuffer(data[: len(data) // 4 * 4], np.float32)
            self.on_audio(resample(x, 48000))
        if self.running:
            self.on_error("permission")

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
