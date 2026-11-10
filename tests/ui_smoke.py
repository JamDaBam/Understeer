"""Drive the whole app against a fake wheel base; run by test_ui.py.

Usage: ui_smoke.py <scratch dir>
Expects an isolated HOME and a display (test_ui.py starts Xvfb). Nothing
goes to the network: SteamGridDB is replaced. Every uncaught exception,
every ERROR log line and every failed check counts as a failure; the
exit code is the number of failures.
"""
import logging
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

from understeer import app, backup, config, inputs, releases, log as session_log  # noqa: E402
from understeer.ui import cover_search  # noqa: E402
from understeer.ui.report_dialog import ReportDialog  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

failures, steps_done = [], []
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


def close_dialogs(win):
    for w in Gtk.Window.list_toplevels():
        if w is not win and w.get_visible():
            w.close()
    for _i in range(10):
        d = win.get_visible_dialog()
        if d is None:
            break
        d.force_close()


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
        win._update_active_preset()
        _n, vals = config.read_preset(win.active_preset)
        check(vals.get("SHO") == 40, "preset updated")
    yield "update preset", modify_and_update

    def apply_preset():
        win._apply_values({"FF": 70, "SHO": 80})
        win._apply_preset(config.preset_path("Test GT3"))
        check((dev / "SHO").read_text().strip() == "40", "preset applied to device")
    yield "apply preset", apply_preset

    def rename():
        win._rename_preset(config.preset_path("Test GT3"), "Test GT3", "Renamed")
        check(config.preset_path("Renamed").is_file(), "renamed file exists")
        check(win.active_preset == config.preset_path("Renamed"), "active follows rename")
        check(win.preset_button.get_label().startswith("Renamed"),
              f"preset button shows new name: {win.preset_button.get_label()!r}")
    yield "rename preset", rename

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
            except StopIteration:
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
    print(f"steps done: {len(steps_done)}")
    print(f"failures: {len(failures)}")
    for f in failures:
        print("  -", f)
    return min(len(failures), 100)


sys.exit(main())
