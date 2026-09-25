#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Powerflow — First Time Setup
#  Double-click this file to install what's needed and build the
#  app. Only needs to be run once on a new machine.
# ═══════════════════════════════════════════════════════════════

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Powerflow — First Time Setup"
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
echo "→ Building Powerflow.app..."
echo ""
cd "$PROJECT_ROOT/Application Files"
python3 -m PyInstaller Powerflow.spec --noconfirm --clean 2>&1 | tail -3
echo ""

if [ -d "dist/Powerflow.app" ]; then
    rm -rf "$PROJECT_ROOT/Powerflow.app"
    cp -R "dist/Powerflow.app" "$PROJECT_ROOT/Powerflow.app"
    # iCloud-synced folders add Finder metadata that blocks codesign; clear it, then ad-hoc sign.
    xattr -cr "$PROJECT_ROOT/Powerflow.app"
    codesign --force --deep -s - "$PROJECT_ROOT/Powerflow.app" >/dev/null 2>&1 || echo "  (ad-hoc signing skipped)"
    echo "  ✓ Powerflow.app built and ready"
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
echo "  To launch: double-click 'Powerflow.app'"
echo "  (Optional) drag it into /Applications and add it to"
echo "  System Settings → General → Login Items to start at login."
echo "  To rebuild after updates: double-click 'Build Powerflow.command'"
echo "═══════════════════════════════════════════════════════════"
echo ""

read -p "Launch Powerflow now? (y/n) " -n 1 answer
echo ""
if [[ "$answer" =~ ^[Yy]$ ]]; then
    open "$PROJECT_ROOT/Powerflow.app"
fi

osascript -e 'tell application "Terminal" to close front window' &>/dev/null &
