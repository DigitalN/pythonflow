#!/bin/bash
# Double-click this file to rebuild Pythonflow.app
# Pulls latest from your own git remote, rebuilds, installs to /Applications, and launches.

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT/Application Files" || exit 1

echo "═══════════════════════════════════════════"
echo "  Building Pythonflow..."
echo "═══════════════════════════════════════════"
echo ""

# Quit any running instance first (exact process name, so this script isn't matched)
echo "→ Closing old instance..."
pkill -x Pythonflow 2>/dev/null
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
python3 -m PyInstaller Pythonflow.spec --noconfirm --clean 2>&1 | tail -5
echo ""

if [ -d "dist/Pythonflow.app" ]; then
    echo "→ Installing to /Applications..."
    rm -rf "/Applications/Pythonflow.app"
    ditto "dist/Pythonflow.app" "/Applications/Pythonflow.app"
    # Clear Finder/iCloud metadata picked up during the build (it blocks codesign), then ad-hoc sign.
    xattr -cr "/Applications/Pythonflow.app"
    codesign --force --deep -s - "/Applications/Pythonflow.app" >/dev/null 2>&1 || echo "  (ad-hoc signing skipped)"
    rm -rf build dist

    echo "✓ Build complete! Launching..."
    open "/Applications/Pythonflow.app"
    osascript -e 'tell application "Terminal" to close front window' &>/dev/null &
else
    echo "✗ Build failed — check output above"
    echo ""
    echo "Press any key to close..."
    read -n 1
fi
