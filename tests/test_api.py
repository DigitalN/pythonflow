"""Methods the dashboard page calls (no window or app is created)."""

from types import SimpleNamespace

from PyObjCTools import AppHelper

from main import CHARGING_SETTINGS_URL, LOG_PATH, Api, LiveState


def test_log_file_button_shows_the_log_file(monkeypatch):
    calls = []
    monkeypatch.setattr(AppHelper, "callAfter", lambda func, *args: calls.append((func, args)))
    Api(app=None).reveal_log_file()
    [(func, ([url],))] = calls
    assert func.__name__ == "activateFileViewerSelectingURLs_"
    assert url.path() == str(LOG_PATH)


def test_charge_limit_button_opens_charging(monkeypatch):
    calls = []
    monkeypatch.setattr(AppHelper, "callAfter", lambda func, *args: calls.append((func, args)))
    Api(app=None).open_charging_settings()
    [(func, (url,))] = calls
    assert func.__name__ == "openURL_"
    assert url is not None and url.absoluteString() == CHARGING_SETTINGS_URL
    assert url.scheme() == "x-apple.systempreferences"
    assert url.absoluteString().endswith("?charging")


def test_check_for_updates_button_does_what_the_menu_item_does(monkeypatch):
    calls = []
    monkeypatch.setattr(AppHelper, "callAfter", lambda func, *args: calls.append((func, args)))
    updater = SimpleNamespace(check_now=lambda: None)
    Api(app=SimpleNamespace(_updater=updater)).check_for_updates()
    assert calls == [(updater.check_now, ())]


def test_live_data_carries_the_update_status_for_settings():
    app = SimpleNamespace(_updater=SimpleNamespace(status="downloading", version="1.2.5"),
                          _sampler=SimpleNamespace(dashboard_polled=lambda: None), _live=LiveState())
    assert Api(app=app).get_live(0)["update"] == ["downloading", "1.2.5"]
