"""Index again that always runs (and goes first), starting after an update, Ask Lumina with attachments and
actions on notes (0.2.1)."""

import time

from fastapi.testclient import TestClient

from lumina import chat, chat_tools, cli
from lumina.db import db
from lumina.kb import kb
from lumina.server import create_app

PORT = 8801


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def _fake_model(monkeypatch, answer: str, seen: dict | None = None):
    monkeypatch.setattr(chat.kb, "search", lambda *a, **k: [])
    monkeypatch.setattr(chat.kb, "counts", lambda: {"meetings": 0, "documents": 0, "notes": 0})

    def stream(messages, **kw):
        if seen is not None:
            seen["messages"] = messages
        yield answer

    monkeypatch.setattr(chat.llm, "stream", stream)


# ---------- index again -------------------------------------------------------------------------------------------

def test_a_forced_request_is_not_lost_to_one_already_waiting(monkeypatch):
    note = kb.add_note("Queue probe", "text")
    taken = []
    monkeypatch.setattr(kb._q, "put", lambda item: taken.append(item))
    with kb._plock:
        kb._queued.add(("source", note["id"]))  # a plain scan already queued it
    kb.enqueue(note["id"], force=True, urgent=True)
    assert ("source", note["id"]) in kb._forced
    assert taken and taken[-1][0] == 0 and taken[-1][3] == note["id"]  # at the front of the queue
    with kb._plock:
        kb._queued.discard(("source", note["id"]))
        kb._forced.discard(("source", note["id"]))


def test_index_again_answers_with_the_new_state():
    note = kb.add_note("Reindex probe", "some text")
    with client() as c:
        r = c.post(f"/api/sources/{note['id']}/reindex").json()
        assert r["ok"] and r["status"] in ("pending", "indexing", "ready", "error")
        assert c.post("/api/sources/nope/reindex").status_code == 404


# ---------- starting after an update ----------------------------------------------------------------------------------

def test_an_older_lumina_on_the_port_is_asked_to_close(monkeypatch):
    asked = []
    monkeypatch.setattr(cli, "_running_version", lambda port: "0.0.1")
    monkeypatch.setattr("httpx.post", lambda url, **kw: asked.append(url))
    monkeypatch.setattr(cli, "_wait_port", lambda port, s: True)
    assert cli._free_port_for_us(47790)
    assert asked == ["http://127.0.0.1:47790/api/quit"]
    # the same version is used as it is
    monkeypatch.setattr(cli, "_running_version", lambda port: cli.__version__)
    assert not cli._free_port_for_us(47790)


def test_a_port_that_frees_up_is_waited_for(monkeypatch):
    states = iter([False, False, True])
    monkeypatch.setattr(cli, "_port_free", lambda port: next(states))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli._wait_port(47790, 5)


def test_the_update_script_waits_for_the_old_server():
    import inspect

    from lumina import updater

    src = inspect.getsource(updater.Updater._spawn_unix)
    assert "gone()" in src and "sleep 2" not in src


# ---------- actions ---------------------------------------------------------------------------------------------------

def test_action_blocks_are_split_from_the_answer():
    text = 'Here it is.\n```lumina-action\n{"action": "create_note", "title": "Plan", "text": "# Plan"}\n```\n'
    clean, actions = chat_tools.split_actions(text)
    assert clean == "Here it is." and actions == [{"action": "create_note", "title": "Plan", "text": "# Plan"}]
    assert chat_tools.split_actions("```json\n{\"a\": 1}\n```")[1] == []  # other JSON stays in the answer
    assert chat_tools.asked_to_act("Creá una nota con el resumen") and chat_tools.asked_to_act("save this as a note")
    assert not chat_tools.asked_to_act("¿Qué dijo Ana sobre el precio?")


def test_the_chat_creates_a_note_when_asked(monkeypatch):
    seen = {}
    _fake_model(monkeypatch, 'Listo.\n```lumina-action\n{"action":"create_note","title":"Resumen Q4","text":"- Precio 12"}\n```',
                seen)
    c = db.create_chat()
    done = list(chat.answer(c["id"], "Creá una nota con el resumen del Q4", web=False))[-1]["message"]
    assert done["content"] == "Listo." and done["actions"][0]["ok"] and done["actions"][0]["state"] == "done"
    row = db.one("SELECT * FROM sources WHERE id=?", [done["actions"][0]["id"]])
    assert row["kind"] == "note" and row["title"] == "Resumen Q4" and row["text"] == "- Precio 12"
    assert "# Actions" in seen["messages"][0]["content"] and "<notes>" in seen["messages"][-1]["content"]


def test_a_document_cannot_make_the_chat_act(monkeypatch):
    _fake_model(monkeypatch, 'Ana dijo 12.\n```lumina-action\n{"action":"create_note","title":"x","text":"y"}\n```')
    c = db.create_chat()
    before = db.one("SELECT COUNT(*) AS n FROM sources")["n"]
    done = list(chat.answer(c["id"], "¿Qué dijo Ana sobre el precio?", web=False))[-1]["message"]
    assert done["actions"] == [] and done["content"] == "Ana dijo 12."
    assert db.one("SELECT COUNT(*) AS n FROM sources")["n"] == before


def test_deleting_a_note_waits_for_the_user(monkeypatch):
    note = kb.add_note("Borrador viejo", "texto")
    notes = chat_tools.notes_list()
    n = [x["id"] for x in notes].index(note["id"]) + 1
    _fake_model(monkeypatch, f'¿La borro?\n```lumina-action\n{{"action":"delete_note","note":{n}}}\n```')
    c = db.create_chat()
    done = list(chat.answer(c["id"], "Borrá la nota Borrador viejo", web=False))[-1]["message"]
    assert done["actions"][0]["state"] == "confirm" and db.one("SELECT id FROM sources WHERE id=?", [note["id"]])
    with client() as cl:
        r = cl.post(f"/api/chats/{c['id']}/messages/{done['id']}/actions/0", json={"op": "confirm"}).json()
    assert r["state"] == "done" and not db.one("SELECT id FROM sources WHERE id=?", [note["id"]])


def test_an_edited_note_can_be_undone(monkeypatch):
    note = kb.add_note("Lista", "uno")
    n = [x["id"] for x in chat_tools.notes_list()].index(note["id"]) + 1
    _fake_model(monkeypatch, f'Hecho.\n```lumina-action\n{{"action":"update_note","note":{n},"text":"uno\\ndos"}}\n```')
    c = db.create_chat()
    done = list(chat.answer(c["id"], "Agregá dos a la nota Lista", web=False))[-1]["message"]
    assert db.one("SELECT text FROM sources WHERE id=?", [note["id"]])["text"] == "uno\ndos"
    with client() as cl:
        cl.post(f"/api/chats/{c['id']}/messages/{done['id']}/actions/0", json={"op": "undo"})
        assert cl.post(f"/api/chats/{c['id']}/messages/{done['id']}/actions/0",
                       json={"op": "undo"}).status_code == 409  # only once
    assert db.one("SELECT text FROM sources WHERE id=?", [note["id"]])["text"] == "uno"


def test_a_wrong_action_is_reported_not_raised():
    out = chat_tools.run([{"action": "update_note", "note": 99, "text": "x"}],
                         {"notes": [], "sources": [], "attachments": [], "chat_id": ""})
    assert out[0]["ok"] is False and "not in the list" in out[0]["error"]


# ---------- attachments -----------------------------------------------------------------------------------------------

def _wait_ready(aid):
    deadline = time.time() + 10
    while time.time() < deadline:
        a = db.one("SELECT * FROM attachments WHERE id=?", [aid])
        if a["status"] != "reading":
            return a
        time.sleep(0.05)
    return db.one("SELECT * FROM attachments WHERE id=?", [aid])


def test_an_attached_file_is_read_and_used_in_the_answer(monkeypatch):
    seen = {}
    _fake_model(monkeypatch, "El plan cuesta 12 [1].", seen)
    c = db.create_chat()
    with client() as cl:
        att = cl.post(f"/api/chats/{c['id']}/attachments",
                      files=[("files", ("plan.md", b"# Plan\nEl plan cuesta 12 USD.", "text/markdown"))]).json()[0]
        assert att["status"] == "reading" and att["kind"] == "document"
        assert _wait_ready(att["id"])["status"] == "ready"
        assert cl.post(f"/api/chats/{c['id']}/attachments",
                       files=[("files", ("x.exe", b"MZ", "application/octet-stream"))]).status_code == 415
        events = list(chat.answer(c["id"], "¿Cuánto cuesta?", web=False, attachment_ids=[att["id"]]))
        chat_row = cl.get(f"/api/chats/{c['id']}").json()
    assert 'type="attachment" title="plan.md"' in seen["messages"][-1]["content"]
    assert events[-1]["message"]["sources"][0]["title"] == "plan.md"
    user = [m for m in chat_row["messages"] if m["role"] == "user"][-1]
    assert user["attachments"][0]["name"] == "plan.md" and chat_row["attachments"][0]["chars"] > 10


def test_an_attachment_can_be_kept_in_sources():
    c = db.create_chat()
    with client() as cl:
        att = cl.post(f"/api/chats/{c['id']}/attachments", data={"save": "1"},
                      files=[("files", ("apunte.txt", b"Contrato inteligente: programa que se ejecuta solo.", "text/plain"))]
                      ).json()[0]
        got = _wait_ready(att["id"])
        assert got["source_id"] and db.one("SELECT title FROM sources WHERE id=?", [got["source_id"]])["title"] == "apunte.txt"
        # removing it from the chat keeps the file Sources reads
        cl.delete(f"/api/chats/{c['id']}/attachments/{att['id']}")
    assert db.one("SELECT id FROM sources WHERE id=?", [got["source_id"]])


def test_long_attachments_send_the_passages_that_matter():
    text = "\n\n".join([f"Capítulo {i}. " + "relleno " * 150 for i in range(40)] + ["La prescripción es de cinco años."])
    got = chat_tools.relevant(text, "¿Cuál es el plazo de prescripción?", 3000)
    assert "prescripción es de cinco años" in got and got.startswith("Capítulo 0") and len(got) < 3600
    assert chat_tools.wants_summary("Resumí el PDF") and not chat_tools.wants_summary("¿Quién firmó?")
