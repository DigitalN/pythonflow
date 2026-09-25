"""normalize() against registry shapes seen on real Macs, plus SMC decoding."""

import struct

import pytest

import power
from power import decode_smc_value, normalize

# AppleSmartBattery on macOS 27.0 (26A428), M1 Pro, charging on a 140 W adapter.
# AppleRaw*, DesignCapacity, AbsoluteCapacity and Temperature are gone from the top level.
MACOS_27 = {
    "AdapterDetails": {"Name": "140W USB-C Power Adapter", "Description": "pd charger", "Watts": 140,
                       "AdapterVoltage": 28000, "Current": 4990, "IsWireless": False},
    "Amperage": 8566, "AvgTimeToEmpty": 65535, "AvgTimeToFull": 77,
    "BatteryData": {"DesignCapacity": 8694, "FullChargeCapacity": 7798, "RemainingCapacity": 3042},
    "CurrentCapacity": 40, "CycleCount": 377, "ExternalConnected": True, "FullyCharged": False,
    "InstantAmperage": 8566, "IsCharging": True, "MaxCapacity": 100,
    "PowerTelemetryData": {"AdapterEfficiencyLoss": 3882, "BatteryPower": 105524,
                           "SystemLoad": 30883, "SystemPowerIn": 136407},
    "TimeRemaining": 77, "UpdateTime": 1790376450, "Voltage": 12319,
}
SMC_CHARGING = {"PDTR": 123.13, "PSTR": 28.63, "PPBR": 0.78, "PDBR": 10.43, "PHPC": 10.5,
                "TB0T": 33.4, "CHCC": 1.0, "B0TE": 65535.0, "B0TF": 71.0}

# Pre-27 Apple silicon: raw mAh values at the top level, no BatteryData.
LEGACY_APPLE_SILICON = {
    "AppleRawCurrentCapacity": 5932, "AppleRawMaxCapacity": 7756, "DesignCapacity": 8694,
    "CurrentCapacity": 81, "MaxCapacity": 100, "CycleCount": 376, "Temperature": 3059,
    "ExternalConnected": False, "IsCharging": False, "FullyCharged": False,
    "InstantAmperage": -1616, "TimeRemaining": 240, "Voltage": 12400,
}


def test_macos_27_reads_capacity_from_nested_battery_data():
    s = normalize(MACOS_27, SMC_CHARGING, now=1.0)
    assert s["battery_level"] == 40
    assert (s["design_capacity"], s["max_capacity"], s["current_capacity"]) == (8694, 7798, 3042)
    assert s["health_pct"] == pytest.approx(89.7, abs=0.05)
    assert s["cycle_count"] == 377
    assert s["temperature"] == 33.4  # from SMC; the registry no longer has it


def test_macos_27_power_flow_while_charging():
    s = normalize(MACOS_27, SMC_CHARGING, now=1.0)
    assert s["is_charging"] and s["external_connected"] and not s["fully_charged"]
    assert s["system_in"] == 123.13
    assert s["system_load"] == 28.63
    assert s["battery_power"] == pytest.approx(94.5, abs=0.01)
    assert s["efficiency_loss"] == 3.88
    assert s["adapter_power"] == pytest.approx(127.01, abs=0.01)
    assert s["time_remaining_min"] == 77
    assert s["adapter"] == {"name": "140W USB-C Power Adapter", "watts": 140, "voltage": 28.0,
                            "current": 4.99, "wireless": False}


def test_legacy_top_level_keys_still_work():
    smc = {"PSTR": 9.8, "PPBR": 9.9, "PDTR": 0.0, "TB0T": 30.6}
    s = normalize(LEGACY_APPLE_SILICON, smc, now=1.0)
    assert (s["design_capacity"], s["max_capacity"], s["current_capacity"]) == (8694, 7756, 5932)
    assert s["battery_level"] == 81
    assert not s["is_charging"] and not s["external_connected"]
    assert s["battery_power"] == -9.9  # discharging: PPBR, signed negative
    assert s["system_in"] == 0.0 and s["adapter_power"] == 0.0
    assert s["time_remaining_min"] == 240
    assert s["adapter"] is None


def test_registry_temperature_used_when_smc_missing():
    s = normalize(LEGACY_APPLE_SILICON, {}, now=1.0)
    assert s["temperature"] == 30.6  # centi-degrees -> °C
    assert s["has_smc"] is False
    assert s["battery_power"] == pytest.approx(-20.04, abs=0.01)  # amps × volts fallback


def test_intel_style_mah_capacity_gives_percentage():
    s = normalize({"CurrentCapacity": 3000, "MaxCapacity": 6000, "ExternalConnected": False}, {}, now=1.0)
    assert s["battery_level"] == 50


def test_fully_charged_on_adapter_is_not_charging_even_if_smc_says_so():
    battery = {**MACOS_27, "CurrentCapacity": 100, "FullyCharged": True, "IsCharging": False,
               "InstantAmperage": 0, "Amperage": 0, "TimeRemaining": 0}
    s = normalize(battery, {**SMC_CHARGING, "CHCC": 1.0}, now=1.0)
    assert s["fully_charged"] and not s["is_charging"]
    assert s["time_remaining_min"] is None


def test_amperage_sign_beats_stale_charging_flags():
    battery = {**MACOS_27, "IsCharging": True, "InstantAmperage": -250}
    assert normalize(battery, SMC_CHARGING, now=1.0)["is_charging"] is False


def test_unsigned_negative_current_is_treated_as_negative():
    battery = {**LEGACY_APPLE_SILICON, "ExternalConnected": True, "IsCharging": True,
               "InstantAmperage": 2**64 - 1500}
    s = normalize(battery, {}, now=1.0)
    assert s["is_charging"] is False
    assert s["amperage"] == -1.5


def test_time_remaining_sentinel_falls_back_then_gives_up():
    on_battery = {**LEGACY_APPLE_SILICON, "TimeRemaining": 65535, "AvgTimeToEmpty": 65535}
    assert normalize(on_battery, {"B0TE": 312.0}, now=1.0)["time_remaining_min"] == 312
    assert normalize(on_battery, {"B0TE": 65535.0}, now=1.0)["time_remaining_min"] is None


def test_plugged_in_not_charging_has_no_time_remaining():
    battery = {**MACOS_27, "IsCharging": False, "InstantAmperage": 0, "Amperage": 0}
    s = normalize(battery, {**SMC_CHARGING, "CHCC": 0.0}, now=1.0)
    assert s["external_connected"] and not s["is_charging"]
    assert s["time_remaining_min"] is None


def test_desktop_mac_without_battery():
    s = normalize(None, {"PSTR": 22.0, "PDTR": 23.5}, now=1.0)
    assert s["has_battery"] is False
    assert s["battery_level"] is None and s["battery_power"] is None
    assert s["system_load"] == 22.0


def test_empty_inputs_never_raise():
    s = normalize({}, {}, now=1.0)
    assert s["battery_level"] is None and s["system_load"] is None


def test_decode_smc_value_types():
    assert decode_smc_value("flt ", struct.pack("<f", 12.5), little_endian=True) == 12.5
    assert decode_smc_value("ui16", struct.pack("<H", 7821), little_endian=True) == 7821
    assert decode_smc_value("ui16", struct.pack(">H", 7821), little_endian=False) == 7821
    assert decode_smc_value("ui8 ", b"\x01", little_endian=True) == 1
    assert decode_smc_value("sp78", struct.pack(">h", int(30.5 * 256)), little_endian=True) == 30.5
    assert decode_smc_value("fpe2", struct.pack(">H", 4 * 100), little_endian=False) == 100.0
    assert decode_smc_value("????", b"\x00" * 4, little_endian=True) is None


def test_reads_this_mac():
    """Live read through the real IOKit/SMC path (skipped where there's nothing to read)."""
    sample = power.PowerReader().read()
    if not sample["has_battery"] and not sample["has_smc"]:
        pytest.skip("no battery registry or SMC on this machine")
    assert set(sample) >= {"system_load", "battery_level", "is_charging", "adapter"}
    if sample["has_battery"]:
        assert 0 <= sample["battery_level"] <= 100
