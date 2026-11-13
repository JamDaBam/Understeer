"""Drive the whole app against a fake wheelbase; run by test_ui.py.

Usage: ui_smoke.py <scratch dir>
Expects an isolated HOME and a display (test_ui.py starts Xvfb). Nothing
goes to the network: SteamGridDB is replaced. Every uncaught exception,
every ERROR log line and every failed check counts as a failure; the
exit code is the number of failures.
"""
import json
import logging
import os
import pathlib
import struct
import xml.etree.ElementTree as ET
import sys
import traceback
import zlib

scratch = pathlib.Path(sys.argv[1])
sys.argv = sys.argv[:1]

# A fake wheelbase: one sysfs folder with every setting as a plain file.
dev = scratch / "sysfs" / "ftec_tuning" / "0003:0EB7:0020.0005"
dev.mkdir(parents=True, exist_ok=True)
VALUES = {"SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0, "FFS": 1,
          "FEI": 30, "INT": 0, "NDP": 0, "NFR": 0, "NIN": 0, "SHO": 80,
          "DRI": 0, "brF": 50, "BLI": 0, "FUL": 0, "ACP": 1, "SLOT": 1, "RESET": 0}
for k, v in VALUES.items():
    (dev / k).write_text(f"{v}\n")
# Its HID device, with what newer drivers tell about it.
hid = scratch / "sysfs" / "hid-device"
hid.mkdir(exist_ok=True)
for k, v in {"fw_version": 1234, "rim_id": 0x0A, "fw_version_rim": 52}.items():
    (hid / k).write_text(f"{v}\n")
if not (dev / "device").exists():
    (dev / "device").symlink_to(hid)

(hid / "display").write_text("")
(hid / "display").chmod(0o220)          # write only, as the driver makes it

from understeer import capabilities, device, steamgriddb  # noqa: E402

device.SYSFS_GLOB = str(scratch / "sysfs" / "ftec_tuning" / "*")
device.RUMBLE_GLOB = str(scratch / "sysfs" / "none" / "*" / "rumble")
capabilities.HID_GLOB = str(scratch / "sysfs" / "none" / "*")
capabilities.LEDS_GLOB = str(scratch / "sysfs" / "none" / "*")
capabilities.WHEEL_GLOB = str(scratch / "sysfs" / "none" / "*")


def fake_search(term, key):
    """SteamGridDB stand-in: keys starting with "good" are accepted."""
    if not key or not key.startswith("good"):
        raise steamgriddb.Error("key", "refused (test)")
    return []


def offline(*_args, **_kwargs):
    raise steamgriddb.Error("network", "offline (test)")


steamgriddb.search = fake_search
steamgriddb.covers = steamgriddb.fetch = steamgriddb.download = offline

from understeer import app, background, backup, config, inputs, releases, sharing, trash, updates, log as session_log  # noqa: E402
from understeer.ui import cover_search, files, theme  # noqa: E402
from understeer.ui.setting_tile import SettingTile  # noqa: E402
from understeer.ui import window as window_module  # noqa: E402
from understeer.ui.report_dialog import ReportDialog  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

failures, steps_done = [], []
started = []            # set once the steps began
finished = []           # set once every step ran
STEP_MS = 300


EXPECTED_ERRORS = {"Could not save: Disk full",    # shown on purpose below
                   "Could not load preset: Broken.json holds no preset",
                   "Could not delete preset: Disk full"}


class Collect(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR and \
                record.getMessage() not in EXPECTED_ERRORS:
            failures.append(f"log: {record.getMessage()}")


sys.excepthook = lambda kind, value, tb: failures.append(
    "exception: " + "".join(traceback.format_exception(kind, value, tb)))


def check(cond, what):
    if not cond:
        failures.append(f"check failed: {what}")


class ChosenFile:
    """Stands in for a file dialog: its user picks path at once."""

    def __init__(self, path):
        self.path = path

    def open(self, _parent, _cancellable, callback):
        callback(self, None)

    def open_finish(self, _result):
        return Gio.File.new_for_path(str(self.path))

    save, save_finish = open, open_finish


def tiny_png():
    """A picture of one pixel that GTK can show."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def find_row(widget, title):
    """Depth-first search for a preferences row with this title."""
    if isinstance(widget, Adw.PreferencesRow) and widget.get_title() == title:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_row(child, title)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def close_dialogs(win):
    for w in Gtk.Window.list_toplevels():
        if w is not win and w.get_visible():
            w.close()
    for _i in range(10):
        d = win.get_visible_dialog()
        if d is None:
            break
        d.force_close()


def walk(widget):
    """The widget and everything inside it."""
    if widget is None:
        return
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def r_suffix(row):
    """Text of the labels in an Adw.ActionRow besides its title."""
    return " ".join(w.get_label() for w in walk(row)
                    if isinstance(w, Gtk.Label) and w.get_label() not in
                    (row.get_title(), row.get_subtitle(), ""))


def steps(win):
    """Each step runs on its own main loop turn, so the UI can settle."""
    yield "start", lambda: check(win.device_path is not None, "fake wheelbase found")
    yield "hardware functions", lambda: check(
        win.capabilities.can_write(capabilities.DISPLAY)
        and not win.capabilities.has(capabilities.ADVANCED_MODE),
        "display found, no advanced_mode in the fake wheelbase")

    def device_information():
        rows = win.device_info_rows
        check(win.base_row.get_subtitle().startswith("CSL DD · ")
              and win.base_row.get_subtitle().endswith("1234"),
              f"wheelbase shown: {win.base_row.get_subtitle()!r}")
        check(win.rim_row.get_visible()
              and win.rim_row.get_subtitle().startswith(
                  "ClubSport Steering Wheel Formula V2 · "),
              f"wheel shown: {win.rim_row.get_subtitle()!r}")
        check(rows["vendor_id"].get_subtitle() == "0x0EB7"
              and rows["product_id"].get_subtitle() == "0x0020",
              "USB ids from the folder name")
        check(rows["device_name"].get_subtitle() != "", "no uevent: still a text")
        # Unplugged: the rows say so; plugged in again: shown again
        path = win.device_path
        win.device_path = None
        win._show_wheel_info()
        check(win.base_row.get_subtitle() != "" and not win.rim_row.get_visible()
              and rows["vendor_id"].get_subtitle() != "0x0EB7",
              f"disconnected: {rows['vendor_id'].get_subtitle()!r}")
        win.device_path = path
        win._show_wheel_info()
        check(rows["vendor_id"].get_subtitle() == "0x0EB7", "reconnected")
        win._copy_device_info()            # must not raise
    yield "device information", device_information

    # Testing the display of the steering wheel
    display_file = hid / "display"

    def display_text():
        display_file.chmod(0o640)
        text = display_file.read_text()
        display_file.chmod(0o220)
        return text

    def display_usable():
        check(display_text() == "", "nothing written to the display at start")
        check(win.display_entry.get_sensitive()
              and win.display_clear_row.get_sensitive(), "display usable")
        check(win.display_entry.get_text() == "888"
              and win.display_show_btn.get_sensitive(), "888 ready to show")
        win.display_entry.set_text("")
        check(not win.display_show_btn.get_sensitive(), "no Show without text")
        win.display_entry.set_text("k")
        check(not win.display_show_btn.get_sensitive()
              and win.display_status_row.has_css_class("error")
              and "“k”" in win.display_status_row.get_subtitle(),
              f"k can't be shown: {win.display_status_row.get_subtitle()!r}")
        win.display_entry.set_text("1234")
        check(not win.display_show_btn.get_sensitive(), "four don't fit")
        win._show_display_text()            # as Enter in the entry
        check(display_text() == "", "invalid text not written")
        win.display_entry.set_text("P1.")
        check(win.display_show_btn.get_sensitive()
              and not win.display_status_row.has_css_class("error"),
              "valid text can be shown")
        check(display_text() == "", "typing writes nothing")
    yield "display usable", display_usable

    def display_show():
        win.display_show_btn.emit("clicked")
        check(display_text() == "P1.", f"text written: {display_text()!r}")
        check(win._toast_text.get_label() == "Sent to the display: P1.",
              f"told: {win._toast_text.get_label()!r}")
        win._check_capabilities()           # the periodic check
        check(display_text() == "P1.", "not written again by itself")
    yield "show text", display_show

    def display_clear():
        win._clear_display()
        check(display_text() == "   ", f"cleared: {display_text()!r}")
        check(win._toast_text.get_label() == "Display cleared",
              f"told: {win._toast_text.get_label()!r}")
    yield "clear display", display_clear

    def display_not_writable():
        if os.geteuid() == 0:
            return                          # root may write anyway
        display_file.chmod(0o444)
        win._check_capabilities()
        check(not win.display_entry.get_sensitive()
              and not win.display_show_btn.get_sensitive()
              and not win.display_clear_row.get_sensitive(),
              "not usable without permission")
        check("No permission" in win.display_status_row.get_subtitle(),
              f"told why: {win.display_status_row.get_subtitle()!r}")
        display_file.chmod(0o220)
        win._check_capabilities()
        check(win.display_entry.get_sensitive(), "usable again")
    yield "display not writable", display_not_writable

    def display_unplugged():
        display_file.unlink()               # gone before the next check
        win._show_display_text()
        check(not display_file.exists(), "no display file made")
        check(win._toast_text.get_label().startswith(
            "The display is not available any more"),
            f"told: {win._toast_text.get_label()!r}")
        check(not win.capabilities.has(capabilities.DISPLAY)
              and not win.display_entry.get_sensitive()
              and not win.display_clear_row.get_sensitive(),
              "not usable after the display went")
        check(win.display_status_row.get_subtitle().startswith("No display output"),
              f"told why: {win.display_status_row.get_subtitle()!r}")
        display_file.write_text("")
        display_file.chmod(0o220)
        win._check_capabilities()
        check(win.display_entry.get_sensitive(), "usable once it is back")
    yield "display unplugged", display_unplugged

    # Standard / Advanced tuning menu: a driver that offers advanced_mode
    mode_file = dev / "advanced_mode"
    others = {}

    def other_settings():
        return {p.name: p.read_text().strip() for p in dev.iterdir()
                if p.is_file() and p.name != "advanced_mode"}

    def shown():
        """(what the mode switch shows as asked for, as reached)"""
        return win.mode_switch.get_active(), win.mode_switch.get_state()

    yield "no mode switch", lambda: check(
        not win.mode_row.get_visible(), "mode switch hidden without advanced_mode")

    def driver_with_mode():
        # The steps below play the wheelbase and call _check_tuning_mode()
        # themselves. The window's own watch would also poll between two
        # steps and could take a mode event (and the reload after it) away
        # from the step that checks it; on a slow runner it does.
        GLib.source_remove(win._watch)
        win._watch = 0
        mode_file.write_text("1\n")
        win._check_capabilities()
        check(win.mode_row.get_visible() and win.mode_row.get_sensitive(),
              "mode switch shown and usable")
        check(shown() == (True, True), f"Advanced shown: {shown()}")
    yield "driver with tuning mode", driver_with_mode

    def to_standard():
        others.update(other_settings())
        win.mode_switch.set_active(False)       # as clicked
        check(mode_file.read_text() == "0", f"0 written: {mode_file.read_text()!r}")
        check(win.tuning_mode.switching and win.tuning_mode.advanced is True,
              "waiting for the wheelbase")
        check(shown() == (False, True), f"asked for, not reached yet: {shown()}")
        check(not win.mode_row.get_sensitive(), "no second switch meanwhile")
        # A CSL DD leaves its slot for the Auto setup when it switches
        (dev / "SLOT").write_text("auto\n")
    yield "switch to Standard", to_standard

    # The wheelbase confirms the switch; the app goes back to the slot and
    # reads the values again a moment later, so the checks follow a step on.
    yield "Standard reached", lambda: win._check_tuning_mode()

    def standard_confirmed():
        check(win.tuning_mode.advanced is False and not win.tuning_mode.switching,
              "switch confirmed")
        check(shown() == (False, False), f"Standard shown: {shown()}")
        check((dev / "SLOT").read_text() == "1" and win.device_values["SLOT"] == 1,
              f"back on the slot it was on: {(dev / 'SLOT').read_text()!r}")
        check("Slot changed" not in win._toast_text.get_label()
              if win._toast is not None else True,
              "going back to the slot is no change made on the wheelbase")
        check(other_settings() == others, "other settings left alone")
        check(win.status_label.get_label() == "In sync with device",
              f"status: {win.status_label.get_label()!r}")
        # The wheelbase ignores a request right after switching
        check(win.tuning_mode.busy and not win.mode_row.get_sensitive(),
              "not usable right after switching")
        win.tuning_mode._settled_at = 0
        win._check_tuning_mode()
        check(win.mode_row.get_sensitive(), "usable again later")
        mode_file.write_text("1\n")         # switched on the wheelbase
        (dev / "SLOT").write_text("auto\n")  # ... and left for Auto there
    yield "Standard confirmed", standard_confirmed

    def changed_on_the_wheelbase():
        win._check_tuning_mode()
        check(shown() == (True, True), f"switch follows the wheelbase: {shown()}")
        check((dev / "SLOT").read_text() == "1" and win.device_values["SLOT"] == 1,
              f"back on the slot after a switch on the wheelbase: "
              f"{(dev / 'SLOT').read_text()!r}")
        check(win._toast_text.get_label()
              == "Tuning menu changed on the wheelbase: Advanced",
              f"told: {win._toast_text.get_label()!r}")
        check(other_settings() == others, "other settings still left alone")
        check(not win.mode_row.get_sensitive(), "not usable right after that either")
        # A wheelbase that keeps its mode: the driver accepts the write
        others["real"] = device.write_values, device.MODE_SWITCH_SECONDS
        device.write_values, device.MODE_SWITCH_SECONDS = (lambda writes: []), 0.1
        win.tuning_mode._settled_at = 0
        win.mode_switch.set_active(False)
        check(win.tuning_mode.switching, "waiting for the wheelbase again")
    yield "mode changed on the wheelbase", changed_on_the_wheelbase

    def not_switched():
        device.write_values, device.MODE_SWITCH_SECONDS = others.pop("real")
        win._check_tuning_mode()
        check(not win.tuning_mode.switching and shown() == (True, True),
              f"switch back at the mode of the wheelbase: {shown()}")
        check(win._toast_text.get_label() == "Could not switch the tuning menu",
              f"told: {win._toast_text.get_label()!r}")
        check(win.mode_row.get_sensitive(), "usable after a failed switch")
        mode_file.chmod(0o444)
        win._check_capabilities()
        check(win.mode_row.get_visible() and not win.mode_row.get_sensitive(),
              "read only: shown, not usable")
    yield "wheelbase keeps its mode", not_switched

    def menu():
        model = win.slot_dropdown.get_model()
        return ([model.get_string(i) for i in range(model.get_n_items())],
                win.slot_dropdown.get_selected())

    def slots_per_mode():
        """Standard offers Auto and Custom only; each mode keeps the slot
        last used in it, a switch made on the wheelbase too."""
        mode_file.chmod(0o644)
        win._check_capabilities()
        win.slot_dropdown.set_selected(4)               # slot 5, Advanced
        check((dev / "SLOT").read_text() == "5", "slot 5 chosen")
        win._load_from_device()
        win._mode_slots.pop(False, None)    # never in Standard so far
        mode_file.write_text("0\n")         # Standard on the wheelbase
        (dev / "SLOT").write_text("auto\n")
        win.tuning_mode._settled_at = 0
        win._check_tuning_mode()
        check((dev / "SLOT").read_text().strip() == "auto",
              f"slot 5 isn't in the Standard menu: stays on Auto: "
              f"{(dev / 'SLOT').read_text()!r}")
        check(menu() == (["Auto", "Custom"], 0), f"Standard menu: {menu()}")
        # A window opened while the wheelbase is in Standard mode
        other = window_module.TunerWindow(application=win.get_application())
        try:
            model = other.slot_dropdown.get_model()
            names = [model.get_string(i) for i in range(model.get_n_items())]
            check(names == ["Auto", "Custom"], f"new window, Standard menu: {names}")
        finally:
            other.shut_down()
            other.destroy()
        win.standard_name_rows[1].set_text("C SET")
        check(menu()[0] == ["Auto", "C SET"], f"named: {menu()}")
        win.slot_dropdown.set_selected(1)               # C SET
        check((dev / "SLOT").read_text() == "1", "Custom is slot 1")
        win._load_from_device()
        mode_file.write_text("1\n")         # Advanced on the wheelbase
        (dev / "SLOT").write_text("auto\n")
        win.tuning_mode._settled_at = 0
        win._check_tuning_mode()
        check((dev / "SLOT").read_text() == "5", "back on slot 5 in Advanced")
        check(menu()[0][:2] == ["Slot 1", "Slot 2"], f"Advanced menu: {menu()}")
        check(sorted(win.slot_name_rows) == [0, 1, 2, 3, 4, 5],
              f"names for Auto and slots 1-5: {sorted(win.slot_name_rows)}")
        win._load_from_device()
        mode_file.write_text("0\n")         # Standard again
        (dev / "SLOT").write_text("auto\n")
        win.tuning_mode._settled_at = 0
        win._check_tuning_mode()
        check((dev / "SLOT").read_text() == "1", "back on C SET in Standard")
        win.standard_name_rows[1].set_text("")
        # back to Advanced on slot 1 for the steps after
        mode_file.write_text("1\n")
        (dev / "SLOT").write_text("1\n")
        win.tuning_mode._settled_at = 0
        win._check_tuning_mode()            # goes to slot 5 again
        (dev / "SLOT").write_text("1\n")
        win._load_from_device()
    yield "slots per mode", slots_per_mode

    def watch_again():
        win._watch = GLib.timeout_add(window_module.WATCH_MS, win._check_device)
    yield "watch the wheelbase again", watch_again

    def one_message_in_prefs():
        win._show_preferences("wheelbase")
        win._notify("first")
        first, dismissed = win._prefs_toast, []
        first.connect("dismissed", lambda t: dismissed.append(t))
        win._notify("second")
        check(dismissed == [first] and win._prefs_toast is not first,
              "a message in the preferences replaces the one shown")
        win.prefs.close()
    yield "one message in the preferences", one_message_in_prefs

    # Only a switch itself turns a setting on or off, not a click on its row
    switch_rows = []

    def click_rows_with_switches():
        win._show_preferences("wheelbase")
        rows = [w for w in walk(win.prefs) if isinstance(w, Adw.ActionRow)]
        check(not any(isinstance(r, Adw.SwitchRow) for r in rows),
              "no Adw.SwitchRow, which toggles on a click on the row")
        for row in rows:
            switches = [w for w in walk(row) if isinstance(w, Gtk.Switch)]
            if switches:
                switch_rows.append((row, switches[0], switches[0].get_active()))
                row.activate()
        check(len(switch_rows) >= 6, f"switches found: {len(switch_rows)}")
    yield "click rows with switches", click_rows_with_switches

    def rows_did_not_switch():
        for row, switch, before in switch_rows:
            check(switch.get_active() == before,
                  f"a click on the row switched “{row.get_title()}”")
        win.prefs.close()
    yield "rows did not switch", rows_did_not_switch

    def driver_without_mode():
        mode_file.unlink()
        win._check_capabilities()
        check(not win.mode_row.get_visible()
              and not win.capabilities.has(capabilities.ADVANCED_MODE),
              "mode switch hidden again")
        check(win.tuning_mode.path is None and win.tuning_mode.poll() is None,
              "nothing followed any more")
    yield "driver without tuning mode", driver_without_mode

    def slot_names():
        model = win.slot_dropdown.get_model()
        return [model.get_string(i) for i in range(model.get_n_items())]

    def auto_setup():
        """The Auto setup of the wheelbase, chosen there: the slot menu
        shows it instead of the slot before."""
        check(win.slot_dropdown.get_selected() == 0 and "Auto" not in slot_names(),
              f"slot 1 shown, no Auto listed: {slot_names()}")
        (dev / "SLOT").write_text("auto\n")
        win._check_device()
        check(win.device_values["SLOT"] == 0 and slot_names()[-1] == "Auto"
              and win.slot_dropdown.get_selected() == len(slot_names()) - 1,
              f"Auto shown as the active slot: {slot_names()}")
        check(win._toast_text.get_label() == "Slot changed on the wheelbase: Auto",
              f"told: {win._toast_text.get_label()!r}")
        win.slot_name_rows[0].set_text("A")
        check(slot_names()[-1] == "A", f"Auto named in Advanced: {slot_names()}")
        win.slot_name_rows[0].set_text("")
        written = (dev / "SLOT").read_text()
        win._on_slot_selected()             # Auto is shown, not chosen
        check((dev / "SLOT").read_text() == written, "nothing written for Auto")
        win.slot_dropdown.set_selected(1)   # slot 2, as chosen in the menu
        check((dev / "SLOT").read_text() == "2", "a slot chosen from Auto is written")
    yield "Auto setup", auto_setup

    def back_on_a_slot():
        check(win.device_values["SLOT"] == 2 and "Auto" not in slot_names()
              and win.slot_dropdown.get_selected() == 1,
              f"slot 2 shown, Auto gone: {slot_names()}")
        (dev / "SLOT").write_text("1\n")
        win._load_from_device()
        check(win.slot_dropdown.get_selected() == 0, "slot 1 again")
    yield "back on a slot", back_on_a_slot

    def live_write():
        win.rows["FF"].set(55, silent=False)
        win.flush_live()
        check((dev / "FF").read_text().strip() == "55", "FF written live")
    yield "live write", live_write

    def changed_on_wheel_base():
        (dev / "SHO").write_text("60\n")
    yield "change on the wheelbase", changed_on_wheel_base
    yield "change followed", lambda: check(win.rows["SHO"].get() == 60,
                                           "control follows the wheelbase")

    def steering_auto():
        (dev / "SEN").write_text("2530\n")     # how a CSL DD reports Auto
        win._load_from_device()
        sen = win.rows["SEN"]
        check(sen.get() == 0 and sen.value_label.get_label() == "Auto",
              f"Auto shown: {sen.get()} {sen.value_label.get_label()!r}")
        check(win.angle_max == 2520 and sen.adj.get_upper() == 2520,
              f"slider up to 2520°: {sen.adj.get_upper()}")
        (dev / "SEN").write_text("2520\n")     # set on the wheelbase
    yield "steering angle Auto", steering_auto
    yield "angle above 1080", lambda: check(
        win.rows["SEN"].get() == 2520
        and win.rows["SEN"].value_label.get_label() == "2520°",
        f"2520° shown: {win.rows['SEN'].value_label.get_label()!r}")

    def ten_steps():
        """The wheelbase keeps the angle, vibration and more in steps of
        ten only: the controls take no other values."""
        sen, sho = win.rows["SEN"], win.rows["SHO"]
        scale = [w for w in walk(sen.row) if isinstance(w, Gtk.Scale)][0]
        scale.emit("change-value", Gtk.ScrollType.JUMP, 2356.0)
        check(sen.get() == 2360, f"slider moves in steps of ten: {sen.get()}")
        sho.set(35)                             # e.g. from an older preset
        check(sho.get() == 30, f"shown as the wheelbase keeps it: {sho.get()}")
        win._load_from_device()
    yield "steps of ten", ten_steps

    def auto_written():
        win._set_setting("SEN", 0)             # the Auto button
        win.flush_live()
        check((dev / "SEN").read_text().strip() == "0", "Auto written as 0")
        win._apply_values({"SEN": 1440})
        check((dev / "SEN").read_text().strip() == "1440", "1440° written")
        win._apply_values({"SEN": 0})          # a preset with Auto
        check((dev / "SEN").read_text().strip() == "0" and win.rows["SEN"].get() == 0,
              "Auto of a preset written as 0, not as 90°")
        (dev / "SEN").write_text("900\n")
        win._load_from_device()
    yield "Auto written", auto_written

    def save_preset():
        path = config.preset_path("Test GT3")
        check(win._write_preset(path, "Test GT3"), "preset saved")
        check(win.active_preset == path, "preset active")
        win._select_page("presets")
    yield "save preset", save_preset

    def modify_and_update():
        win.rows["SHO"].set(40, silent=False)
        win.flush_live()
        check(win.update_action.get_enabled(), "update action enabled after change")
        check(win.preset_label.get_label() == "Test GT3 •",
              f"modified marker: {win.preset_label.get_label()!r}")
        win.activate_action("win.update-preset", None)
    yield "save to the active preset", modify_and_update

    def update_confirmed():
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog), "changes shown before saving")
        if not isinstance(d, Adw.AlertDialog):
            return
        lines = [f"{r.get_title()}: {r_suffix(r)}" for r in walk(d.get_extra_child())
                 if isinstance(r, Adw.ActionRow)]
        check(lines == ["Wheel Vibration: 60 % → 40 %"], f"changes listed: {lines}")
        _n, vals = config.read_preset(win.active_preset)
        check(vals.get("SHO") == 60, "nothing saved before confirming")
        d.emit("response", "overwrite")
        d.force_close()
        _n, vals = config.read_preset(win.active_preset)
        check(vals.get("SHO") == 40, "preset updated")
    yield "preset updated", update_confirmed

    application = win.get_application()
    quits = []

    def ask_on_quit():
        application._quit_now = lambda: quits.append(True)   # stay running
        win.rows["SHO"].set(20, silent=False)
        win.flush_live()
        application.activate_action("quit", None)
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog) and not quits,
              "asked before quitting with unsaved preset changes")
        if not isinstance(d, Adw.AlertDialog):
            return
        lines = [f"{r.get_title()}: {r_suffix(r)}" for r in walk(d.get_extra_child())
                 if isinstance(r, Adw.ActionRow)]
        check(lines == ["Wheel Vibration: 40 % → 20 %"], f"quit changes: {lines}")
        application.activate_action("quit", None)
        check(win.get_visible_dialog() is d, "asked only once")
        d.emit("response", "cancel")
        d.force_close()
        check(not quits, "cancel keeps running")
    yield "quit with unsaved changes", ask_on_quit

    def quit_without_saving():
        win.close()                         # ✕ without background mode
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog) and win.get_visible(),
              "closing the window asks too")
        if not isinstance(d, Adw.AlertDialog):
            return
        d.emit("response", "discard")
        d.force_close()
        _n, vals = config.read_preset(win.active_preset)
        check(quits and vals.get("SHO") == 40, "quit, preset left as it was")
        check((dev / "SHO").read_text().strip() == "20",
              "wheelbase keeps the changed value")
    yield "quit without saving", quit_without_saving

    def quit_and_save():
        quits.clear()
        application.activate_action("quit", None)
        d = win.get_visible_dialog()
        if isinstance(d, Adw.AlertDialog):
            d.emit("response", "save")
            d.force_close()
        _n, vals = config.read_preset(win.active_preset)
        check(quits and vals.get("SHO") == 20, "saved, then quit")
        quits.clear()
        application.activate_action("quit", None)
        check(quits and win.get_visible_dialog() is None,
              "no question without unsaved changes")
        win.rows["SHO"].set(40, silent=False)       # as the next steps expect
        win.flush_live()
        win._write_preset(win.active_preset, "Test GT3")
        del application._quit_now
    yield "quit and save", quit_and_save

    gt3, rally = config.preset_path("Test GT3"), config.preset_path("Test Rally")

    def answer(response):
        """Answer the save question; the changes it listed."""
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog), f"asked to save ({response})")
        if not isinstance(d, Adw.AlertDialog):
            return []
        lines = [f"{r.get_title()}: {r_suffix(r)}" for r in walk(d.get_extra_child())
                 if isinstance(r, Adw.ActionRow)]
        d.emit("response", response)
        d.force_close()
        return lines

    def switch_cancelled():
        config.write_preset(rally, "Test Rally", {"FF": 60, "SHO": 90})
        win._presets_changed()
        win._apply_values({"FF": 70, "SHO": 80})
        win.activate_action("win.apply-preset", GLib.Variant.new_string(str(rally)))
        check((dev / "SHO").read_text().strip() == "80", "nothing applied while asking")
        lines = answer("cancel")
        check("Wheel Vibration: 40 % → 80 %" in lines, f"switch changes: {lines}")
        check(win.active_preset == gt3 and (dev / "SHO").read_text().strip() == "80",
              "cancel keeps the changes")
    yield "switch preset with unsaved changes", switch_cancelled

    def switch_saved():
        win._ask_apply_preset(rally)            # the Apply button of a tile
        answer("save")
        _n, vals = config.read_preset(gt3)
        check(vals.get("SHO") == 80, "changes saved into the old preset")
        check(win.active_preset == rally and (dev / "SHO").read_text().strip() == "90",
              "then the other preset applied")
    yield "switch and save", switch_saved

    def switch_discarded():
        win._apply_values({"SHO": 30})
        win._ask_apply_preset(gt3)
        answer("discard")
        _n, vals = config.read_preset(rally)
        check(vals.get("SHO") == 90, "old preset left as it was")
        check(win.active_preset == gt3 and (dev / "SHO").read_text().strip() == "80",
              "switched without saving")
        win._apply_values({"FF": 70, "SHO": 40})  # as the next steps expect
        win._write_preset(gt3, "Test GT3")
    yield "switch without saving", switch_discarded

    def apply_preset():
        win.activate_action("win.apply-preset", GLib.Variant.new_string(str(rally)))
        check(win.get_visible_dialog() is None, "no question without changes")
        check((dev / "SHO").read_text().strip() == "90", "preset applied to device")
        win.activate_action("win.apply-preset", GLib.Variant.new_string(str(gt3)))
        check((dev / "SHO").read_text().strip() == "40", "and back")
        # An applied preset is what the tiles compare to: none looks changed
        marked = [a for a, r in win.rows.items() if r.row.has_css_class("modified")]
        check(marked == [], f"no tile marked as changed after a preset: {marked}")
        sho = win.rows["SHO"]
        sho.set(70, silent=False)
        win.flush_live()
        check(sho.row.has_css_class("modified") and sho.revert_btn.get_sensitive(),
              "a change made by hand is marked")
        win._undo_all()
        check(sho.get() == 40 and (dev / "SHO").read_text().strip() == "40"
              and not sho.row.has_css_class("modified"),
              f"Undo goes back to the preset: {sho.get()}")
        rally.unlink()
        win._presets_changed()
    yield "apply preset", apply_preset

    def preset_on_display():
        race = config.preset_path("GT3 Race")
        config.write_preset(race, "GT3 Race", {"SHO": 30})
        win._presets_changed()
        try:
            check(not win.preset_display_row.get_active()
                  and config.get_setting("preset_on_display") is None,
                  "off unless switched on")
            before = display_text()
            win._apply_preset(race)
            check((dev / "SHO").read_text().strip() == "30"
                  and display_text() == before, "off: preset applied, display left alone: "
                  f"{(dev / 'SHO').read_text()!r} {display_text()!r} {before!r}")

            win.preset_display_row.set_active(True)
            check(config.get_setting("preset_on_display") is True, "switched on")
            win._apply_preset(gt3)
            check(display_text() == "Tes", f"name shown: {display_text()!r}")
            win._apply_preset(race)
            check(display_text() == "GT3", f"next name shown: {display_text()!r}")
            check(win._toast_text.get_label() == "Preset “GT3 Race” applied",
                  f"the usual message: {win._toast_text.get_label()!r}")

            display_file.chmod(0o640)
            display_file.write_text("same")     # to see whether it is written
            display_file.chmod(0o220)
            win._apply_preset(race)
            check(display_text() == "same", "the same name isn't written again")

            win.writable = False                # the preset waits for Apply
            try:
                win._apply_preset(gt3)
                check(win.pending and display_text() == "same",
                      "not written: no name either")
            finally:
                win.writable = True
                win._cancel_pending()

            win._desktop_notify = lambda *a: None
            try:
                win._apply_preset(gt3, game="Some Game")
                check((dev / "SHO").read_text().strip() == "40"
                      and display_text() == "Tes",
                      f"applied for a game: its name shown: {display_text()!r}")
                win._apply_preset(race, game="Some Game", connected=True)
                check(display_text() == "GT3",
                      f"also when the wheelbase gets connected: {display_text()!r}")
            finally:
                del win._desktop_notify

            display_file.unlink()               # a wheel without display file
            win._check_capabilities()
            win._apply_preset(race)
            check((dev / "SHO").read_text().strip() == "30"
                  and not display_file.exists()
                  and win._toast_text.get_label() == "Preset “GT3 Race” applied",
                  "without a display the preset applies as usual: "
                  f"{(dev / 'SHO').read_text()!r} {win._toast_text.get_label()!r}")
        finally:
            if not display_file.exists():
                display_file.write_text("")
                display_file.chmod(0o220)
            win._check_capabilities()
            win.preset_display_row.set_active(False)
            race.unlink(missing_ok=True)
            win._apply_preset(gt3)              # as the next steps expect
        check(config.get_setting("preset_on_display") is None, "switched off again")
    yield "preset name on the display", preset_on_display

    def preset_per_slot():
        two = config.preset_path("Slot Two")
        config.write_preset(two, "Slot Two", {"SHO": 30})
        win._presets_changed()
        first = int((dev / "SLOT").read_text())
        second = 2 if first != 2 else 3

        def on_slot(slot, sho):
            """The wheelbase switched slots: they hold their own values."""
            (dev / "SLOT").write_text(f"{slot}\n")
            (dev / "SHO").write_text(f"{sho}\n")
            win._load_from_device()

        def active():
            return win.active_preset, win.preset_label.get_label()
        try:
            check(win.active_preset == gt3, f"GT3 active: {win.active_preset}")
            on_slot(second, 70)
            check(active() == (None, "Presets") and not win._unsaved_preset_changes(),
                  f"no preset active in another slot: {active()}")
            win._apply_preset(two)
            check(win.active_preset == two, "a preset applied in that slot")
            on_slot(first, 40)
            check(active() == (gt3, "Test GT3") and not win._unsaved_preset_changes(),
                  f"back in the first slot: its preset again: {active()}")
            on_slot(second, 60)                 # changed meanwhile on the wheelbase
            check(active() == (two, "Slot Two •"),
                  f"the preset of the slot, with the change: {active()}")
            on_slot(first, 40)
            two.unlink()
            on_slot(second, 30)
            check(active() == (None, "Presets"), f"a deleted preset isn't active: {active()}")
        finally:
            two.unlink(missing_ok=True)
            on_slot(first, 40)
            win._presets_changed()
        check(win.active_preset == gt3, "GT3 active again, as the next steps expect")
    yield "preset per slot", preset_per_slot

    def preset_name_per_slot():
        two = config.preset_path("Slot Two")
        odd = config.preset_path("kmx")             # nothing the display can show
        config.write_preset(two, "Slot Two", {"SHO": 30})
        config.write_preset(odd, "kmx", {"SHO": 50})
        win._presets_changed()
        first = int((dev / "SLOT").read_text())
        second, third = [s for s in (2, 3, 4) if s != first][:2]

        def on_slot(slot, sho):
            (dev / "SLOT").write_text(f"{slot}\n")
            (dev / "SHO").write_text(f"{sho}\n")
            win._load_from_device()
        try:
            win.preset_display_row.set_active(True)
            win._apply_preset(gt3)
            check(display_text() == "Tes", f"GT3 shown: {display_text()!r}")
            on_slot(second, 70)
            check(display_text() == "   ", f"a slot without preset: cleared: {display_text()!r}")
            win._apply_preset(two)
            check(display_text() == "Slo", f"its preset shown: {display_text()!r}")
            on_slot(first, 40)
            check(display_text() == "Tes", f"back: the preset of that slot: {display_text()!r}")
            on_slot(second, 30)
            check(display_text() == "Slo", f"and the other one: {display_text()!r}")
            win._apply_preset(odd)
            check(display_text() == "   ",
                  f"a name it can't show clears it: {display_text()!r}")
            on_slot(third, 30)
            check(display_text() == "   ", "still clear in a slot without preset")
            win.preset_display_row.set_active(False)
            on_slot(first, 40)
            check(display_text() == "   ", "option off: nothing written on switching")
        finally:
            win.preset_display_row.set_active(False)
            two.unlink(missing_ok=True)
            odd.unlink(missing_ok=True)
            on_slot(first, 40)
            win._presets_changed()
        check(win.active_preset == gt3, "GT3 active again, as the next steps expect")
    yield "preset name per slot", preset_name_per_slot

    def menu_entries():
        labels = []
        menu = win.preset_button.get_menu_model()
        for i in range(menu.get_n_items()):
            section = menu.get_item_link(i, "section")
            for j in range(section.get_n_items() if section else 0):
                labels.append(section.get_item_attribute_value(j, "label").unpack())
        return labels

    def remove_preset_from_slot():
        first = int((dev / "SLOT").read_text())
        second = 2 if first != 2 else 3

        def on_slot(slot, sho):
            (dev / "SLOT").write_text(f"{slot}\n")
            (dev / "SHO").write_text(f"{sho}\n")
            win._load_from_device()
        stored = gt3.read_text()
        try:
            win.preset_display_row.set_active(True)
            win._apply_preset(gt3)
            check("Remove “Test GT3” from Slot" in menu_entries(),
                  f"offered in the Presets menu: {menu_entries()}")
            win.rows["SHO"].set(60, silent=False)   # an unsaved change
            win.flush_live()
            values = {p.name: p.read_text() for p in dev.iterdir() if p.is_file()}
            win.activate_action("win.remove-preset-from-slot", None)
            check(win.active_preset is None
                  and win.preset_label.get_label() == "Presets",
                  f"no preset active: {win.preset_label.get_label()!r}")
            check({p.name: p.read_text() for p in dev.iterdir() if p.is_file()}
                  == values and win.rows["SHO"].get() == 60,
                  "the values stay, nothing written")
            check(gt3.read_text() == stored, "the preset itself stays as it is")
            check(display_text() == "   ", f"display cleared: {display_text()!r}")
            check(win._toast_text.get_label()
                  == "“Test GT3” removed from the slot, its values stay",
                  f"told: {win._toast_text.get_label()!r}")
            check(not any(e.startswith("Remove") for e in menu_entries()),
                  "nothing to remove any more")
            on_slot(second, 70)
            on_slot(first, 60)
            check(win.active_preset is None, "the slot stays without preset")
        finally:
            win.preset_display_row.set_active(False)
            on_slot(first, 40)
            win._apply_preset(gt3)              # as the next steps expect
        check(win.active_preset == gt3, "GT3 active again")
    yield "remove preset from slot", remove_preset_from_slot

    def preset_values():
        win._apply_values({"SHO": 60})
        win._show_preset(gt3)
        d = win.get_visible_dialog()
        rows = {r.get_title(): (r_suffix(r), r.get_subtitle())
                for r in walk(d) if isinstance(r, Adw.ActionRow)}
        check(rows.get("Wheel Vibration") == ("40 %", "Now: 60 %"),
              f"preset value and the current one: {rows.get('Wheel Vibration')}")
        check(rows.get("Force Feedback Strength", ("", "x"))[1] in ("", None),
              "equal values not marked")
        titles = [g.get_title() for g in walk(d) if isinstance(g, Adw.PreferencesGroup)]
        check("Pedals &amp; Paddles" in titles or "Wheel Feel" in titles,
              f"groups by tab: {titles}")
        check((dev / "SHO").read_text().strip() == "60", "nothing applied by looking")
        apply_btn = next(w for w in walk(d) if isinstance(w, Gtk.Button)
                         and w.get_label() == "Apply")
        apply_btn.emit("clicked")
        d.force_close()
        answer("discard")                       # unsaved changes of Test GT3
        check((dev / "SHO").read_text().strip() == "40", "applied from the values")
    yield "preset values", preset_values

    clip = []
    copy = config.preset_path("Test GT3 (2)")

    def read_clipboard():
        clip.clear()
        win.get_clipboard().read_text_async(
            None, lambda c, r: clip.append(c.read_text_finish(r)))

    def share_as_text():
        win.activate_action("win.copy-preset-text",
                            GLib.Variant.new_string(str(gt3)))
        read_clipboard()
    yield "share as text", share_as_text

    def import_shared_text():
        text = clip[0] if clip else ""
        check(text.startswith("Understeer preset “Test GT3”\n")
              and "\nWheel Vibration: 40 %\n" in text, f"shared text: {text!r}")
        win._import_text(text)
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog) and d.has_response("replace")
              and d.has_response("keep"), "same preset: replace or keep both")
        answer("replace")
        _n, vals = config.read_preset(gt3)
        check(vals.get("SHO") == 40, "replaced by the same values")
        win._import_text(text.replace('"SHO": 40', '"SHO": 70'))
        lines = answer("keep")
        check(lines == ["Wheel Vibration: 40 % → 70 %"], f"import changes: {lines}")
        _n, vals = config.read_preset(copy)
        check(vals.get("SHO") == 70, "kept both")
        win._import_text("no preset here")
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog)
              and d.get_heading() == "No Preset Found", "not a preset")
        close_dialogs(win)
    yield "import shared text", import_shared_text

    def share_as_json():
        win.activate_action("win.copy-preset-json",
                            GLib.Variant.new_string(str(copy)))
        copy.unlink()
        win._presets_changed()
        win.activate_action("win.paste-preset", None)
    yield "share as JSON and paste", share_as_json

    def text_view(d):
        return next((w for w in walk(d) if isinstance(w, Gtk.TextView)), None)

    def add_button(d):
        return next(w for w in walk(d) if isinstance(w, Gtk.Button)
                    and "suggested-action" in w.get_css_classes())

    def paste_dialog():
        d = win.get_visible_dialog()
        check(d is not None and text_view(d) is not None, "paste dialog shown")
        if d is None or text_view(d) is None:
            return
        check(not isinstance(d, Adw.AlertDialog),
              "no window handle around the text field")
        buffer = text_view(d).get_buffer()
        pasted = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        check('"Test GT3 (2)"' in pasted, f"preset taken from the clipboard: {pasted!r}")
        check(not config.preset_path("Test GT3 (2)").exists(),
              "nothing added before confirming")
        buffer.set_text("rm -rf ~")
        check(not add_button(d).get_sensitive(), "no preset: can't add")
        buffer.set_text(pasted)
        check(add_button(d).get_sensitive(), "preset: can add")
        add_button(d).emit("clicked")
        d.force_close()
    yield "paste dialog", paste_dialog

    def private_clipboard():
        win.get_clipboard().set("my secret")
    yield "other text on the clipboard", private_clipboard

    def not_shown():
        win.activate_action("win.paste-preset", None)
    yield "paste again", not_shown

    def private_kept():
        d = win.get_visible_dialog()
        view = text_view(d) if d is not None else None
        buffer = view.get_buffer() if view else None
        check(buffer is not None and buffer.get_char_count() == 0,
              "other clipboard text is not shown")
        close_dialogs(win)
        name = "<b>Bold</b> & My_Preset"
        win._import_text(sharing.preset_json(name, {"FF": 33}))
        tiles = [w.get_label() for w in walk(win.presets_flow)
                 if isinstance(w, Gtk.Label) and "preset-name" in w.get_css_classes()]
        check(name in tiles, f"name shown as plain text: {tiles}")
        config.preset_path(name).unlink()
        win._presets_changed()
    yield "clipboard stays private", private_kept

    def paste_thread():
        thread = ("Hi, my presets:\n" + sharing.preset_text("Thread A", {"FF": 11})
                  + "\n> quoted: " + sharing.preset_json("Thread A", {"FF": 11})
                  + "\nand " + sharing.preset_json("Test GT3", {"SHO": 5}))
        win._ask_paste_preset(thread)
        d = win.get_visible_dialog()
        rows = [w for w in walk(d) if isinstance(w, Adw.ExpanderRow)]
        check([r.get_title() for r in rows] == ["Thread A", "Test GT3"],
              f"presets found in the thread: {[r.get_title() for r in rows]}")
        check(add_button(d).get_label() == "Add (2)",
              f"add label: {add_button(d).get_label()!r}")
        boxes = [w for w in walk(d) if isinstance(w, Gtk.CheckButton)]
        boxes[1].set_active(False)
        check(add_button(d).get_label() == "Add (1)", "only the checked ones")
        add_button(d).emit("clicked")
        d.force_close()
        _n, vals = config.read_preset(config.preset_path("Thread A"))
        check(vals == {"FF": 11}, f"thread preset added: {vals}")
        _n, vals = config.read_preset(gt3)
        check(vals.get("SHO") == 40, "unchecked preset left out")
        check(win.get_visible_dialog() is None, "nothing to ask")
        config.preset_path("Thread A").unlink()
        win._presets_changed()
    yield "paste a thread", paste_thread

    def one_after_another():
        win._import_presets([sharing.Found("Test GT3", {"SHO": 5}, 1),
                             sharing.Found("Test GT3", {"SHO": 6}, 2)])
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog)
              and d.get_response_label("cancel") == "Skip", "skip offered")
        answer("cancel")
        answer("keep")
        _n, vals = config.read_preset(gt3)
        check(vals.get("SHO") == 40, "skipped one left out")
        third = config.preset_path("Test GT3 (3)")     # (2) is there already
        _n, vals = config.read_preset(third)
        check(vals == {"SHO": 6}, f"next one asked and kept: {vals}")
        third.unlink()
        win._presets_changed()
    yield "names taken, one after another", one_after_another

    def pasted():
        _n, vals = config.read_preset(copy)
        check(vals.get("SHO") == 70, "pasted preset added")
        names = [n for n, _p in config.list_presets()]
        check("Test GT3 (2)" in names, f"pasted preset listed: {names}")
        text = sharing.preset_json("Test GT3", {"SHO": 10})
        win._import_text(text)
        answer("replace")
        _n, vals = config.read_preset(gt3)
        check(vals == {"SHO": 10}, f"replaced: {vals}")
        check(win.preset_label.get_label() == "Test GT3 •",
              "replaced active preset differs from the settings")
        win._write_preset(gt3, "Test GT3")      # as the next steps expect
        copy.unlink()
        win._presets_changed()
    yield "pasted", pasted

    def folders():
        drift, climb = config.preset_path("Drift F"), config.preset_path("Climb F")
        config.write_preset(drift, "Drift F", {"FF": 55}, "BeamNG")
        config.write_preset(climb, "Climb F", {"FF": 60})
        try:
            win._presets_changed()
            check(list(win.folder_flows) == ["BeamNG"], f"folders: {list(win.folder_flows)}")

            def names(flow):
                return [w.get_label() for w in walk(flow) if isinstance(w, Gtk.Label)
                        and "preset-name" in w.get_css_classes()]
            check(names(win.folder_flows["BeamNG"]) == ["Drift F"], "tile in its folder")
            check("Climb F" in names(win.presets_flow) and "Drift F" not in
                  names(win.presets_flow), "the others outside")
            # Dropped on a folder; one typed in another spelling is the same
            win._drop_preset(climb, "beamng")
            check(config.preset_folder(climb) == "BeamNG", "moved, spelled as the folder")
            check(names(win.folder_flows["BeamNG"]) == ["Climb F", "Drift F"],
                  "both in the folder")
            # The Presets menu: a submenu for the folder
            model = win.preset_button.get_menu_model()
            subs = []
            for i in range(model.get_n_items()):
                section = model.get_item_link(i, "section")
                for j in range(section.get_n_items() if section else 0):
                    sub = section.get_item_link(j, "submenu")
                    if sub is not None:
                        label = section.get_item_attribute_value(j, "label", None)
                        subs.append((label.unpack(), sub.get_n_items()))
            check(subs == [("BeamNG", 2)], f"submenu in the Presets menu: {subs}")
            tray_entries = [e for e in application._tray_presets() if len(e) == 2]
            check([(f, [p[0] for p in ps]) for f, ps in tray_entries]
                  == [("BeamNG", ["Climb F", "Drift F"])], f"tray: {tray_entries}")
            # A new preset is suggested the folder of the active one
            win._set_active_preset(drift, {"FF": 55})
            win._ask_preset_name()
            d = win.get_visible_dialog()
            rows = {r.get_title(): r.get_text() for r in walk(d)
                    if isinstance(r, Adw.EntryRow)}
            check(rows == {"Name": "", "Folder (optional)": "BeamNG"}, f"save as: {rows}")
            offered = [r for r in walk(d) if isinstance(r, Adw.ActionRow)]
            check([r.get_title() for r in offered] == ["BeamNG"],
                  f"folders suggested: {[r.get_title() for r in offered]}")
            folder_row = [r for r in walk(d) if isinstance(r, Adw.EntryRow)][1]
            folder_row.set_text("")
            offered[0].emit("activated")
            check(folder_row.get_text() == "BeamNG", "a click fills in the folder")
            d.force_close()
            # A new folder, empty: on the page, not in the menus
            win._ask_new_folder()
            d = win.get_visible_dialog()
            check(isinstance(d, Adw.Dialog) and d.get_title() == "New Folder",
                  "new folder asked")
            entry = [w for w in walk(d) if isinstance(w, Adw.EntryRow)][0]
            hint = [w for w in walk(d) if isinstance(w, Gtk.Label)
                    and "error" in w.get_css_classes()][0]
            check(not d.ok_button.get_sensitive() and not hint.get_opacity(),
                  "no name yet: Create off, no red hint")
            entry.set_text("x")
            check(d.ok_button.get_sensitive(), "Create on with a name")
            entry.set_text("  ")
            check(not d.ok_button.get_sensitive() and hint.get_opacity() == 1
                  and "error" in entry.get_css_classes(), "emptied: red hint")
            d.force_close()
            win._new_folder(" Rally ")
            check(list(win.folder_flows) == ["BeamNG", "Rally"]
                  and win.folder_flows["Rally"] is None, "empty folder shown")
            check(not any(len(e) == 2 and e[0] == "Rally"
                          for e in application._tray_presets()), "not in the tray")
            win._drop_preset(drift, "Rally")
            check(names(win.folder_flows["Rally"]) == ["Drift F"], "dropped in")
            win._drop_preset(drift, None)
            check("Drift F" in names(win.presets_flow) and win.folder_flows["Rally"] is None,
                  "dropped out; the folder stays")
            # A preset dropped on New Folder: asked for the name, then in it
            win._ask_new_folder(drift)
            d = win.get_visible_dialog()
            body = [w.get_label() for w in walk(d) if isinstance(w, Gtk.Label)]
            check("“Drift F” is put into it." in body, f"body: {body}")
            [w for w in walk(d) if isinstance(w, Adw.EntryRow)][0].set_text("Stage")
            d.ok_button.emit("clicked")
            check(config.preset_folder(drift) == "Stage", "new folder with the preset")
            win._ask_delete_folder("Stage", 1)
            answer("keep")
            check(config.preset_folder(drift) is None and "Stage" not in win.folder_flows,
                  "folder deleted, preset kept")
            # Deleted with its presets, then undone
            config.write_preset(config.preset_path("Gone F"), "Gone F", {"FF": 30}, "Trash")
            win._presets_changed()
            win._set_active_preset(config.preset_path("Gone F"), {"FF": 30})
            games_before = win.games_page.games
            win.games_page.games = [dict(game="g", name="BeamNG.drive", auto=True,
                                         preset="Gone F.json")]
            # One preset from its menu: at once into the trash, with Undo
            gone = config.preset_path("Gone F")
            win.activate_action("win.delete-preset", GLib.Variant.new_string(str(gone)))
            check(not gone.exists(), f"deleted at once (dialog: {win.get_visible_dialog()})")
            check("Used by: BeamNG.drive" in win._toast_text.get_label(),
                  f"game named: {win._toast_text.get_label()}")
            check([i["name"] for i in trash.items()] == ["Gone F"], "in the trash")
            win._on_toast_button(win._toast)       # Undo
            check(config.preset_folder(gone) == "Trash" and not trash.items(),
                  "undone: back in its folder, out of the trash")
            win._set_active_preset(gone, {"FF": 30})
            win._ask_delete_folder("Trash", 1)
            d = win.get_visible_dialog()
            check("Used by: BeamNG.drive" in d.get_body(), "game named for the folder")
            win.games_page.games = games_before
            check(d.get_response_label("all") == "Delete with Presets",
                  "delete with presets offered")
            answer("all")
            check(not config.preset_path("Gone F").exists()
                  and "Trash" not in win.folder_flows, "folder and preset deleted")
            check(win.active_preset is None, "deleted preset no longer active")
            win._on_toast_button(win._toast)       # Undo
            check(config.preset_folder(config.preset_path("Gone F")) == "Trash",
                  "undone: preset back in its folder")
            config.preset_path("Gone F").unlink()
            config.delete_folder("Trash")
            win._presets_changed()
            win._ask_delete_folder("Rally", 0)
            check("Rally" not in win.folder_flows, "empty folder deleted without asking")
            win._drop_preset(drift, "BeamNG")
            check(win._shared(drift)[2] == "BeamNG", "shared with its folder")
            shared = config.preset_path("Shared F")
            win._import_text(sharing.preset_json("Shared F", {"FF": 40}, "beamng"))
            check(config.preset_folder(shared) == "BeamNG", "pasted into its folder")
            config.write_preset(shared, "Shared F", {"FF": 40}, "Mine")
            win._import_text(sharing.preset_json("Shared F", {"FF": 41}))
            answer("replace")
            check(config.preset_folder(shared) == "Mine", "replaced, stays in its folder")
            shared.unlink()
            config.delete_folder("Mine")            # kept while empty
            win._presets_changed()
            win._rename_folder("BeamNG", "Beam")
            check(config.list_folders() == ["Beam"] and list(win.folder_flows) == ["Beam"],
                  "folder renamed")
            win._rename_folder("Beam", "")
            check(config.list_folders() == [] and not win.folder_flows, "taken out")
            win._set_preset_folder(drift, "Drift F", "Beam")
            win._set_preset_folder(drift, "Drift F", None)
            check(config.preset_folder(drift) is None, "no folder again")
        finally:
            drift.unlink(missing_ok=True)
            climb.unlink(missing_ok=True)
            config.set_setting("preset_folders", None)
            win._set_active_preset(gt3, config.read_preset(gt3)[1])
    yield "folders", folders

    def choose_and_trash():
        a, b = config.preset_path("Pick A"), config.preset_path("Pick B")
        config.write_preset(a, "Pick A", {"FF": 10})
        config.write_preset(b, "Pick B", {"FF": 20}, "Picks")
        sel = win.preset_selection
        try:
            win._presets_changed()
            sel.start()
            catchers = [w for w in walk(win.presets_box) if isinstance(w, Gtk.Button)
                        and "select-catcher" in w.get_css_classes()]
            check(sel.bar.get_revealed() and len(catchers) == len(sel._available),
                  f"choosing: {len(catchers)} tiles to pick")
            check(sel.button.get_label() == "Done", "Select turned into Done")
            sel.toggle(str(a))
            sel.toggle(str(b))
            check(sel.count.get_label() == "Selected: 2", sel.count.get_label())
            sel.delete_button.emit("clicked")
            check(not a.exists() and not b.exists() and not sel.active,
                  "chosen ones deleted, choosing ended")
            check(win._toast_text.get_label() == "Presets deleted: 2", win._toast_text.get_label())
            win._on_toast_button(win._toast)
            check(a.exists() and config.preset_folder(b) == "Picks", "both back")
            sel.start()
            sel.all_button.emit("clicked")
            check(sel.keys == set(sel._available)
                  and sel.all_button.get_label() == "Select None", "all chosen")
            win._select_page("games")
            check(not sel.active, "choosing ends on another page")
            # Games: rename, choose, remove, back from the trash
            page = win.games_page
            before = list(page.games)
            page._add("dummy:1", "Dummy One")
            page._add("dummy:2", "Dummy Two")
            dummy = [g for g in page.games if g["game"] == "dummy:1"][0]
            page._rename(dummy, "  Dummy   First ")
            check(dummy["name"] == "Dummy First", f"renamed: {dummy['name']!r}")
            page.selection.start()
            page.selection.toggle("dummy:1")
            page.selection.toggle("dummy:2")
            page.selection.delete_button.emit("clicked")
            check(not any(g["game"].startswith("dummy:") for g in page.games)
                  and len(config.load_games()) == len(before), "games removed")
            check(win._toast_text.get_label() == "Games deleted: 2", win._toast_text.get_label())
            # The trash of the Games page: only games, put one back from there
            sel.start()
            sel.toggle(str(a))
            sel.delete_button.emit("clicked")
            shown = win._show_trash("game")

            def titles():
                return [r.get_title() for r in walk(shown.rows)
                        if isinstance(r, Adw.ActionRow)]
            check({"Dummy First", "Dummy Two"} == set(titles()),
                  f"games in their trash: {titles()}")
            item = [i for i in trash.items() if i["name"] == "Dummy Two"][0]
            win._restore_from_trash(item)
            check(any(g["game"] == "dummy:2" for g in page.games)
                  and titles() == ["Dummy First"], "restored from the trash")
            win._ask_empty_trash("game").emit("response", "empty")
            check(titles() == ["The trash is empty"]
                  and [i["name"] for i in trash.items()] == ["Pick A"],
                  "games emptied, the preset still there")
            shown.force_close()
            page.games = [g for g in page.games if not g["game"].startswith("dummy:")]
            page._save()
            page.refresh()
        finally:
            sel.stop()
            a.unlink(missing_ok=True)
            b.unlink(missing_ok=True)
            config.delete_folder("Picks")
            trash.empty()
            win._select_page("presets")
            win._presets_changed()
    yield "choose and trash", choose_and_trash

    def long_names():
        """Very long names end in "…": they must not make tiles, menus,
        lists or messages wider, nor the window."""
        name = "Nordschleife Endurance for long nights with rain and little grip at the end"
        nospace = "Supercalifragilistic_Setup_Without_Any_Spaces_1234567890_ABCDEFGHIJ"
        folder = "Assetto Corsa Competizione Endurance Championship Season 2026 Part Two"
        game_name = "The Very Long Racing Game Title Ultimate Deluxe Remastered Edition"
        long_path, nospace_path = config.preset_path(name), config.preset_path(nospace)
        config.write_preset(long_path, name, {"FF": 10})
        config.write_preset(nospace_path, nospace, {"FF": 20}, folder)
        page = win.games_page
        page._add("dummy:long", game_name)
        game = [g for g in page.games if g["game"] == "dummy:long"][0]
        game["preset"] = nospace_path.name

        def width(widget):
            return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
        before = (win.active_preset, win.active_values)
        try:
            win._presets_changed()
            win._set_active_preset(long_path, config.read_preset(long_path)[1])
            win._select_page("presets")
            tiles = [w for w in walk(win.presets_box)
                     if "preset-tile" in w.get_css_classes()]
            widest = max(width(t)[1] for t in tiles)
            check(len(tiles) >= 3 and widest < 400, f"preset tiles not widened: {widest}")
            check(width(win.preset_label)[1] < 300,
                  f"preset button short: {width(win.preset_label)}")
            labels = []
            menu = win.preset_button.get_menu_model()
            for i in range(menu.get_n_items()):
                section = menu.get_item_link(i, "section")
                for j in range(section.get_n_items() if section else 0):
                    labels.append(section.get_item_attribute_value(j, "label").unpack())
            check(max(map(len, labels)) <= 60 and any(n.endswith("…") for n in labels),
                  f"menu entries cut: {max(map(len, labels))}")
            win._set_preset_folder(long_path, name, folder)
            moved = win._toast_text.get_label()
            check(len(moved) <= 100 and moved.endswith("”"), f"names cut in message: {moved!r}")
            win._delete_presets([long_path])
            check(width(win._toast_text)[0] < 600, f"message short: {width(win._toast_text)}")
            win._on_toast_button(win._toast)            # undo
            win._select_page("games")
            game_tiles = [w for w in walk(page.flow) if "game-tile" in w.get_css_classes()]
            widest = max(width(t)[1] for t in game_tiles)
            check(widest < 300, f"game tiles not widened: {widest}")
            choose = [w for w in walk(page.flow) if isinstance(w, Gtk.DropDown)]
            check(all(width(d)[1] < 260 for d in choose),
                  f"preset choice short: {[width(d) for d in choose]}")
            check(width(win)[0] < 1000, f"window not widened: {width(win)}")
        finally:
            page.games = [g for g in page.games if g["game"] != "dummy:long"]
            page._save()
            for path in (long_path, nospace_path):
                path.unlink(missing_ok=True)
            config.delete_folder(folder)
            trash.empty()
            win._set_active_preset(*before)
            win._select_page("presets")
    yield "long names", long_names

    def damaged_presets():
        broken = config.CONFIG_DIR / "Broken.json"
        broken.write_text("[1, 2]")
        bad = config.preset_path("Bad Values")
        bad.write_text('{"name": "Bad Values", "values": {"FF": "x", "SHO": 30}}')
        before = (win.active_preset, win.active_values)
        values = win._collect_values()
        try:
            win._presets_changed()
            win._apply_preset(bad)
            check(win.rows["SHO"].get() == 30, "the valid values of a damaged preset applied")
            win._apply_preset(broken)
            d = win.get_visible_dialog()
            check(d is not None and d.get_heading() == "Could not load preset",
                  "error shown for a file without a preset")
        finally:
            close_dialogs(win)
            broken.unlink()
            bad.unlink()
            win._apply_values(values)
            win._set_active_preset(*before)
    yield "damaged presets", damaged_presets

    def save_as_into_folder():
        path = config.preset_path("Folder Test")
        config.write_preset(path, "Folder Test", {"FF": 10})
        before = (win.active_preset, win.active_values)
        try:
            win._save_preset("Folder Test", "Saved Into")
            win.get_visible_dialog().emit("response", "overwrite")
            check(config.preset_folder(path) == "Saved Into",
                  f"saved over it into the folder chosen: {config.preset_folder(path)}")
            win._save_preset("Folder Test", "")
            win.get_visible_dialog().emit("response", "overwrite")
            check(config.preset_folder(path) == "Saved Into",
                  "no folder entered: saved over it where it is")
        finally:
            close_dialogs(win)
            path.unlink(missing_ok=True)
            config.delete_folder("Saved Into")
            win._set_active_preset(*before)
    yield "save as preset into a folder", save_as_into_folder

    def no_write_access():
        """Every write would ask for a password: changes wait for Apply."""
        before = (win.active_preset, win.active_values)
        values = win._collect_values()
        waiting = config.preset_path("Waiting")
        win._load_from_device()         # as at the start: nothing to undo
        ff = win.rows["FF"].get()
        win.writable = False
        try:
            win.rows["FF"].set(ff - 7, silent=False)
            check(win.pending == {"FF": ff - 7} and win.pending_bar.get_revealed(),
                  f"a change waits for Apply: {win.pending}")
            check((dev / "FF").read_text().strip() == str(ff), "nothing written yet")
            win._undo_all()                             # Discard
            check(not win.pending and not win.pending_bar.get_revealed(),
                  f"nothing left to apply after Discard: {win.pending}")
            outcome, _failed = win._apply_values(values)
            check(outcome == "written" and not win.pending,
                  f"no password for what the wheelbase has: {outcome} {win.pending}")
            # The preset of a game that starts while the window is closed
            config.write_preset(waiting, "Waiting", {"FF": ff - 10})
            win._presets_changed()
            win._desktop_notify = lambda *a: None
            win.go_to_background(announce=False)
            win._apply_preset(waiting, game="Some Game")
            check(win.pending == {"FF": ff - 10}, f"preset waits: {win.pending}")
            check(win.rows["FF"].baseline == ff,
                  "a preset waiting for Apply is still a change to discard")
            win.show_again()
            check(win.pending == {"FF": ff - 10} and win.pending_bar.get_revealed()
                  and win.rows["FF"].get() == ff - 10,
                  f"still waiting for Apply with the window open: {win.pending}")
            win._flush_pending()                        # Apply
            check((dev / "FF").read_text().strip() == str(ff - 10), "written by Apply")
        finally:
            win.writable = True
            win._cancel_pending()
            del win._desktop_notify
            waiting.unlink(missing_ok=True)
            win._apply_values(values)
            win._set_active_preset(*before)
    yield "no write access", no_write_access
    yield "window open again", lambda: check(win.get_visible(), "window shown")

    def damaged_settings():
        """Settings changed by hand or from a damaged export: the window
        is still built, with what can be used of them."""
        kept = {f: f.read_bytes() if f.exists() else None
                for f in (config.APP_SETTINGS_FILE, config.AXES_FILE,
                          config.SLOT_NAMES_FILE, config.SLOT_COUNTS_FILE)}
        old_page = win.games_page
        try:
            data = config.load_json(config.APP_SETTINGS_FILE, {})
            data.update({"theme": ["carbon"], "accent": {"a": 1}, "color_scheme": 5,
                         "steamgriddb_key": 123, "last_version": 7, "layout": [],
                         "language": {"de": 1}})
            config.APP_SETTINGS_FILE.write_text(json.dumps(data))
            config.AXES_FILE.write_text('{"FANATEC Pedals:1": ["x"]}')
            config.SLOT_NAMES_FILE.write_text(
                '{"0EB7:0020": {"\u00b2": "x", "1": "Race"}}')
            config.SLOT_COUNTS_FILE.write_text('{"0EB7:0020": 6}')   # older versions
            theme.apply()
            win.axis_roles = config.load_axis_roles()
            win.relocalize()
            win._announce_update()
            check(win.slot_names == {1: "Race"} and win.slot_count == 5,
                  f"usable slot names kept, 5 slots: {win.slot_names} {win.slot_count}")
            check(win.games_page.key_row.get_text() == "", "a key that is no text is none")
            check(old_page.replaced and not win.games_page.replaced,
                  "the Games page before the rebuild no longer saves")
        finally:
            for f, content in kept.items():
                if content is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_bytes(content)
            theme.apply()
            win.axis_roles = config.load_axis_roles()
            win.relocalize()
    yield "damaged settings", damaged_settings

    def not_utf8():
        bad = config.CONFIG_DIR / "Latin.json"
        bad.write_bytes(b'{"name": "N\xfcrburgring", "values": {"FF": 50}}')
        win._presets_changed()
        win._delete_presets([bad])
        check(not bad.exists(), "a preset file that is no UTF-8 can be deleted")
        trash.empty()
        win._trash_changed()
    yield "delete a file that is no UTF-8", not_utf8

    def folder_partly_deleted():
        """Delete with Presets, but one can't go: its folder stays."""
        first, second = config.preset_path("Part A"), config.preset_path("Part B")
        config.write_preset(first, "Part A", {"FF": 10}, "Partly")
        config.write_preset(second, "Part B", {"FF": 20}, "Partly")
        win._presets_changed()
        real = trash.put_preset

        def fail_second(path):
            if pathlib.Path(path) == second:
                raise OSError("Disk full")
            return real(path)
        trash.put_preset = fail_second
        try:
            win._delete_presets([first, second], folder="Partly")
            check(config.preset_folder(second) == "Partly"
                  and "Partly" in config.list_folders(),
                  "the folder stays with the preset that could not be deleted")
            check(win._toast_text.get_label() == "“Part A” deleted",
                  f"told what was deleted: {win._toast_text.get_label()!r}")
        finally:
            trash.put_preset = real
            close_dialogs(win)
            second.unlink(missing_ok=True)
            config.delete_folder("Partly")
            trash.empty()
            win._presets_changed()
            win._trash_changed()
    yield "folder deleted in part", folder_partly_deleted

    def no_auto():
        """A wheelbase without Auto (e.g. ClubSport V2.5) takes its largest
        angle for it."""
        tile = SettingTile(win.rows["SEN"].setting, lambda t: None)
        tile.set_upper(900, has_auto=False)
        tile.set(0)
        check(tile.get() == 900 and tile.value_label.get_label() == "900°",
              f"largest angle instead of Auto: {tile.value_label.get_label()!r}")
        tile.set_upper(2520, has_auto=True)
        tile.set(0)
        check(tile.get() == 0 and tile.value_label.get_label() == "Auto", "Auto with it")
    yield "wheelbase without Auto", no_auto

    def file_dialogs():
        """What happens once a file is chosen, for every file dialog."""
        real = files.file_dialog
        shared, exported = scratch / "shared.json", scratch / "by-dialog"
        picture = scratch / "cover.png"
        picture.write_bytes(tiny_png())
        page = win.games_page
        try:
            files.file_dialog = lambda *a, **k: ChosenFile(shared)
            win._export_preset(str(gt3))
            check("understeer_preset" in shared.read_text(), "preset saved as file")
            win._ask_import_preset()
            d = win.get_visible_dialog()
            check(isinstance(d, Adw.AlertDialog) and d.has_response("replace"),
                  "preset file read: its name is taken")
            close_dialogs(win)
            files.file_dialog = lambda *a, **k: ChosenFile(exported)
            win._ask_export()
            check(exported.with_suffix(".zip").is_file(), ".zip added and written")
            files.file_dialog = lambda *a, **k: ChosenFile(exported.with_suffix(".zip"))
            win._ask_import()
            d = win.get_visible_dialog()
            check(isinstance(d, Adw.AlertDialog)
                  and d.get_heading() == "Import from This File?", "export read")
            close_dialogs(win)
            page._add("dummy:cover", "Cover Test")
            game = [g for g in page.games if g["game"] == "dummy:cover"][0]
            files.file_dialog = lambda *a, **k: ChosenFile(picture)
            page._choose_cover(game)
            check(config.game_cover_path(game) is not None, "picture chosen for a game")
            config.remove_game_cover(game)
        finally:
            files.file_dialog = real
            close_dialogs(win)
            page.games = [g for g in page.games if g["game"] != "dummy:cover"]
            page._save()
            page.refresh()
    yield "file dialogs", file_dialogs


    def rename():
        win._rename_preset(config.preset_path("Test GT3"), "Test GT3", "Renamed")
        check(config.preset_path("Renamed").is_file(), "renamed file exists")
        check(win.active_preset == config.preset_path("Renamed"), "active follows rename")
        check(win.preset_label.get_label().startswith("Renamed"),
              f"preset button shows new name: {win.preset_label.get_label()!r}")
    yield "rename preset", rename

    def rebuilt_without_wheel_base():
        # Nothing to read the values from again after the window is rebuilt
        path, win.device_path = win.device_path, None
        try:
            win._apply_values({"SEN": 540, "SHO": 30})
            win.relocalize()
            shown = {a: win.rows[a].get() for a in ("SEN", "SHO")}
            check(shown == {"SEN": 540, "SHO": 30},
                  f"values kept without a wheelbase: {shown}")
            win._set_setting("SEN", 0)                  # the Auto button
            check(win.rows["SEN"].value_label.get_label() == "Auto",
                  "Auto also without a wheelbase, e.g. for a preset")
        finally:
            win.device_path = path
            win.relocalize()
    yield "rebuilt without wheelbase", rebuilt_without_wheel_base

    def dialogs():
        win._ask_preset_name()
        win._confirm_overwrite(config.preset_path("Renamed"))
        win._ask_rename_preset(config.preset_path("Renamed"), "Renamed")
        win._confirm_reset()
        win._error("Could not save", "Disk full")
    yield "open dialogs", dialogs
    yield "close dialogs", lambda: close_dialogs(win)

    def reset_failed():
        real = window_module.write_values
        window_module.write_values = lambda writes: list(writes)
        try:
            win._reset_slots()
        finally:
            window_module.write_values = real
        check(win._toast_text.get_label() == "Could not reset the tuning slots",
              f"failed reset told: {win._toast_text.get_label()!r}")
    yield "reset failed", reset_failed

    def steam_names():
        from understeer import games
        real = games.installed_games
        games.installed_games = lambda: [("1213210", "Command & Conquer Remastered")]
        try:
            win.games_page._ask_game()
        finally:
            games.installed_games = real
        texts = [w.get_text() for w in walk(win.get_visible_dialog())
                 if isinstance(w, Gtk.Label)]
        check("Command & Conquer Remastered" in texts, f"name with & shown: {texts}")
    yield "Steam names with &", steam_names
    yield "close the game list", lambda: close_dialogs(win)

    def live_input():
        # A wheel and pedals, as the input devices would report them.
        win.inputs.axes = {}
        for name, code, value in (("FANATEC Wheel", 0, 40000),
                                  ("FANATEC Pedals", 1, 30000),
                                  ("FANATEC Pedals", 2, 65535),
                                  ("FANATEC Pedals", 5, 0)):
            axis = inputs.Axis(name, code, 0, 65535, value)
            win.inputs.axes[axis.key] = axis
        win._toggle_hidden(True)
        win._reset_peaks()
        win._update_inputs()
        win._rebuild_axes_list()
        win.tabs.set_visible_child_name("pedals")
        win._select_page("tuning")
    yield "live input", live_input
    yield "live input shown", lambda: (
        win._update_inputs(),
        check(win.steer_label.get_label() not in ("", "—"),
              f"steering angle shown: {win.steer_label.get_label()!r}"))

    # -- hiding an axis on the pedals tab: the eye, not a role --------------
    def role_lists():
        return [w for w in walk(win.axes_flow) if isinstance(w, Gtk.DropDown)]

    def pedals_hide():
        items = role_lists()[0].get_model()
        texts = [items.get_string(i) for i in range(items.get_n_items())]
        check("Hidden" not in texts, f"hiding is no role: {texts}")
        win._set_axis_role("FANATEC Pedals:2", "brake")
    yield "pedals hide", pedals_hide
    yield "pedals hide eye", lambda: next(
        w for w in walk(win.axes_flow) if isinstance(w, Gtk.Button)
        and w.get_icon_name() == "view-conceal-symbolic"
        and "Brake" in [x.get_label() for x in walk(w.get_parent())
                        if isinstance(x, Gtk.Label)]).emit("clicked")
    def pedals_hidden():
        check(win.axis_roles.get("FANATEC Pedals:2") == "hidden"
              and win.roles_before_hidden.get("FANATEC Pedals:2") == "brake",
              f"hidden, role kept: {win.axis_roles}")
        # shown with the hidden axes; its list shows the kept role
        lists = role_lists()
        check(len(lists) == 3, f"hidden axis shown on request: {len(lists)}")
        win._choose_axis_role("FANATEC Pedals:2", "clutch")
    yield "pedals hidden", pedals_hidden
    yield "pedals role while hidden", lambda: (
        check(win.axis_roles.get("FANATEC Pedals:2") == "hidden"
              and win.roles_before_hidden.get("FANATEC Pedals:2") == "clutch",
              "role changed, still hidden"),
        win._set_axis_hidden("FANATEC Pedals:2", False))
    yield "pedals shown again", lambda: (
        check(win.axis_roles.get("FANATEC Pedals:2") == "clutch",
              f"role back: {win.axis_roles}"),
        win._set_axis_role("FANATEC Pedals:2", ""))

    # -- axis diagnostics on the same axes, no second reader --------------
    def axis_diagnostics():
        hat = inputs.Axis("FANATEC Wheel", 0x10, -1, 1, 0, fuzz=0, flat=0)
        win.inputs.all_axes = dict(win.inputs.axes)
        win.inputs.all_axes[hat.key] = hat
        win.inputs.devices = ["FANATEC Wheel", "FANATEC Pedals"]
        check(not any(win.lookup_action(a) for a in (
            "axis-diagnostics", "button-diagnostics", "led-diagnostics")),
            "no diagnostics in the menu any more")
        win._select_page("diagnostics")
        win._on_input_gone()        # the devices changed: pages rebuilt
    yield "axis diagnostics", axis_diagnostics

    def diagnostics_shown():
        diag = win.axis_diagnostics
        page = win.diagnostics_page
        check(page.tab == "axes" and page.shown("axes")
              and win.views.get_visible_child_name() == "diagnostics",
              "diagnostics view on its first tab")
        check(set(diag.widgets) == set(win.inputs.all_axes),
              f"a card per axis: {sorted(diag.widgets)}")
        throttle = win.inputs.all_axes["FANATEC Pedals:1"]
        card = diag.widgets[throttle.key]["bar"].get_parent().get_parent()
        before = card.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        throttle.update(0)
        throttle.update(65535)
        win._update_inputs()
        w = diag.widgets[throttle.key]
        after = card.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        check(before == after, f"tile keeps its width: {before} -> {after}")
        check(w["observed"].get_label() == "0 … 65535 (100 %)",
              f"observed range: {w['observed'].get_label()}")
        check(w["status"].get_label() == "Full range reached",
              f"status: {w['status'].get_label()}")
        diag.reset()
        check(w["observed"].get_label() == "65535 … 65535 (0 %)",
              f"reset: {w['observed'].get_label()}")
    yield "axis diagnostics shown", diagnostics_shown

    def diagnostics_hide():
        # An assigned axis keeps its role while hidden
        win._set_axis_role("FANATEC Pedals:1", "throttle")
        diag = win.axis_diagnostics
        diag.widgets["FANATEC Pedals:1"]["hide"].emit("clicked")
    yield "axis diagnostics hide", diagnostics_hide
    yield "axis diagnostics hidden", lambda: (
        check(win.axis_roles.get("FANATEC Pedals:1") == "hidden",
              "hidden in the saved roles"),
        check("FANATEC Pedals:1" not in win.axis_diagnostics.widgets,
              "hidden in the diagnostics"),
        check(win.axis_diagnostics.hidden_box.get_visible(),
              "switch to show hidden axes"),
        win.axis_diagnostics.hidden_switch.set_active(True))
    yield "axis diagnostics show hidden", lambda: (
        check("FANATEC Pedals:1" in win.axis_diagnostics.widgets,
              "hidden axis shown on request"),
        win.axis_diagnostics.widgets["FANATEC Pedals:1"]["hide"].emit("clicked"))
    yield "axis diagnostics unhidden", lambda: (
        check(win.axis_roles.get("FANATEC Pedals:1") == "throttle",
              f"role back: {win.axis_roles.get('FANATEC Pedals:1')}"),
        check(config.load_axis_roles().get("FANATEC Pedals:1") == "throttle"
              and not config.load_roles_before_hidden(), "role saved"),
        win._set_axis_role("FANATEC Pedals:1", ""))

    def diagnostics_unplugged():
        for key in [k for k in win.inputs.all_axes if k.startswith("FANATEC Pedals")]:
            win.inputs.all_axes.pop(key)
            win.inputs.axes.pop(key, None)
        win.inputs.devices.remove("FANATEC Pedals")
        win._on_input_gone()
    yield "axis diagnostics unplugged", diagnostics_unplugged
    yield "axis diagnostics after unplug", lambda: (
        check(all(k.startswith("FANATEC Wheel")
                  for k in win.axis_diagnostics.widgets),
              f"pedal cards gone: {sorted(win.axis_diagnostics.widgets)}"))

    # -- button diagnostics on the same reader ----------------------------
    def button_diagnostics():
        win.inputs.devices = ["FANATEC Wheel", "FANATEC Pedals"]
        win.inputs.buttons = {}
        for name, code in [("FANATEC Wheel", c) for c in
                           list(range(0x120, 0x130)) + list(range(0x2c0, 0x2e8))
                           ] + [("FANATEC Pedals", 0x120)]:
            b = inputs.Button(name, code)
            win.inputs.buttons[b.key] = b
        win.diagnostics_page.tabs.set_visible_child_name("buttons")
        win.button_diagnostics.rebuild()
    yield "button diagnostics", button_diagnostics

    def buttons_shown():
        diag = win.button_diagnostics
        check(win.diagnostics_page.shown("buttons")
              and not win.diagnostics_page.shown("axes"), "buttons tab shown")
        check(set(diag.widgets) == set(win.inputs.buttons),
              f"a tile per button: {len(diag.widgets)}")
        check(diag.summary.get_label() == "0 of 57 buttons tested",
              f"summary: {diag.summary.get_label()}")
        first = win.inputs.buttons["FANATEC Wheel:key:288"]
        last = win.inputs.buttons["FANATEC Wheel:key:743"]
        first.update(1)
        last.update(1)          # two at once
        win.inputs.last_button = last.key   # as the monitor does on a press
        win._update_inputs()
        check(diag.widgets[first.key].has_css_class("button-held")
              and diag.widgets[last.key].has_css_class("button-held"),
              "both held buttons lit")
        check(diag.last.get_label() == "Last pressed: Button 56 · SDL 55 · "
              "BTN_TRIGGER_HAPPY40 · 743 · pressed 1×",
              f"last: {diag.last.get_label()}")
        check(diag.widgets[last.key].get_label() == "56"
              and "BTN_TRIGGER_HAPPY40" in
              diag.widgets[last.key].get_tooltip_text(),
              "a cell with the number, the name in its tooltip")
        check(diag.held.get_label() == "Held down: Button 1, Button 56",
              f"held: {diag.held.get_label()}")
        first.update(0)
        win._update_inputs()
        w = diag.widgets[first.key]
        check(not w.has_css_class("button-held")
              and w.has_css_class("button-tested"), "released, tested")
        check(diag.summary.get_label() == "2 of 57 buttons tested",
              f"summary: {diag.summary.get_label()}")
        diag.reset()
        check(not w.has_css_class("button-tested")
              and diag.last.get_label() ==
              "Press a button to see its name and code"
              and diag.summary.get_label() == "0 of 57 buttons tested",
              f"reset: {diag.summary.get_label()}")
        check(diag.widgets[last.key].has_css_class("button-held"),
              "held button stays lit after the reset")
    yield "button diagnostics shown", buttons_shown

    def buttons_unplugged():
        for key in [k for k in win.inputs.buttons if k.startswith("FANATEC Pedals")]:
            win.inputs.buttons.pop(key)
        win.inputs.devices.remove("FANATEC Pedals")
        win._on_input_gone()
    yield "button diagnostics unplugged", buttons_unplugged
    yield "button diagnostics after unplug", lambda: (
        check(all(k.startswith("FANATEC Wheel")
                  for k in win.button_diagnostics.widgets),
              f"pedal tiles gone: {len(win.button_diagnostics.widgets)}"),
        win.inputs.buttons.clear(), win.inputs.devices.clear(),
        win.button_diagnostics.rebuild(),
        check(win.button_diagnostics.problem.get_visible()
              and not win.button_diagnostics.widgets,
              "no device: a hint, no tiles"))

    # -- LED diagnostics: a Formula V2 with 9 RGB rev LEDs and 3+3 flags ---
    led_root = scratch / "sysfs" / "leds"
    led_gone = scratch / "sysfs" / "leds-gone"
    led_names = ([f"hid-device:0a:RGB:RPM{n}" for n in range(1, 10)]
                 + [f"hid-device:0a:RGB:{side}FLAG{n}"
                    for side in "LR" for n in range(1, 4)])

    def led_file(name, file="brightness"):
        return (led_root / name / file).read_text().strip()

    def led_diagnostics():
        for name in led_names:
            folder = led_root / name
            folder.mkdir(parents=True, exist_ok=True)
            # as the driver starts them: full brightness, no colour
            for file, value in (("brightness", 255), ("max_brightness", 255),
                                ("multi_index", "red green blue"),
                                ("multi_intensity", "0 0 0")):
                (folder / file).write_text(f"{value}\n")
            (folder / "max_brightness").chmod(0o444)
            (folder / "multi_index").chmod(0o444)
        capabilities.LEDS_GLOB = str(led_root / "*")
        win._check_capabilities()
        check(not win.led_diagnostics.active and not win.led_diagnostics.toggles,
              "LED page idle while another tab is shown")
        win.diagnostics_page.tabs.set_visible_child_name("leds")
    yield "LED diagnostics", led_diagnostics

    def leds_shown():
        diag = win.led_diagnostics
        check(diag.active, "LED page active on its tab")
        check(len(diag.toggles) == 15
              and not any(t.get_active() for t in diag.toggles.values()),
              f"15 LEDs, all dark: {len(diag.toggles)}")
        check(len(diag.color_buttons) == 7, "seven colours")
        check(diag.test_button.get_visible() and diag.off_button.get_visible()
              and not diag.problem.get_visible(), "test and All Off offered")
        first = led_root / led_names[0]
        diag.toggles[str(first)].set_active(True)
        check(led_file(led_names[0], "multi_intensity") == "255 255 255"
              and led_file(led_names[0]) == "255", "LED 1 lit in white")
        diag.color_buttons["red"].set_active(True)
        check(led_file(led_names[0], "multi_intensity") == "255 0 0",
              "lit LED turned red")
        check(led_file(led_names[1], "multi_intensity") == "0 0 0",
              "dark LED stays dark")
        diag.toggles[str(led_root / led_names[12])].set_active(True)
        check(led_file(led_names[12], "multi_intensity") == "255 0 0",
              "right flag 1 lit in red")
        diag.toggles[str(led_root / led_names[12])].set_active(False)
        check(led_file(led_names[12]) == "0", "right flag 1 off")
    yield "LED diagnostics shown", leds_shown

    def led_test_cancelled():
        diag = win.led_diagnostics
        diag.test_button.emit("clicked")
        check(diag.running and not diag.column.get_sensitive()
              and diag.test_button.get_label() == "Stop Test",
              "test running, LEDs locked")
        for _ in range(4):
            diag._step()
        check(led_file(led_names[2]) == "255"
              and led_file(led_names[2], "multi_intensity") == "255 255 255",
              "third LED lit by the test")
        diag.test_button.emit("clicked")
        check(not diag.running and diag.column.get_sensitive()
              and diag.status.get_label() == "Test stopped", "test stopped")
        check(led_file(led_names[0], "multi_intensity") == "255 0 0"
              and led_file(led_names[0]) == "255"
              and led_file(led_names[2], "multi_intensity") == "0 0 0",
              "LEDs set back as before the test")
        check(diag.toggles[str(led_root / led_names[0])].get_active(),
              "LED 1 shown lit again")
    yield "LED test cancelled", led_test_cancelled

    def led_test_finished():
        diag = win.led_diagnostics
        diag.start_test()
        steps_run = 0
        while diag._step() and steps_run < 100:
            steps_run += 1
        check(not diag.running and diag.status.get_label() == "Test finished",
              f"test finished after {steps_run} steps: "
              f"{diag.status.get_label()!r}")
        check(led_file(led_names[0], "multi_intensity") == "255 0 0",
              "LED 1 set back after the test")
        diag.off_button.emit("clicked")
        check(led_file(led_names[0]) == "0"
              and not any(t.get_active() for t in diag.toggles.values()),
              "All Off")
        diag.toggles[str(led_root / led_names[0])].set_active(True)
    yield "LED test finished", led_test_finished

    def led_unplugged():
        diag = win.led_diagnostics
        diag.start_test()
        diag._step()
        led_root.rename(led_gone)           # wheelbase unplugged
        diag._step()                        # writing fails: looks again
        check(not diag.running and not diag.toggles
              and diag.problem.get_visible()
              and diag.problem.get_label().startswith("No LEDs found"),
              f"test stopped, no LEDs: {diag.problem.get_label()!r}")
        check(not diag.test_button.get_visible()
              and not diag.off_button.get_visible(), "no controls without LEDs")
        led_gone.rename(led_root)
        win._check_capabilities()
        check(len(diag.toggles) == 15, "LEDs back once plugged in")
        check(not diag.problem.get_visible() and not diag.status.get_visible(),
              f"no message left once back: {diag.status.get_label()!r}")
    yield "LED diagnostics unplugged", led_unplugged

    def led_read_only():
        if os.geteuid() == 0:
            return                          # root may write anyway
        diag = win.led_diagnostics
        for name in led_names:
            (led_root / name / "brightness").chmod(0o444)
        win._check_capabilities()
        check(not any(t.get_sensitive() for t in diag.toggles.values())
              and not diag.test_button.get_sensitive()
              and "No permission" in diag.problem.get_label(),
              f"read only: {diag.problem.get_label()!r}")
        for name in led_names:
            (led_root / name / "brightness").chmod(0o644)
        win._check_capabilities()
        check(all(t.get_sensitive() for t in diag.toggles.values()),
              "usable again")
    yield "LED diagnostics read only", led_read_only

    def leds_left_running():
        diag = win.led_diagnostics
        diag.start_test()
        diag._step()
        diag._step()
        win.diagnostics_page.tabs.set_visible_child_name("axes")
        check(not diag.active and not diag.running and not diag._timer,
              "leaving the tab stops the test")
        check(all(led_file(n, "multi_intensity") == "0 0 0"
                  and led_file(n) == "255" for n in led_names),
              "LEDs set back as they were before the tab was shown")
        win.diagnostics_page.tabs.set_visible_child_name("leds")
        diag.toggles[str(led_root / led_names[0])].set_active(True)
        win._select_page("tuning")          # another view
        check(not diag.active and led_file(n := led_names[0], "multi_intensity")
              == "0 0 0", f"LED {n} set back when the view is left")
        win.diagnostics_page.tabs.set_visible_child_name("leds")
        check(not diag.active, "the tab alone does not wake it up")
    yield "LED diagnostics left", leds_left_running

    def leds_closed():
        capabilities.LEDS_GLOB = str(scratch / "sysfs" / "none" / "*")
        win._check_capabilities()
    yield "LED diagnostics closed", leds_closed

    for page in ("general", "wheelbase", "games", "data"):
        yield f"prefs {page}", lambda p=page: win._show_preferences(p)

    def language_with_prefs_open():
        win._show_preferences("wheelbase")
        win._set_language("de")
    yield "German with prefs open", language_with_prefs_open
    yield "prefs survive rebuild", lambda: (
        check(win._prefs_open(), "prefs still open after language change"),
        check(win.prefs.get_visible_page_name() == "wheelbase",
              f"prefs page kept: {win.prefs.get_visible_page_name()}"))

    # Another page chosen right before the language changes: replacing the
    # pages then made Libadwaita crash now and then.
    for i in range(16):
        yield f"page and language {i}", lambda i=i: (
            win._show_preferences(("general", "wheelbase", "games", "data")[i % 4]),
            win._set_language("en" if i % 2 else "de"))
    yield "prefs after many changes", lambda: check(
        win.prefs.get_visible_page_name() == "data" and win._prefs_open(),
        f"prefs page kept: {win.prefs.get_visible_page_name()}")

    def toast_in_prefs():
        before = win._toast
        win._notify("Shown in the preferences")
        check(win._toast is before, "message goes to the open preferences")
    yield "message while prefs open", toast_in_prefs
    yield "close prefs", lambda: (win.prefs.close(), win._set_language("en"))

    def toast_in_window():
        check(not win._prefs_open(), "prefs closed")
        win._notify("Shown in the window")
        check(win._toast is not None, "message goes to the main window")
    yield "message in window", toast_in_window

    # -- report dialog, alone and over the preferences ----------------------
    report = {}

    def open_report():
        report["d"] = ReportDialog(win, title="Oops", what="Body")
    yield "report", open_report
    yield "report shown", lambda: (
        check(win.get_visible_dialog() is report["d"].dialog, "report is a dialog"),
        report["d"]._copy("copied"), report["d"].dialog.close())

    def report_over_prefs():
        win._show_preferences("data")
        report["d"] = ReportDialog(win)
    yield "report over prefs", report_over_prefs
    yield "report on top", lambda: (
        check(win.get_visible_dialog() is report["d"].dialog, "report over prefs"),
        report["d"].dialog.close())
    yield "prefs again", lambda: (
        check(win.get_visible_dialog() is win.prefs, "prefs back after report"))

    # -- SteamGridDB key: counts without an apply button --------------------
    def key_entry():
        return win.games_page.key_entry
    key = cover_search.KEY_SETTING
    yield "key typed, prefs closed", lambda: (
        win._show_preferences("games"), key_entry().row.set_text("good-1"),
        win.prefs.close())
    yield "key saved on close", lambda: check(config.get_setting(key) == "good-1",
                                              "key saved when closing")
    yield "refused key", lambda: (win._show_preferences("games"),
                                  key_entry().row.set_text("bad"))
    yield "wait for key check", lambda: None
    yield "wait for key check", lambda: None
    yield "refused key marked", lambda: (
        check(config.get_setting(key) == "bad", "key saved after typing stops"),
        check(key_entry().row.has_css_class("error"), "refused key marked red"),
        key_entry().row.set_text(""), key_entry().row.emit("entry-activated"),
        check(config.get_setting(key) is None, "key removed with Enter"),
        win.prefs.close())

    cover = {}

    def cover_search_without_key():
        cover["c"] = cover_search.CoverSearch(win.games_page, {"name": "Game"})
    yield "cover search", cover_search_without_key
    yield "cover key page", lambda: (
        check(win.get_visible_dialog() is cover["c"].dialog, "cover search is a dialog"),
        check(cover["c"].nav.get_visible_page().get_tag() == "key", "key page shown"),
        cover["c"].key_row.row.set_text("good-2"))
    yield "wait for key check", lambda: None
    yield "wait for key check", lambda: None
    yield "cover search goes on", lambda: (
        check(config.get_setting(key) == "good-2", "cover search saved the key"),
        check(cover["c"].nav.get_visible_page().get_tag() != "key", "search shown"),
        check(key_entry().row.get_text() == "good-2", "prefs row shows the key"),
        cover["c"].dialog.close())

    # -- after an update: what's new, once, also after the background ------
    newest = ET.parse(releases.METAINFO).getroot().find("releases/release")
    newest, older = newest.get("version"), "1.0.0"

    def news_shown():
        d = win.get_visible_dialog()
        return d if d is not None and d.get_title() == "What's New" else None

    def news_lines(d):
        return [w.get_label() for w in walk(d) if isinstance(w, Gtk.Label)]

    def updated():
        session_log.version = lambda: f"v{newest}"
        config.set_setting("last_version", older)
        win._announce_update()
        d = news_shown()
        check(d is not None, "what's new shown after an update")
        lines = news_lines(d) if d else []
        check(f"Understeer {newest}" in lines, f"newest version listed: {lines[:3]}")
        listed = [w.get_label() for w in walk(d) if isinstance(w, Gtk.Label)
                  and "title-2" in w.get_css_classes()] if d else []
        check(len(listed) == len(releases.all_versions()), f"whole history: {listed}")
        badges = [w for w in walk(d) if isinstance(w, Gtk.Label)
                  and "new-badge" in w.get_css_classes()] if d else []
        check(len(badges) == len(releases.versions_since(older, newest)),
              f"versions since {older} marked new: {len(badges)}")
        check(config.get_setting("last_version") == newest, "new version remembered")
        width = d.get_content_width() if d else 0
        check(width >= 560, f"big dialog: {width}")
        close_dialogs(win)
    yield "what's new after an update", updated

    def not_again():
        win._announce_update()
        check(news_shown() is None, "what's new only once")
    yield "what's new once", not_again

    def hidden_update():
        config.set_setting("last_version", older)
        win._in_background = True               # started in the background
        try:
            win._announce_update()
            check(news_shown() is None, "not shown while hidden")
            check(config.get_setting("last_version") == older,
                  "not seen yet while hidden")
        finally:
            win._in_background = False
    yield "update while hidden", hidden_update

    def shown_when_opened():
        win.show_again()
    yield "window opened", shown_when_opened
    yield "what's new when opened", lambda: (
        check(news_shown() is not None, "what's new shown once the window opens"),
        check(config.get_setting("last_version") == newest, "remembered then"),
        close_dialogs(win))

    def same_size(d):
        return (d.get_content_width(), d.get_content_height()) == win._dialog_size()

    def from_menu():
        win.activate_action("win.whats-new", None)
        d = news_shown()
        lines = news_lines(d) if d else []
        check(lines[:1] == [f"Understeer {newest}"], f"menu starts with this version: {lines[:1]}")
        check(not [w for w in walk(d) if isinstance(w, Gtk.Label)
                   and "new-badge" in w.get_css_classes()], "nothing marked new")
        close_dialogs(win)
        check(d is not None and same_size(d), "what's new as big as the others")
        win._show_about()
    yield "what's new from the menu", from_menu
    yield "about", lambda: (
        check(isinstance(win.get_visible_dialog(), Adw.AboutDialog), "About shown"),
        check(same_size(win.get_visible_dialog()), "About as big as the others"),
        check(not win.get_visible_dialog().get_release_notes(),
              "no notes in About, What's New has them"),
        check(win.get_visible_dialog().emit(
            "activate-link", win.get_visible_dialog().get_issue_url()) is True,
            "Report an Issue handled by Understeer"))
    yield "report from About", lambda: (
        check(win.get_visible_dialog() is not None and
              win.get_visible_dialog().get_title() == "Report a Problem",
              "report window instead of GitHub"),
        win.get_visible_dialog().close())
    yield "back in About", lambda: (
        check(isinstance(win.get_visible_dialog(), Adw.AboutDialog),
              "closing the report window goes back to About"),
        close_dialogs(win))

    # -- new versions on GitHub (only the AppImage looks) --------------------
    fetched, restarted = [], []
    image = scratch / "Understeer.AppImage"

    def ask_update_check():
        check(not win.update_banner.get_revealed(), "no update banner from git")
        image.write_bytes(b"old AppImage")
        os.environ["APPIMAGE"] = str(image)
        updates.fetch_latest = lambda: fetched.append(1) or {
            "version": "99.0.0", "url": updates.RELEASES_URL + "/tag/v99.0.0",
            "asset": {"url": updates.DOWNLOAD_URL + "v99.0.0/x", "sha256": "0" * 64,
                      "size": 3}}
        win._start_update_check()
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog)
              and d.get_heading() == "Look for New Versions?", "asked once")
        check(not fetched, "nothing fetched before agreeing")
        if isinstance(d, Adw.AlertDialog):
            d.emit("response", "yes")
            d.force_close()
    yield "look for new versions?", ask_update_check
    yield "new version shown", lambda: (
        check(fetched == [1], "fetched once"),
        check(win.update_banner.get_revealed() and
              "99.0.0" in win.update_banner.get_title(),
              f"update banner: {win.update_banner.get_title()!r}"),
        win._start_update_check(),
        check(fetched == [1] and win.get_visible_dialog() is None,
              "not asked or fetched again within the hour"),
        check(win.update_banner.get_button_label() == "Update Now",
              f"can update itself: {win.update_banner.get_button_label()!r}"))

    def update_itself():
        def download(asset, appimage, **_kw):
            new = scratch / "downloaded.AppImage"
            new.write_bytes(b"new AppImage")
            return str(new)
        updates.download = download
        win.update_banner.emit("button-clicked")
        check("Updating" in win.update_banner.get_title(), "updating shown")
    yield "update itself", update_itself

    def update_installed():
        check(image.read_bytes() == b"new AppImage", "AppImage replaced")
        check(os.access(image, os.X_OK), "new AppImage can run")
        check(win.update_banner.get_button_label() == "Restart",
              f"restart offered: {win.update_banner.get_title()!r}")
        updates.restart = restarted.append
        quits.clear()
        application._quit_now = lambda: quits.append(True)   # stay running
        win.update_banner.emit("button-clicked")
        check(restarted == [str(image)] and quits, "restarted after quitting")
        del application._quit_now
        win._update_state = None
    yield "update installed", update_installed

    def update_check_off():
        win._show_preferences("general")
        row = find_row(win.prefs, "Look for New Versions")
        check(row is not None and row.get_sensitive() and row.get_active(),
              "preference on after agreeing")
        row.set_active(False)
        check(config.get_setting("update_check") is False, "switched off")
        check(not win.update_banner.get_revealed(), "banner gone when off")
        win.prefs.close()
        del os.environ["APPIMAGE"]
        win._show_update_banner()
    yield "update check off", update_check_off

    # -- wheelbase and steering wheel ---------------------------------------
    yield "hardware shown", lambda: (
        win._show_preferences("wheelbase"),
        check(win.base_row.get_subtitle() == "CSL DD · Firmware 1234",
              f"wheelbase row: {win.base_row.get_subtitle()!r}"),
        check(win.rim_row.get_visible() and win.rim_row.get_subtitle() ==
              "ClubSport Steering Wheel Formula V2 · Firmware 52",
              f"wheel row: {win.rim_row.get_subtitle()!r}"),
        check("Formula V2" in dict(win.report_details()).get(
            "Steering wheel model", ""),
              "wheel in the report"),
        check("display w" in dict(win.report_details()).get("Hardware functions", "")
              and "advanced_mode -" in dict(win.report_details())["Hardware functions"],
              "hardware functions in the report"),
        check(dict(win.report_details()).get("Driver generation")
              == "October 2026 or newer (rim_id)", "driver generation in the report"))
    yield "wheel changed", lambda: (hid / "rim_id").write_text("15\n")
    yield "wait for the wheel", lambda: None
    yield "new wheel shown", lambda: (
        check(win.rim_row.get_subtitle().startswith("Podium Steering Wheel BMW M4 GT3"),
              f"changed wheel: {win.rim_row.get_subtitle()!r}"),
        win.prefs.close())

    # -- running in the background ------------------------------------------
    application = win.get_application()

    def background_on():
        win._show_preferences("games")
        run = find_row(win.prefs, "Run in the Background")
        login = find_row(win.prefs, "Start at Login")
        check(run is not None and login is not None, "background switches found")
        check(not login.get_sensitive(), "start at login needs the background")
        notify = find_row(win.prefs, "Notify When a Game Starts")
        check(notify is not None and not notify.get_sensitive()
              and notify.get_active(), "game notifications on, need the background")
        run.set_active(True)
        check(login.get_sensitive(), "start at login can be switched on")
        check(notify.get_sensitive(), "game notifications can be switched")
        login.set_active(True)
        check(background.autostart_enabled(), "autostart file written")
        if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            check(application.tray.running, "tray icon up while in the background")
        win.prefs.close()
    yield "background on", background_on
    yield "close the window", lambda: win.close()
    yield "runs on hidden", lambda: (
        check(not win.get_visible(), "window hidden"),
        check(win._in_background and win in application.get_windows(),
              "still running"),
        check(not win.inputs.axes, "live input stopped"),
        check(config.get_setting("background_hint_shown"), "hint shown once"))
    yield "games still watched", lambda: check(win._check_games() is True,
                                               "games watch runs on")

    notes, active = [], []
    bg_preset = config.preset_path("Background Test")

    def game_in_background():
        active.append((win.active_preset, dict(win.active_values)))
        config.write_preset(bg_preset, "Background Test", {"FF": 64})
        win._desktop_notify = lambda *note: notes.append(note)
        win._apply_preset(bg_preset, game="BeamNG")
        check((dev / "FF").read_text().strip() == "64", "applied in the background")
        check(notes == [("game-preset", "BeamNG started",
                         "Preset “Background Test” applied.")], f"notified: {notes}")
        notes.clear()
        path, win.device_path = win.device_path, None
        try:
            win._apply_preset(bg_preset, game="BeamNG")
        finally:
            win.device_path = path
        check(notes and "as soon as the wheelbase is connected" in notes[0][2],
              f"applied once connected: {notes}")
        notes.clear()
        config.set_setting("game_notifications", False)
        win._apply_preset(bg_preset, game="BeamNG")
        check(not notes, "no notification when switched off")
        config.set_setting("game_notifications", None)
    yield "game starts in the background", game_in_background

    def connected_while_game_runs():
        games_before = win.games_page.games
        win.games_page.games = [dict(game="test-game", name="BeamNG", auto=True,
                                     preset=bg_preset.name)]
        win._game_watch.running.add("test-game")
        path, win.device_path = win.device_path, None
        try:
            win._apply_preset(bg_preset, game="BeamNG")     # no wheelbase yet
            (dev / "FF").write_text("20\n")                 # its own value
            notes.clear()
            win._plug_tick = -1                             # look right now
            win._check_plugged()
            check(win.device_path == path, "wheelbase found again")
            check((dev / "FF").read_text().strip() == "64",
                  "preset of the running game written once connected")
            check(notes == [("game-preset", "Wheelbase connected, BeamNG runs",
                             "Preset “Background Test” applied.")], f"told: {notes}")
        finally:
            win.device_path = win.device_path or path
            win.games_page.games = games_before
            win._game_watch.running.discard("test-game")
            notes.clear()
    yield "connected while a game runs", connected_while_game_runs

    def preset_from_tray():
        tray_preset = config.preset_path("Tray Test")
        config.write_preset(tray_preset, "Tray Test", {"FF": 30})
        try:
            listed = {name: (key, on) for name, key, on in application._tray_presets()}
            check(listed.get("Background Test") == (str(bg_preset), True)
                  and listed.get("Tray Test") == (str(tray_preset), False),
                  f"presets in the tray menu, the active one checked: {listed}")
            notes.clear()
            application._tray_apply_preset(str(tray_preset))
            check((dev / "FF").read_text().strip() == "30", "applied from the tray")
            check(win._in_background and not win.get_visible(), "window stays closed")
            check(win.active_preset == tray_preset, "now the active preset")
            check(notes == [("tray-preset", "Understeer",
                             "Preset “Tray Test” applied.")], f"told: {notes}")
            check(dict((n, on) for n, _k, on in application._tray_presets())
                  .get("Tray Test") is True, "checked in the tray menu now")
            notes.clear()
            path, win.device_path = win.device_path, None
            try:
                application._tray_apply_preset(str(bg_preset))
            finally:
                win.device_path = path
            check(notes and "was not written" in notes[0][2],
                  f"told it was not written without a wheelbase: {notes}")
            # Unsaved changes: the window opens and asks before they are lost
            application._tray_apply_preset(str(tray_preset))
            win._set_setting("FF", 55)           # written, as by the user
            win.flush_live()
            check((dev / "FF").read_text().strip() == "55", "change written")
            notes.clear()
            application._tray_apply_preset(str(bg_preset))
            check(win.get_visible() and win._save_dialog is not None,
                  "window shown to ask about unsaved changes")
            check(win.active_preset == tray_preset and not notes,
                  "nothing applied before the answer")
            win._save_dialog.force_close()
            win._save_dialog = None
        finally:
            tray_preset.unlink()
            win._rebuild_preset_menu()
            notes.clear()
    yield "preset from the tray menu", preset_from_tray
    yield "started again", lambda: application.activate()
    def window_back():
        check(win.get_visible() and not win._in_background, "window shown again")
        win._apply_preset(bg_preset, game="BeamNG")
        check(not notes, "no notification with the window open")
        del win._desktop_notify
        bg_preset.unlink()
        win._set_active_preset(*active[0])     # as the next steps expect
    yield "window back", window_back

    def background_off():
        win._show_preferences("games")
        find_row(win.prefs, "Run in the Background").set_active(False)
        check(not background.autostart_enabled(), "autostart removed with it")
        check(not application.tray.running, "tray icon gone")
        win.prefs.close()
    yield "background off", background_off

    # -- export, import, layout ---------------------------------------------
    def export_import():
        dest = scratch / "export.zip"
        config.set_setting("theme", "carbon")
        config.set_setting("background", True)
        counts = backup.export(dest)
        config.set_setting("theme", None)
        config.set_setting("background", None)
        theme.apply()
        application.update_tray()
        check(counts["presets"] == 1, f"export counts {counts}")
        win._import(backup.read(dest))
        check(Adw.StyleManager.get_default().get_color_scheme()
              == Adw.ColorScheme.FORCE_DARK, "imported theme shown right away")
        check(application.tray.running, "tray icon of the import shown right away")
        config.set_setting("theme", None)
        config.set_setting("background", None)
        theme.apply()
        application.update_tray()
    yield "export/import", export_import
    yield "after import", lambda: check(config.preset_path("Renamed").is_file(),
                                        "preset still there after import")

    def layout_and_language():
        win._set_layout("compact")
        win._set_language("de")
    yield "compact + German", layout_and_language
    yield "German shown", lambda: (
        check(win.active_preset == config.preset_path("Renamed"),
              "active preset kept over relocalize"),
        win._select_page("tuning"))

    def delete():
        path = config.preset_path("Renamed")
        path.unlink()
        if win.active_preset == path:
            win.active_preset, win.active_values = None, {}
        win._presets_changed()
        win._select_page("games")
    yield "delete preset", delete
    yield "reload", lambda: win._load_from_device()


def main():
    application = app.TunerApplication()
    session_log.log.addHandler(Collect())

    def run_steps():
        if started:     # activated again, e.g. by showing the window
            return False
        started.append(True)
        win = application.get_active_window()
        check(isinstance(win, Adw.ApplicationWindow), "main window shown")
        it = steps(win)

        def next_step():
            try:
                name, fn = next(it)
                if os.environ.get("SMOKE_TRACE"):
                    print("step:", name, file=sys.stderr, flush=True)
            except StopIteration:
                finished.append(True)
                application.quit()
                return False
            try:
                fn()
                steps_done.append(name)
            except Exception:
                failures.append(f"step {name}: " + traceback.format_exc())
            return True
        GLib.timeout_add(STEP_MS, next_step)
        return False

    application.connect("activate", lambda a: GLib.timeout_add(1500, run_steps))
    GLib.timeout_add_seconds(90, lambda: (failures.append("timeout"),
                                          application.quit()))
    session_log.setup()
    config.migrate_config()
    application.run([])
    if not finished and "timeout" not in failures:
        failures.append("the app ended before all steps ran")
    print(f"steps done: {len(steps_done)}")
    print(f"failures: {len(failures)}")
    for f in failures:
        print("  -", f)
    return min(len(failures), 100)


sys.exit(main())
