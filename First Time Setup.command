#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Pythonflow — First Time Setup
#  Double-click this file to install what's needed and build the
#  app. Only needs to be run once on a new machine.
# ═══════════════════════════════════════════════════════════════

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Pythonflow — First Time Setup"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ── 1. Check for Python 3 ────────────────────────────────────
echo "→ Checking for Python 3..."
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  ✗ Python 3 is not installed."
    echo "    Download it from: https://www.python.org/downloads/"
    echo ""
    echo "  After installing Python, run this setup again."
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi
PYVER=$(python3 --version 2>&1)
echo "  ✓ Found $PYVER"
echo ""

# ── 2. Install Python packages (pinned versions) ─────────────
echo "→ Installing Python packages..."
echo ""
pip3 install -r "$PROJECT_ROOT/Application Files/requirements.txt" \
    2>&1 | grep -E "^(Successfully|Requirement|Installing)" || true
echo ""
echo "  ✓ All packages installed"
echo ""

# ── 3. Build the app ─────────────────────────────────────────
pkill -x Pythonflow 2>/dev/null || true
echo "→ Building Pythonflow.app..."
echo ""
cd "$PROJECT_ROOT/Application Files"
python3 -m PyInstaller Pythonflow.spec --noconfirm --clean 2>&1 | tail -3
echo ""

if [ -d "dist/Pythonflow.app" ]; then
    rm -rf "/Applications/Pythonflow.app"
    ditto "dist/Pythonflow.app" "/Applications/Pythonflow.app"
    # Clear Finder/iCloud metadata picked up during the build (it blocks codesign), then ad-hoc sign.
    xattr -cr "/Applications/Pythonflow.app"
    codesign --force --deep -s - "/Applications/Pythonflow.app" >/dev/null 2>&1 || echo "  (ad-hoc signing skipped)"
    rm -rf build dist
    echo "  ✓ Pythonflow.app installed in /Applications"
else
    echo "  ✗ Build failed — check output above"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Setup complete!"
echo ""
echo "  To launch: open Pythonflow from Applications, Launchpad or Spotlight"
echo "  (Optional) add it to System Settings → General → Login Items"
echo "  to start it at login."
echo "  To rebuild after updates: double-click 'Build Pythonflow.command'"
echo "═══════════════════════════════════════════════════════════"
echo ""

read -p "Launch Pythonflow now? (y/n) " -n 1 answer
echo ""
if [[ "$answer" =~ ^[Yy]$ ]]; then
    open "/Applications/Pythonflow.app"
fi

osascript -e 'tell application "Terminal" to close front window' &>/dev/null &
