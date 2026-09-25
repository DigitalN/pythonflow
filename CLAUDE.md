# Pythonflow

## Debugging

Application logs are written to `~/Library/Logs/Pythonflow/app.log` (rotating,
3 x 1 MB). Dashboard (JavaScript) errors are forwarded there too. When the user
reports a bug, **read the log first** before asking questions.

`~/Library/Logs/Powerflow/` belongs to the original Rust app (`powerflow.log`)
and to the first Python build, which ran under the old name.

To reproduce without touching the user's data, run with
`PYTHONFLOW_DATA_DIR=<tmp folder>` (logs then go to `<tmp folder>/Logs/`).

## Architecture

- **Launcher**: `Application Files/main.py`. Handles logging, the single-instance
  lock, the sampler thread, the menu bar item, the pywebview window, and the `Api`
  class exposed to the page.
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
  save.
- **Frontend**: `Application Files/index.html`. A single page in vanilla JS with a
  hand-rolled canvas chart and no external resources.
- **Build**: `Build Pythonflow.command` runs PyInstaller with
  `Application Files/Pythonflow.spec` and installs the result in
  `/Applications/Pythonflow.app` (a self-contained bundle).

## Key rules

- **No network, ever.** Don't add an HTTP server (unlike ReconciliationTool, the
  page uses pywebview's `js_api` bridge), update checks, CDNs, or remote fonts.
  The CSP in `index.html` sets `connect-src 'none'`. `'unsafe-eval'` is there only
  because pywebview returns API results via `eval`.
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
- SMC `PHPC` is chip (CPU + GPU package) power, **not** "heatpipe" or fan power,
  despite Apple's key name. Verified: it rises about 11 W under full CPU load while
  the fans stay at minimum.
- `~/Documents` is iCloud-synced on this machine. Finder metadata blocks
  `codesign`, so the build scripts run `xattr -cr` and then ad-hoc sign.

## Tests

`python3 -m pytest`. `tests/test_power.py::test_reads_this_mac` does a live
hardware read, and is skipped where there's no battery or SMC.
