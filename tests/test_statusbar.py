"""Menu bar text formatting (pure functions; no status item is created)."""

from statusbar import FIGURE_SPACE, battery_status, format_watts, menu_bar_value


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


def test_menu_bar_value_prefers_adapter_input_while_charging():
    s = {"is_charging": True, "system_in": 105.0, "system_load": 27.0, "screen_power": 11.0}
    assert menu_bar_value(s, {"menu_bar_show_charging": True, "menu_bar_metric": "system"}) == 105.0
    assert menu_bar_value(s, {"menu_bar_show_charging": False, "menu_bar_metric": "screen"}) == 11.0
    assert menu_bar_value({**s, "is_charging": False}, {"menu_bar_show_charging": True,
                                                        "menu_bar_metric": "system"}) == 27.0
