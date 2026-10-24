"""Prompts and parsing for Live Enhance, the post-meeting summary and Live Assistance."""

from __future__ import annotations

import re
import threading
from typing import Any

from . import llm

LANG_RULE = ("Write in the same language as the user's notes and the transcript (if they differ, follow the "
             "notes). Never translate names, numbers or quotes.")


def cue_lines(cues: str) -> list[str]:
    out = []
    for line in cues.splitlines():
        t = re.sub(r"^\s*([-*•]|\d+[.)])\s*", "", line).strip()
        if t:
            out.append(t)
    return out


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 4]
    tail = text[-(limit * 3 // 4):]
    return head + "\n[…]\n" + tail


def _tail(text: str, limit: int, step: int = 4000) -> str:
    """The end of a transcript. The start moves in big jumps, so the beginning of the prompt stays the same
    between runs and the model server can reuse what it already read."""
    if len(text) <= limit:
        return text
    cut = ((len(text) - limit) // step + 1) * step
    nl = text.find("\n", cut)
    return text[(nl + 1 if nl != -1 else cut):]


def enhance(cues: str, transcript: str, title: str = "", cancel: threading.Event | None = None) -> list[dict[str, Any]]:
    """Expands each of the user's cues into a note, row-aligned with the cue. Returns [{cue, text, auto}].

    Only what the user wrote is enhanced: Lumina does not add notes of its own (that is the summary's job)."""
    lines = cue_lines(cues)
    if not lines or not transcript.strip():
        return [{"cue": c, "text": "", "auto": False} for c in lines]
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(lines))
    system = (
        "You are Lumina, a meeting note-taker. The user types short cues (shorthand) while listening. For each cue, "
        "write the note it stands for, using only facts from the transcript: who said what, numbers, dates, "
        "decisions. Keep each note to 1-3 short markdown bullets, no headings, no preamble. If the transcript has "
        "not said anything about a cue yet, give an empty text for it. Never add notes for things the user did not "
        "write. " + LANG_RULE + ' Answer with JSON only: {"blocks":[{"n":1,"text":"- ..."}]}'
    )
    # the transcript comes first and only grows, the cues come last: the prompt prefix is reused between runs
    user = (f"Meeting: {title or 'untitled'}\n\nTranscript:\n{_tail(transcript, 24000)}\n\n"
            f"User's cues:\n{numbered}")
    data = llm.complete_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature=0.2, max_tokens=300 + 140 * len(lines), cancel=cancel)
    by_n: dict[int, str] = {}
    for b in data.get("blocks", []) if isinstance(data, dict) else []:
        try:
            by_n[int(b.get("n"))] = str(b.get("text", "")).strip()
        except (TypeError, ValueError, AttributeError):
            continue
    return [{"cue": c, "text": by_n.get(i + 1, ""), "auto": False} for i, c in enumerate(lines)]


def summarize(cues: str, transcript: str, title: str = "") -> dict[str, Any]:
    transcript = _clip(transcript, 42000)
    system = (
        "You summarize meetings for the person who attended. Be specific: names, numbers, dates. " + LANG_RULE +
        ' Answer with JSON only: {"title":"short meeting title (max 7 words)","overview":"2-3 sentences",'
        '"decisions":["..."],"action_items":["Name: task (due date if said)"],"topics":["..."],'
        '"open_questions":["..."]}. Use empty lists when there is nothing.'
    )
    user = f"Current title: {title or '(none)'}\n\nUser's notes:\n{cues or '(none)'}\n\nTranscript:\n{transcript}"
    data = llm.complete_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature=0.2, max_tokens=1800)
    if not isinstance(data, dict):
        data = {}
    clean: dict[str, Any] = {"title": str(data.get("title", "")).strip(), "overview": str(data.get("overview", "")).strip()}
    for k in ("decisions", "action_items", "topics", "open_questions"):
        v = data.get(k) or []
        clean[k] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    return clean


QUESTION_START = re.compile(
    r"^(what|who|when|where|why|how|which|can|could|do|does|did|is|are|should|would|will|"
    r"qué|que|quién|quien|cuándo|cuando|dónde|donde|por qué|cómo|como|cuál|cual|cuánto|cuanto|podemos|puedes|"
    r"pueden|hay|tenemos|sabés|sabes|"
    r"o que|quem|quando|onde|qual|quanto|"
    r"qu'est|quoi|quand|où|pourquoi|comment|quel|quelle|est-ce|"
    r"was|wer|wann|wo|warum|wie|welche|"
    r"cosa|chi|quando|dove|perché|come|quale)\b",
    re.IGNORECASE,
)


def looks_like_question(text: str) -> bool:
    t = text.strip()
    if len(t.split()) < 4:
        return False
    if "?" in t:
        return True
    return bool(QUESTION_START.match(t))


def extract_question(text: str) -> str:
    parts = re.split(r"(?<=[.!?¿¡])\s+", text.strip())
    qs = [p for p in parts if "?" in p]
    return (qs[-1] if qs else parts[-1]).strip()


def assist_answer(question: str, hits: list[dict], context: str) -> str:
    sources = "\n\n".join(f"[{i + 1}] {h.get('title', '')}\n{h['text'][:1200]}" for i, h in enumerate(hits))
    system = (
        "Someone in a live meeting just asked a question. Using only the sources, give the user a short answer "
        "they can say out loud: 1-3 sentences, then cite sources like [1]. If the sources do not answer it, reply "
        "exactly NONE. " + LANG_RULE.replace("user's notes and the transcript", "question")
    )
    user = f"Question: {question}\n\nRecent conversation:\n{context[-1500:]}\n\nSources:\n{sources}"
    ans = llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                       temperature=0.1, max_tokens=300)
    return "" if ans.strip().upper().startswith("NONE") else ans.strip()
