"""Menu bar item: live wattage in the title, current readings in its menu.

A plain NSStatusItem with a native NSMenu. The Rust app used a Tauri tray icon
plus an NSPopover, and on macOS 27 AppKit swallowed the clicks so the panel
never opened; a standard status-item menu has no such dependency.

Every method here must run on the main thread (main.py dispatches with
AppHelper.callAfter).
"""

import AppKit
from AppKit import (
    NSAttributedString, NSColor, NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
    NSImage, NSImageSymbolConfiguration, NSMenu, NSMenuItem, NSMutableParagraphStyle,
    NSParagraphStyleAttributeName, NSStatusBar, NSTextTab, NSVariableStatusItemLength,
)
from Foundation import NSObject

FIGURE_SPACE = " "  # same width as a digit in a monospaced-digit font
ROWS = ("battery", "system", "screen", "chip", "adapter")
ROW_LABELS = {"battery": "Battery", "system": "System", "screen": "Screen",
              "chip": "Chip (CPU + GPU)", "adapter": "Adapter"}
VALUE_TAB_LOCATION = 250.0


def format_watts(value, pad_to=4):
    """'27.1 W', padded with figure spaces so the menu bar width stays put."""
    if not isinstance(value, (int, float)):
        return "— W"
    return f"{value:.1f}".rjust(pad_to, FIGURE_SPACE) + " W"


def format_minutes(minutes):
    if not minutes:
        return None
    hours, mins = divmod(int(minutes), 60)
    return f"{hours}:{mins:02d}"


def battery_status(sample):
    """Short human status, e.g. '63% · 1:04 to full'."""
    level = sample.get("battery_level")
    if level is None:
        return "No battery"
    remaining = format_minutes(sample.get("time_remaining_min"))
    if sample.get("fully_charged"):
        state = "Fully charged"
    elif sample.get("is_charging"):
        state = f"{remaining} to full" if remaining else "Charging"
    elif sample.get("external_connected"):
        state = "Not charging"
    else:
        state = f"{remaining} left" if remaining else "On battery"
    return f"{level}% · {state}"


def menu_bar_value(sample, settings):
    """The number shown in the menu bar for the user's chosen metric."""
    if settings.get("menu_bar_show_charging") and sample.get("is_charging"):
        return sample.get("system_in")
    return {
        "system": sample.get("system_load"),
        "screen": sample.get("screen_power"),
        "chip": sample.get("chip_power"),
    }.get(settings.get("menu_bar_metric"), sample.get("system_load"))


class _MenuTarget(NSObject):
    """Receives menu item actions and forwards them to Python callbacks."""

    def openDashboard_(self, sender):
        self.on_open()

    def quitApp_(self, sender):
        self.on_quit()


class StatusBar:
    def __init__(self, on_open, on_quit):
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._button = self._item.button()
        size = NSFont.menuBarFontOfSize_(0).pointSize()
        self._button.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(size, AppKit.NSFontWeightRegular))
        self._button.setImagePosition_(AppKit.NSImageLeading)
        self._button.setTitle_(format_watts(None))
        self._button.setToolTip_("Pythonflow")

        bolt = NSImage.imageWithSystemSymbolName_accessibilityDescription_("bolt.fill", "Charging")
        config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size - 2, AppKit.NSFontWeightRegular)
        self._bolt = bolt.imageWithSymbolConfiguration_(config) if bolt else None
        if self._bolt:
            self._bolt.setTemplate_(True)
        self._showing_bolt = False

        self._target = _MenuTarget.alloc().init()
        self._target.on_open = on_open
        self._target.on_quit = on_quit

        self._menu_font = NSFont.menuFontOfSize_(0)
        self._value_font = NSFont.monospacedDigitSystemFontOfSize_weight_(
            self._menu_font.pointSize(), AppKit.NSFontWeightRegular)
        style = NSMutableParagraphStyle.alloc().init()
        style.setTabStops_([NSTextTab.alloc().initWithTextAlignment_location_options_(
            AppKit.NSTextAlignmentRight, VALUE_TAB_LOCATION, {})])
        self._paragraph = style

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self._rows = {}
        for key in ROWS:
            row = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(ROW_LABELS[key], None, "")
            row.setEnabled_(False)
            menu.addItem_(row)
            self._rows[key] = row
            self._set_row(key, "—")
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(self._action_item("Open Dashboard", "openDashboard:", "d"))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(self._action_item("Quit Pythonflow", "quitApp:", "q"))
        self._item.setMenu_(menu)
        self._menu = menu

    def _action_item(self, title, selector, key):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, key)
        item.setTarget_(self._target)
        return item

    def _set_row(self, key, value):
        text = f"{ROW_LABELS[key]}\t{value}"
        title = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(text, {
            NSFontAttributeName: self._menu_font,
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
            NSParagraphStyleAttributeName: self._paragraph,
        })
        start = len(ROW_LABELS[key]) + 1
        title.addAttributes_range_({
            NSFontAttributeName: self._value_font,
            NSForegroundColorAttributeName: NSColor.labelColor(),
        }, (start, len(text) - start))
        self._rows[key].setAttributedTitle_(title)

    def update(self, sample, settings):
        charging_title = settings.get("menu_bar_show_charging") and sample.get("is_charging")
        self._button.setTitle_(format_watts(menu_bar_value(sample, settings)))
        if bool(charging_title) != self._showing_bolt and self._bolt:
            self._button.setImage_(self._bolt if charging_title else None)
            self._showing_bolt = bool(charging_title)

        self._set_row("battery", battery_status(sample))
        self._set_row("system", format_watts(sample.get("system_load"), pad_to=0))
        self._set_row("screen", format_watts(sample.get("screen_power"), pad_to=0))
        self._set_row("chip", format_watts(sample.get("chip_power"), pad_to=0))
        adapter = sample.get("adapter")
        if sample.get("external_connected") and adapter:
            name = adapter.get("name") or (f"{adapter['watts']}W adapter" if adapter.get("watts") else "Adapter")
            self._set_row("adapter", f"{name} · {format_watts(sample.get('adapter_power'), pad_to=0)}")
        else:
            self._set_row("adapter", "Not connected")
        self._rows["battery"].setHidden_(not sample.get("has_battery"))
