"""Audio capture: the microphone ("you") and the computer's own output ("others").

- Microphone: PortAudio through sounddevice, on every OS.
- System audio: macOS uses the bundled ScreenCaptureKit helper (lumina-audiotap, compiled on first
  use with the Xcode command line tools); Windows (WASAPI loopback) and Linux (PulseAudio/PipeWire
  monitor) use the soundcard library.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import BIN, LOGS

log = logging.getLogger("lumina.audio")
SR = 16000
HELPER_SRC = Path(__file__).parent / "native" / "audiotap.swift"
HELPER = BIN / "lumina-audiotap"


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
    def __init__(self, on_audio: Callable[[np.ndarray], None], device: int | None = None):
        self.on_audio = on_audio
        self.device = device
        self.stream = None

    def start(self) -> None:
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
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


def build_helper() -> Path | None:
    """Compile the macOS helper once; returns its path or None."""
    if platform.system() != "Darwin":
        return None
    if HELPER.exists() and HELPER.stat().st_mtime >= HELPER_SRC.stat().st_mtime:
        return HELPER
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    try:
        r = subprocess.run([swiftc, "-O", "-o", str(HELPER), str(HELPER_SRC), "-framework", "ScreenCaptureKit",
                            "-framework", "CoreMedia"], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("cannot build audio helper: %s", e)
        return None
    if r.returncode != 0:
        log.warning("audio helper build failed: %s", r.stderr[-2000:])
        return None
    return HELPER


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
