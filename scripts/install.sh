#!/bin/bash
# Builds Pythonflow, installs it in /Applications and launches it.
#
#   scripts/install.sh            sign with your Apple Development certificate, if you have one
#   scripts/install.sh --adhoc    sign locally only (the app then can't update itself)

set -euo pipefail
cd "$(dirname "$0")/.."

DEST="/Applications/Pythonflow.app"

APP=$(scripts/build.sh "$@")

echo "Installing to $DEST…" >&2
pkill -x Pythonflow >/dev/null 2>&1 || true
sleep 1
rm -rf "$DEST"
ditto "$APP" "$DEST"
rm -rf "$(dirname "$APP")"

open "$DEST"
echo "Done. Pythonflow is running (look for its wattage in the menu bar)." >&2
