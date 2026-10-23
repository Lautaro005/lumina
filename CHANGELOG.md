# Changelog

All notable changes to Lumina are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses [Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-10-23

### Fixed
- **`lumina update` and the install command now really update.** Copies installed without git history (the first
  installs) were never refreshed: the installer said "Using the Lumina folder" and kept the old code, so it kept
  reporting `0.1.0`. The installer now downloads the current code once, keeps your `data/` folder and turns the folder
  into a normal git checkout, so `lumina update` works from then on. Same fix in `install.ps1`.
- **UI positioning.** The meeting header no longer squeezes the title under the buttons (the title has its own line,
  the date, tags and actions sit below it and stack on narrow windows); the sidebar collapses to icons on windows
  narrower than 1000 px so the chat has room; the meetings table no longer overflows on phone-width windows; panels of
  the notes views lost stray rounded corners; the tag field is a small pill instead of a boxed input; the warning
  banners are quieter.

### Added
- **Stop the local model from the app.** Settings → Models has a **Running now** panel with the notes and chat model
  (Gemma 4 through llama.cpp), the speech model and the search model, each with a **Stop** button that frees its
  memory (and **Start now** for the notes model). They start again by themselves the next time they are needed. The
  speech model cannot be stopped while a meeting is being recorded. Settings → Notes model also has a Stop button.
  New endpoints: `POST /api/running/{llm|stt|embeddings}/stop` and `POST /api/running/llm/start`; `/api/status` reports
  what is running.

### Changed
- **A lighter, more minimal look.** The saturated blue sidebar is gone: a white sidebar with a quiet selected item, a
  near-white desk, hairline borders instead of heavy blue rules, softer shadows, rounder corners, a soft blue used only
  for links, focus and selection, and charcoal primary buttons. The dark theme follows with neutral greys instead of
  navy.

## [0.1.1] - 2026-10-23

### Added
- **Three views for meeting notes**, chosen in Settings → General (with a small drawing of each so you can see what you
  pick): **Side by side** (the new default: your shorthand notes on the left, the live enhanced notes on the right, like
  Google AI Edge Foresight), **Cornell rows** (the 0.1.0 layout: every line of your notes with its expanded note beside
  it) and **One at a time** (a wide page with a switch between your notes and the enhanced ones).
- **EmbeddingGemma 2 as a speech option (experimental).** EmbeddingGemma 2 is an embedding model and does not
  transcribe by itself, but its audio encoder plus a small community reader
  ([`mlboydaisuke/EmbeddingGemma-2-Transcribe-LiteRT`](https://huggingface.co/mlboydaisuke/EmbeddingGemma-2-Transcribe-LiteRT),
  Apache-2.0) does: about 4 % word error rate on English read speech, runs on the CPU through `ai-edge-litert`. Pick it
  in Settings → Transcription → Speech model. English only, no punctuation, each phrase appears when the speaker pauses
  (no live partial words), about 1 GB. It is never chosen by "Automatic".
- **Models grouped by what they do** in Settings → Models: Chat and notes, Transcription, Embeddings and Read aloud,
  each with how many are downloaded.
- **A real harness for the chat** (`lumina/chat.py`): a structured system prompt (grounding, citation, language,
  style and prompt-injection rules), follow-up questions rewritten into standalone searches, retrieval that keeps at most
  two chunks per file or meeting and drops near-duplicates, a character budget per source and for the whole context,
  a digest of recent meetings for questions like "what happened in the last meeting?", the full notes of the meeting a
  chat is about, tagged sources that cannot close their own tag, trimmed history without stale citation numbers, and a
  clean-up pass that merges `[1, 2]` into `[1][2]` and removes citations to sources that do not exist.

### Changed
- **New chat design**: minimal and quiet. A centred column, your messages as soft bubbles, answers as plain text, sources
  as small chips under each answer (click to open the meeting or link), a floating composer and a greeting with
  suggestions on an empty chat.
- The default notes view is now **Side by side**. Choose **Cornell rows** in Settings → General to keep the old one.
- `ai-edge-litert` is a new dependency (Windows, Linux and Apple Silicon Macs; Intel Macs keep every other model).

## [0.1.0] - 2026-10-22

First release.

### Meetings
- Live transcription of the microphone ("You") and the computer's audio ("Others": Zoom, Meet, Teams…), on the CPU.
  macOS captures system audio with ScreenCaptureKit; Windows with WASAPI loopback; Linux with the PulseAudio/PipeWire
  monitor. When macOS has not granted the permission, the meeting shows a notice with a button to the right settings pane.
- Speech models: **Parakeet TDT 0.6B v3** (25 European languages, detects the language), **Canary 1B v2** (same 25
  languages, follows the language you choose) and **Whisper** tiny to large-v3-turbo (99 languages). Silero VAD cuts the
  audio into utterances; words show up as they are spoken.
- Transcription language selector with **Automatic** plus every supported language. With "Automatic" engine, Lumina
  picks Parakeet for automatic detection, Canary for a fixed European language and Whisper for the rest.
- Cornell-style notes: your shorthand in the cue column, **Enhanced notes** aligned row by row, extra points Lumina
  noticed, and the summary (overview, decisions, action items, open questions, topics) at the foot of the page.
- Live Enhance during the meeting (automatic or **Enhance now**) and a final pass when the meeting ends; automatic
  meeting titles.
- **Live assistance**: questions asked in the meeting that your sources can answer get a short, cited answer card.
- Pause, resume, end; import an audio or video recording; full transcript with search; tags; filters by text, tag and
  date.
- Export to Markdown, plain text, SubRip subtitles (`.srt`) and JSON.

### Knowledge
- **EmbeddingGemma 2** (8-bit ONNX, 768 dimensions, truncatable to 512/256/128) downloaded from the app, running in
  process.
- **ChromaDB** collections for documents, meetings and notes; meetings are indexed when they end.
- Sources: files, folders watched for changes (watchdog), uploads and notes. PDF, DOCX, PPTX, XLSX, Markdown, text,
  HTML, CSV, JSON and code. Tags per source.
- Chat over everything with numbered citations, history, an optional **Web search** toggle (DuckDuckGo or SearXNG) and
  "Ask about this meeting".

### Models
- Notes model: **Gemma 4 E2B, E4B or 12B** (4-bit GGUF) run by a pinned llama.cpp server (`b11823`), or **Ollama**,
  **LM Studio** or any OpenAI-compatible server. Ollama and LM Studio are detected automatically.
- If the chosen model server is closed (for example LM Studio), Lumina uses another one that works (Gemma 4, Ollama) and says so in the meeting.
- Every model shows its size, license and memory needs before downloading; downloads resume and can be cancelled.
- Optional read-aloud with **Kokoro 82M** or the system voice.

### Agents
- MCP server with `search_knowledge`, `list_sources`, `get_document`, `add_note`, `query_meetings` and `get_meeting`,
  over stdio (`lumina mcp`) and HTTP (`/mcp`, bearer token). Ready-made snippets for Claude Desktop, Claude Code and
  Cursor in Settings → Agents.

### App and install
- One-line install: `curl -fsSL https://lautaro005.github.io/lumina/install.sh | sh` (macOS, Linux) and
  `irm https://lautaro005.github.io/lumina/install.ps1 | iex` (Windows). `lumina` opens the browser, `lumina app` the
  desktop app.
- Desktop app (Tauri 2) with a tray icon; it starts and stops the local server.
- First-run setup: interface language, meeting language, notes model and downloads.
- English and Spanish interface; light and dark themes.
- Website with documentation, privacy policy, security policy, changelog, `llms.txt` and sitemap.

### Known limits
- Installers are not code-signed or notarized yet: macOS may ask you to confirm the first launch, Windows SmartScreen may
  warn.
- No speaker diarization beyond You / Others.
- The Linux desktop build is x86-64 only; other Linux machines use `lumina` in the browser.

[0.1.0]: https://github.com/Lautaro005/lumina/releases/tag/v0.1.0
