"""0.2.3: llama.cpp as a generator (the Llama app or llama-server), live assistance in the language of the meeting."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lumina import enhance, llm, session
from lumina.config import settings
from lumina.db import db


class _Llama(BaseHTTPRequestHandler):
    models = [{"id": "qwen3-4b-q4"}, {"id": "nomic-embed-text"}, {"id": "gemma-4-e4b"}]
    key = ""
    seen: list[dict] = []

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.key and self.headers.get("Authorization") != f"Bearer {self.key}":
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": self.models}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def llama_server(monkeypatch):
    _Llama.key = ""
    srv = HTTPServer(("127.0.0.1", 0), _Llama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: url if k == "llamacpp_url" else real_get(k, d))
    monkeypatch.setattr(llm, "_llamacpp_seen", (0.0, ""))
    yield url
    srv.shutdown()


def test_llamacpp_lists_the_servers_models(llama_server):
    assert llm.list_models("llamacpp") == ["qwen3-4b-q4", "gemma-4-e4b"]  # the embedding model is left out


def test_llamacpp_is_a_provider(llama_server, monkeypatch):
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: "llamacpp" if k == "llm_provider" else real_get(k, d))
    base, model, headers = llm.provider_config("llamacpp")
    assert base == llama_server + "/v1" and model == "qwen3-4b-q4" and headers == {}
    assert llm.detect()["llamacpp"] is True
    assert llm.ready() == (True, "")


def test_llamacpp_uses_the_model_chosen_in_settings(llama_server, monkeypatch):
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: "gemma-4-e4b" if k == "llamacpp_model" else real_get(k, d))
    assert llm.provider_config("llamacpp")[1] == "gemma-4-e4b"


def test_llamacpp_sends_the_api_key(llama_server, monkeypatch):
    _Llama.key = "secret"
    assert llm.list_models("llamacpp") == []  # refused without the key
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: "secret" if k == "llamacpp_key" else real_get(k, d))
    monkeypatch.setattr(llm, "_llamacpp_seen", (0.0, ""))
    assert llm.list_models("llamacpp") == ["qwen3-4b-q4", "gemma-4-e4b"]
    assert llm.provider_config("llamacpp")[2] == {"Authorization": "Bearer secret"}


def test_llamacpp_is_found_on_its_usual_ports(monkeypatch):
    """With the address in Settings wrong, the Llama app's port and llama-server's are tried."""
    srv = HTTPServer(("127.0.0.1", 0), _Llama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: "http://127.0.0.1:9" if k == "llamacpp_url" else real_get(k, d))
    monkeypatch.setattr(llm, "LLAMACPP_PORTS", (f"http://127.0.0.1:{srv.server_port}",))
    monkeypatch.setattr(llm, "_llamacpp_seen", (0.0, ""))
    try:
        assert llm.llamacpp_base() == f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


def test_the_key_is_masked_for_the_ui(monkeypatch):
    real_all = settings.all
    monkeypatch.setattr(settings, "all", lambda: {**real_all(), "llamacpp_key": "abcd1234"})
    assert settings.public()["llamacpp_key"] == "••••1234"


# ---------- live assistance in the language of the meeting ---------------------------------------------------------

def test_the_language_is_guessed_from_the_words():
    assert enhance.guess_language("¿Qué es un contrato inteligente y cómo se ejecuta solo?") == "es"
    assert enhance.guess_language("What is a smart contract and how does it run by itself?") == "en"
    assert enhance.guess_language("ok") == ""
    assert enhance.language_name("es-AR") == "Spanish" and enhance.language_name("xx") == ""


def test_the_prompts_name_the_language(monkeypatch):
    seen = []

    def fake(messages, **kw):
        seen.append(messages[0]["content"])
        return {"ask": True, "question": "¿Qué es un contrato inteligente?"} if "live meeting. A line" in seen[-1] else {
            "answer": "Es un programa que se ejecuta solo [1].", "used": [1]}

    monkeypatch.setattr(llm, "complete_json", fake)
    assert enhance.assist_triage("¿Qué es un contrato inteligente y cómo funciona?", "ctx", "blockchain", lang="es")
    ans, used = enhance.assist_answer("¿Qué es un contrato inteligente?", [{"title": "t", "text": "x"}], "ctx", lang="es")
    assert used == [1] and all("The meeting is in Spanish" in s for s in seen)


def test_an_english_card_in_a_spanish_meeting_is_translated(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {
        "answer": "A smart contract is a program that runs by itself when the conditions are met [1].", "used": [1]})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "Un contrato inteligente es un programa que se ejecuta solo "
                                                          "cuando se cumplen las condiciones [1].")
    ans, used = enhance.assist_answer("¿Qué es un contrato inteligente?", [{"title": "t", "text": "x"}], "ctx", lang="es")
    assert ans.startswith("Un contrato inteligente") and used == [1]


def test_a_card_that_stays_in_english_is_left_out(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {
        "answer": "A smart contract is a program that runs by itself when the conditions are met [1].", "used": [1]})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "A smart contract is a program that runs by itself when the "
                                                          "conditions are met.")
    assert enhance.assist_answer("¿Qué es un contrato inteligente?", [{"title": "t", "text": "x"}], "ctx", lang="es") == ("", [])


def test_the_meeting_language_comes_from_the_transcript(monkeypatch):
    m = db.create_meeting("Clase", "auto", [])
    db.add_segment(m["id"], "others", 0, 3, "El contrato se ejecuta solo.", "es")
    db.add_segment(m["id"], "others", 3, 6, "Y también hay que verificarlo.", "es")
    db.add_segment(m["id"], "others", 6, 8, "ok", "en")
    live = session.LiveSession(m["id"]) if hasattr(session, "LiveSession") else None
    assert live is not None
    real_get = settings.get
    monkeypatch.setattr(settings, "get", lambda k, d=None: "auto" if k == "stt_language" else real_get(k, d))
    assert live.language("") == "es"
    monkeypatch.setattr(settings, "get", lambda k, d=None: "en" if k == "stt_language" else real_get(k, d))
    assert live.language("") == "en"  # a language fixed in Settings wins
