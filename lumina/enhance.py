"""Prompts and parsing for Live Enhance, the post-meeting summary and Live Assistance."""

from __future__ import annotations

import re
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


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


def _overlap(a: str, b: str) -> float:
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 4]
    tail = text[-(limit * 3 // 4):]
    return head + "\n[…]\n" + tail


def enhance(cues: str, transcript: str, title: str = "", final: bool = False) -> list[dict[str, Any]]:
    """Expand each cue into notes, row-aligned with the cue. Returns [{cue, text, auto}]."""
    lines = cue_lines(cues)
    transcript = _clip(transcript, 42000 if final else 24000)
    if not transcript.strip() and not lines:
        return []
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(lines)) or "(no notes yet)"
    system = (
        "You are Lumina, a meeting note-taker. The user writes short cues while listening. For each cue, write "
        "the enhanced note it stands for, using only facts from the transcript: who said what, numbers, dates, "
        "decisions and open questions. Use short markdown bullets (2-5 per cue), no headings, no preamble. "
        "If the transcript says nothing about a cue, return an empty text for it. Then add up to 4 extra "
        "blocks for important points that none of the user's cues cover (never repeat a cue), each with a 2-5 word cue. "
        + LANG_RULE + ' Answer with JSON only: {"blocks":[{"n":1,"text":"- ..."}],"extra":[{"cue":"...","text":"- ..."}]}'
    )
    user = f"Meeting: {title or 'untitled'}\n\nUser's cues:\n{numbered}\n\nTranscript:\n{transcript or '(empty)'}"
    data = llm.complete_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature=0.2, max_tokens=2500)
    by_n: dict[int, str] = {}
    for b in data.get("blocks", []) if isinstance(data, dict) else []:
        try:
            by_n[int(b.get("n"))] = str(b.get("text", "")).strip()
        except (TypeError, ValueError):
            continue
    out = [{"cue": c, "text": by_n.get(i + 1, ""), "auto": False} for i, c in enumerate(lines)]
    seen_cues = {_norm(c) for c in lines}
    seen_text = {_norm(b["text"]) for b in out if b["text"]}
    for b in data.get("extra", []) if isinstance(data, dict) else []:
        cue, text = str(b.get("cue", "")).strip(), str(b.get("text", "")).strip()
        if not cue or not text or _norm(cue) in seen_cues or _norm(text) in seen_text:
            continue
        if any(_overlap(text, t) > 0.6 for t in seen_text):
            continue
        seen_cues.add(_norm(cue))
        seen_text.add(_norm(text))
        out.append({"cue": cue, "text": text, "auto": True})
    return out


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
