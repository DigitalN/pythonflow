"""Pythonflow — a menu bar power monitor for macOS.

Launcher: logging, single-instance lock, sampler thread, menu bar item and the
pywebview dashboard window.

There is deliberately no HTTP server: the dashboard page talks to Python over
pywebview's in-process bridge, so the app opens no network ports and makes no
network requests. All data stays in ~/Library/Application Support/Pythonflow.
"""

import fcntl
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Pythonflow"
VERSION = "1.0.0"
if os.environ.get("PYTHONFLOW_DATA_DIR"):  # for development/testing against a throwaway folder
    DATA_DIR = Path(os.environ["PYTHONFLOW_DATA_DIR"]).expanduser()
    LOG_DIR = DATA_DIR / "Logs"
    LEGACY_DIR = DATA_DIR  # tests put copies of the old app's files here
else:
    DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
    LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
    LEGACY_DIR = Path.home() / "Library" / "Application Support" / "Powerflow"
LOG_PATH = LOG_DIR / "app.log"
# Files written by the original Rust app (Powerflow 0.2.x), imported read-only on first launch.
LEGACY_DB = LEGACY_DIR / "db.sqlite"
LEGACY_PREFS = LEGACY_DIR / "tauri-plugin-pinia" / "preference.json"
LIVE_BUFFER_POINTS = 1800  # 15 minutes at the fastest (0.5 s) interval

log = logging.getLogger("pythonflow")


def _app_files_dir():
    """Directory holding index.html — the PyInstaller bundle when frozen, else this folder."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])
    logging.getLogger("pywebview").setLevel(logging.WARNING)


def _acquire_single_instance_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(DATA_DIR / ".lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle  # held open (and locked) for the life of the process


class LiveState:
    """Latest sample plus a ring buffer of chart points, shared with the page."""

    def __init__(self):
        self._lock = threading.Lock()
        self._seq = 0
        self._latest = None
        self._points = deque(maxlen=LIVE_BUFFER_POINTS)

    def push(self, s):
        point = [round(s["t"], 2), s["system_load"], s["system_in"], s["battery_power"],
                 s["screen_power"], s["chip_power"]]
        with self._lock:
            self._seq += 1
            self._latest = s
            self._points.append((self._seq, point))

    def since(self, seq):
        with self._lock:
            return {
                "seq": self._seq,
                "latest": self._latest,
                "point_fields": ["t", "system_load", "system_in", "battery_power", "screen_power", "chip_power"],
                "points": [p for n, p in self._points if n > seq],
            }


class Sampler(threading.Thread):
    def __init__(self, reader, interval_ms, on_sample):
        super().__init__(name="pythonflow-sampler", daemon=True)
        self._reader = reader
        self._on_sample = on_sample
        self._interval = interval_ms / 1000
        self._wake = threading.Event()

    def set_interval(self, interval_ms):
        self._interval = interval_ms / 1000
        self._wake.set()

    def run(self):
        import objc

        while True:
            started = time.monotonic()
            with objc.autorelease_pool():
                try:
                    self._on_sample(self._reader.read())
                except Exception:
                    log.exception("Sampling failed")
            self._wake.wait(max(0.05, self._interval - (time.monotonic() - started)))
            self._wake.clear()


class Api:
    """Methods callable from the dashboard page as window.pywebview.api.<name>().

    pywebview exposes every public attribute recursively, so everything that is
    not meant for JavaScript lives behind a leading underscore.
    """

    def __init__(self, app):
        self._app = app

    def get_live(self, since_seq=0):
        return self._app._live.since(int(since_seq or 0))

    def get_settings(self):
        return self._app._settings.get()

    def save_settings(self, changes):
        return self._app._settings.update(changes)

    def list_sessions(self):
        return self._app._store.list_sessions()

    def get_session(self, session_id):
        return self._app._store.get_session(session_id)

    def delete_session(self, session_id):
        return self._app._store.delete_session(session_id)

    def clear_history(self):
        count = self._app._store.clear()
        log.info("Deleted all charging history (%d sessions)", count)
        return count

    def export_session_csv(self, session_id):
        import webview

        session = self._app._store.get_session(session_id)
        if session is None:
            return None
        day = datetime.fromtimestamp(session["started_at"]).strftime("%Y-%m-%d-%H%M")
        result = self._app._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=f"pythonflow-charge-{day}.csv",
            file_types=("CSV files (*.csv)",),
        )
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        self._app._store.export_csv(session_id, path)
        return path

    def reveal_data_folder(self):
        from AppKit import NSURL, NSWorkspace
        from PyObjCTools import AppHelper

        url = NSURL.fileURLWithPath_(str(DATA_DIR))
        AppHelper.callAfter(NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_, [url])

    def app_info(self):
        return {"version": VERSION, "data_dir": str(DATA_DIR), "log_path": str(LOG_PATH),
                "history_recording": self._app._settings.get()["record_history"]}

    def log_error(self, message):
        """Page errors land in app.log next to the Python ones."""
        log.warning("Dashboard error: %s", str(message)[:1000])


class PythonflowApp:
    def __init__(self):
        from history import ChargingRecorder, HistoryStore
        from power import PowerReader
        from settings import Settings

        self._settings = Settings(DATA_DIR / "settings.json", legacy_path=LEGACY_PREFS)
        self._store = HistoryStore(DATA_DIR / "history.db")
        self._live = LiveState()
        self._reader = PowerReader()
        prefs = self._settings.get()
        self._recorder = ChargingRecorder(self._store, enabled=prefs["record_history"])
        self._sampler = Sampler(self._reader, prefs["update_interval_ms"], self._on_sample)
        self._window = None
        self._statusbar = None
        self._quitting = False
        self._settings.on_change(self._on_settings_changed)

    # ── sampling (runs on the sampler thread) ──

    def _on_sample(self, sample):
        from PyObjCTools import AppHelper

        self._live.push(sample)
        self._recorder.feed(sample)
        if self._statusbar is not None:
            AppHelper.callAfter(self._statusbar.update, sample, self._settings.get())

    def _on_settings_changed(self, values):
        from PyObjCTools import AppHelper

        self._sampler.set_interval(values["update_interval_ms"])
        self._recorder.enabled = values["record_history"]
        AppHelper.callAfter(self._apply_theme, values["theme"])

    # ── window lifecycle (main thread) ──

    def _apply_theme(self, theme):
        import AppKit

        if self._window is None or self._window.native is None:
            return
        name = {"light": AppKit.NSAppearanceNameAqua, "dark": AppKit.NSAppearanceNameDarkAqua}.get(theme)
        self._window.native.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name) if name else None)

    def show_dashboard(self):
        import AppKit

        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        self._apply_theme(self._settings.get()["theme"])
        self._window.show()

    def _on_closing(self):
        """Closing the dashboard hides it; the app keeps running in the menu bar."""
        import AppKit

        if self._quitting:
            return True
        self._window.hide()
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        return False

    def quit(self):
        import AppKit

        log.info("Quitting")
        self._quitting = True
        AppKit.NSApp.terminate_(None)

    def _install_quit_handler(self):
        """Make ⌘Q in the dashboard quit the app instead of just hiding the window.

        pywebview's app delegate asks each window's `closing` handlers before
        terminating, and ours cancels closes (to hide the window instead).
        Replace that delegate method so a real quit request always wins.
        """
        import AppKit
        import objc
        from webview.platforms import cocoa

        app = self

        def applicationShouldTerminate_(self, sender):
            app._quitting = True
            return AppKit.NSTerminateNow

        objc.classAddMethods(cocoa.BrowserView.AppDelegate, [applicationShouldTerminate_])

    def _background_color(self):
        import AppKit

        theme = self._settings.get()["theme"]
        if theme == "system":
            best = AppKit.NSApp.effectiveAppearance().bestMatchFromAppearancesWithNames_(
                [AppKit.NSAppearanceNameAqua, AppKit.NSAppearanceNameDarkAqua])
            theme = "dark" if best == AppKit.NSAppearanceNameDarkAqua else "light"
        return "#1c1c1e" if theme == "dark" else "#f5f5f7"

    def _import_legacy_history(self):
        try:
            self._store.import_legacy(LEGACY_DB)
        except Exception:
            log.exception("Importing history from the previous version failed")

    def run(self):
        import AppKit
        import webview
        import webview.platforms.cocoa  # noqa: F401 — sets up NSApp so the menu bar item can exist early
        from PyObjCTools import AppHelper

        from statusbar import StatusBar

        prefs = self._settings.get()
        start_hidden = not prefs["open_dashboard_at_launch"]
        if start_hidden:
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._install_quit_handler()

        html = (_app_files_dir() / "index.html").read_text(encoding="utf-8")
        self._window = webview.create_window(
            APP_NAME, html=html, js_api=Api(self), width=1060, height=720, min_size=(860, 600),
            hidden=start_hidden, background_color=self._background_color(),
        )
        self._window.events.closing += self._on_closing
        # Non-locking pywebview events fire on a worker thread; AppKit needs the main one.
        self._window.events.loaded += lambda: AppHelper.callAfter(
            self._apply_theme, self._settings.get()["theme"])

        self._statusbar = StatusBar(on_open=self.show_dashboard, on_quit=self.quit)
        self._sampler.start()
        threading.Thread(target=self._import_legacy_history, name="legacy-import", daemon=True).start()

        log.info("Pythonflow %s started (dashboard %s)", VERSION, "hidden" if start_hidden else "shown")
        webview.start(private_mode=True, debug=False)
        os._exit(0)


def main():
    _setup_logging()
    lock = _acquire_single_instance_lock()
    if lock is None:
        log.info("Another Pythonflow instance is already running; exiting")
        return
    sys.path.insert(0, str(_app_files_dir()))
    try:
        PythonflowApp().run()
    except Exception:
        log.exception("Pythonflow failed to start")
        raise


if __name__ == "__main__":
    main()
