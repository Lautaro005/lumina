"""Live assistance that answers real questions (sources, then the web), notes that only grow, imported
recordings that say "Transcribing" at once (0.1.8)."""

import threading
import time

from fastapi.testclient import TestClient

from lumina import chat, enhance, llm, session
from lumina.db import db
from lumina.server import create_app

PORT = 8799


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


# ---------- enhanced notes ----------------------------------------------------------------------------------------

def test_notes_are_plain_text_without_markers_or_fillers():
    assert enhance.plain_note("- Budget cut to 10%\n- • Ana signs on Friday") == "Budget cut to 10%. Ana signs on Friday"
    assert enhance.plain_note("No information yet.") == ""
    assert enhance.plain_note("- Todavía no hay información") == ""
    long = "Budget cut to 10% for Q3, Ana approves it on Friday; the hiring plan was not discussed yet in this call."
    assert enhance.plain_note(long) == long


def test_a_note_only_grows():
    assert enhance.keep_growing("", "new") == "new"
    assert enhance.keep_growing("Budget cut to 10% for Q3.", "") == "Budget cut to 10% for Q3."
    assert enhance.keep_growing("Budget cut to 10% for Q3, Ana approves.", "Budget cut.") == \
        "Budget cut to 10% for Q3, Ana approves."
    assert enhance.keep_growing("Budget cut.", "Budget cut to 10% for Q3.") == "Budget cut to 10% for Q3."


def test_enhance_builds_on_the_current_notes(monkeypatch):
    m = db.create_meeting("Q3", "auto")
    db.add_segment(m["id"], "others", 0, 3, "We cut the budget to 10 percent.", "en")
    db.update("meetings", m["id"], {"cues": "budget\nhiring", "status": "done",
                                    "enhanced": [{"cue": "budget", "text": "- Budget cut to 10% for Q3.", "auto": False}]})
    seen = {}

    def fake(messages, **kw):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        # the model forgets the first note and writes a filler for the second
        return {"notes": [{"n": 1, "text": ""}, {"n": 2, "text": "- No information."}]}

    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    monkeypatch.setattr(llm, "complete_json", fake)
    blocks = session.run_enhance(m["id"])
    assert "current note: Budget cut to 10% for Q3." in seen["user"]
    assert "se habló de" in seen["system"] and "no bullets" in seen["system"]
    assert [b["text"] for b in blocks] == ["Budget cut to 10% for Q3.", ""]


# ---------- live assistance ----------------------------------------------------------------------------------------

def test_only_real_question_lines_reach_the_model():
    assert not enhance.looks_like_question("que se autoejecutan los smart contracts, contactos inteligentes.")
    assert not enhance.looks_like_question("¿Entienden?")
    assert enhance.looks_like_question("¿Qué es un smart contract exactamente?")
    assert enhance.looks_like_question("How does the new pricing work for teams")


def _live(monkeypatch, title="Derecho penal económico", cues="smart contracts"):
    m = db.create_meeting(title, "auto")
    db.update("meetings", m["id"], {"cues": cues})
    db.add_segment(m["id"], "others", 0, 3, "¿Qué es un smart contract y se ejecuta solo?", "es")
    live = session.LiveSession(m["id"])
    events = []
    monkeypatch.setattr(session.hub, "publish", events.append)
    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    return live, events


def test_a_rhetorical_or_off_topic_line_gets_no_card(monkeypatch):
    live, events = _live(monkeypatch)
    monkeypatch.setattr(enhance, "assist_triage", lambda q, c, t: "")
    monkeypatch.setattr(session.kb, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))
    live._assist("¿Entienden lo que les digo, chicos?")
    assert not [e for e in events if e["type"] == "assist"]


def test_the_card_lists_only_the_sources_the_answer_used(monkeypatch):
    live, events = _live(monkeypatch)
    seen = {}

    def triage(q, conversation, topic):
        seen["topic"] = topic
        return "¿Qué es un smart contract?"

    monkeypatch.setattr(enhance, "assist_triage", triage)
    monkeypatch.setattr(session.kb, "search", lambda *a, **k: [
        {"title": "Fallo Cantarero", "text": "llamadas telefónicas", "score": 0.5},
        {"title": "Apunte blockchain", "text": "Un smart contract es un programa que se ejecuta solo.", "score": 0.6},
    ])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"answer": "Es un programa que se ejecuta solo [2].",
                                                               "used": [2]})
    monkeypatch.setattr(chat, "web_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web")))
    live._assist("¿Qué es un smart contract y se ejecuta solo?")
    card = [e for e in events if e["type"] == "assist"][0]["assist"]
    assert card["answer"] == "Es un programa que se ejecuta solo [1]."
    assert [s["title"] for s in card["sources"]] == ["Apunte blockchain"]
    assert "Derecho penal económico" in seen["topic"] and "smart contracts" in seen["topic"]


def test_the_web_answers_when_the_sources_do_not(monkeypatch):
    live, events = _live(monkeypatch)
    monkeypatch.setattr(enhance, "assist_triage", lambda q, c, t: "¿Qué es un smart contract?")
    monkeypatch.setattr(session.kb, "search", lambda *a, **k: [{"title": "Fallo", "text": "otra cosa", "score": 0.5}])
    monkeypatch.setattr(chat, "web_search", lambda q, n=5: [
        {"title": "Smart contract - Wikipedia", "url": "https://es.wikipedia.org/wiki/Contrato_inteligente",
         "text": "Un contrato inteligente es un programa que ejecuta un acuerdo."}])
    answers = iter([
        # the sources: the model reasons out loud instead of answering -> ignored
        {"answer": "El usuario pregunta qué es un smart contract. Revisando las fuentes [1]...", "used": [1]},
        {"answer": "Es un programa que ejecuta un acuerdo por sí solo [1].", "used": [1]},
    ])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: next(answers))
    live._assist("¿Qué es un smart contract y se ejecuta solo?")
    card = [e for e in events if e["type"] == "assist"][0]["assist"]
    assert card["sources"][0]["kind"] == "web" and card["sources"][0]["url"].startswith("https://es.wikipedia")
    # the same question is not answered twice
    live.last_assist = live.last_triage = 0
    live._assist("¿Qué es un smart contract y se ejecuta solo?")
    assert len([e for e in events if e["type"] == "assist"]) == 1


def test_no_web_when_the_user_turned_it_off(monkeypatch):
    live, events = _live(monkeypatch)
    real_get = session.settings.get
    monkeypatch.setattr(session.settings, "get", lambda k, d=None: False if k == "assist_web" else real_get(k, d))
    monkeypatch.setattr(enhance, "assist_triage", lambda q, c, t: "¿Qué es un smart contract?")
    monkeypatch.setattr(session.kb, "search", lambda *a, **k: [])
    monkeypatch.setattr(chat, "web_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("web")))
    live._assist("¿Qué es un smart contract y se ejecuta solo?")
    assert not [e for e in events if e["type"] == "assist"]


def test_the_assistance_switch_is_a_setting():
    with client() as c:
        assert c.get("/api/status").json()["settings"]["assist_web"] is True


# ---------- imported recordings ------------------------------------------------------------------------------------

def test_import_says_transcribing_before_the_model_loads(monkeypatch, tmp_path):
    import lumina.server as server

    events, gate = [], threading.Event()
    monkeypatch.setattr(server.hub, "publish", events.append)
    monkeypatch.setattr(session, "transcribe_meeting_audio", lambda mid, begun=False: gate.wait(2))
    monkeypatch.setattr(server, "transcribe_meeting_audio", lambda mid, begun=False: gate.wait(2))
    with client() as c:
        r = c.post("/api/meetings/import", files={"file": ("talk.wav", b"RIFF0000", "audio/wav")}).json()
        assert r["status"] == "live" and r["audio"]
        assert {"type": "live", "meeting_id": r["id"], "state": "importing"} in events
        # while it is being transcribed, "Transcribe again" is refused instead of starting a second run
        assert c.post(f"/api/meetings/{r['id']}/retranscribe").status_code == 409
    gate.set()


def test_transcribe_again_runs_and_says_so(monkeypatch, tmp_path):
    import numpy as np

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    m = db.create_meeting("", "auto", ["imported"])
    db.update("meetings", m["id"], {"audio": str(audio), "status": "done"})
    db.add_segment(m["id"], "others", 0, 1, "old text", "en")
    events = []
    monkeypatch.setattr(session.hub, "publish", events.append)
    monkeypatch.setattr(session.engine, "load", lambda: "parakeet-v3")
    monkeypatch.setattr(session, "load_audio_file", lambda p: np.zeros(16000, np.float32))
    monkeypatch.setattr(session, "transcribe_file",
                        lambda a, emit, lang: emit(session.Event("final", "file", text="new text", t0=0, t1=1)))
    monkeypatch.setattr(session, "finish_meeting", lambda mid: None)
    with client() as c:
        assert c.post(f"/api/meetings/{m['id']}/retranscribe").json()["ok"]
    deadline = time.time() + 5
    while time.time() < deadline and db.meeting(m["id"])["status"] != "done":
        time.sleep(0.05)
    time.sleep(0.1)
    assert [s["text"] for s in db.segments(m["id"])] == ["new text"]
    states = [e["state"] for e in events if e["type"] == "live"]
    assert states[0] == "importing" and "processing" in states
    assert session.begin_transcription(m["id"])  # free again once it finished
    session._transcribing.discard(m["id"])
