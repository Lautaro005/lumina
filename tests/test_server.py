from fastapi.testclient import TestClient

from lumina.config import settings
from lumina.server import create_app

PORT = 8799


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def test_status_and_settings():
    with client() as c:
        r = c.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] and "models" in body
        assert body["settings"]["mcp_token"]
        r = c.put("/api/settings", json={"stt_language": "es", "not_a_setting": 1})
        assert r.json()["stt_language"] == "es"
        assert "not_a_setting" not in r.json()


def test_foreign_hosts_and_origins_are_refused():
    with client() as c:
        assert c.get("/api/status", headers={"host": "evil.example"}).status_code == 403
        assert c.post("/api/meetings", json={}, headers={"origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/meetings", json={}, headers={"origin": f"http://127.0.0.1:{PORT}"}).status_code == 200


def test_mcp_requires_token():
    with client() as c:
        assert c.post("/mcp/", json={}).status_code == 401
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
        r = c.post("/mcp", json=init, headers={"authorization": f"Bearer {settings.get('mcp_token')}",
                                               "accept": "application/json, text/event-stream"})
        assert r.status_code == 200
        assert "lumina" in r.text


def test_meeting_crud_and_export():
    with client() as c:
        m = c.post("/api/meetings", json={"title": "Demo"}).json()
        c.patch(f"/api/meetings/{m['id']}", json={"cues": "first\nsecond"})
        assert c.get(f"/api/meetings/{m['id']}").json()["cues"] == "first\nsecond"
        r = c.get(f"/api/meetings/{m['id']}/export?format=md")
        assert r.status_code == 200 and "# Demo" in r.text
        assert c.delete(f"/api/meetings/{m['id']}").json()["ok"]
        assert c.get(f"/api/meetings/{m['id']}").status_code == 404


def test_languages_endpoint():
    with client() as c:
        r = c.get("/api/languages?engine_name=parakeet").json()
        assert len(r["languages"]) == 25
        r = c.get("/api/languages?engine_name=whisper").json()
        assert len(r["languages"]) >= 99
