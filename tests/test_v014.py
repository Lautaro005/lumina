"""Update check, memory saver and JSON repair (0.1.4)."""

import time

import pytest
from fastapi.testclient import TestClient

from lumina import llm, memory, updater
from lumina.server import create_app

PORT = 8797


def test_versions_compare_numerically():
    assert updater.newer("0.1.10", "0.1.9")
    assert updater.newer("0.2.0", "0.1.99")
    assert not updater.newer("0.1.3", "0.1.3")
    assert not updater.newer("0.1.2", "0.1.3")


def test_update_check_reads_the_version_and_reports_availability(monkeypatch):
    class R:
        text = 'x = 1\n__version__ = "99.0.0"\n'

        def raise_for_status(self):
            pass

    monkeypatch.setattr(updater.httpx, "get", lambda *a, **k: R())
    u = updater.Updater()
    st = u.check(force=True)
    assert st["latest"] == "99.0.0" and st["available"] and st["error"] == ""


def test_update_check_failure_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise updater.httpx.ConnectError("offline")

    monkeypatch.setattr(updater.httpx, "get", boom)
    st = updater.Updater().check(force=True)
    assert not st["available"] and "Could not look" in st["error"]


def test_update_endpoints_and_status():
    with TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}") as c:
        assert set(c.get("/api/status").json()["update"]) == {"available", "latest", "state"}
        st = c.get("/api/update").json()
        assert st["current"] and "can_update" in st


@pytest.mark.parametrize("text,expected", [
    ('Here it is:\n```json\n{"a":[1,2,],"b":"x"}\n```', {"a": [1, 2], "b": "x"}),
    ('{"blocks":[{"n":1,"text":"- hi\n- there"},{"n":2,"text":"cut',
     {"blocks": [{"n": 1, "text": "- hi\n- there"}, {"n": 2, "text": "cut"}]}),
    ('{"title":"T","decisions":["a","b', {"title": "T", "decisions": ["a", "b"]}),
    ('<think>hmm</think>{"k":1}', {"k": 1}),
    ('{"a":1,"b":', {"a": 1}),
])
def test_json_from_a_small_model_is_repaired(text, expected):
    assert llm.parse_json(text) == expected


def test_json_that_is_not_json_still_fails():
    with pytest.raises(llm.LLMError):
        llm.parse_json("Sorry, I cannot do that.")


def test_complete_json_asks_again(monkeypatch):
    answers = iter(["no json here", '{"ok": true}'])
    seen = []

    def fake(messages, **kw):
        seen.append(kw)
        return next(answers)

    monkeypatch.setattr(llm, "complete", fake)
    assert llm.complete_json([{"role": "user", "content": "x"}], max_tokens=100) == {"ok": True}
    assert seen[0]["json_mode"] is True and seen[1]["max_tokens"] > 100


def test_memory_saver_unloads_only_what_is_idle(monkeypatch):
    from lumina.embed import embedder
    from lumina.stt import engine

    monkeypatch.setattr(embedder, "_session", object())
    monkeypatch.setattr(embedder, "used", time.time() - 10_000)
    monkeypatch.setattr(engine, "model_id", "parakeet-v3")
    monkeypatch.setattr(engine, "used", time.time())  # just used: stays
    freed = memory.sweep()
    assert "embeddings" in freed and "stt" not in freed
    assert not embedder.loaded
    monkeypatch.setattr(engine, "model_id", "")


def test_meetings_list_says_when_a_meeting_was_indexed():
    from lumina.db import db

    with TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}") as c:
        m = c.post("/api/meetings", json={"title": "Indexed one"}).json()
        db.update("meetings", m["id"], {"indexed": 123.0})
        rows = {r["id"]: r for r in c.get("/api/meetings").json()}
        assert rows[m["id"]]["indexed"] == 123.0
        c.delete(f"/api/meetings/{m['id']}")


def test_the_mac_helper_is_not_rebuilt_when_only_the_file_time_changes(tmp_path, monkeypatch):
    from lumina import audio

    helper = tmp_path / "lumina-audiotap"
    helper.write_bytes(b"binary")
    helper.with_suffix(".sha").write_text(audio._helper_sha())
    monkeypatch.setattr(audio, "HELPER", helper)
    monkeypatch.setattr(audio, "HELPER_SHA", helper.with_suffix(".sha"))
    monkeypatch.setattr(audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(audio.subprocess, "run", lambda *a, **k: pytest.fail("rebuilt"))
    helper.touch()  # an update touching the files must not rebuild
    assert audio.build_helper() == helper
