"""Menu bar text formatting, and what an update touches. No status item is created:
the update test swaps in a stand-in for it."""

from types import SimpleNamespace

import statusbar
from statusbar import FIGURE_SPACE, StatusBar, battery_status, format_watts, menu_bar_value


class _FakeButton:
    def __init__(self):
        self.titles = []

    def setTitle_(self, title):
        self.titles.append(title)

    def __getattr__(self, name):  # setFont_, setImage_ and the rest: ignored
        return lambda *args: None


def test_update_only_redraws_what_changed(monkeypatch):
    button = _FakeButton()
    item = SimpleNamespace(button=lambda: button, setMenu_=lambda menu: None)
    monkeypatch.setattr(statusbar, "NSStatusBar", SimpleNamespace(
        systemStatusBar=lambda: SimpleNamespace(statusItemWithLength_=lambda length: item)))
    menu_events = []
    bar = StatusBar(on_open=None, on_check_updates=None, on_quit=None,
                    on_menu_open=lambda: menu_events.append(bar.menu_open))
    system_row = bar._rows["system"]
    settings = {"menu_bar_metric": "system"}
    sample = {"system_load": 7.04, "screen_power": 2.0, "chip_power": 3.0, "has_battery": True,
              "battery_level": 80}

    bar.update(sample, settings)
    bar.update({**sample, "system_load": 6.98}, settings)  # still "7.0 W"
    assert button.titles == ["— W", f"{FIGURE_SPACE}7.0 W"]
    assert system_row.attributedTitle().string() == "System\t—"  # menu closed: rows untouched

    bar._target.menuNeedsUpdate_(bar._menu)  # what AppKit calls as the menu opens
    bar._target.menuWillOpen_(bar._menu)
    assert system_row.attributedTitle().string() == "System\t7.0 W"
    bar.update({**sample, "system_load": 9.5}, settings)
    assert system_row.attributedTitle().string() == "System\t9.5 W"  # open: rows follow along

    bar._target.menuDidClose_(bar._menu)
    bar.update({**sample, "system_load": 12.0}, settings)
    assert system_row.attributedTitle().string() == "System\t9.5 W"
    assert menu_events == [True, False]


def test_title_width_is_stable():
    assert format_watts(9.84) == f"{FIGURE_SPACE}9.8 W"
    assert len(format_watts(9.8)) == len(format_watts(27.1))
    assert format_watts(None) == "— W"


def test_battery_status_states():
    base = {"battery_level": 63, "time_remaining_min": 64, "fully_charged": False,
            "is_charging": True, "external_connected": True}
    assert battery_status(base) == "63% · 1:04 to full"
    assert battery_status({**base, "time_remaining_min": None}) == "63% · Charging"
    assert battery_status({**base, "is_charging": False}) == "63% · Not charging"
    assert battery_status({**base, "is_charging": False, "external_connected": False,
                           "time_remaining_min": 185}) == "63% · 3:05 left"
    assert battery_status({**base, "battery_level": 100, "fully_charged": True}) == "100% · Fully charged"
    assert battery_status({"battery_level": None}) == "No battery"


def test_battery_status_with_a_charge_limit():
    base = {"battery_level": 63, "time_remaining_min": 12, "fully_charged": False,
            "is_charging": True, "external_connected": True, "charge_limit": 80}
    assert battery_status(base) == "63% · 0:12 to 80%"
    assert battery_status({**base, "battery_level": 78, "is_charging": False,
                           "at_charge_limit": True}) == "78% · Held at 80% limit"
    assert battery_status({**base, "is_charging": False}) == "63% · Not charging"


def test_menu_bar_value_prefers_adapter_input_while_charging():
    s = {"is_charging": True, "system_in": 105.0, "system_load": 27.0, "screen_power": 11.0}
    assert menu_bar_value(s, {"menu_bar_show_charging": True, "menu_bar_metric": "system"}) == 105.0
    assert menu_bar_value(s, {"menu_bar_show_charging": False, "menu_bar_metric": "screen"}) == 11.0
    assert menu_bar_value({**s, "is_charging": False}, {"menu_bar_show_charging": True,
                                                        "menu_bar_metric": "system"}) == 27.0
