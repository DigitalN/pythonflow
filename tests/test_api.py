"""Methods the dashboard page calls (no window or app is created)."""

from PyObjCTools import AppHelper

from main import CHARGING_SETTINGS_URL, Api


def test_charge_limit_button_opens_charging(monkeypatch):
    calls = []
    monkeypatch.setattr(AppHelper, "callAfter", lambda func, *args: calls.append((func, args)))
    Api(app=None).open_charging_settings()
    [(func, (url,))] = calls
    assert func.__name__ == "openURL_"
    assert url is not None and url.absoluteString() == CHARGING_SETTINGS_URL
    assert url.scheme() == "x-apple.systempreferences"
    assert url.absoluteString().endswith("?charging")
