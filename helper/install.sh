#!/bin/bash
# Install the InterDAW helper: a small app that starts at login, is restarted if it exits, and
# answers only the InterDAW panel in LUNA on 127.0.0.1:8792.
#
# From the latest release (what the InterDAW panel tells you to run):
#   curl -fsSL https://github.com/cp666-dev/interdaw/releases/latest/download/install-helper.sh | bash
# From a checkout of the repository:
#   bash helper/install.sh
#
# It has to be an app: macOS denies a bare background Python access to Desktop, Documents and
# Downloads without asking, but asks once per folder on behalf of an app.
set -euo pipefail

REPO="cp666-dev/interdaw"
LABEL="com.cp.interdaw.helper"
SUPPORT="$HOME/Library/Application Support/InterDAW"
HELPER="$SUPPORT/helper"
APP="$SUPPORT/InterDAW Helper.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/InterDAW"
PORT=8792

say() { printf '%s\n' "$*"; }
fail() { printf '\nInterDAW helper was not installed: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "it runs on macOS only."

# Apple's python3 is a stub until the Command Line Tools are installed; running it then opens
# an install prompt instead of Python. Check first, so this script says what is going on.
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  fail "it needs Apple's Command Line Tools, which include Python. macOS is asking to install them now; when that finishes, run this command again."
fi
/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || fail "it needs Python 3.9 or later at /usr/bin/python3. Update the Command Line Tools (Software Update), then run this command again."

# The helper's code: next to this script in a checkout, otherwise the latest release's.
SOURCE=""
HERE="${BASH_SOURCE[0]:-}"
if [ -n "$HERE" ] && [ -f "$HERE" ] && [ -d "$(cd "$(dirname "$HERE")" && pwd)/interdaw_helper" ]; then
  SOURCE="$(cd "$(dirname "$HERE")" && pwd)/interdaw_helper"
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ -z "$SOURCE" ]; then
  say "Downloading the InterDAW helper..."
  curl -fsSL "https://github.com/$REPO/releases/latest/download/interdaw-helper.tar.gz" -o "$WORK/helper.tar.gz" \
    || fail "it could not be downloaded. Check your internet connection and try again."
  tar -xzf "$WORK/helper.tar.gz" -C "$WORK"
  SOURCE="$WORK/interdaw_helper"
  [ -f "$SOURCE/server.py" ] || fail "the download was not the InterDAW helper."
fi

say "Installing..."
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# bootout returns before the old service is gone; bootstrapping too early fails with error 5.
for _ in $(seq 1 20); do launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break; sleep 0.5; done

mkdir -p "$SUPPORT" "$LOGS" "$HOME/Library/LaunchAgents"
rm -rf "$HELPER" "$APP"
mkdir -p "$HELPER"
cp -R "$SOURCE" "$HELPER/interdaw_helper"
find "$HELPER" -name __pycache__ -prune -exec rm -rf {} +

osacompile -o "$APP" -e "do shell script \"cd \" & quoted form of \"$HELPER\" & \" && exec /usr/bin/python3 -m interdaw_helper >> \" & quoted form of \"$LOGS/helper.log\" & \" 2>&1\""
# No Dock icon, and a stable identity for the folder access macOS grants it.
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $LABEL" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string $LABEL" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName InterDAW Helper" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string InterDAW Helper" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/Contents/MacOS/applet</string>
  </array>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardErrorPath</key><string>$LOGS/helper.err.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in $(seq 1 20); do
  if curl -fsS -H "Origin: ua-content://com.cp.interdaw" "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    say ""
    say "The InterDAW helper is installed and running. It starts by itself when you log in."
    say "Back in LUNA, the InterDAW panel is ready: drop a Live set on it."
    say "The first time you open a set from Desktop, Documents or Downloads, allow \"InterDAW Helper\" to access that folder."
    exit 0
  fi
  sleep 0.5
done
fail "it was installed but did not start. See $LOGS/helper.log."
