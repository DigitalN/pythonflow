"""Settings validation, persistence and migration from the Rust app's preferences."""

import json

from settings import DEFAULTS, Settings, from_legacy_preferences, validate


def test_validate_fills_defaults_and_drops_junk():
    assert validate(None) == DEFAULTS
    s = validate({"menu_bar_metric": "none", "theme": "neon", "update_interval_ms": "fast",
                  "record_history": "yes", "unknown": 1})
    assert s == DEFAULTS


def test_interval_is_clamped():
    assert validate({"update_interval_ms": 1})["update_interval_ms"] == 500
    assert validate({"update_interval_ms": 86_400_000})["update_interval_ms"] == 60_000  # stalled the old app
    assert validate({"update_interval_ms": True})["update_interval_ms"] == DEFAULTS["update_interval_ms"]


def test_legacy_preferences_are_imported(tmp_path):
    legacy = tmp_path / "preference.json"
    legacy.write_text(json.dumps({"statusBarItem": "screen", "statusBarShowCharging": False,
                                  "language": "en", "theme": "dark", "updateInterval": 500}))
    s = Settings(tmp_path / "settings.json", legacy_path=legacy).get()
    assert (s["menu_bar_metric"], s["menu_bar_show_charging"], s["theme"], s["update_interval_ms"]) == \
        ("screen", False, "dark", 500)
    assert json.loads((tmp_path / "settings.json").read_text()) == s


def test_legacy_heatpipe_metric_becomes_chip():
    assert from_legacy_preferences({"statusBarItem": "heatpipe"})["menu_bar_metric"] == "chip"
    assert validate({"menu_bar_metric": "chip"})["menu_bar_metric"] == "chip"


def test_bad_legacy_value_falls_back_to_default():
    mapped = from_legacy_preferences({"statusBarItem": "none", "updateInterval": 86_400_000})
    s = validate({**DEFAULTS, **{k: v for k, v in mapped.items() if v is not None}})
    assert s["menu_bar_metric"] == "system" and s["update_interval_ms"] == 60_000


def test_update_persists_and_notifies_only_on_change(tmp_path):
    store = Settings(tmp_path / "settings.json")
    seen = []
    store.on_change(seen.append)
    store.update({"theme": "light"})
    store.update({"theme": "light"})
    store.update({"update_interval_ms": 10})
    assert [v["theme"] for v in seen] == ["light", "light"] and seen[-1]["update_interval_ms"] == 500
    assert Settings(tmp_path / "settings.json").get()["theme"] == "light"


def test_corrupt_file_uses_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    assert Settings(tmp_path / "settings.json").get() == DEFAULTS
