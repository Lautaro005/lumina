"""OpenBlocks in the family (0.2.2): search its vault from Ask Lumina, save meetings and answers as pages, MCP tool.

OpenBlocks itself is not installed here, so a small fake `openblocks` command speaks MCP over stdio with the same
tool names (search_pages, read_page, create_page, edit_page) and records what it is asked to write.
"""

import json
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from lumina import chat, chat_tools, integrations
from lumina.db import db
from lumina.server import create_app

PORT = 8797

FAKE = '''#!{python}
import json, sys
from pathlib import Path
from mcp.server.mcpserver import MCPServer

log = Path({log!r})
server = MCPServer("openblocks")

@server.tool()
def search_pages(query: str, limit: int = 5) -> str:
    log.open("a").write(json.dumps({{"tool": "search_pages", "query": query}}) + "\\n")
    return json.dumps([{{"path": "Ideas/Release plan.md", "title": "Release plan", "snippet": "ship"}}])

@server.tool()
def read_page(path: str) -> str:
    return "# Release plan\\nWe ship on Friday after the demo."

@server.tool()
def create_page(title: str, content: str, folder: str = "") -> str:
    log.open("a").write(json.dumps({{"tool": "create_page", "title": title, "folder": folder, "content": content}}) + "\\n")
    return "created " + folder + "/" + title + ".md"

@server.tool()
def edit_page(path: str, mode: str, content: str) -> str:
    log.open("a").write(json.dumps({{"tool": "edit_page", "path": path, "mode": mode, "content": content}}) + "\\n")
    if "gone" in path:
        raise ValueError("page not found")
    return "edited " + path

server.run("stdio")
'''


@pytest.fixture
def fake_openblocks(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    exe = tmp_path / "openblocks"
    exe.write_text(FAKE.format(python=sys.executable, log=str(log)))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(integrations, "openblocks_cli", lambda: str(exe))

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    return calls


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def _meeting():
    m = db.create_meeting("Weekly sync", "auto", ["team"])
    db.add_segment(m["id"], "others", 0, 3, "We ship on Friday.", "en")
    db.update("meetings", m["id"], {"status": "done", "cues": "ship date", "summary": {
        "overview": "Ship Friday.\nAna writes notes.", "decisions": ["Ship on Friday"], "action_items": ["Ana: release notes"],
        "open_questions": [], "topics": ["release"]}})
    return db.meeting(m["id"])


def test_meeting_page_is_obsidian_markdown():
    m = _meeting()
    path, whole, body = integrations.openblocks_meeting_note(m, "[00:00] We ship on Friday.")
    assert path.startswith("Lumina/") and path.endswith("Weekly sync.md")
    assert whole.startswith("---\n") and f"lumina_meeting: {m['id']}" in whole and "tags: [lumina, meeting, team]" in whole
    assert not body.startswith("---")
    assert "> [!info] Overview\n> Ship Friday.\n> Ana writes notes." in body
    assert "- [ ] Ana: release notes" in body and "- Ship on Friday" in body
    assert "<details><summary>Transcript</summary>" in body and "<details><summary>My shorthand</summary>" in body


def test_page_names_are_safe():
    assert integrations.clean_name('a/b:c*d?"e<f>g|h#i[j]k^l') == "a b c d e f g h i j k l"
    assert integrations.clean_name("   ") == "Untitled"


def test_search_hits_in_json_or_text():
    assert integrations._ob_hits('{"results": [{"path": "A.md", "title": "A", "snippet": "x"}]}')[0]["path"] == "A.md"  # noqa: SLF001
    assert integrations._ob_hits('["B/C.md"]')[0]["title"] == "C"  # noqa: SLF001
    plain = integrations._ob_hits("- Notes/Plan.md\n  we ship\n\n- Notes/Other.md\n  hi")  # noqa: SLF001
    assert [h["path"] for h in plain] == ["Notes/Plan.md", "Notes/Other.md"]


def test_arguments_follow_the_tools_schema():
    schema = {"properties": {"markdown": {}, "path": {}}}
    assert integrations._ob_args(schema, content="x", path="A.md", mode="replace") == {"markdown": "x", "path": "A.md"}  # noqa: SLF001


def test_ask_lumina_searches_the_vault(fake_openblocks):
    hits = integrations.openblocks_search("when do we ship", k=3)
    assert hits and hits[0]["title"] == "Release plan" and "Friday" in hits[0]["text"]
    sources = chat.gather("when do we ship", False, None, None, False, True)
    assert any(s["kind"] == "openblocks" and "Friday" in s["text"] for s in sources)
    assert not any(s["kind"] == "openblocks" for s in chat.gather("when do we ship", False, None, None, False, False))


def test_search_is_quiet_without_openblocks(monkeypatch):
    monkeypatch.setattr(integrations, "openblocks_cli", lambda: None)
    assert integrations.openblocks_search("anything") == []
    with pytest.raises(integrations.IntegrationError):
        integrations.openblocks_write("Lumina/x.md", "x")


def test_saving_a_meeting_creates_then_updates(fake_openblocks):
    m = _meeting()
    c = client()
    r = c.post(f"/api/meetings/{m['id']}/openblocks")
    assert r.status_code == 200 and r.json()["path"].startswith("Lumina/")
    first = fake_openblocks()[-1]
    assert first["tool"] == "create_page" and first["folder"] == "Lumina" and "lumina_meeting" in first["content"]
    assert db.meeting(m["id"])["openblocks_path"] == r.json()["path"]
    # saved again: the body of the same page is replaced, no new page
    assert c.post(f"/api/meetings/{m['id']}/openblocks").status_code == 200
    second = fake_openblocks()[-1]
    assert second["tool"] == "edit_page" and second["mode"] == "replace" and not second["content"].startswith("---")
    # the page was deleted in OpenBlocks: it is written again
    db.update("meetings", m["id"], {"openblocks_path": "Lumina/gone.md"})
    assert c.post(f"/api/meetings/{m['id']}/openblocks").status_code == 200
    assert [x["tool"] for x in fake_openblocks()[-2:]] == ["edit_page", "create_page"]


def test_a_failed_save_is_reported(monkeypatch):
    monkeypatch.setattr(integrations, "openblocks_cli", lambda: None)
    m = _meeting()
    r = client().post(f"/api/meetings/{m['id']}/openblocks")
    assert r.status_code == 409


def test_an_answer_becomes_a_page(fake_openblocks):
    cid = db.create_chat("Release")["id"]
    db.add_message(cid, "user", "When do we ship?", [])
    msg = db.add_message(cid, "assistant", "On Friday [1].", [{"title": "Weekly sync", "n": 1}])
    r = client().post(f"/api/chats/{cid}/messages/{msg['id']}/openblocks")
    assert r.status_code == 200
    call = fake_openblocks()[-1]
    assert call["tool"] == "create_page" and "> [!question] When do we ship?" in call["content"]
    assert "On Friday" in call["content"] and "- Weekly sync" in call["content"]


def test_the_chat_can_save_a_page_when_asked(fake_openblocks):
    text, actions = chat_tools.split_actions(
        'Done.\n```lumina-action\n{"action": "save_openblocks", "title": "Plan", "text": "- [ ] ship"}\n```')
    assert actions and actions[0]["action"] == "save_openblocks" and "lumina-action" not in text
    out = chat_tools.run(actions, {"notes": [], "sources": [], "attachments": [], "chat_id": ""})
    assert out[0]["ok"] and out[0]["state"] == "done"
    call = fake_openblocks()[-1]
    assert call["folder"] == "Lumina" and "- [ ] ship" in call["content"] and "source: lumina" in call["content"]


def test_integrations_status_lists_openblocks(fake_openblocks):
    st = client().get("/api/integrations").json()
    assert st["openblocks"]["installed"] and st["openblocks"]["can_write"] and st["openblocks"]["running"] is False


def test_the_mcp_tool_saves_from_the_app_or_alone(fake_openblocks, monkeypatch):
    from lumina import mcp_server

    m = _meeting()
    monkeypatch.setattr(mcp_server, "_api", lambda *a, **k: None)  # the app is closed
    assert mcp_server.save_meeting_to_openblocks(m["id"])["path"].startswith("Lumina/")
    assert mcp_server.save_meeting_to_openblocks("nope") == {"error": "not found"}
