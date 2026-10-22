import json

from lumina import catalog, enhance, export, llm
from lumina.db import db
from lumina.extract import chunk


def test_stt_routing_follows_language():
    assert catalog.stt_choice("auto", "auto", "small") == "parakeet-v3"
    assert catalog.stt_choice("auto", "es", "small") == "canary-1b-v2"
    assert catalog.stt_choice("auto", "ja", "base") == "whisper-base"
    assert catalog.stt_choice("parakeet", "es", "small") == "parakeet-v3"
    assert catalog.stt_choice("whisper", "auto", "tiny") == "whisper-tiny"
    assert len(catalog.EU25) == 25 and "es" in catalog.EU25 and "en" in catalog.EU25
    assert len(catalog.WHISPER_LANGS) >= 99


def test_catalog_items_have_sizes_and_licenses():
    for item in catalog.CATALOG.values():
        assert item.size > 0, item.id
        assert item.license, item.id
        assert item.repo or item.urls, item.id
    for g in ("gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b"):
        assert g in catalog.CATALOG


def test_chunk_keeps_headings_and_size():
    text = "# Title\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(12))
    pieces = chunk(text)
    assert len(pieces) > 2
    assert all(len(p.split()) < 900 for p in pieces)
    assert pieces[0].startswith("# Title")
    assert chunk("") == []


def test_questions_are_detected_in_several_languages():
    assert enhance.looks_like_question("Can we cut the budget by ten percent?")
    assert enhance.looks_like_question("cuándo sale la nueva página de precios")
    assert not enhance.looks_like_question("ok")
    assert not enhance.looks_like_question("We shipped the release yesterday.")
    assert enhance.extract_question("Thanks. Who owns the pricing page? Great.") == "Who owns the pricing page?"


def test_cue_lines_strip_bullets():
    assert enhance.cue_lines("- budget\n\n* hires\n1. date\n") == ["budget", "hires", "date"]


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('Sure: {"blocks": []} done') == {"blocks": []}


def test_meeting_roundtrip_and_exports():
    m = db.create_meeting("Weekly", tags=["team"])
    db.add_segment(m["id"], "you", 0.0, 2.5, "Hola a todos")
    db.add_segment(m["id"], "others", 3.0, 6.0, "Who owns the pricing page?")
    db.update("meetings", m["id"], {"enhanced": [{"cue": "pricing", "text": "- Marta owns it"}],
                                    "summary": {"overview": "Short sync.", "decisions": ["Ship Nov 5"]}})
    got = db.meeting(m["id"])
    assert got["enhanced"][0]["cue"] == "pricing"
    md = export.markdown(m["id"])
    assert "# Weekly" in md and "Ship Nov 5" in md and "Others:" in md
    assert "00:00:00,000 --> 00:00:02,500" in export.srt(m["id"])
    assert json.loads(export.as_json(m["id"]))["segments"][1]["source"] == "others"
    assert "[00:03] Others: Who owns" in db.transcript_text(m["id"])
    assert any(x["id"] == m["id"] for x in db.meetings(q="pricing"))
    assert any(x["id"] == m["id"] for x in db.meetings(tag="team"))
