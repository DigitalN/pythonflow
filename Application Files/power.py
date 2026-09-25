"""Read Mac power telemetry from IOKit (AppleSmartBattery) and the SMC.

Read-only. Uses public IOKit registry properties plus SMC sensor keys — no
private frameworks, no root, no network. Serial numbers and other identifying
registry values are never read.

Two sources, because neither is complete on its own:
  - SMC updates every second: system/adapter/screen/chip power, battery temp.
  - AppleSmartBattery (IORegistry) is authoritative for battery state, capacity
    and adapter details, but its power telemetry only refreshes every ~30 s.

macOS 27 removed AppleRawCurrentCapacity, AppleRawMaxCapacity, DesignCapacity,
AbsoluteCapacity and Temperature from the top level of AppleSmartBattery; the
mAh values now live in the nested BatteryData dict. `normalize()` reads the
nested dict first and falls back to the old top-level keys, and treats every
key as optional so a future schema change degrades a field instead of the app.
"""

import ctypes
import ctypes.util
import logging
import struct
import time

import objc

log = logging.getLogger(__name__)

# Registry keys we read. Deliberately excludes Serial, AdapterDetails.SerialString, etc.
BATTERY_KEYS = (
    "AdapterDetails", "Amperage", "AppleRawCurrentCapacity", "AppleRawMaxCapacity",
    "AvgTimeToEmpty", "AvgTimeToFull", "BatteryData", "CurrentCapacity", "CycleCount",
    "DesignCapacity", "ExternalConnected", "FullyCharged", "InstantAmperage", "IsCharging",
    "MaxCapacity", "PowerTelemetryData", "Temperature", "TimeRemaining", "UpdateTime", "Voltage",
)
ADAPTER_KEYS = ("Name", "Description", "Watts", "AdapterVoltage", "Current", "IsWireless")
BATTERY_DATA_KEYS = ("DesignCapacity", "FullChargeCapacity", "RemainingCapacity")
TELEMETRY_KEYS = ("AdapterEfficiencyLoss", "BatteryPower", "SystemLoad", "SystemPowerIn")

SMC_KEYS = (
    "PDTR",  # power delivered from the adapter into the system (W)
    "PSTR",  # total system power (W)
    "PPBR",  # battery discharge power (W)
    "PDBR",  # display backlight power (W)
    "PHPC",  # chip power (W): the CPU/GPU package on the heatpipe-cooled rail (not fans)
    "TB0T",  # battery temperature (°C)
    "CHCC",  # charging flag — unreliable on macOS 27, only a last resort
    "B0TE",  # minutes to empty
    "B0TF",  # minutes to full
)

TIME_UNKNOWN = 65535           # IOKit/SMC sentinel for "still estimating"
MAX_PLAUSIBLE_MINUTES = 100 * 60
BATTERY_REFRESH_SECONDS = 3.0  # AppleSmartBattery changes slowly; SMC is read every tick
SMC_RETRY_SECONDS = 30.0


# ── IOKit / CoreFoundation via ctypes ─────────────────────────────────────

_iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
_libc = ctypes.CDLL(None)

_iokit.IOServiceMatching.restype = ctypes.c_void_p
_iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
_iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
_iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
_iokit.IORegistryEntryCreateCFProperty.argtypes = [
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
]
_iokit.IOObjectRelease.restype = ctypes.c_int
_iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
_iokit.IOServiceOpen.restype = ctypes.c_int
_iokit.IOServiceOpen.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
]
_iokit.IOServiceClose.restype = ctypes.c_int
_iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
_iokit.IOConnectCallStructMethod.restype = ctypes.c_int
_iokit.IOConnectCallStructMethod.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
]
_cf.CFRelease.argtypes = [ctypes.c_void_p]
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]

_K_CFSTRING_ENCODING_UTF8 = 0x08000100
_MAIN_PORT_DEFAULT = 0
_TASK_SELF = ctypes.c_uint32.in_dll(_libc, "mach_task_self_").value

# CFStrings for the keys, created once and kept for the life of the process.
_CF_KEYS = {k: _cf.CFStringCreateWithCString(None, k.encode(), _K_CFSTRING_ENCODING_UTF8)
            for k in BATTERY_KEYS}


def _to_python(value):
    """Convert a bridged Foundation object tree into plain Python values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if hasattr(value, "keys"):
        return {str(k): _to_python(value[k]) for k in value.keys()}
    if isinstance(value, (list, tuple)) or hasattr(value, "count") and hasattr(value, "objectAtIndex_"):
        return [_to_python(v) for v in value]
    if hasattr(value, "bytes") and hasattr(value, "length"):
        return bytes(value)
    return value


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d} if isinstance(d, dict) else None


def read_battery_registry():
    """Return the AppleSmartBattery properties we use, or None on Macs without a battery.

    The caller must hold an autorelease pool (see PowerReader.read) — PyObjC
    bridging autoreleases objects, and a background thread never drains them
    otherwise (measured: ~2.5 KB leaked per call without one).
    """
    matching = _iokit.IOServiceMatching(b"AppleSmartBattery")
    service = _iokit.IOServiceGetMatchingService(_MAIN_PORT_DEFAULT, matching)  # consumes `matching`
    if not service:
        return None
    props = {}
    try:
        for key, cf_key in _CF_KEYS.items():
            ref = _iokit.IORegistryEntryCreateCFProperty(service, cf_key, None, 0)
            if not ref:
                continue
            try:
                props[key] = _to_python(objc.objc_object(c_void_p=ref))
            finally:
                _cf.CFRelease(ref)
    finally:
        _iokit.IOObjectRelease(service)

    # Keep only the nested fields we use, so nothing identifying is ever held.
    props["AdapterDetails"] = _pick(props.get("AdapterDetails"), ADAPTER_KEYS)
    props["BatteryData"] = _pick(props.get("BatteryData"), BATTERY_DATA_KEYS)
    props["PowerTelemetryData"] = _pick(props.get("PowerTelemetryData"), TELEMETRY_KEYS)
    return props


# ── SMC ───────────────────────────────────────────────────────────────────

class _SMCKeyInfo(ctypes.Structure):
    _fields_ = [("data_size", ctypes.c_uint32), ("data_type", ctypes.c_uint32),
                ("data_attributes", ctypes.c_uint8)]


class _SMCVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("build", ctypes.c_uint8),
                ("reserved", ctypes.c_uint8), ("release", ctypes.c_uint16)]


class _SMCPLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16),
                ("cpu", ctypes.c_uint32), ("gpu", ctypes.c_uint32), ("mem", ctypes.c_uint32)]


class _SMCKeyData(ctypes.Structure):
    # Mirrors the kernel's SMCKeyData_t; must be exactly 80 bytes.
    _fields_ = [("key", ctypes.c_uint32), ("vers", _SMCVersion), ("plimit", _SMCPLimit),
                ("key_info", _SMCKeyInfo), ("result", ctypes.c_uint8), ("status", ctypes.c_uint8),
                ("data8", ctypes.c_uint8), ("data32", ctypes.c_uint32),
                ("bytes", ctypes.c_uint8 * 32)]


assert ctypes.sizeof(_SMCKeyData) == 80

_SMC_SELECTOR = 2
_SMC_CMD_READ_BYTES = 5
_SMC_CMD_READ_KEYINFO = 9

# SMC fixed-point types: name -> (fraction bits, signed)
_FIXED_POINT = {
    "fp1f": (15, False), "fp2e": (14, False), "fp3d": (13, False), "fp4c": (12, False),
    "fp5b": (11, False), "fp6a": (10, False), "fp79": (9, False), "fp88": (8, False),
    "fpa6": (6, False), "fpc4": (4, False), "fpe2": (2, False),
    "sp1e": (14, True), "sp2d": (13, True), "sp3c": (12, True), "sp4b": (11, True),
    "sp5a": (10, True), "sp69": (9, True), "sp78": (8, True), "sp87": (7, True),
    "sp96": (6, True), "spa5": (5, True), "spb4": (4, True), "spf0": (0, True),
}


def _is_apple_silicon():
    """True on Apple silicon, including an x86_64 build running under Rosetta."""
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    rc = _libc.sysctlbyname(b"hw.optional.arm64", ctypes.byref(value), ctypes.byref(size), None, 0)
    return rc == 0 and value.value == 1


def decode_smc_value(data_type, raw, little_endian):
    """Decode raw SMC bytes. Apple silicon SMCs are little-endian, Intel big-endian."""
    order = "<" if little_endian else ">"
    t = data_type.strip()
    try:
        if t == "flt":
            return struct.unpack(order + "f", raw[:4])[0]
        if t in ("ui8", "flag"):
            return raw[0]
        if t == "si8":
            return struct.unpack("b", raw[:1])[0]
        if t == "ui16":
            return struct.unpack(order + "H", raw[:2])[0]
        if t == "si16":
            return struct.unpack(order + "h", raw[:2])[0]
        if t == "ui32":
            return struct.unpack(order + "I", raw[:4])[0]
        if t == "si32":
            return struct.unpack(order + "i", raw[:4])[0]
        if t in _FIXED_POINT:
            frac, signed = _FIXED_POINT[t]
            # Fixed-point values are big-endian on every SMC that uses them.
            n = struct.unpack(">h" if signed else ">H", raw[:2])[0]
            return n / (1 << frac)
    except struct.error:
        return None
    return None


class SMC:
    """Read-only connection to AppleSMC. Write support is intentionally omitted."""

    def __init__(self):
        matching = _iokit.IOServiceMatching(b"AppleSMC")
        service = _iokit.IOServiceGetMatchingService(_MAIN_PORT_DEFAULT, matching)
        if not service:
            raise OSError("AppleSMC service not found")
        conn = ctypes.c_uint32()
        kr = _iokit.IOServiceOpen(service, _TASK_SELF, 0, ctypes.byref(conn))
        _iokit.IOObjectRelease(service)
        if kr != 0:
            raise OSError(f"IOServiceOpen(AppleSMC) failed: {kr:#x}")
        self._conn = conn.value
        self._key_info = {}
        self._little_endian = _is_apple_silicon()

    def close(self):
        if self._conn:
            _iokit.IOServiceClose(self._conn)
            self._conn = 0

    def __del__(self):
        self.close()

    def _call(self, request):
        response = _SMCKeyData()
        size = ctypes.c_size_t(ctypes.sizeof(_SMCKeyData))
        kr = _iokit.IOConnectCallStructMethod(
            self._conn, _SMC_SELECTOR, ctypes.byref(request), ctypes.sizeof(_SMCKeyData),
            ctypes.byref(response), ctypes.byref(size),
        )
        if kr != 0:
            raise OSError(f"SMC call failed: {kr:#x}")
        return response

    def read(self, key):
        """Return the decoded value of a 4-char key, or None if this Mac doesn't have it."""
        code = struct.unpack(">I", key.encode("ascii"))[0]
        info = self._key_info.get(code)
        if info is None:
            response = self._call(_SMCKeyData(key=code, data8=_SMC_CMD_READ_KEYINFO))
            if response.result != 0:
                self._key_info[code] = False
                return None
            info = (response.key_info.data_size, struct.pack(">I", response.key_info.data_type).decode("latin-1"))
            self._key_info[code] = info
        if info is False:
            return None
        request = _SMCKeyData(key=code, data8=_SMC_CMD_READ_BYTES)
        request.key_info.data_size = info[0]
        response = self._call(request)
        if response.result != 0:
            return None
        return decode_smc_value(info[1], bytes(response.bytes[: info[0]]), self._little_endian)

    def read_all(self, keys=SMC_KEYS):
        values = {}
        for key in keys:
            value = self.read(key)
            if value is not None:
                values[key] = float(value)
        return values


# ── Normalization (pure; unit-tested with captured registry dicts) ────────

def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int) and value >= 2**63:
        value -= 2**64  # negative currents sometimes surface as unsigned 64-bit
    return value


def _positive(value):
    value = _num(value)
    return value if value is not None and value > 0 else None


def _valid_minutes(value):
    value = _num(value)
    if value is None or value <= 0 or value >= TIME_UNKNOWN or value > MAX_PLAUSIBLE_MINUTES:
        return None
    return int(round(value))


def _watts(milliwatts):
    value = _num(milliwatts)
    return value / 1000.0 if value is not None else None


def _round(value, digits=2):
    return round(value, digits) if isinstance(value, (int, float)) else value


def _is_charging(b, smc):
    if b.get("FullyCharged") is True:
        # macOS 27 keeps SMC CHCC at 1 while resting on the adapter at 100%.
        return False
    if b.get("ExternalConnected") is False:
        return False
    amps = _num(b.get("InstantAmperage"))
    if not amps:
        amps = _num(b.get("Amperage"))
    if amps:
        return amps > 0
    if isinstance(b.get("IsCharging"), bool):
        return b["IsCharging"]
    return smc.get("CHCC", 0) > 0


def normalize(battery, smc, now=None):
    """Merge a battery registry dict and SMC values into one sample.

    Power values are watts. `battery_power` is signed: positive while charging,
    negative while the battery is powering the Mac. Missing data is None.
    """
    now = time.time() if now is None else now
    b = battery or {}
    bd = b.get("BatteryData") or {}
    ptd = b.get("PowerTelemetryData") or {}
    has_battery = battery is not None

    if isinstance(b.get("ExternalConnected"), bool):
        external = b["ExternalConnected"]
    else:
        external = smc.get("PDTR", 0) > 1.0
    charging = _is_charging(b, smc) if has_battery else False
    fully_charged = bool(b.get("FullyCharged")) if has_battery else False

    # Power flow. SMC is live; the registry telemetry is a slower fallback.
    system_load = smc.get("PSTR", _watts(ptd.get("SystemLoad")))
    system_in = smc.get("PDTR", _watts(ptd.get("SystemPowerIn")))
    if not external:
        system_in = 0.0
    efficiency_loss = (_watts(ptd.get("AdapterEfficiencyLoss")) or 0.0) if external else 0.0

    battery_power = None
    if has_battery:
        if system_in is not None and system_load is not None:
            battery_power = system_in - system_load
            if not external and smc.get("PPBR", 0) > 0:
                battery_power = -smc["PPBR"]
        else:
            amps, volts = _num(b.get("InstantAmperage")), _num(b.get("Voltage"))
            if amps is not None and volts is not None:
                battery_power = amps * volts / 1e6
        if battery_power is not None and abs(battery_power) < 0.05:
            battery_power = 0.0

    # Charge level. CurrentCapacity/MaxCapacity is a percentage on Apple silicon
    # and mAh/mAh on older Intel Macs — the ratio is right for both.
    current, maximum = _num(b.get("CurrentCapacity")), _positive(b.get("MaxCapacity"))
    level = int(round(current / maximum * 100)) if current is not None and maximum else None
    if level is not None:
        level = max(0, min(100, level))

    design_mah = _positive(bd.get("DesignCapacity")) or _positive(b.get("DesignCapacity"))
    full_mah = _positive(bd.get("FullChargeCapacity")) or _positive(b.get("AppleRawMaxCapacity"))
    remaining_mah = _positive(bd.get("RemainingCapacity")) or _positive(b.get("AppleRawCurrentCapacity"))
    health = full_mah / design_mah * 100 if full_mah and design_mah else None

    temperature = smc.get("TB0T")
    if temperature is None or not 0 < temperature < 110:
        raw_temp = _num(b.get("Temperature"))  # pre-27 registry: centi-degrees
        temperature = raw_temp / 100 if raw_temp else None

    minutes = None
    if has_battery and not fully_charged and (charging or not external):
        candidates = [b.get("TimeRemaining")]
        if charging:
            candidates += [b.get("AvgTimeToFull"), smc.get("B0TF")]
        else:
            candidates += [b.get("AvgTimeToEmpty"), smc.get("B0TE")]
        minutes = next((m for m in map(_valid_minutes, candidates) if m is not None), None)

    adapter = None
    ad = b.get("AdapterDetails") or {}
    if external and ad:
        name = (ad.get("Name") or ad.get("Description") or "").strip() or None
        adapter = {
            "name": name,
            "watts": _positive(ad.get("Watts")),
            "voltage": _round((_num(ad.get("AdapterVoltage")) or 0) / 1000) or None,
            "current": _round((_num(ad.get("Current")) or 0) / 1000) or None,
            "wireless": bool(ad.get("IsWireless")),
        }

    amps = _num(b.get("InstantAmperage"))
    volts = _num(b.get("Voltage"))
    return {
        "t": now,
        "has_battery": has_battery,
        "has_smc": bool(smc),
        "is_charging": charging,
        "fully_charged": fully_charged,
        "external_connected": external,
        "battery_level": level,
        "time_remaining_min": minutes,
        "system_load": _round(system_load),
        "system_in": _round(system_in),
        "battery_power": _round(battery_power),
        "adapter_power": _round((system_in or 0) + efficiency_loss) if external else 0.0,
        "efficiency_loss": _round(efficiency_loss),
        "screen_power": _round(smc.get("PDBR")),
        "chip_power": _round(smc.get("PHPC")),
        "temperature": _round(temperature, 1),
        "cycle_count": _num(b.get("CycleCount")),
        "design_capacity": design_mah,
        "max_capacity": full_mah,
        "current_capacity": remaining_mah,
        "health_pct": _round(health, 1),
        "voltage": _round(volts / 1000) if volts else None,
        "amperage": _round(amps / 1000) if amps is not None else None,
        "adapter": adapter,
    }


class PowerReader:
    """Owns the SMC connection and battery-registry cache; call read() once per tick."""

    def __init__(self):
        self._smc = None
        self._smc_retry_at = 0.0
        self._battery = None
        self._battery_read_at = 0.0
        self._last_external = None
        self._open_smc()

    def _open_smc(self):
        try:
            self._smc = SMC()
            log.info("AppleSMC connected")
        except OSError as e:
            self._smc = None
            self._smc_retry_at = time.monotonic() + SMC_RETRY_SECONDS
            log.warning("AppleSMC unavailable (%s); using IORegistry telemetry only", e)

    def _read_smc(self):
        if self._smc is None and time.monotonic() >= self._smc_retry_at:
            self._open_smc()
        if self._smc is None:
            return {}
        try:
            return self._smc.read_all()
        except OSError as e:
            log.warning("SMC read failed (%s); reconnecting", e)
            self._smc.close()
            self._smc = None
            self._smc_retry_at = time.monotonic() + SMC_RETRY_SECONDS
            return {}

    def read(self):
        with objc.autorelease_pool():
            smc = self._read_smc()
            # Plugging in or unplugging shows up in SMC instantly; refresh the
            # registry right away so charge state doesn't lag the power numbers.
            external_hint = smc.get("PDTR", 0) > 1.0 if "PDTR" in smc else None
            stale = time.monotonic() - self._battery_read_at >= BATTERY_REFRESH_SECONDS
            if stale or (external_hint is not None and external_hint != self._last_external):
                try:
                    self._battery = read_battery_registry()
                except Exception:
                    log.exception("Reading AppleSmartBattery failed")
                self._battery_read_at = time.monotonic()
                self._last_external = external_hint
            return normalize(self._battery, smc)
