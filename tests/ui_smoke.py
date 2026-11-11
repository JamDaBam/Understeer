"""Drive the whole app against a fake wheel base; run by test_ui.py.

Usage: ui_smoke.py <scratch dir>
Expects an isolated HOME and a display (test_ui.py starts Xvfb). Nothing
goes to the network: SteamGridDB is replaced. Every uncaught exception,
every ERROR log line and every failed check counts as a failure; the
exit code is the number of failures.
"""
import logging
import os
import pathlib
import xml.etree.ElementTree as ET
import sys
import traceback

scratch = pathlib.Path(sys.argv[1])
sys.argv = sys.argv[:1]

# A fake wheel base: one sysfs folder with every setting as a plain file.
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

from understeer import device, steamgriddb  # noqa: E402

device.SYSFS_GLOB = str(scratch / "sysfs" / "ftec_tuning" / "*")
device.RUMBLE_GLOB = str(scratch / "sysfs" / "none" / "*" / "rumble")


def fake_search(term, key):
    """SteamGridDB stand-in: keys starting with "good" are accepted."""
    if not key or not key.startswith("good"):
        raise steamgriddb.Error("key", "refused (test)")
    return []


def offline(*_args, **_kwargs):
    raise steamgriddb.Error("network", "offline (test)")


steamgriddb.search = fake_search
steamgriddb.covers = steamgriddb.fetch = steamgriddb.download = offline

from understeer import app, background, backup, config, inputs, releases, sharing, log as session_log  # noqa: E402
from understeer.ui import cover_search  # noqa: E402
from understeer.ui.report_dialog import ReportDialog  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

failures, steps_done = [], []
finished = []           # set once every step ran
STEP_MS = 300


EXPECTED_ERRORS = {"Could not save: Disk full"}     # shown on purpose below


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
    yield "start", lambda: check(win.device_path is not None, "fake wheel base found")

    def live_write():
        win.rows["FF"].set(55, silent=False)
        win.flush_live()
        check((dev / "FF").read_text().strip() == "55", "FF written live")
    yield "live write", live_write

    def changed_on_wheel_base():
        (dev / "SHO").write_text("60\n")
    yield "change on the wheel base", changed_on_wheel_base
    yield "change followed", lambda: check(win.rows["SHO"].get() == 60,
                                           "control follows the wheel base")

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
        check(win.preset_button.get_label() == "Test GT3 •",
              f"modified marker: {win.preset_button.get_label()!r}")
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
              "wheel base keeps the changed value")
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
        rally.unlink()
        win._presets_changed()
    yield "apply preset", apply_preset

    def preset_values():
        win._apply_values({"SHO": 55})
        win._show_preset(gt3)
        d = win.get_visible_dialog()
        rows = {r.get_title(): (r_suffix(r), r.get_subtitle())
                for r in walk(d) if isinstance(r, Adw.ActionRow)}
        check(rows.get("Wheel Vibration") == ("40 %", "Now: 55 %"),
              f"preset value and the current one: {rows.get('Wheel Vibration')}")
        check(rows.get("Force Feedback Strength", ("", "x"))[1] in ("", None),
              "equal values not marked")
        titles = [g.get_title() for g in walk(d) if isinstance(g, Adw.PreferencesGroup)]
        check("Pedals &amp; Paddles" in titles or "Wheel Feel" in titles,
              f"groups by tab: {titles}")
        check((dev / "SHO").read_text().strip() == "55", "nothing applied by looking")
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
        win._import_preset(text)
        d = win.get_visible_dialog()
        check(isinstance(d, Adw.AlertDialog) and d.has_response("replace")
              and d.has_response("keep"), "same preset: replace or keep both")
        answer("replace")
        _n, vals = config.read_preset(gt3)
        check(vals.get("SHO") == 40, "replaced by the same values")
        win._import_preset(text.replace('"SHO": 40', '"SHO": 70'))
        lines = answer("keep")
        check(lines == ["Wheel Vibration: 40 % → 70 %"], f"import changes: {lines}")
        _n, vals = config.read_preset(copy)
        check(vals.get("SHO") == 70, "kept both")
        win._import_preset("no preset here")
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
        win._import_preset(sharing.preset_json(name, {"FF": 33}))
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
        win._import_preset(text)
        answer("replace")
        _n, vals = config.read_preset(gt3)
        check(vals == {"SHO": 10}, f"replaced: {vals}")
        check(win.preset_button.get_label() == "Test GT3 •",
              "replaced active preset differs from the settings")
        win._write_preset(gt3, "Test GT3")      # as the next steps expect
        copy.unlink()
        win._presets_changed()
    yield "pasted", pasted


    def rename():
        win._rename_preset(config.preset_path("Test GT3"), "Test GT3", "Renamed")
        check(config.preset_path("Renamed").is_file(), "renamed file exists")
        check(win.active_preset == config.preset_path("Renamed"), "active follows rename")
        check(win.preset_button.get_label().startswith("Renamed"),
              f"preset button shows new name: {win.preset_button.get_label()!r}")
    yield "rename preset", rename

    def rebuilt_without_wheel_base():
        # Nothing to read the values from again after the window is rebuilt
        path, win.device_path = win.device_path, None
        try:
            win._apply_values({"SEN": 540, "SHO": 35})
            win.relocalize()
            shown = {a: win.rows[a].get() for a in ("SEN", "SHO")}
            check(shown == {"SEN": 540, "SHO": 35},
                  f"values kept without a wheel base: {shown}")
        finally:
            win.device_path = path
            win.relocalize()
    yield "rebuilt without wheel base", rebuilt_without_wheel_base

    def dialogs():
        win._ask_preset_name()
        win._confirm_overwrite(config.preset_path("Renamed"))
        win._ask_rename_preset(config.preset_path("Renamed"), "Renamed")
        win._confirm_delete_preset(config.preset_path("Renamed"), "Renamed")
        win._confirm_reset()
        win._error("Could not save", "Disk full")
    yield "open dialogs", dialogs
    yield "close dialogs", lambda: close_dialogs(win)

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

    # -- wheel base and steering wheel ---------------------------------------
    yield "hardware shown", lambda: (
        win._show_preferences("wheelbase"),
        check(win.base_row.get_subtitle() == "CSL DD · Firmware 1234",
              f"wheel base row: {win.base_row.get_subtitle()!r}"),
        check(win.rim_row.get_visible() and win.rim_row.get_subtitle() ==
              "ClubSport Steering Wheel Formula V2 · Firmware 52",
              f"wheel row: {win.rim_row.get_subtitle()!r}"),
        check("Formula V2" in dict(win.report_details()).get("Wheel", ""),
              "wheel in the report"))
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
        check(notes and "as soon as the wheel base is connected" in notes[0][2],
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
            win._apply_preset(bg_preset, game="BeamNG")     # no wheel base yet
            (dev / "FF").write_text("20\n")                 # its own value
            notes.clear()
            win._plug_tick = -1                             # look right now
            win._check_plugged()
            check(win.device_path == path, "wheel base found again")
            check((dev / "FF").read_text().strip() == "64",
                  "preset of the running game written once connected")
            check(notes == [("game-preset", "Wheel base connected, BeamNG runs",
                             "Preset “Background Test” applied.")], f"told: {notes}")
        finally:
            win.device_path = win.device_path or path
            win.games_page.games = games_before
            win._game_watch.running.discard("test-game")
            notes.clear()
    yield "connected while a game runs", connected_while_game_runs
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
        counts = backup.export(dest)
        check(counts["presets"] == 1, f"export counts {counts}")
        win._import(backup.read(dest))
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
