#!/bin/sh
# The `lumina` command (install.sh links ~/.local/bin/lumina here).
#
#   lumina              open Lumina in your browser (the server runs in this terminal; Ctrl+C stops it)
#   lumina app          open the desktop app (window + tray icon)
#   lumina mcp          MCP server over stdio, for Claude Desktop, Cursor, Claude Code...
#   lumina update       update to the latest version
#   lumina doctor       check microphones, models and model servers
#   lumina models       list models; `lumina models download <id>` downloads one
#   lumina version      print the version
#   lumina uninstall    remove the command and the desktop app (keeps your data)
set -eu

DIR="$(cd "$(dirname "$0")" && pwd -P)"
# when called through the ~/.local/bin symlink, resolve the real folder
if [ -L "$0" ]; then
  LINK="$(readlink "$0")"
  case "$LINK" in /*) DIR="$(cd "$(dirname "$LINK")" && pwd -P)" ;; *) DIR="$(cd "$(dirname "$0")/$(dirname "$LINK")" && pwd -P)" ;; esac
fi
export LUMINA_HOME="$DIR"
[ -n "${LUMINA_DATA:-}" ] || export LUMINA_DATA="$DIR/data"

run() {
  if [ -x "$DIR/.venv/bin/lumina" ]; then
    exec "$DIR/.venv/bin/lumina" "$@"
  fi
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  exec "$UV" run --project "$DIR" --quiet lumina "$@"
}

open_app() {
  if [ "$(uname -s)" = Darwin ]; then
    for APP in "$HOME/Applications/Lumina.app" "/Applications/Lumina.app"; do
      if [ -d "$APP" ]; then exec open --env LUMINA_HOME="$DIR" --env LUMINA_DATA="$LUMINA_DATA" "$APP"; fi
    done
  else
    APP="$HOME/.local/opt/lumina/Lumina.AppImage"
    if [ -x "$APP" ]; then
      if ! { [ -e /dev/fuse ] && { ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2'; }; }; then export APPIMAGE_EXTRACT_AND_RUN=1; fi
      nohup "$APP" >/dev/null 2>&1 &
      exit 0
    fi
  fi
  echo "The desktop app is not installed. Run: lumina update --app   (or use \`lumina\` to open it in the browser)"
  exit 1
}

case "${1:-}" in
  "") shift 0; run serve --open ;;
  app) open_app ;;
  update)
    shift
    if [ -d "$DIR/.git" ]; then
      # uv.lock is written by uv on this computer (no longer part of the code): a local copy must not stop the update
      git -C "$DIR" checkout --quiet -- uv.lock 2>/dev/null || true
      git -C "$DIR" pull --ff-only --quiet
    fi
    LUMINA_HOME="$DIR" exec sh "$DIR/install.sh" "$@"
    ;;
  uninstall)
    rm -f "$HOME/.local/bin/lumina"
    rm -rf "$HOME/Applications/Lumina.app" "$HOME/.local/opt/lumina" "$HOME/.local/share/applications/lumina.desktop" \
      "$HOME/.local/share/icons/hicolor/256x256/apps/lumina.png"
    echo "Removed the lumina command and the desktop app."
    echo "Your meetings, notes and models are still in $LUMINA_DATA. Delete $DIR to remove everything."
    ;;
  help|-h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) run "$@" ;;
esac
