# Changelog

All notable changes to Lumina are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses [Semantic Versioning](https://semver.org/).

## [0.1.7] - 2026-10-28

### Added
- **Summary the way you want it.** Under the summary of a finished meeting or an imported recording there is a box to
  say how you want it ("as an email to the team", "only the decisions, in English", "a table of tasks") and a
  **Regenerate summary** button, plus one-click **Expand**, **Shorten**, **Simplify**, **More formal** and **Action
  items first**, which rewrite the summary that is there. When you ask for a form the usual sections cannot hold, the
  summary is shown (and exported, indexed and saved to brain) in that form. `POST /api/meetings/{id}/summarize` takes
  `{"instructions", "preset"}`.
- **Connected apps** (Settings → Connected apps): Lumina now works with the rest of the family on this computer.
  - **brain**: **Save to brain** in the ⋯ menu writes the meeting (summary, decisions, action items, notes, optionally
    the transcript) to `knowledge/lumina-<date>-<title>.md` through brain's own MCP server; a saved meeting is updated
    when its summary changes, and a switch saves every finished meeting. Ask Lumina has a **brain** pill that adds
    brain's search results (profile, projects, saved pages) to the sources while brain is open.
  - **OpenCue**: **Send to an OpenCue agent…** in the ⋯ menu sends the summary, decisions and action items, with what
    you want done, to the agent you pick; its reply appears under the summary.
  - **AI WorkStudio**: shows whether it is installed, opens it, and tells whether Lumina's tools are in WorkStudio's
    MCP server.
- **Two MCP tools** for agents (and WorkStudio's hub): `rewrite_summary` and `save_meeting_to_brain`.

### Changed
- The summary's header shows "Writing the summary…" while it runs; the button lives next to the instructions box.

## [0.1.6] - 2026-10-25

### Changed
- **macOS asks for permissions once, in the walkthrough.** The first-run setup has a new last step, **Permissions**, with
  an **Allow** button for the microphone and one for the computer's audio; the same panel is in Settings → Permissions.
  Starting or resuming a meeting never opens a permission window any more: if a permission is missing, the meeting
  shows a notice with a link to Settings (`GET /api/permissions`, `POST /api/permissions/request`).
- **The permissions survive updates.** Following Apple's documentation (macOS keeps a privacy decision for the code
  signature of the *responsible* app, and an ad hoc signature is tied to one exact build), the decision was being
  recorded for `Lumina.app`, which every update replaced and re-signed, so macOS asked again. The audio helper now
  lives in its own small bundle, `data/bin/LuminaAudio.app`, starts itself as its own responsible process and is
  rebuilt only when its source changes, so macOS remembers the answer for it.
- **Computer audio without screen recording** on macOS 14.2 and later: a Core Audio process tap replaces
  ScreenCaptureKit, so the only permission is System Audio Recording (no "screen recording" window, no monthly
  reminder). macOS 13 to 14.1 keep ScreenCaptureKit.
- **No second microphone path on a Mac.** When the helper cannot start the microphone, Lumina no longer falls back to
  PortAudio (which made macOS ask again, for another program); the microphone list and the chosen microphone now come
  from the helper too.
- **Sidebar:** the Recording, indexing and Update cards sit at the bottom, right above "Everything stays on this
  computer", which is a little smaller so it fits on one line.

### Fixed
- **Updates failing because of `uv.lock`.** The file is no longer tracked in git (each computer's uv writes its own, and
  a different uv version rewriting it blocked `git pull`); `lumina update`, the in-app update and the installers
  discard a local copy before pulling. Coming from 0.1.5 or earlier, if the update still fails, delete
  `~/.lumina/uv.lock` once.
- **Tablet layout.** Fields in two-column settings no longer drift apart when the neighbour has a hint (Settings →
  Knowledge: *Vector size* and *Web search engine* now line up); the notes-view cards keep their titles on one line;
  the Settings index hides below 1100 px so the settings get the width; the meeting header keeps the buttons on the
  date row and shares the left edge with the notes panels.

## [0.1.5] - 2026-10-24

### Added
- **Regenerate summary.** A finished meeting has a **Regenerate summary** button in its Summary (and **Write the summary**
  when there is none). `POST /api/meetings/{id}/summarize` now writes only the summary and the title.
- **Imported recordings have their own page.** The recording is kept; the page shows the player, the summary and the
  transcript (searchable, copyable), with **Transcribe again** (`POST /api/meetings/{id}/retranscribe`,
  `GET /api/meetings/{id}/audio`). No notes columns: an imported recording has nothing to enhance.
- **Stop in the chat.** While Lumina writes an answer, a Stop button sits above Send; the part already written is kept.
- **Edit and ask again.** Under your last question: **Answer again** and **Edit question**.
- **Right-click on chats and meetings** opens a floating menu with **Rename** and **Delete** (chat history and the
  meetings list). `PATCH /api/chats/{id}`.

### Changed
- **Enhanced notes are only your notes, automatically.** Lumina expands what you wrote as the meeting goes (no Enhance
  button), and no longer adds notes of its own, so the "Lumina ·" label is gone.
- **Faster enhancing.** The transcript goes first and only grows, so the built-in model reuses what it already read
  (`cache_prompt`); only your cues are answered, in about a third of the tokens; it runs every 12 seconds when something
  changed; with no notes written it does not call the model at all.
- **Ending a meeting does not enhance.** Ending cancels any enhancing in progress (the model stops generating) and goes
  straight to the summary. Enhanced notes and summary are separate: **Enhance my notes again** is in the ⋯ menu.
- **No scrollbar in the chat list.**

### Fixed
- **macOS permission windows** (best effort, see the notes below): the desktop app is now signed ad hoc as a whole
  (`install.sh` and the bundle), because macOS only remembers permissions of a validly signed app; the microphone
  window is answered off the helper's main thread. `lumina doctor` prints the helper's build errors and the two
  permissions as macOS reports them.

## [0.1.4] - 2026-10-24

### Added
- **Update from inside the app.** Lumina looks for a newer version by itself (when it starts and every 6 hours) and
  shows an **Update to x.y.z** button under the menu; Settings → About has **Check now**, the same button and a switch
  to turn the automatic check off. Updating runs `lumina update` for you, closes Lumina and opens the new version.
  `GET /api/update`, `POST /api/update/check` and `POST /api/update`.
- **Meetings in Sources.** Finished meetings, with their summary, are listed in Sources under **Meetings**, so
  everything Lumina can search is in one place. Click one to open it.
- **Free memory when idle** (Settings → General, on by default). Lumina unloads the speech model, the embedding model
  and the built-in notes model a few minutes after you stop using them and loads them again by itself.

### Changed
- **New, simpler logo**: an L and a dot of light, in the app, the website, the app icon and the share image.
- **One program asks for the Mac permissions.** On macOS the microphone and the computer's audio now go through the same
  signed helper (`lumina-audiotap`), so the microphone and Screen & System Audio Recording permissions belong to one
  program and are asked once. The helper is rebuilt only when its source really changed (before, an update that merely
  touched the file rebuilt it and macOS asked again) and it is signed with a fixed name. If the helper cannot give
  audio, Lumina falls back to the previous microphone path.
- **The engine follows the app.** If the Lumina app closes or crashes, its server closes with it; if someone closes the
  server (the `python` process in the Activity Monitor), the app starts it again and reloads the page.

### Fixed
- **"The model did not return valid JSON"** in Enhanced notes and in the summary. Small models often wrap the JSON in
  text, leave a trailing comma or stop halfway; Lumina now repairs those answers and, if it still cannot read one, asks
  again with more room and without the JSON grammar before giving up.
- **Sources no longer breaks with long names.** A long file or folder name, a long path or a long indexing message used
  to push the layout wider than the window and hide the buttons and the search box.

## [0.1.3] - 2026-10-24

### Added
- **Indexing progress bar.** While Lumina reads and indexes files, Sources shows what it is doing ("Reading
  report.pdf", "Indexing report.pdf · 120 of 201 passages"), files done out of total and a rough time left; a thin
  bar under the sidebar shows it on every page, and folders show their own bar. `/api/status` carries it (`index`) and
  `GET /api/index` returns it.
- **Folders open like a dropdown.** In Sources a folder has an arrow: click it to see every file inside, with its
  path inside the folder, status, passages and size.
- **Settings → Delete data.** Delete all chats, all sources, all meetings, or everything, each after a confirmation.
  Passages are deleted with them; your settings and downloaded models stay. `DELETE /api/data/{chats|sources|meetings|all}`.
- **Live transcript in the notch (desktop app).** While you record, a black island grows out of the notch (or from the
  top edge of the screen on computers without one, and on Windows and Linux) and shows the last three lines of the
  transcript, with the elapsed time. It covers the notch the same way brain's does (a window above the menu bar). Click
  it to go back to the meeting. Settings → Transcription → "Show the live transcript in the notch" turns it on or off
  (on by default on macOS). `GET /api/notch` feeds it.

### Changed
- **Much faster indexing.** Files are read and split by two helper threads while the model embeds the previous one
  (it never waits for a PDF); PDFs are read with PDFium instead of pypdf (about 6 times faster on a plain 200-page
  file, more on complex ones); passages are packed into batches by length so short ones do not pay for long ones;
  identical passages are embedded once; a file whose text did not change is not embedded again after a rescan,
  a touch or a restart; the model loads while the first file is still being read; and a search from the chat no
  longer waits for a whole file to finish embedding.
- **The website uses the app's look**: near-white desk, white sheets, hairlines, charcoal buttons and a soft blue,
  with the same neutral dark theme. New logo and favicon; the starting page of the desktop app too.

### Fixed
- **Deleting a file or a folder from Sources now deletes its passages, and the counter shows it.** The passage counter
  was only refreshed every 15 seconds and the indexing events never reached the page (so a source could stay "Indexing"
  until you changed page); a file that was being indexed while you removed it could write its passages after the
  removal; copies of dropped files stayed in `data/uploads`. Everything is now removed under one lock, the counter updates
  at once, and Lumina deletes passages that have no source at every start.
- A damaged file is reported on its own row instead of stopping the queue; adding a folder that is already there
  rescans it instead of queuing the folder itself.

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
