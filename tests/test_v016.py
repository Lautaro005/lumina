"""macOS permissions asked once (never while recording), helper devices, uv.lock out of git (0.1.6)."""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from lumina import audio, session
from lumina.db import db
from lumina.server import create_app

PORT = 8797
ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"X-Lumina": "1"}


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def test_permissions_are_not_needed_off_macos(monkeypatch):
    monkeypatch.setattr(audio.platform, "system", lambda: "Linux")
    import lumina.server as server

    monkeypatch.setattr(server.platform, "system", lambda: "Linux")
    c = client()
    assert c.get("/api/permissions").json() == {"needed": False}
    assert c.post("/api/permissions/request", json={"kind": "microphone"}, headers=HEADERS).json() == {"needed": False}


def test_permissions_on_macos_read_and_ask_through_the_helper(monkeypatch):
    import lumina.server as server

    monkeypatch.setattr(server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(server, "audio_permissions",
                        lambda: {"microphone": "granted", "system_audio": "not_asked", "method": "tap"})
    asked = []

    def fake_request(kind):
        asked.append(kind)
        return {"microphone": "granted", "system_audio": "granted", "method": "tap"}

    monkeypatch.setattr(server, "request_permission", fake_request)
    c = client()
    p = c.get("/api/permissions").json()
    assert p["needed"] and p["helper"] and p["system_audio"] == "not_asked"
    r = c.post("/api/permissions/request", json={"kind": "system_audio"}, headers=HEADERS).json()
    assert asked == ["system_audio"] and r["system_audio"] == "granted"
    # without the helper (no Xcode tools) the page says so instead of asking
    monkeypatch.setattr(server, "audio_permissions", lambda: None)
    assert c.get("/api/permissions").json() == {"needed": True, "helper": False}


def test_recording_with_a_missing_permission_shows_a_notice_not_a_window(monkeypatch):
    m = db.create_meeting("perm")
    events = []
    monkeypatch.setattr(session.hub, "publish", events.append)
    monkeypatch.setattr(session.engine, "load", lambda: "parakeet-v3")

    class FakeTr:
        def __init__(self, *a):
            pass

        def stream(self, name):
            return type("S", (), {"feed": lambda self, x: None})()

        def stop(self):
            pass

    class NoMic:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise audio.PermissionMissing("microphone", "not_asked")

    monkeypatch.setattr(session, "Transcriber", FakeTr)
    monkeypatch.setattr(session, "Mic", NoMic)
    monkeypatch.setattr(session.settings, "get", lambda k, d=None: False if k == "system_audio" else d)
    live = session.LiveSession(m["id"])
    try:
        live.start()
        raise AssertionError("should not start without any source")
    except RuntimeError as e:
        assert "Settings → Permissions" in str(e)
    perm = [e for e in events if e.get("type") == "permission"]
    assert perm and perm[0]["kind"] == "microphone" and perm[0]["state"] == "not_asked"


def test_permission_messages():
    assert "System Settings" in str(audio.PermissionMissing("microphone", "denied"))
    assert "Settings → Permissions" in str(audio.PermissionMissing("system_audio", "not_asked"))
    assert audio.NOT_GRANTED == {4: "denied", 5: "not_asked"}


def test_old_portaudio_microphone_number_maps_to_the_helper_device(monkeypatch):
    import sys
    import types

    fake_sd = types.SimpleNamespace(query_devices=lambda i=None, kind=None: {"name": "USB Mic"})
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(audio, "helper_devices", lambda: [{"id": "AppleUSB:1", "name": "USB Mic"}])
    assert audio.Mic(lambda x: None, 3)._helper_device() == "AppleUSB:1"
    assert audio.Mic(lambda x: None, "3")._helper_device() == "AppleUSB:1"
    assert audio.Mic(lambda x: None, "BuiltInMicrophoneDevice")._helper_device() == "BuiltInMicrophoneDevice"
    assert audio.Mic(lambda x: None, None)._helper_device() is None


def test_helper_is_an_app_bundle_with_both_usage_texts():
    assert audio.HELPER.parts[-4:] == ("LuminaAudio.app", "Contents", "MacOS", "lumina-audiotap")
    for key in ("NSMicrophoneUsageDescription", "NSAudioCaptureUsageDescription", "LSUIElement", "app.lumina.audiotap"):
        assert key in audio.HELPER_PLIST
    src = audio.HELPER_SRC.read_text()
    # recording never asks: the capture modes exit with 4/5 instead of requesting access
    assert "responsibility_spawnattrs_setdisclaim" in src and "AudioHardwareCreateProcessTap" in src
    capture = src[src.index('case "mic":'):]
    assert "requestAccess" not in capture and "exit(5)" in capture


def test_uv_lock_is_not_tracked_and_updates_discard_it():
    assert "uv.lock" in (ROOT / ".gitignore").read_text().split()
    tracked = subprocess.run(["git", "ls-files", "uv.lock"], cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode == 0:
        assert tracked.stdout.strip() == ""
    for script in ("lumina.sh", "install.sh"):
        text = (ROOT / script).read_text()
        assert text.index("checkout --quiet -- uv.lock") < text.index("pull --ff-only")
