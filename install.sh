#!/bin/sh
# Lumina installer for macOS and Linux.
#
#   curl -fsSL https://lautaro005.github.io/lumina/install.sh | sh
#
# Puts Lumina in ~/.lumina (your data lives in ~/.lumina/data), adds the `lumina` command to
# ~/.local/bin and installs the desktop app from the latest GitHub release. Run it again to update.
#
# Options (or the matching environment variables):
#   --no-app        skip the desktop app                       LUMINA_NO_APP=1
#   --build-app     build the desktop app from source (needs Rust and Node)   LUMINA_BUILD_APP=1
#   --models        also download the base models now (~1 GB)  LUMINA_MODELS=1
#   LUMINA_HOME=/path  install folder (default ~/.lumina)
#   LUMINA_REPO=url    git repository (default https://github.com/Lautaro005/lumina)
#   LUMINA_BRANCH=name branch (default main)
#   LUMINA_NO_PATH=1   do not add ~/.local/bin to your shell profile
set -eu

REPO="${LUMINA_REPO:-https://github.com/Lautaro005/lumina}"
GH_REPO="${LUMINA_GH_REPO:-Lautaro005/lumina}"
BRANCH="${LUMINA_BRANCH:-main}"
DIR="${LUMINA_HOME:-$HOME/.lumina}"
BIN="${LUMINA_BIN:-$HOME/.local/bin}"
NO_APP="${LUMINA_NO_APP:-}"
BUILD_APP="${LUMINA_BUILD_APP:-}"
MODELS="${LUMINA_MODELS:-}"
for arg in "$@"; do
  case "$arg" in
    --no-app) NO_APP=1 ;;
    --build-app) BUILD_APP=1 ;;
    --models) MODELS=1 ;;
    --app) NO_APP="" ;;
    *) ;;
  esac
done

if [ -t 1 ]; then B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; BL="$(printf '\033[34m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; R="$(printf '\033[0m')"; else B=""; D=""; BL=""; G=""; Y=""; R=""; fi
say() { printf '%s›%s %s\n' "$BL" "$R" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$R" "$*"; }
fail() { printf '%s✗%s %s\n' "$Y" "$R" "$*" >&2; exit 1; }

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin|Linux) ;;
  *) fail "This installer is for macOS and Linux. On Windows run in PowerShell: irm https://lautaro005.github.io/lumina/install.ps1 | iex" ;;
esac
command -v git >/dev/null 2>&1 || fail "git is required. macOS: xcode-select --install · Debian/Ubuntu: sudo apt install git"
command -v curl >/dev/null 2>&1 || fail "curl is required."

# ---------- uv (manages Python and the dependencies) ----------
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  say "Installing uv (Python manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || fail "Could not install uv. See https://docs.astral.sh/uv/"
  UV="$HOME/.local/bin/uv"
fi

# ---------- code ----------
if [ -d "$DIR/.git" ]; then
  if [ -z "${LUMINA_SKIP_PULL:-}" ]; then
    say "Updating Lumina in $DIR"
    git -C "$DIR" pull --ff-only --quiet || warn "Could not update the code (local changes?). Continuing with what is there."
  fi
elif [ -f "$DIR/pyproject.toml" ] && [ -f "$DIR/lumina/server.py" ]; then
  if [ -n "${LUMINA_SKIP_PULL:-}" ]; then
    say "Using the Lumina folder at $DIR"
  else
    # a copy without git history (older installs): fetch the current code once and keep your data, so that
    # `lumina update` works from now on
    say "Updating Lumina in $DIR (this copy has no git history, so it is replaced with the latest version)"
    TMPCLONE="$(mktemp -d)"
    if git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$TMPCLONE/lumina"; then
      rm -rf "$DIR/lumina/ui" "$DIR/site" "$DIR/web/src"
      cp -R "$TMPCLONE/lumina/." "$DIR/"
    else
      warn "Could not download the latest code; continuing with what is in $DIR."
    fi
    rm -rf "$TMPCLONE"
  fi
elif [ -e "$DIR" ] && [ -n "$(find "$DIR" -mindepth 1 -maxdepth 1 ! -name data 2>/dev/null | head -n1)" ]; then
  fail "$DIR exists and is not a Lumina install. Move it, or set LUMINA_HOME=/another/folder."
else
  say "Downloading Lumina into $DIR"
  mkdir -p "$DIR"
  TMPCLONE="$(mktemp -d)"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$TMPCLONE/lumina" || fail "Could not download $REPO"
  # keep an existing data folder in place
  cp -R "$TMPCLONE/lumina/." "$DIR/"
  rm -rf "$TMPCLONE"
fi
cd "$DIR"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)"

say "Installing dependencies (the first time takes a few minutes)…"
"$UV" sync --quiet --no-dev || fail "Dependency install failed. Run: cd $DIR && $UV sync"

export LUMINA_DATA="${LUMINA_DATA:-$DIR/data}"
mkdir -p "$LUMINA_DATA"

# ---------- system pieces ----------
if [ "$OS" = Darwin ]; then
  if command -v swiftc >/dev/null 2>&1 || [ -x /usr/bin/swiftc ]; then
    say "Building the system-audio helper (ScreenCaptureKit)…"
    "$DIR/.venv/bin/python" -c "from lumina.audio import build_helper; import sys; sys.exit(0 if build_helper() else 1)" \
      || warn "Could not build it; Lumina will record only the microphone. Install the tools with: xcode-select --install"
  else
    warn "Install the Xcode command line tools (xcode-select --install) so Lumina can record your computer's audio."
  fi
else
  if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    warn "PortAudio was not found. For the microphone install it: sudo apt install libportaudio2 (Debian/Ubuntu) · sudo dnf install portaudio (Fedora)"
  fi
  if ! command -v pactl >/dev/null 2>&1; then
    warn "pactl (PulseAudio/PipeWire) was not found; recording the computer's audio may not work."
  fi
fi

# ---------- desktop app ----------
app_done=0
install_release_app() {
  if [ "$OS" = Linux ] && [ "$ARCH" != x86_64 ] && [ "$ARCH" != amd64 ]; then
    warn "There is no Linux desktop build for $ARCH yet; use \`lumina\` (browser) instead."
    return 0
  fi
  JSON="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$GH_REPO/releases/latest" 2>/dev/null)" || {
    warn "No desktop release found yet; use \`lumina\` (browser), or run this installer with --build-app."; return 0; }
  URLS="$(printf '%s' "$JSON" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"$/\1/')"
  if [ "$OS" = Darwin ]; then URL="$(printf '%s\n' "$URLS" | grep '\.app\.tar\.gz$' | head -n1)"; else URL="$(printf '%s\n' "$URLS" | grep -i 'amd64\.AppImage$' | head -n1)"; fi
  [ -n "$URL" ] || { warn "The latest release has no desktop app for $OS; use \`lumina\` (browser)."; return 0; }
  TMP="$(mktemp -d)"
  say "Downloading the desktop app…"
  curl -fL --progress-bar "$URL" -o "$TMP/app" || { warn "Download failed."; rm -rf "$TMP"; return 0; }
  if [ "$OS" = Darwin ]; then
    tar -xzf "$TMP/app" -C "$TMP" && [ -d "$TMP/Lumina.app" ] || { warn "Unexpected archive."; rm -rf "$TMP"; return 0; }
    mkdir -p "$HOME/Applications"; rm -rf "$HOME/Applications/Lumina.app"; mv "$TMP/Lumina.app" "$HOME/Applications/Lumina.app"
    xattr -dr com.apple.quarantine "$HOME/Applications/Lumina.app" 2>/dev/null || true
  else
    install_linux_app "$TMP/app"
  fi
  rm -rf "$TMP"
  app_done=1
}

install_linux_app() {
  mkdir -p "$HOME/.local/opt/lumina" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
  mv "$1" "$HOME/.local/opt/lumina/Lumina.AppImage"; chmod +x "$HOME/.local/opt/lumina/Lumina.AppImage"
  cp "$DIR/apps/desktop/src-tauri/icons/128x128@2x.png" "$HOME/.local/share/icons/hicolor/256x256/apps/lumina.png" 2>/dev/null || true
  cat > "$HOME/.local/share/applications/lumina.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=Lumina
Comment=Local-first meeting notes and knowledge base
Exec=$BIN/lumina app
Icon=lumina
Categories=Office;AudioVideo;
Terminal=false
StartupWMClass=lumina-app
ENTRY
}

build_app() {
  command -v cargo >/dev/null 2>&1 || { warn "Building the app needs Rust (https://rustup.rs)."; return 0; }
  command -v npx >/dev/null 2>&1 || { warn "Building the app needs Node.js (https://nodejs.org)."; return 0; }
  say "Building the desktop app from source (5-10 minutes the first time)…"
  if [ "$OS" = Darwin ]; then BUNDLES="app"; else BUNDLES="appimage"; fi
  ( cd "$DIR/apps/desktop" && npx -y @tauri-apps/cli@2 build --bundles "$BUNDLES" >"$LUMINA_DATA/build-app.log" 2>&1 ) \
    || { warn "The app build failed; see $LUMINA_DATA/build-app.log. \`lumina\` (browser) still works."; return 0; }
  OUT="$DIR/apps/desktop/src-tauri/target/release/bundle"
  if [ "$OS" = Darwin ]; then
    mkdir -p "$HOME/Applications"; rm -rf "$HOME/Applications/Lumina.app"; cp -R "$OUT/macos/Lumina.app" "$HOME/Applications/Lumina.app"
  else
    install_linux_app "$(ls "$OUT"/appimage/*.AppImage | head -n1)"
  fi
  app_done=1
}

if [ -z "$NO_APP" ]; then
  if [ -n "$BUILD_APP" ]; then build_app; else install_release_app; fi
fi

# ---------- models (optional; the app's first run offers them too) ----------
if [ -n "$MODELS" ]; then
  say "Downloading the base models (EmbeddingGemma 2, Silero VAD, Parakeet v3)…"
  "$DIR/.venv/bin/lumina" models download embeddinggemma-2 silero-vad parakeet-v3 || warn "Some downloads failed; retry from the app's Settings."
fi

# ---------- the `lumina` command ----------
mkdir -p "$BIN"
cat > "$BIN/lumina" <<EOF
#!/bin/sh
# lumina command (written by install.sh)
exec "$DIR/lumina.sh" "\$@"
EOF
chmod +x "$BIN/lumina" "$DIR/lumina.sh"

on_path=1
case ":$PATH:" in *":$BIN:"*) ;; *) on_path=0 ;; esac
if [ "$on_path" = 0 ] && [ -z "${LUMINA_NO_PATH:-}" ]; then
  case "${SHELL##*/}" in bash) RC="$HOME/.bashrc"; [ "$OS" = Darwin ] && RC="$HOME/.bash_profile" ;; fish) RC="" ;; *) RC="$HOME/.zshrc" ;; esac
  if [ -n "$RC" ]; then
    LINE="export PATH=\"$BIN:\$PATH\""
    grep -qsF "$LINE" "$RC" || printf '\n# lumina\n%s\n' "$LINE" >> "$RC"
  fi
fi

printf '\n%s✓ Lumina %s installed%s in %s\n\n' "$G" "$VERSION" "$R" "$DIR"
[ "$on_path" = 0 ] && printf '  Open a new terminal first (or run %ssource %s%s), then:\n\n' "$D" "${RC:-your shell profile}" "$R"
printf '    %slumina%s          open Lumina in your browser\n' "$B" "$R"
[ "$app_done" = 1 ] && printf '    %slumina app%s      open the desktop app (also in your Applications)\n' "$B" "$R"
printf '    %slumina mcp%s      connect Claude, Cursor or other agents (see Settings → Agents)\n' "$B" "$R"
printf '    %slumina update%s   update\n' "$B" "$R"
printf '    %slumina doctor%s   check the install\n\n' "$B" "$R"
