# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

(A local web UI served by the Python backend on 127.0.0.1, opened either in the browser from the terminal or inside a Tauri 2 desktop window on macOS, Windows and Linux.)

## Stack

React + TypeScript + Vite for the UI, served by a FastAPI backend. Tauri 2 (Rust) desktop shell. Static HTML/CSS/JS landing page on GitHub Pages.

## Users

Professionals and students who sit in online and in-person meetings and classes, take short notes while listening, and later need polished notes and answers drawn from their own documents. They want this without a cloud account and on whatever laptop they already own, often with no dedicated GPU.

## Product Purpose

Lumina is a local-first meeting companion and personal knowledge base: it listens to the microphone and system audio, transcribes live, expands the user's shorthand into enhanced notes, indexes their files and past meetings, and answers questions over all of it. Success means a user finishes a meeting with notes they would have written themselves on a good day, and can ask "what did we decide about X" weeks later and get a sourced answer.

## Positioning

An open, cross-platform alternative to Google AI Edge Foresight with no Google sign-in. Everything (audio, transcript, embeddings, generation) stays on the device. It exposes the knowledge base to external agents through MCP, and lets the user pick the generator: a built-in Gemma 4 (E2B / E4B / 12B) or their own Ollama / LM Studio.

## Operating Context

- Used during live calls (Zoom, Meet, Teams) and in-person conversations, often in a second window next to the call; glanceability during the meeting matters.
- Used after meetings to review, edit, export and chat.
- First launch downloads models (EmbeddingGemma 2, a speech model, optionally Gemma 4); downloads must show progress and size.
- Installed with a single terminal command; opened with `lumina` (browser) or `lumina app` (desktop window).

## Capabilities and Constraints

- Live transcription with Parakeet TDT 0.6B v3 (25 European languages) or Whisper (99 languages), on CPU.
- Split view: user shorthand left, Enhanced Notes right; Live Enhance; post-meeting summary (decisions, action items, topics).
- Live Assistance: detects questions in the conversation and suggests answers from sources.
- Knowledge sources: files and watched folders (PDF, DOCX, PPTX, XLSX, MD, TXT, HTML), tags; meetings auto-added.
- Chat over everything with optional web search toggle; chat history.
- MCP server (stdio + HTTP) for agents.
- Optional TTS to read notes and answers aloud.
- Interface in English and Spanish.
- System-audio capture needs OS permission (macOS Screen Recording, Windows loopback, Linux monitor source).

## Brand Commitments

- Name: Lumina (lowercase `lumina` for the command).
- License: free to use, modify and share; selling is not allowed (Apache-2.0 + Commons Clause, like OpenCue).
- Author: Lautaro Silva Sikora. Repo: github.com/Lautaro005/lumina. Site: lautaro005.github.io/lumina.

## Evidence on Hand

No users, testimonials, benchmarks of our own, or press yet. Do not fabricate any. Model facts (sizes, languages, licenses) come from their model cards.

## Product Principles

1. Nothing leaves the device unless the user turns on a feature that says it does (web search).
2. Runs on an ordinary laptop: CPU-first defaults, heavier options are opt-in.
3. The user's own words come first; AI output is visibly separate and editable.
4. Every answer points back to its source.
5. Open to other tools: MCP, Ollama, LM Studio, plain-file exports.

## Accessibility & Inclusion

Keyboard-operable throughout, readable contrast in light and dark themes, respects reduced motion. Bilingual UI.
