"""Meeting exports: Markdown, plain-text transcript, JSON and SubRip subtitles."""

from __future__ import annotations

import json
import time

from .db import db


def _ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def markdown(mid: str) -> str:
    m = db.meeting(mid)
    if not m:
        raise KeyError(mid)
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["started"] or m["created"]))
    out = [f"# {m['title'] or 'Meeting'}", "", f"{date} · {round((m['duration'] or 0) / 60)} min"]
    if m["tags"]:
        out.append("Tags: " + ", ".join(m["tags"]))
    s = m["summary"] or {}
    if s.get("text"):  # a summary the user asked for in a form of their own (an email, a paragraph...)
        out += ["", "## Summary", "", s["text"]]
    elif s.get("overview"):
        out += ["", "## Summary", "", s["overview"]]
    for key, label in (("decisions", "Decisions"), ("action_items", "Action items"), ("open_questions", "Open questions"),
                       ("topics", "Topics")):
        if s.get(key):
            out += ["", f"### {label}", ""] + [f"- {x}" for x in s[key]]
    if m["enhanced"]:
        out += ["", "## Notes", ""]
        for b in m["enhanced"]:
            out += [f"### {b.get('cue', '')}", "", b.get("text") or "_(nothing in the transcript)_", ""]
    if m["cues"].strip():
        out += ["## My shorthand", "", m["cues"].strip(), ""]
    segs = db.segments(mid)
    if segs:
        out += ["## Transcript", ""]
        for x in segs:
            who = "You" if x["source"] == "you" else "Others"
            out.append(f"**{_ts(x['t0'])} {who}:** {x['text']}  ")
    return "\n".join(out).rstrip() + "\n"


def transcript(mid: str) -> str:
    return db.transcript_text(mid) + "\n"


def as_json(mid: str) -> str:
    m = db.meeting(mid)
    if not m:
        raise KeyError(mid)
    return json.dumps({**m, "segments": db.segments(mid), "assists": db.assists(mid)}, ensure_ascii=False, indent=2)


def srt(mid: str) -> str:
    def t(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, x in enumerate(db.segments(mid), 1):
        lines += [str(i), f"{t(x['t0'])} --> {t(max(x['t1'], x['t0'] + 0.5))}", x["text"], ""]
    return "\n".join(lines)
