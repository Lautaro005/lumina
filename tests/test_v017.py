"""Summary on request (instructions and presets) and the family of apps: brain, OpenCue, AI WorkStudio (0.1.7)."""

import os
import sqlite3
import sys
import time

from fastapi.testclient import TestClient

from lumina import chat, enhance, integrations, llm, session
from lumina.db import db
from lumina.server import create_app

PORT = 8798


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def _done_meeting(title="Weekly", text="We agreed to ship on Friday. Ana will write the release notes."):
    m = db.create_meeting(title, "auto", ["team"])
    db.add_segment(m["id"], "others", 0, 3, text, "en")
    db.update("meetings", m["id"], {"status": "done", "cues": "ship date\nnotes"})
    return db.meeting(m["id"])


# ---------- summary on request -----------------------------------------------------------------------------------

def test_summary_follows_the_users_instructions(monkeypatch):
    seen = {}

    def fake(messages, **kw):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return {"title": "T", "overview": "o", "decisions": ["ship Friday"], "action_items": [], "topics": [],
                "open_questions": [], "text": "Hi team,\n\nWe ship on Friday."}

    monkeypatch.setattr(llm, "complete_json", fake)
    s = enhance.summarize("cues", "transcript", "T", "as an email to the team")
    assert s["text"].startswith("Hi team") and s["request"] == "as an email to the team"
    assert "<request>as an email to the team</request>" in seen["user"] and '"text"' in seen["system"]
    # without instructions the model is not offered a free-form field
    plain = enhance.summarize("cues", "transcript", "T")
    assert "text" not in plain and "request" not in plain


def test_a_preset_rewrites_the_current_summary(monkeypatch):
    m = _done_meeting()
    db.update("meetings", m["id"], {"summary": {"overview": "Long overview", "decisions": ["a", "b", "c", "d"]}})
    seen = {}

    def fake(cues, transcript, title, instructions="", previous=None, style=""):
        seen.update(instructions=instructions, previous=previous)
        return {"title": "", "overview": "Short", "decisions": ["a"], "action_items": [], "topics": [],
                "open_questions": [], "request": instructions}

    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    monkeypatch.setattr(enhance, "summarize", fake)
    assert session.run_summary(m["id"], preset="shorten")
    assert seen["previous"]["overview"] == "Long overview" and "shorter" in seen["instructions"]
    got = db.meeting(m["id"])["summary"]
    assert got["overview"] == "Short" and got["request"] == "shorten"


def test_summarize_endpoint_takes_instructions_and_checks_presets(monkeypatch):
    import lumina.server as server

    m = _done_meeting()
    calls = []
    monkeypatch.setattr(server, "finish_meeting", lambda mid, **kw: calls.append((mid, kw)))
    with client() as c:
        assert c.post(f"/api/meetings/{m['id']}/summarize", json={"preset": "nope"}).status_code == 400
        assert c.post(f"/api/meetings/{m['id']}/summarize",
                      json={"instructions": "only decisions", "preset": "formal"}).json()["ok"]
        assert c.post(f"/api/meetings/{m['id']}/summarize").json()["ok"]  # no body: as before
    time.sleep(0.2)
    assert calls[0] == (m["id"], {"instructions": "only decisions", "preset": "formal"})
    assert calls[1] == (m["id"], {"instructions": "", "preset": ""})


def test_exports_use_the_summary_written_as_asked():
    from lumina import export

    m = _done_meeting()
    db.update("meetings", m["id"], {"summary": {"overview": "o", "text": "Dear team, we ship Friday."}})
    md = export.markdown(m["id"])
    assert "Dear team, we ship Friday." in md


# ---------- brain -------------------------------------------------------------------------------------------------

def test_meeting_note_follows_brains_format():
    m = _done_meeting("Pricing review: Q4")
    db.update("meetings", m["id"], {"summary": {"overview": "We set the new price.", "decisions": ["$12/month"]}})
    path, md = integrations.meeting_note(db.meeting(m["id"]))
    assert path.startswith("knowledge/lumina-") and path.endswith("-pricing-review-q4.md")
    assert "type: Reference" in md and "tags: [lumina, meeting, team]" in md and f"lumina_meeting: {m['id']}" in md
    assert "## Decisions" in md and "- $12/month" in md and "## Transcript" not in md
    _, with_tr = integrations.meeting_note(db.meeting(m["id"]), "Others: hello")
    assert "## Transcript" in with_tr


def test_save_to_brain_remembers_the_note_and_updates_it(monkeypatch):
    m = _done_meeting()
    written, events = [], []
    monkeypatch.setattr(integrations, "brain_write", lambda path, content: written.append((path, content)) or path)
    monkeypatch.setattr(session.hub, "publish", events.append)
    path = session.save_to_brain(m["id"])
    assert path and db.meeting(m["id"])["brain_path"] == path
    # renaming the meeting keeps the same note in brain (it is updated, not duplicated)
    db.update("meetings", m["id"], {"title": "Another title"})
    assert session.save_to_brain(m["id"]) == path and written[1][0] == path
    assert [e["ok"] for e in events if e["type"] == "brain"] == [True, True]


def test_brain_errors_become_a_notice(monkeypatch):
    m = _done_meeting()
    events = []

    def fail(path, content):
        raise integrations.IntegrationError("brain is not installed")

    monkeypatch.setattr(integrations, "brain_write", fail)
    monkeypatch.setattr(session.hub, "publish", events.append)
    assert session.save_to_brain(m["id"]) is None
    assert events[-1] == {"type": "brain", "meeting_id": m["id"], "ok": False, "text": "brain is not installed",
                          "quiet": False}


def test_finished_meetings_go_to_brain_when_the_user_asked(monkeypatch):
    m = _done_meeting()
    saved = []
    monkeypatch.setattr(session, "run_summary", lambda *a, **k: True)
    monkeypatch.setattr(session.kb, "index_meeting", lambda mid: None)
    monkeypatch.setattr(session, "save_to_brain", lambda mid, quiet=False: saved.append((mid, quiet)))
    real_get = session.settings.get
    monkeypatch.setattr(session.settings, "get", lambda k, d=None: False if k == "brain_autosave" else real_get(k, d))
    session.finish_meeting(m["id"])
    assert saved == []
    monkeypatch.setattr(session.settings, "get", lambda k, d=None: True if k == "brain_autosave" else real_get(k, d))
    session.finish_meeting(m["id"])
    assert saved == [(m["id"], True)]


def test_ask_lumina_can_search_brain(monkeypatch):
    monkeypatch.setattr(chat.kb, "search", lambda *a, **k: [])
    monkeypatch.setattr(integrations, "brain_search",
                        lambda q, k=5: [{"title": "projects/lumina.md", "text": "Lumina is local-first", "path": "p",
                                         "url": ""}])
    got = chat.gather("what is lumina", web=False, use_brain=True)
    assert got and got[0]["kind"] == "brain" and got[0]["text"] == "Lumina is local-first"
    assert chat.gather("what is lumina", web=False, use_brain=False) == []


def test_brain_search_needs_brain_open(monkeypatch):
    import httpx

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(integrations.httpx, "post", down)
    assert integrations.brain_search("x") == []


# ---------- OpenCue -----------------------------------------------------------------------------------------------

def test_opencue_agents_are_read_from_its_database(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "opencue.db")
    con.execute("CREATE TABLE agents (id TEXT, name TEXT)")
    con.executemany("INSERT INTO agents VALUES (?, ?)", [("a2", "pacman"), ("a1", "atlas")])
    con.commit()
    con.close()
    monkeypatch.setenv("OPENCUE_HOME", str(tmp_path))
    assert [a["name"] for a in integrations.opencue_agents()] == ["atlas", "pacman"]


def test_sending_a_meeting_to_an_agent_reports_its_reply(monkeypatch):
    m = _done_meeting()
    db.update("meetings", m["id"], {"summary": {"overview": "o", "action_items": ["Ana: release notes"]}})
    sent, events = {}, []
    monkeypatch.setattr(integrations, "opencue_send", lambda agent, msg: sent.update(agent=agent, msg=msg) or "Done: 1 task")
    import lumina.server as server

    monkeypatch.setattr(server.hub, "publish", events.append)
    with client() as c:
        assert c.post(f"/api/meetings/{m['id']}/opencue", json={"agent": ""}).status_code == 400
        assert c.post(f"/api/meetings/{m['id']}/opencue", json={"agent": "atlas", "message": "make tasks"}).json()["ok"]
    deadline = time.time() + 5
    while time.time() < deadline and not any(e.get("state") == "done" for e in events):
        time.sleep(0.05)
    assert sent["agent"] == "atlas" and "make tasks" in sent["msg"] and "Ana: release notes" in sent["msg"]
    assert events[-1]["state"] == "done" and events[-1]["text"] == "Done: 1 task"


def test_integrations_endpoint_reports_each_app(monkeypatch):
    with client() as c:
        r = c.get("/api/integrations").json()
    assert set(r) == {"brain", "opencue", "workstudio"}
    assert {"installed", "running", "can_write"} <= set(r["brain"])
    assert isinstance(r["opencue"]["agents"], list) and "hub" in r["workstudio"]


# ---------- the MCP client, end to end against Lumina's own MCP server -------------------------------------------

def test_mcp_client_calls_a_real_stdio_server():
    m = _done_meeting("MCP probe")
    out = integrations.mcp_call(sys.executable, ["-m", "lumina", "mcp"], "query_meetings", {"text": "MCP probe"},
                                timeout=60, env_extra={"LUMINA_DATA": os.environ["LUMINA_DATA"]})
    assert m["id"] in out
