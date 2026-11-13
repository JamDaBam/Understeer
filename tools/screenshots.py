#!/usr/bin/env python3
"""Make the screenshots in docs/ for the README.

    python3 tools/screenshots.py [work folder]

Runs Understeer on an invisible screen (Xvfb) with its own home folder in
the work folder (a new folder in /tmp if none is given; it is left there
for a look afterwards), a fake wheelbase, example presets and games, and
saves docs/screenshot.png, presets.png, select.png, games.png and
diagnostics.png. Nothing of your own configuration is read or changed;
only the covers of the example games are copied from your Steam cache if
it has them (otherwise the tiles show their name). Nothing goes to the
network.

Needs Xvfb, like the UI test.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SIZE = (1050, 770)      # the window; its shadow takes 5 px of each side

GAMES = [   # Steam app id, name, preset
    (805550, "Assetto Corsa Competizione", "Spa GT3"),
    (244210, "Assetto Corsa", "GT3 Endurance"),
    (284160, "BeamNG.drive", "BeamNG Drift"),
    (690790, "DiRT Rally 2.0", "Rally Stage"),
]
RUNNING = 284160
BASE = {"SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0, "FFS": 1,
        "FEI": 30, "INT": 0, "NDP": 0, "NFR": 0, "NIN": 0, "SHO": 80,
        "DRI": 0, "brF": 50, "BLI": 0, "FUL": 0, "ACP": 1}
PRESETS = [     # name, folder, changes to BASE
    ("Daily Driving", None, {"SEN": 900, "FF": 60}),
    ("GT3 Endurance", None, {"SEN": 480, "FF": 65, "brF": 70}),
    ("Nordschleife", "Assetto Corsa Competizione", {"SEN": 540, "FF": 70}),
    ("Spa GT3", "Assetto Corsa Competizione", {"SEN": 480, "FF": 75}),
    ("BeamNG Drift", "BeamNG.drive", {"SEN": 540, "FF": 55, "DRI": 2}),
    ("BeamNG Crawling", "BeamNG.drive", {"SEN": 900, "FF": 45}),
    ("BeamNG Rally", "BeamNG.drive", {"SEN": 720, "FF": 65}),
    ("Rally Stage", "DiRT Rally 2.0", {"SEN": 540, "FF": 80, "DRI": 1}),
]


def steam_covers(target):
    """Copy the covers of GAMES from the user's Steam cache into Understeer's
    cover cache in the fake home; games without one show "No cover"."""
    sys.path.insert(0, str(ROOT))
    from understeer import games
    target.mkdir(parents=True, exist_ok=True)
    for appid, _name, _preset in GAMES:
        cover = games.find_cover(appid)
        if cover:
            shutil.copyfile(cover, target / f"steam_{appid}.jpg")
        else:
            print(f"No cover of {appid} in your Steam cache")


def outer(work):
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    steam_covers(home / ".cache" / "Understeer" / "covers")
    read, write = os.pipe()
    xvfb = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write), "-screen", "0", "1600x1000x24",
         "-nolisten", "tcp"], pass_fds=(write,),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write)
    with os.fdopen(read) as f:
        display = f.readline().strip()
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                        "LANGUAGE", "LC_ALL", "LC_MESSAGES")}
    env.update({
        "DISPLAY": f":{display}", "GDK_BACKEND": "x11",
        "GSK_RENDERER": "cairo", "NO_AT_BRIDGE": "1", "LANG": "C.UTF-8",
        "ADW_DISABLE_PORTAL": "1", "GDK_DEBUG": "no-portals",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PYTHONPATH": str(ROOT),
    })
    command = [sys.executable, __file__, "--inner", str(work)]
    if shutil.which("dbus-run-session"):
        command = ["dbus-run-session", "--", *command]
    try:
        result = subprocess.run(command, env=env, cwd=ROOT, timeout=120,
                                stdin=subprocess.DEVNULL)
    finally:
        xvfb.terminate()
        xvfb.wait(timeout=10)
    print(f"Work folder (left for a look): {work}")
    return result.returncode


# -- inside the fake home, on the invisible screen ---------------------------
def inner(work):
    sysfs = work / "sysfs"
    dev = sysfs / "ftec_tuning" / "0003:0EB7:0020.0005"
    dev.mkdir(parents=True, exist_ok=True)
    for k, v in dict(BASE, SLOT=1, RESET=0).items():
        (dev / k).write_text(f"{v}\n")
    hid = sysfs / "hid-device"
    hid.mkdir(exist_ok=True)
    for k, v in {"fw_version": 1537, "rim_id": 0x0A, "fw_version_rim": 8}.items():
        (hid / k).write_text(f"{v}\n")
    if not (dev / "device").exists():
        (dev / "device").symlink_to(hid)

    from understeer import capabilities, config, device, games
    device.SYSFS_GLOB = str(sysfs / "ftec_tuning" / "*")
    device.RUMBLE_GLOB = str(sysfs / "none" / "*" / "rumble")
    for name in ("HID_GLOB", "LEDS_GLOB", "WHEEL_GLOB"):
        setattr(capabilities, name, str(sysfs / "none" / "*"))
    games.running_games = lambda proc=None: {f"steam:{RUNNING}"}

    for key, value in {"language": "en", "theme": "carbon", "layout": "full",
                       "update_check": False, "download_covers": False,
                       "background_hint_shown": True,
                       "last_version": "99.0.0"}.items():
        config.set_setting(key, value)
    for name, folder, changes in PRESETS:
        config.write_preset(config.preset_path(name), name,
                            dict(BASE, **changes), folder=folder)
    config.save_games([{"game": f"steam:{appid}", "name": name,
                        "preset": f"{preset}.json", "auto": True}
                       for appid, name, preset in GAMES])
    pedals = "FANATEC Pedals"
    config.save_axis_roles({f"{pedals}:1": "throttle", f"{pedals}:2": "brake",
                            f"{pedals}:3": "clutch"})

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Graphene", "1.0")
    from gi.repository import GLib, Graphene, Gtk
    from understeer import app, inputs

    def shoot(win, name):
        width, height = win.get_width(), win.get_height()
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(win).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        texture = win.get_renderer().render_texture(
            node, Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(str(DOCS / f"{name}.png"))
        print(f"docs/{name}.png: {width}×{height}")

    def axis(device_name, code, lo, hi, value):
        return inputs.Axis(device_name, code, lo, hi, value)

    def steps(win):
        wheel = axis("FANATEC Wheel", 0, 0, 65535, 32767)
        gas = axis(pedals, 1, 0, 65535, 0)
        brake = axis(pedals, 2, 0, 65535, 0)
        clutch = axis(pedals, 3, 0, 65535, 0)

        def live_inputs():
            win.set_default_size(*SIZE)
            monitor = win.inputs
            monitor.axes = {a.key: a for a in (wheel, gas, brake, clutch)}
            monitor.all_axes = dict(monitor.axes)
            monitor.devices = ["FANATEC Wheel", pedals]
            # +60° at 540°, throttle at 72 %
            wheel.update(round(32767 + 60 / 270 * 32767))
            gas.update(round(0.72 * 65535))
            win._rebuild_axes_list()
            win.axis_diagnostics.rebuild()
            win._update_inputs()
            win._select_page("tuning")
        yield live_inputs
        yield lambda: None          # BeamNG.drive found running, preset applied
        yield lambda: None

        def no_toast():
            if win._toast is not None:
                win._toast.dismiss()
        yield no_toast
        yield lambda: shoot(win, "screenshot")
        yield lambda: win._select_page("presets")
        yield lambda: shoot(win, "presets")

        def select():
            win.preset_selection.start()
            for name in ("GT3 Endurance", "Spa GT3"):
                win.preset_selection.toggle(str(config.preset_path(name)))
        yield select
        yield lambda: shoot(win, "select")
        yield lambda: (win.preset_selection.stop(), win._select_page("games"))
        yield lambda: None
        yield lambda: shoot(win, "games")

        def diagnostics():
            win._select_page("diagnostics")
            gas.value = brake.value = clutch.value = 0     # let go
            for a in (wheel, gas, brake, clutch):
                a.reset_observed()
            health = win.axis_diagnostics.health
            health.start_measuring()
            health.finish_measuring()
            for v in (0, 65535, 33000):
                wheel.update(v)
            for v in (65535, 0):
                gas.update(v)
            for v in (round(0.93 * 65535), 0):
                brake.update(v)
            win._update_inputs()
        yield diagnostics
        yield lambda: None
        yield lambda: shoot(win, "diagnostics")

    application = app.TunerApplication()

    def run_steps():
        win = application.get_active_window()
        it = steps(win)

        def next_step():
            try:
                next(it)()
            except StopIteration:
                application.quit()
                return False
            return True
        GLib.timeout_add(400, next_step)
        return False

    application.connect("activate",
                        lambda a: GLib.timeout_add(1500, run_steps))
    GLib.timeout_add_seconds(60, application.quit)
    application.run([])


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--inner":
        inner(pathlib.Path(sys.argv[2]))
    else:
        work = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else tempfile.mkdtemp(prefix="understeer-shots-"))
        sys.exit(outer(work))
