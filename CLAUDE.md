# Pythonflow

Building, the release checklist, how updates work, and testing the updater are in
@DEVELOPMENT.md.

## Debugging

Application logs are written to `~/Library/Logs/Pythonflow/app.log` (rotating,
3 x 1 MB). Dashboard (JavaScript) errors are forwarded there too. When the user
reports a bug, **read the log first** before asking questions. The updater logs
`Automatic updates off: <reason>` at launch, and `Up to date at`, `Downloading`,
`ready to install`, `Installed …; relaunching` or `Update check failed: …` after a
check.

`~/Library/Logs/Powerflow/` belongs to the original Rust app (`powerflow.log`)
and to the first Python build, which ran under the old name.

To reproduce without touching the user's data, run with
`PYTHONFLOW_DATA_DIR=<tmp folder>` (logs then go to `<tmp folder>/Logs/`).

## Architecture

- **Launcher**: `Application Files/main.py`. Handles logging, the single-instance
  lock, the sampler thread, the menu bar item, the pywebview window, and the `Api`
  class exposed to the page. `VERSION` here is the app's only version number.
- **Launching**: `Application Files/launch.py`. The offer to move into Applications,
  relaunching, and open at login (`SMAppService`, loaded with `objc.loadBundle`).
- **Updates**: `Application Files/updater.py`. Daily check of GitHub releases after
  a wake, signature check, swap and relaunch. See *How updates work* in DEVELOPMENT.md.
- **Sensors**: `Application Files/power.py`. Reads IOKit `AppleSmartBattery`
  (ctypes + PyObjC bridging) and the SMC (ctypes `IOConnectCallStructMethod`).
  `normalize()` is pure and unit-tested with captured registry dicts.
- **Menu bar**: `Application Files/statusbar.py`. Native `NSStatusItem` and `NSMenu`
  (PyObjC). Main thread only.
- **History**: `Application Files/history.py`. SQLite
  (`~/Library/Application Support/Pythonflow/history.db`), the `ChargingRecorder`
  state machine, CSV export, and the one-time read-only import from the original
  app's `~/Library/Application Support/Powerflow/db.sqlite`.
- **Settings**: `Application Files/settings.py`. JSON, validated on every load and
  save. Open at login isn't in it: it lives in ServiceManagement, and `Api` merges it
  in for the page.
- **Frontend**: `Application Files/index.html`. A single page in vanilla JS with a
  hand-rolled canvas chart and no external resources.
- **Build**: `scripts/build.sh` runs PyInstaller with `Application Files/Pythonflow.spec`
  in `~/Library/Caches/Pythonflow-build` and signs the app. `scripts/install.sh` installs
  it in `/Applications`; `scripts/package.sh` makes the release disk image. The
  `.command` files are double-click wrappers around `install.sh`.

## Key rules

- **The updater is the only network access.** Once a day it asks GitHub's API for the
  latest release and downloads its disk image, through `/usr/bin/curl`. Don't add
  anything else that connects: no HTTP server (unlike ReconciliationTool, the page uses
  pywebview's `js_api` bridge), analytics, CDNs or remote fonts. The CSP in
  `index.html` sets `connect-src 'none'`. `'unsafe-eval'` is there only because
  pywebview returns API results via `eval`.
- **Never read identifying registry keys** (`Serial`, `AdapterDetails.SerialString`,
  the computer name). `power.BATTERY_KEYS` and the `_pick` filters whitelist what
  is read.
- **Always sample inside `objc.autorelease_pool()`.** Without it, PyObjC bridging
  leaks about 2.5 KB per sample on the background thread (measured).
- **Treat every registry key as optional.** macOS 27 removed several of them, and
  the original app crashed on that.
- pywebview exposes every public attribute of `Api` to JavaScript, recursively.
  Keep internals behind a leading underscore.
- AppKit calls must run on the main thread: use `AppHelper.callAfter`.
  Non-locking pywebview events (`loaded`, `shown`) fire on worker threads.
- **Nothing on the dashboard may animate continuously**, and no CSS transition may
  outlast the 1 s refresh. The original flowing pipes and eased bars kept WebKit
  repainting every display frame: about 28% of a core, against 3% now (measured).
- The sampler runs every 1 s while the page is polling (it only polls when visible)
  and every 2.5 s otherwise. These are fixed, not a setting.
- SMC `PHPC` is chip (CPU + GPU package) power, **not** "heatpipe" or fan power,
  despite Apple's key name. Verified: it rises about 11 W under full CPU load while
  the fans stay at minimum.
- `~/Documents` is iCloud-synced on this machine. Finder metadata blocks
  `codesign`, so `scripts/build.sh` builds in `~/Library/Caches` and runs `xattr -cr`
  before signing.
- **Don't relaunch into an update while a charge is being recorded.** A session in
  progress lives in memory until it ends, so quitting loses it.
- WebKit pauses rendering in a window that's covered or hidden, so `ResizeObserver` and
  `requestAnimationFrame` don't run there. A test that drives the page while other
  windows cover it sees no size reports; that's not a bug.

## Releasing

- A release means the whole **Release checklist** in DEVELOPMENT.md, every time, without
  being reminded: version bump, tests, DMG checks, release notes, publishing, drafting
  the previous release, the README, and checking the live GitHub page afterwards.
- Merging to `main` is not a release. If user-facing changes are merged but unreleased,
  say so: installed copies only update when a release is published.
- Ask once before anything public on GitHub (publishing, drafting the previous release,
  editing release notes or files, pushing to `main`), then do all of it.
- Check each step's result before the next step that can't be undone. Don't chain a
  check and a publish with `&&`.
- Releases are signed with the maintainer's Apple Development certificate (team
  `XT4LT7MS6Y`). Installed copies refuse updates from any other team. The paid developer
  membership has lapsed and the maintainer doesn't want to renew it yet, so don't plan
  around Developer ID or notarization.
- Releases have only the `.dmg`. The page should list only the current release.

## Testing on the maintainer's Mac

- The maintainer's own Pythonflow is running from `/Applications`. Don't quit it,
  reinstall it or change its data to test something without asking. Test with
  `PYTHONFLOW_DATA_DIR` and, for the updater, a copy with its own bundle identifier
  (see *Testing the updater*).
- Say so before opening windows or menu bar items on screen for a test. The maintainer
  may be in the middle of something.
- After testing the updater, delete the test copy, its data folder and the test disk
  images, and make sure no test copy is left in Login Items.
- Claude's shell can't take screenshots or click menus. To see what the app decided,
  read its log. To drive something only a menu or a wake can trigger, add a temporary
  hook to a scratch copy of the source (not the repository) and build from that.

## Git

- Work on a branch, not `main`. The maintainer opens pull requests with Create PR,
  which needs a branch other than `main`.

## Decisions already made

Don't undo these without asking:

- Updates are checked once a day, only when the Mac wakes from sleep (plus **Check for
  Updates…** in the menu), and installed silently at a quiet moment. On by default;
  there's a switch in Settings.
- Open at login is turned on once, the first time the app runs from Applications.
  Turning it off sticks.
- The dashboard window opens as tall as the page, up to the screen's height, and can't
  be made wider or taller than the page needs: extra size only added empty space.
  Switching tabs resizes it to the new page.

## Working style

- When fixing a reported problem, think through the related cases and fix the whole
  class, so the maintainer doesn't have to report each one.

## Tests

`python3 -m pytest`. `tests/test_power.py::test_reads_this_mac` does a live
hardware read, and is skipped where there's no battery or SMC.
