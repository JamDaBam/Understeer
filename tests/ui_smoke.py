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

from understeer import app, background, backup, config, inputs, releases, log as session_log  # noqa: E402
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

    # -- after an update: message once, "What's New" in the About dialog ---
    newest = ET.parse(releases.METAINFO).getroot().find("releases/release")
    newest, older = newest.get("version"), "1.0.0"

    def updated():
        session_log.version = lambda: f"v{newest}"
        config.set_setting("last_version", older)
        win._announce_update()
        check(win._toast is not None and newest in win._toast.get_title(),
              "update message shown")
        check(config.get_setting("last_version") == newest, "new version remembered")
    yield "update message", updated

    def not_again():
        win._hide_toast()
        win._announce_update()
        check(win._toast is None, "update message only once")
        win._show_about()
    yield "update message once", not_again
    yield "what's new", lambda: (
        check(isinstance(win.get_visible_dialog(), Adw.AboutDialog), "About shown"),
        check(win.get_visible_dialog().get_release_notes_version() == newest,
              "About has the notes of this version"),
        check(bool(win.get_visible_dialog().get_release_notes()), "notes not empty"),
        win.get_visible_dialog().close())

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
        run.set_active(True)
        check(login.get_sensitive(), "start at login can be switched on")
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
    yield "started again", lambda: application.activate()
    yield "window back", lambda: (
        check(win.get_visible() and not win._in_background, "window shown again"))

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
