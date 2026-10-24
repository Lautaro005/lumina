"""Indexing pipeline, progress, removal of passages and the delete-everything endpoints (0.1.3)."""

import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lumina import embed, extract
from lumina.config import UPLOADS
from lumina.db import db
from lumina.kb import kb
from lumina.server import create_app

PORT = 8798
DIM = 8


class FakeEmbedder:
    """Stands in for EmbeddingGemma 2: deterministic vectors, counts what it was asked to embed."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.gate: threading.Event | None = None
        self.loaded = True

    def warm(self) -> None:  # noqa: D102
        pass

    @staticmethod
    def vec(text: str) -> list[float]:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.standard_normal(DIM)
        return (v / np.linalg.norm(v)).tolist()

    def documents(self, items, progress=None):  # noqa: ANN001
        self.calls.append(len(items))
        if self.gate is not None:
            self.gate.wait(10)
        out = []
        for i, (_, text) in enumerate(items, 1):
            out.append(self.vec(text))
            if progress:
                progress(i, len(items))
        return out

    def query(self, text: str, task: str = "search result"):  # noqa: ARG002
        return self.vec(text)


@pytest.fixture()
def fake(monkeypatch):
    f = FakeEmbedder()
    monkeypatch.setattr("lumina.kb.embedder", f)
    kb.clear_sources()
    kb.clear_meetings()
    yield f
    kb.clear_sources()
    kb.clear_meetings()


def wait_idle(timeout: float = 20.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        p = kb.progress()
        if not p["active"] and kb._q.empty() and kb._ready.empty():
            return
        time.sleep(0.05)
    raise AssertionError(f"indexing did not finish: {kb.progress()}")


def write_files(folder, n: int) -> None:
    folder.mkdir(exist_ok=True)
    for i in range(n):
        (folder / f"doc{i}.md").write_text(f"# Document {i}\n\n" + " ".join(f"word{i}x{j}" for j in range(900)))


def test_folder_is_indexed_and_progress_ends_complete(fake, tmp_path):
    write_files(tmp_path / "docs", 3)
    folder = kb.add_path(str(tmp_path / "docs"), watch=False)
    wait_idle()
    kids = db.q("SELECT * FROM sources WHERE parent=?", [folder["id"]])
    assert len(kids) == 3 and all(k["status"] == "ready" and k["chunks"] > 0 for k in kids)
    assert kb.counts()["documents"] == sum(k["chunks"] for k in kids)
    p = kb.progress()
    assert not p["active"] and p["total"] == p["done"] == 3 and p["fraction"] == 1.0


def test_unchanged_files_are_not_embedded_again(fake, tmp_path):
    write_files(tmp_path / "docs", 2)
    folder = kb.add_path(str(tmp_path / "docs"), watch=False)
    wait_idle()
    first = len(fake.calls)
    assert first == 2
    for kid in db.q("SELECT id FROM sources WHERE parent=?", [folder["id"]]):
        kb.enqueue(kid["id"])  # what a rescan or a touched file does
    wait_idle()
    assert len(fake.calls) == first
    kid = db.q("SELECT id FROM sources WHERE parent=?", [folder["id"]])[0]
    kb.enqueue(kid["id"], force=True)  # the "index again" button
    wait_idle()
    assert len(fake.calls) == first + 1


def test_repeated_passages_are_embedded_once(fake, tmp_path):
    f = tmp_path / "slides.md"
    f.write_text("\n\n".join(["# Same slide\n\n" + "lorem ipsum " * 230] * 6))
    kb.add_path(str(f), watch=False)
    wait_idle()
    row = db.one("SELECT * FROM sources WHERE path=?", [str(f)])
    assert row["chunks"] > 1
    assert fake.calls[-1] < row["chunks"]


def test_removing_a_folder_removes_its_passages(fake, tmp_path):
    write_files(tmp_path / "docs", 3)
    folder = kb.add_path(str(tmp_path / "docs"), watch=False)
    wait_idle()
    assert kb.counts()["documents"] > 0
    kb.remove(folder["id"])
    assert kb.counts()["documents"] == 0
    assert db.q("SELECT id FROM sources") == []


def test_removing_while_it_is_being_embedded_leaves_nothing_behind(fake, tmp_path):
    f = tmp_path / "big.md"
    f.write_text("# Big\n\n" + " ".join(f"w{i}" for i in range(3000)))
    fake.gate = threading.Event()
    row = kb.add_path(str(f), watch=False)
    time.sleep(0.5)  # the worker is now waiting inside the fake model
    kb.remove(row["id"])
    fake.gate.set()
    wait_idle()
    assert kb.counts()["documents"] == 0
    assert db.one("SELECT id FROM sources WHERE id=?", [row["id"]]) is None


def test_uploaded_copy_is_deleted_with_its_source(fake):
    copy = UPLOADS / "abc_note.txt"
    copy.write_text("hello " * 50)
    row = kb.add_path(str(copy), watch=False)
    wait_idle()
    assert copy.exists()
    kb.remove(row["id"])
    assert not copy.exists()


def test_orphaned_passages_are_purged(fake):
    col = kb.col("documents")
    col.add(ids=["ghost:0"], embeddings=[FakeEmbedder.vec("x")], documents=["x"], metadatas=[{"source_id": "ghost"}])
    assert kb.counts()["documents"] == 1
    assert kb.purge_orphans() == 1
    assert kb.counts()["documents"] == 0


def test_a_broken_file_is_reported_not_fatal(fake, tmp_path, monkeypatch):
    def boom(_path):
        raise ValueError("cannot read this")

    monkeypatch.setattr("lumina.kb.extract", boom)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4")
    row = kb.add_path(str(f), watch=False)
    wait_idle()
    got = db.one("SELECT * FROM sources WHERE id=?", [row["id"]])
    assert got["status"] == "error" and "cannot read" in got["error"]


def test_pdf_text_comes_from_pdfium_with_page_markers(tmp_path):
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    path = tmp_path / "empty.pdf"
    pdf.save(str(path))
    pdf.close()
    assert extract.extract(path) == ""  # no text, and no crash


def test_embedder_batches_by_token_budget_and_reports_progress(monkeypatch):
    class Enc:
        def __init__(self, n: int) -> None:
            self.ids = list(range(n))

    class Tok:
        def encode_batch(self, texts):  # noqa: ANN001
            return [Enc(len(t)) for t in texts]

    e = embed.Embedder()
    e._session, e._tok = object(), Tok()
    seen: list[int] = []
    monkeypatch.setattr(e, "_infer", lambda encs: (seen.append(len(encs)), np.ones((len(encs), 4)))[1])
    texts = ["a" * 100] * 40 + ["b" * 3000]
    marks: list[tuple[int, int]] = []
    vecs = e.embed(texts, lambda d, t: marks.append((d, t)))
    assert len(vecs) == 41 and sum(seen) == 41
    assert max(seen) <= embed.MAX_BATCH and seen[-1] == 1  # the long one is alone
    assert marks[-1] == (41, 41)


def client():
    return TestClient(create_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def test_delete_endpoints(fake, tmp_path):
    write_files(tmp_path / "docs", 2)
    with client() as c:
        kb.add_path(str(tmp_path / "docs"), watch=False)
        m = c.post("/api/meetings", json={"title": "To delete"}).json()
        c.patch(f"/api/meetings/{m['id']}", json={"cues": "a cue"})
        chat = c.post("/api/chats", json={}).json()
        wait_idle()
        assert kb.counts()["documents"] > 0

        assert c.delete("/api/data/nothing").status_code == 404
        assert c.delete("/api/data/chats").json()["deleted"]["chats"] >= 1
        assert c.get(f"/api/chats/{chat['id']}").status_code == 404
        assert c.get(f"/api/meetings/{m['id']}").status_code == 200  # only the chats went

        r = c.delete("/api/data/sources").json()
        assert r["deleted"]["sources"] == 3 and r["kb"]["documents"] == 0
        assert c.get("/api/sources").json() == []

        write_files(tmp_path / "again", 1)
        kb.add_path(str(tmp_path / "again"), watch=False)
        wait_idle()
        r = c.delete("/api/data/all").json()
        assert r["kb"] == {"documents": 0, "meetings": 0, "notes": 0}
        assert c.get("/api/meetings").json() == [] and c.get("/api/sources").json() == []


def test_status_carries_progress_and_notch_is_idle(fake):
    with client() as c:
        body = c.get("/api/status").json()
        assert body["index"]["active"] is False and "fraction" in body["index"]
        assert body["system"]["app"] in (True, False)
        n = c.get("/api/notch").json()
        assert n["recording"] is False and "enabled" in n
        assert c.get("/api/index").json()["total"] >= 0
