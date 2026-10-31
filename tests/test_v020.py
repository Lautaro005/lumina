"""Summary layouts in Settings, web pages as sources, files taken out of a folder (0.2.0)."""

import time

import pytest
from fastapi.testclient import TestClient

from lumina import chat, enhance, llm, session, webpage
from lumina import kb as kb_mod
from lumina.db import db
from lumina.server import create_app

PORT = 8800


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


# ---------- summary layouts ---------------------------------------------------------------------------------------

def test_a_blank_custom_layout_is_the_standard_one():
    vals = {"summary_style": "custom", "summary_custom": "   "}
    assert enhance.summary_style(vals.get) == ("standard", "")
    vals["summary_custom"] = "One paragraph, then a table."
    assert enhance.summary_style(vals.get) == ("custom", "One paragraph, then a table.")
    assert enhance.summary_style({"summary_style": "nope"}.get) == ("standard", "")
    sid, text = enhance.summary_style({"summary_style": "class"}.get)
    assert sid == "class" and "## Key concepts" in text
    assert set(enhance.SUMMARY_STYLES) == {"standard", "brief", "class", "minutes", "actions"}


def test_the_layout_goes_into_the_prompt_and_the_summary(monkeypatch):
    m = db.create_meeting("Derecho constitucional", "auto")
    db.add_segment(m["id"], "others", 0, 3, "El artículo 25 garantiza un nivel de vida adecuado.", "es")
    db.update("meetings", m["id"], {"status": "done"})
    seen = {}

    def fake(messages, **kw):
        seen["system"] = messages[0]["content"]
        return {"title": "", "overview": "o", "decisions": [], "action_items": [], "topics": [], "open_questions": [],
                "text": "## Tema\nArtículo 25"}

    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    monkeypatch.setattr(llm, "complete_json", fake)
    real_get = session.settings.get
    monkeypatch.setattr(session.settings, "get", lambda k, d=None: "class" if k == "summary_style" else real_get(k, d))
    assert session.run_summary(m["id"])
    s = db.meeting(m["id"])["summary"]
    assert s["style"] == "class" and s["text"].startswith("## Tema") and "request" not in s
    assert "## Key concepts" in seen["system"] and '"text"' in seen["system"]


def test_the_standard_layout_keeps_the_classic_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "complete_json", lambda messages, **kw: seen.update(s=messages[0]["content"]) or {})
    enhance.summarize("", "transcript", "T")
    assert '"text"' not in seen["s"]


# ---------- web pages -----------------------------------------------------------------------------------------------

PAGE = """<html><head><title>Smart contracts</title><script>var x=1</script></head><body>
<nav><a>Home</a><a>Blog</a></nav>
<article><h1>Smart contracts</h1><p>A smart contract is a program that runs on a blockchain .</p>
<ul><li>It executes by itself</li><li>It cannot be changed</li></ul>
<pre>const a = 1;
const b = 2;</pre></article><footer>© 2026</footer></body></html>"""


def test_readable_keeps_the_article_and_drops_the_rest():
    title, text = webpage.readable(PAGE, "https://example.org/sc")
    assert title == "Smart contracts"
    assert "# Smart contracts" in text and "- It executes by itself" in text
    assert "runs on a blockchain." in text and "const a = 1;\nconst b = 2;" in text
    assert "Home" not in text and "var x" not in text and "©" not in text


def test_only_public_web_addresses():
    assert webpage.normalize("example.org/page") == "https://example.org/page"
    for bad in ("ftp://example.org", "file:///etc/passwd"):
        with pytest.raises(webpage.PageError):
            webpage.normalize(bad)
    for local in ("localhost", "127.0.0.1", "169.254.169.254"):
        with pytest.raises(webpage.PageError):
            webpage._check_host(local)


def _fake_page(monkeypatch):
    page = {"url": "https://example.org/sc", "title": "Smart contracts", "kind": "html",
            "text": "A smart contract is a program that runs on a blockchain. " * 20}
    monkeypatch.setattr(webpage, "fetch", lambda url: page)
    return page


def test_preview_then_save_the_page_content(monkeypatch):
    _fake_page(monkeypatch)
    with client() as c:
        p = c.post("/api/sources/web/preview", json={"url": "example.org/sc"}).json()
        assert p["title"] == "Smart contracts" and p["chars"] > 400 and len(p["excerpt"]) == 400
        row = c.post("/api/sources/web", json={"url": p["url"], "mode": "content"}).json()
        assert row["kind"] == "web" and row["path"] == "https://example.org/sc" and row["ext"] == "content"
        assert db.one("SELECT text FROM sources WHERE id=?", [row["id"]])["text"].startswith("A smart contract")
        # the same address again updates the same source
        again = c.post("/api/sources/web", json={"url": p["url"], "mode": "content"}).json()
        assert again["id"] == row["id"]
        assert c.post("/api/sources/web", json={"url": p["url"], "mode": "other"}).status_code == 400


def test_save_a_summary_of_the_page(monkeypatch):
    _fake_page(monkeypatch)
    monkeypatch.setattr(llm, "ready", lambda: (True, ""))
    monkeypatch.setattr(webpage, "summarize", lambda page: "## Smart contracts\n- Programs that run by themselves.")
    db.x("DELETE FROM sources WHERE kind='web'")
    with client() as c:
        row = c.post("/api/sources/web", json={"url": "https://example.org/sc", "mode": "summary"}).json()
    assert row["status"] == "summarizing" and row["ext"] == "summary"
    deadline = time.time() + 5
    while time.time() < deadline and not db.one("SELECT text FROM sources WHERE id=?", [row["id"]])["text"]:
        time.sleep(0.05)
    assert db.one("SELECT text FROM sources WHERE id=?", [row["id"]])["text"].startswith("## Smart contracts")


def test_a_bad_page_is_a_clear_error(monkeypatch):
    def fail(url):
        raise webpage.PageError("The page answered 404.")

    monkeypatch.setattr(webpage, "fetch", fail)
    with client() as c:
        r = c.post("/api/sources/web/preview", json={"url": "https://example.org/missing"})
    assert r.status_code == 422 and r.json()["detail"] == "The page answered 404."


def test_saved_pages_are_cited_with_their_address(monkeypatch):
    monkeypatch.setattr(chat.kb, "search", lambda *a, **k: [
        {"collection": "documents", "title": "Smart contracts", "text": "A program", "score": 0.6,
         "path": "https://example.org/sc", "source_id": "x"}])
    got = chat.gather("what is a smart contract", web=False)
    assert got[0]["url"] == "https://example.org/sc"


def test_agents_can_read_a_saved_page():
    from lumina import mcp_server

    row = kb_mod.kb.add_web("https://example.org/doc", "Doc", "Body of the page", mode="content")
    assert mcp_server.get_document(row["id"])["text"] == "Body of the page"


# ---------- folders: a file taken out stays out ------------------------------------------------------------------------

def test_a_file_taken_out_of_a_folder_stays_out(tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "b.txt").write_text("beta")
    folder = kb_mod.kb.add_path(str(tmp_path), watch=False)
    kids = {r["title"]: r for r in db.q("SELECT * FROM sources WHERE parent=?", [folder["id"]])}
    with client() as c:
        assert c.delete(f"/api/sources/{kids['a.txt']['id']}").json()["ok"]
        assert db.one("SELECT status FROM sources WHERE id=?", [kids["a.txt"]["id"]])["status"] == "excluded"
        kb_mod.kb._scan_folder(folder["id"], tmp_path)  # the next scan leaves it out
        assert db.one("SELECT status FROM sources WHERE id=?", [kids["a.txt"]["id"]])["status"] == "excluded"
        kb_mod.kb.enqueue(kids["a.txt"]["id"], force=True)  # a folder reindex does not bring it back either
        assert db.one("SELECT status FROM sources WHERE id=?", [kids["a.txt"]["id"]])["status"] == "excluded"
        # its own "Index again" button does
        c.post(f"/api/sources/{kids['a.txt']['id']}/reindex")
        assert db.one("SELECT status FROM sources WHERE id=?", [kids["a.txt"]["id"]])["status"] != "excluded"
        # removing the folder itself still removes everything
        c.delete(f"/api/sources/{folder['id']}")
    assert db.q("SELECT id FROM sources WHERE parent=?", [folder["id"]]) == []
