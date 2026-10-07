"""Pythonflow — a menu bar power monitor for macOS.

Launcher: logging, single-instance lock, sampler thread, menu bar item and the
pywebview dashboard window.

There is deliberately no HTTP server: the dashboard page talks to Python over
pywebview's in-process bridge, so the app opens no network ports. Its only network
request is the daily update check (updater.py). All data stays in
~/Library/Application Support/Pythonflow.
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
VERSION = "1.1.0"  # the build reads it from here; see the release checklist in DEVELOPMENT.md
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
# Sampling is the app's whole idle cost, so it only runs fast while someone is looking.
DASHBOARD_INTERVAL = 1.0  # seconds, while the dashboard page is visible
BACKGROUND_INTERVAL = 2.5  # seconds, menu bar only
DASHBOARD_IDLE_AFTER = 3.0  # seconds without a poll from the page before dropping back
LIVE_BUFFER_POINTS = 900  # 15 minutes at the dashboard interval
# The dashboard opens as tall as its Now page, or the screen, whichever is smaller.
# The page then reports its exact size (Api.set_content_size); this just avoids a jump.
WINDOW_WIDTH = 1060
NOW_PAGE_HEIGHT = 1040

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
    def __init__(self, reader, on_sample):
        super().__init__(name="pythonflow-sampler", daemon=True)
        self._reader = reader
        self._on_sample = on_sample
        self._last_poll = float("-inf")
        self._wake = threading.Event()

    def interval(self):
        watched = time.monotonic() - self._last_poll < DASHBOARD_IDLE_AFTER
        return DASHBOARD_INTERVAL if watched else BACKGROUND_INTERVAL

    def dashboard_polled(self):
        """The page only polls while it is visible (not hidden, minimised or covered),
        so its polls are what keep the faster rate on."""
        was_background = self.interval() == BACKGROUND_INTERVAL
        self._last_poll = time.monotonic()
        if was_background:
            self._wake.set()  # sample now rather than at the end of a 2.5 s wait

    def run(self):
        import objc

        while True:
            started = time.monotonic()
            with objc.autorelease_pool():
                try:
                    self._on_sample(self._reader.read())
                except Exception:
                    log.exception("Sampling failed")
            self._wake.wait(max(0.05, self.interval() - (time.monotonic() - started)))
            self._wake.clear()


class Api:
    """Methods callable from the dashboard page as window.pywebview.api.<name>().

    pywebview exposes every public attribute recursively, so everything that is
    not meant for JavaScript lives behind a leading underscore.
    """

    def __init__(self, app):
        self._app = app

    def get_live(self, since_seq=0):
        self._app._sampler.dashboard_polled()
        return self._app._live.since(int(since_seq or 0))

    def get_settings(self):
        return self._app._page_settings()

    def save_settings(self, changes):
        changes = dict(changes) if isinstance(changes, dict) else {}
        if "launch_at_login" in changes:  # lives in ServiceManagement, not settings.json
            self._app._login_item.set_enabled(bool(changes.pop("launch_at_login")))
        self._app._settings.update(changes)
        return self._app._page_settings()

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
                "history_recording": self._app._settings.get()["record_history"],
                "update_unavailable_reason": self._app._updater.unavailable_reason}

    def set_content_size(self, width, height, fit=False):
        """The page reports the size it needs; the window can't be made bigger.
        `fit` (page loaded or tab changed) also makes the window that tall."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._app._fit_window, float(width), float(height), bool(fit))

    def log_error(self, message):
        """Page errors land in app.log next to the Python ones."""
        log.warning("Dashboard error: %s", str(message)[:1000])


class PythonflowApp:
    def __init__(self):
        import launch
        from history import ChargingRecorder, HistoryStore
        from power import PowerReader
        from settings import Settings
        from updater import Updater

        self._settings = Settings(DATA_DIR / "settings.json", legacy_path=LEGACY_PREFS)
        self._store = HistoryStore(DATA_DIR / "history.db")
        self._live = LiveState()
        self._reader = PowerReader()
        prefs = self._settings.get()
        self._recorder = ChargingRecorder(self._store, enabled=prefs["record_history"])
        self._sampler = Sampler(self._reader, self._on_sample)
        self._app_path = launch.bundle_path()
        self._login_item = launch.LoginItem(self._app_path, DATA_DIR)
        self._updater = Updater(
            self._app_path, VERSION, settings=self._settings, data_dir=DATA_DIR,
            is_busy=self._busy_for_update, on_status=self._on_update_status, quit_app=self.quit)
        self._window = None
        self._page_size = None  # (width, height) the dashboard page last asked for
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

    def _page_settings(self):
        """Settings plus "open at login" (None where it isn't available, e.g. from source)."""
        login = self._login_item.enabled if self._login_item.available else None
        return {**self._settings.get(), "launch_at_login": login}

    def _on_settings_changed(self, values):
        from PyObjCTools import AppHelper

        self._recorder.enabled = values["record_history"]
        AppHelper.callAfter(self._apply_theme, values["theme"])

    # ── window lifecycle (main thread) ──

    def _apply_theme(self, theme):
        import AppKit

        if self._window is None or self._window.native is None:
            return
        name = {"light": AppKit.NSAppearanceNameAqua, "dark": AppKit.NSAppearanceNameDarkAqua}.get(theme)
        self._window.native.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name) if name else None)

    def _fit_window(self, page_width=None, page_height=None, grow=False):
        """Cap the window at the size the current page needs: any wider only adds empty
        space at the sides, any taller at the bottom. Shrink it if it's bigger (e.g.
        after switching to a shorter tab). With `grow` (the page loaded, the tab
        changed, or the dashboard opened), also make it as tall as the page, as far as
        the screen allows. A window that was exactly as tall as the page keeps fitting
        it when the page grows; one dragged shorter stays that way. The top edge stays
        put unless that would push the window off the screen."""
        import AppKit

        window = self._window.native if self._window else None
        if page_width is not None:
            self._page_size = (page_width, page_height)
        if window is None or self._page_size is None:
            return
        # pywebview sets the minimum as a frame size (title bar included).
        min_size = window.contentRectForFrameRect_(((0, 0), window.minSize())).size
        max_width = max(min_size.width, self._page_size[0])
        max_height = max(min_size.height, self._page_size[1])
        was_fitting = abs(window.contentMaxSize().height - window.contentRectForFrameRect_(window.frame()).size.height) < 1
        window.setContentMaxSize_((max_width, max_height))

        content = window.contentRectForFrameRect_(window.frame())
        width = min(content.size.width, max_width)
        height = max_height if grow or was_fitting else min(content.size.height, max_height)
        if abs(width - content.size.width) < 0.5 and abs(height - content.size.height) < 0.5:
            return
        content.origin.y += content.size.height - height
        content.size = (width, height)
        frame = window.frameRectForContentRect_(content)
        screen = window.screen() or AppKit.NSScreen.mainScreen()
        if screen is not None:
            visible = screen.visibleFrame()
            if frame.size.height > visible.size.height:
                frame.origin.y += frame.size.height - visible.size.height
                frame.size.height = visible.size.height
            top = visible.origin.y + visible.size.height
            frame.origin.y = min(max(frame.origin.y, visible.origin.y), top - frame.size.height)
        window.setFrame_display_animate_(frame, True, bool(window.isVisible()))

    @staticmethod
    def _initial_height():
        import AppKit

        screen = AppKit.NSScreen.mainScreen()
        if screen is None:
            return NOW_PAGE_HEIGHT
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        fits = AppKit.NSWindow.contentRectForFrameRect_styleMask_(screen.visibleFrame(), style).size.height
        return int(min(NOW_PAGE_HEIGHT, fits))

    def show_dashboard(self):
        import AppKit

        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        self._apply_theme(self._settings.get()["theme"])
        self._window.show()
        self._fit_window(grow=True)

    def _busy_for_update(self):
        """Don't relaunch into an update while the dashboard is open or a charge is
        being recorded (a session in progress lives in memory until it ends)."""
        window = self._window.native if self._window else None
        shown = window is not None and (window.isVisible() or window.isMiniaturized())
        return shown or self._recorder.recording

    def _on_update_status(self, status, version):
        if self._statusbar is not None:
            self._statusbar.set_update_status(status, version)

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

        import launch
        from statusbar import StatusBar

        if self._app_path and not launch.in_applications_folder(self._app_path):
            if launch.offer_to_move(self._app_path):
                return  # the copy in Applications starts once this one has quit

        prefs = self._settings.get()
        # Relaunching into an update shouldn't pop the dashboard up.
        start_hidden = not prefs["open_dashboard_at_launch"] or launch.BACKGROUND_ARGUMENT in sys.argv
        if start_hidden:
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._install_quit_handler()

        html = (_app_files_dir() / "index.html").read_text(encoding="utf-8")
        self._window = webview.create_window(
            APP_NAME, html=html, js_api=Api(self), width=WINDOW_WIDTH, height=self._initial_height(),
            min_size=(860, 600), hidden=start_hidden, background_color=self._background_color(),
        )
        self._window.events.closing += self._on_closing
        # Non-locking pywebview events fire on a worker thread; AppKit needs the main one.
        self._window.events.loaded += lambda: AppHelper.callAfter(
            self._apply_theme, self._settings.get()["theme"])

        self._statusbar = StatusBar(on_open=self.show_dashboard, on_check_updates=self._updater.check_now,
                                    on_quit=self.quit)
        self._sampler.start()
        threading.Thread(target=self._import_legacy_history, name="legacy-import", daemon=True).start()
        self._login_item.turn_on_by_default()
        self._updater.start()

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
