#!/bin/bash
# Double-click this file to rebuild Powerflow.app
# Pulls latest from your own git remote, rebuilds, copies to project root, and launches.

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT/Application Files" || exit 1

echo "═══════════════════════════════════════════"
echo "  Building Powerflow..."
echo "═══════════════════════════════════════════"
echo ""

# Quit any running instance first (exact process name, so this script isn't matched)
echo "→ Closing old instance..."
pkill -x Powerflow 2>/dev/null
sleep 1

# Pull latest
echo "→ Pulling latest from git..."
git -C "$PROJECT_ROOT" pull --ff-only 2>&1
echo ""

# Run the tests before building
echo "→ Running tests..."
python3 -m pytest "$PROJECT_ROOT/tests" -q 2>&1 | tail -3
echo ""

# Rebuild (clean to avoid stale cache)
echo "→ Running PyInstaller..."
python3 -m PyInstaller Powerflow.spec --noconfirm --clean 2>&1 | tail -5
echo ""

if [ -d "dist/Powerflow.app" ]; then
    echo "→ Installing to project root..."
    rm -rf "$PROJECT_ROOT/Powerflow.app"
    cp -R "dist/Powerflow.app" "$PROJECT_ROOT/Powerflow.app"
    # iCloud-synced folders add Finder metadata that blocks codesign; clear it, then ad-hoc sign.
    xattr -cr "$PROJECT_ROOT/Powerflow.app"
    codesign --force --deep -s - "$PROJECT_ROOT/Powerflow.app" >/dev/null 2>&1 || echo "  (ad-hoc signing skipped)"

    echo "✓ Build complete! Launching..."
    open "$PROJECT_ROOT/Powerflow.app"
    osascript -e 'tell application "Terminal" to close front window' &>/dev/null &
else
    echo "✗ Build failed — check output above"
    echo ""
    echo "Press any key to close..."
    read -n 1
fi
