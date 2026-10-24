# Security Policy

Lumina records meetings, keeps transcripts, notes and an index of your files on your computer, runs local models and
exposes that knowledge to AI agents over MCP. That is private material, so security reports are taken seriously and
handled privately.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` (latest: `0.1.5`) | ✅ |
| Older | ❌ — please update with `lumina update` |

## Reporting a vulnerability

**Do not open a public issue, discussion or pull request for security problems.**

Report privately through GitHub: open the repository's **Security** tab →
[**Report a vulnerability**](https://github.com/Lautaro005/lumina/security/advisories/new).

Please include:

- What is affected (server, web UI, desktop app, MCP server, install scripts, website) and the version or commit
- Your operating system and how you installed Lumina (`install.sh`, `install.ps1`, from source)
- Which features are involved (recording, system audio, sources and folder watching, chat and web search, MCP over stdio
  or HTTP, model downloads, Ollama / LM Studio / OpenAI-compatible server)
- Steps to reproduce, a proof of concept or logs (remove transcripts, tokens and personal data first)
- The impact you expect (what an attacker can do)

### What to expect

- Acknowledgement within **7 days**
- An initial assessment within **14 days**
- A fix or mitigation plan for confirmed issues, coordinated with you before anything is disclosed
- Credit in the advisory and the changelog, if you want it

Please allow a reasonable time for a fix before public disclosure (90 days by default, shorter when an issue is already
being exploited).

## Security model

- **Local only.** The server binds to `127.0.0.1`. Requests whose `Host` header is not `127.0.0.1:<port>` or
  `localhost:<port>` are refused (protects against DNS rebinding), and browser requests from any other origin are refused
  (protects against websites you visit calling the API).
- **MCP.** Over stdio, the agent runs `lumina mcp` as your user. Over HTTP (`/mcp`), every request needs the bearer token
  shown in Settings → Agents; the token is stored in `settings.json` with `0600` permissions.
- **Data.** Everything lives in the data folder (`~/.lumina/data` by default): `lumina.db` (SQLite), `chroma/` (vector
  index), `models/`, `uploads/`, `logs/` and `settings.json`. Nothing is sent anywhere by default. No telemetry
  (ChromaDB's telemetry is turned off).
- **What leaves the computer:** a version check (`raw.githubusercontent.com`, one small file, when Lumina starts and every
  6 hours; Settings → About turns it off), and, only when you ask, model downloads from Hugging Face and GitHub, web search queries
  when the chat's Web search toggle is on (DuckDuckGo or your SearXNG), and prompts sent to an Ollama, LM Studio or
  OpenAI-compatible server you configure (which may be remote if you point it at one).
- **Untrusted text.** Transcripts, files, web results and MCP inputs are data. The chat and assistance prompts tell the
  model so, and Lumina's models cannot run commands or tools: the worst a prompt injection can do is change what an answer
  says. Since 0.1.1 every source reaches the model inside a numbered `<source>` tag that its own text cannot close or
  reopen, and the answer is cleaned of leaked tags and of citations to sources that do not exist.
- **Speech models.** The optional EmbeddingGemma 2 speech files are downloaded from Hugging Face into `models/` and
  run in process with `ai-edge-litert`. They are LiteRT model files, not pickled Python.
- **The desktop app** loads only the local UI, opens every other link in your browser and gives the page no app
  commands. Since 0.1.3 it also polls `GET /api/notch` on `127.0.0.1` for the live-transcript notch; the notch page is
  a local file with no network access, and it can only ask the app to open the meeting being recorded.
- **Deleting data.** Settings → Delete data (`DELETE /api/data/...`) removes chats, sources, meetings and their passages
  from the SQLite database and the vector index, and the copies of dropped files in `uploads/`. It is protected by the
  same host and origin checks as the rest of the API. Your own files on disk are never touched. Freed space inside the
  database files is reused by SQLite and ChromaDB rather than returned to the system at once.
- **Updating from the app.** `POST /api/update` runs `lumina update` (the same code path as the terminal command: `git pull`
  from this repository, `uv sync`, the desktop app from this repository's latest release) in a detached script, then
  restarts Lumina. It takes no input, so a web page cannot choose what runs; it is behind the same host and origin checks.
- **macOS permissions.** The microphone and system audio go through one helper, `lumina-audiotap`, compiled on your Mac
  from `lumina/native/audiotap.swift`, signed ad hoc with a fixed identifier and rebuilt only when its source changes.
- **Imported recordings** are kept in `uploads/` (so they can be transcribed again) until you delete the meeting;
  `GET /api/meetings/{id}/audio` serves only that file, behind the same host and origin checks.
- **PDF reading.** PDFs are read with PDFium (`pypdfium2`), which is native code; a malformed PDF falls back to the
  pure Python reader and a file that cannot be read is reported on its own row.

## Scope

In scope:

- Any way for a website, another user on the machine or a remote host to reach the Lumina API, the WebSocket or `/mcp`
  without the token
- Reading or writing files outside the data folder or the sources you added (path traversal in uploads, exports, the
  file picker or `get_document`)
- Leaks of transcripts, notes, the MCP token or an OpenAI-compatible API key (logs, UI, network)
- The installers (`install.sh`, `install.ps1`) or the `lumina update` flow fetching or running something other than this
  repository and its GitHub releases
- Model downloads written outside `models/` or `bin/`, or archives that escape their folder when extracted
- Recording that starts without a user action, or keeps running after **End meeting**
- The macOS system-audio helper capturing anything other than audio
- Supply-chain issues in the GitHub Actions workflows or the website

Out of scope:

- Answers that are wrong, incomplete or manipulated by the content you indexed (model quality, not a vulnerability)
- Problems in third-party servers you connect (Ollama, LM Studio, SearXNG, OpenAI-compatible APIs) or in the models
- Attacks that need an already compromised user account or administrator access to the computer
- Installers not being code-signed or notarized by Apple or Microsoft (known; tracked in the changelog)
- Hardening suggestions without a concrete impact

Thank you for helping keep Lumina and its users safe.
