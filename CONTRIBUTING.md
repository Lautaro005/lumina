# Contributing to Lumina

Thanks for helping. A few things keep the project easy to work on:

- **Setup:** `uv sync`, then `cd web && npm install`. Run the server with test data that never touches your real
  meetings: `LUMINA_DEV=1 LUMINA_DATA=/tmp/lumina-dev uv run lumina serve --port 8799`, and the UI with hot reload:
  `cd web && npm run dev` (proxies `/api` to port 8799).
- **Before a pull request:** `uv run ruff check lumina tests`, `uv run pytest`, `cd web && npm run build` (commit the
  updated `lumina/ui`), and for the desktop app `cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test`
  in `apps/desktop/src-tauri`.
- **User-visible changes:** add a line to `CHANGELOG.md` and update the README and the website (`site/`) when the
  behaviour they describe changes. UI text lives in `web/src/i18n.ts` in English and Spanish; add both.
- **Design:** the visual system is described in `DESIGN.md` (Cornell page: one blue rule colour, margin red only for the
  cue/body line and recording, highlighter yellow only for detected questions).
- **Privacy:** nothing may leave the computer without a visible user action. New network calls need a line in
  `SECURITY.md` and the privacy page.
- **Security problems:** never in a public issue; see [SECURITY.md](SECURITY.md).

By contributing you agree that your contribution is licensed under the project's license (Apache-2.0 with the Commons
Clause).
