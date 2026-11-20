#!/bin/bash
# Test everything, then write the files a GitHub release needs to dist/:
#   InterDAW.lunaaddon     the Add On (what UA Universe lists and LUNA installs)
#   interdaw-helper.tar.gz the helper's code, fetched by install-helper.sh
#   install-helper.sh      what the panel tells users to run
#   uninstall-helper.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

(cd "$ROOT/addon" && npm test)
(cd "$ROOT/helper" && /usr/bin/python3 -m unittest discover -s tests -t .)

ADDON_VERSION="$(node -p "require('$ROOT/addon/manifest.json').version")"
HELPER_VERSION="$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$ROOT/helper/interdaw_helper/server.py")"
[ "$ADDON_VERSION" = "$HELPER_VERSION" ] || { echo "Add On $ADDON_VERSION and helper $HELPER_VERSION differ; bump both." >&2; exit 1; }

rm -rf "$DIST" && mkdir -p "$DIST"
# Pack a copy named InterDAW, so the folder inside the .lunaaddon is InterDAW, not addon.
rsync -a --exclude node_modules --exclude tests --exclude package.json --exclude package-lock.json --exclude '.*' --exclude '*.lunaaddon' "$ROOT/addon/" "$STAGE/InterDAW/"
(cd "$STAGE" && node "$ROOT/addon/node_modules/.bin/ua-builder" pack InterDAW)
mv "$STAGE/InterDAW.lunaaddon" "$DIST/InterDAW.lunaaddon"
# pack refreshes the vendored SDK in the copy; keep the checkout's in step with what shipped.
cp "$STAGE/InterDAW/vendor/luna.global.js" "$ROOT/addon/vendor/luna.global.js"

COPYFILE_DISABLE=1 tar -czf "$DIST/interdaw-helper.tar.gz" -C "$ROOT/helper" --exclude __pycache__ --exclude '*.pyc' interdaw_helper
cp "$ROOT/helper/install.sh" "$DIST/install-helper.sh"
cp "$ROOT/helper/uninstall.sh" "$DIST/uninstall-helper.sh"

echo
echo "InterDAW $ADDON_VERSION release files:"
ls -l "$DIST"
