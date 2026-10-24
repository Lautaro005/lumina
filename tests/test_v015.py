"""Automatic enhance, separate summary, chat stop/edit/rename, imported recordings (0.1.5)."""

import threading

import pytest
from fastapi.testclient import TestClient

from lumina import chat, enhance, llm, session
from lumina.db import db
from lumina.server import create_app

PORT = 8796


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def test_enhance_only_expands_what_the_user_wrote(monkeypatch):
    seen = {}

    def fake(messages, **kw):
        seen["user"] = messages[1]["content"]
        return {"blocks": [{"n": 1, "text": "- GPU is 24 GB"}], "extra": [{"cue": "x", "text": "- invented"}]}

    monkeypatch.setattr(llm, "complete_json", fake)
    out = enhance.enhance("- gpu\n- token", "Others: the GPU has 24 GB", "T")
    assert [b["cue"] for b in out] == ["gpu", "token"]
    assert out[0]["text"] == "- GPU is 24 GB" and out[1]["text"] == "" and not any(b["auto"] for b in out)
    # the transcript comes before the cues, so the model server can reuse the start of the prompt
    assert seen["user"].index("Transcript:") < seen["user"].index("User's cues:")


def test_enhance_without_cues_or_transcript_does_not_call_the_model(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: pytest.fail("called"))
    assert enhance.enhance("", "something said") == []
    assert enhance.enhance("gpu", "  ")[0]["text"] == ""


def test_transcript_tail_moves_in_big_steps():
    text = "\n".join(f"line {i:05d} " + "x" * 40 for i in range(1990))
    a, b = enhance._tail(text, 20000), enhance._tail(text + "\nmore", 20000)
    assert a.splitlines()[0] == b.splitlines()[0]  # one more line does not move the start
    assert len(a) <= 20000 + 4000


def test_a_cancelled_enhance_writes_nothing(monkeypatch):
    m = db.create_meeting("Cancel", "auto", [])
    db.update("meetings", m["id"], {"cues": "gpu"})
    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    cancel = threading.Event()

    def fake(*a, **k):
        cancel.set()  # the meeting ended while the model was working
        return {"blocks": [{"n": 1, "text": "- late"}]}

    monkeypatch.setattr(llm, "complete_json", fake)
    monkeypatch.setattr(db, "transcript_text", lambda *a, **k: "hello")
    session.run_enhance(m["id"], cancel=cancel)
    assert db.meeting(m["id"])["enhanced"] == []


def test_finishing_a_meeting_summarizes_but_does_not_enhance(monkeypatch):
    m = db.create_meeting("", "auto", [])
    db.add_segment(m["id"], "others", 0, 1, "We agreed to ship on Friday", "en")
    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    monkeypatch.setattr(session, "run_enhance", lambda *a, **k: pytest.fail("enhanced"))
    monkeypatch.setattr(enhance, "summarize", lambda *a, **k: {"title": "Ship plan", "overview": "o",
                                                                "decisions": [], "action_items": [], "topics": [],
                                                                "open_questions": []})
    monkeypatch.setattr(session.kb, "index_meeting", lambda mid: None)
    session.finish_meeting(m["id"])
    got = db.meeting(m["id"])
    assert got["summary"]["overview"] == "o" and got["title"] == "Ship plan"


def test_chat_rename_and_edit_last_question(monkeypatch):
    def fake_stream(messages, **kw):
        yield "Answer to: " + messages[-1]["content"].split("<question>\n")[1].split("\n")[0]

    monkeypatch.setattr(llm, "stream", fake_stream)
    monkeypatch.setattr(chat, "gather", lambda *a, **k: [])
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: q)
    with client() as c:
        cid = c.post("/api/chats", json={}).json()["id"]
        assert c.patch(f"/api/chats/{cid}", json={"title": "  "}).status_code == 400
        assert c.patch(f"/api/chats/{cid}", json={"title": "Pricing"}).json()["title"] == "Pricing"
        c.post(f"/api/chats/{cid}/messages", json={"text": "first"})
        c.post(f"/api/chats/{cid}/messages", json={"text": "second"})
        c.post(f"/api/chats/{cid}/messages", json={"text": "second edited", "replace_last": True})
        msgs = c.get(f"/api/chats/{cid}").json()["messages"]
        assert [m["content"] for m in msgs if m["role"] == "user"] == ["first", "second edited"]
        assert len(msgs) == 4 and "second edited" in msgs[-1]["content"]
        c.delete(f"/api/chats/{cid}")


def test_stopping_an_answer_keeps_the_part_written(monkeypatch):
    def slow(messages, **kw):
        yield "Half an answer"
        yield " that never finishes"

    monkeypatch.setattr(llm, "stream", slow)
    monkeypatch.setattr(chat, "gather", lambda *a, **k: [])
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: q)
    chat_row = db.create_chat("")
    gen = chat.answer(chat_row["id"], "question")
    for ev in gen:
        if ev["type"] == "delta":
            break
    gen.close()
    last = db.q("SELECT role, content FROM messages WHERE chat_id=? ORDER BY created", [chat_row["id"]])[-1]
    assert last["role"] == "assistant" and last["content"].startswith("Half an answer")


def test_imported_recording_can_be_transcribed_again(tmp_path):
    m = db.create_meeting("Imported", "auto", ["imported"])
    with client() as c:
        assert c.post(f"/api/meetings/{m['id']}/retranscribe").status_code == 409  # no recording kept
        assert c.get(f"/api/meetings/{m['id']}/audio").status_code == 404
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        db.update("meetings", m["id"], {"audio": str(audio)})
        assert c.get(f"/api/meetings/{m['id']}").json()["audio"] == str(audio)
        assert c.get(f"/api/meetings/{m['id']}/audio").status_code == 200
        c.delete(f"/api/meetings/{m['id']}")
        assert not audio.exists()
