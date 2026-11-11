"""The tray icon, against a stand-in for the panel on a private D-Bus."""
import os
import shutil
import subprocess
import time

import pytest

from understeer import DATA_DIR

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from understeer.ui import tray  # noqa: E402

# The panel stand-in below registers its object like the tray does.
# PyGObject on Python 3.14 warns about asyncio internals when the test turns
# the main loop by hand; neither is Understeer's code.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Gio.DBusConnection.register_object is deprecated"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:gi.events"),
]

ICON = DATA_DIR / "io.github.JamDaBam.Understeer.png"
WATCHER_XML = """
<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem">
    <arg name="service" type="s" direction="in"/></method>
</interface></node>"""


def spin(condition, seconds=5):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.005)
    return condition()


def connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


@pytest.fixture
def bus():
    if not shutil.which("dbus-daemon"):
        if os.environ.get("CI"):
            pytest.fail("dbus-daemon is needed on CI")
        pytest.skip("dbus-daemon not installed")
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--nopidfile", "--print-address=1"],
        stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    yield address
    daemon.terminate()
    daemon.wait(timeout=10)


@pytest.fixture
def panel(bus):
    """A StatusNotifierWatcher that records the items registering."""
    conn = connect(bus)
    items = []

    def call(_c, sender, _p, _i, method, params, invocation):
        items.append(params.unpack()[0])
        invocation.return_value(None)
    info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
    conn.register_object("/StatusNotifierWatcher", info, call, None, None)
    owner = Gio.bus_own_name_on_connection(
        conn, tray.WATCHER, Gio.BusNameOwnerFlags.NONE, None, None)
    spin(lambda: False, 0.2)
    yield conn, items
    Gio.bus_unown_name(owner)


@pytest.fixture
def item(bus):
    calls = {"open": [], "quit": 0}

    def texts():
        return {"title": "Understeer", "tooltip": "Applies presets",
                "open": "Open Understeer", "quit": "Quit"}

    def quit_():
        calls["quit"] += 1
    t = tray.Tray(ICON, texts, calls["open"].append, quit_, connection=connect(bus))
    yield t, calls
    t.stop()


def call(conn, name, path, interface, method, params):
    """A D-Bus call that lets the main loop run meanwhile: the tray answers
    from the same thread."""
    result = []
    conn.call(name, path, interface, method, params, None,
              Gio.DBusCallFlags.NONE, 2000, None,
              lambda c, r: result.append(c.call_finish(r)))
    assert spin(lambda: result), f"no answer to {method}"
    return result[0].unpack()


def get(conn, name, path, interface, prop):
    return call(conn, name, path, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (interface, prop)))[0]


def test_pixmaps_are_argb():
    pixmaps = tray.argb_pixmaps(ICON, sizes=(16, 32))
    assert [(w, h, len(data)) for w, h, data in pixmaps] == \
        [(16, 16, 16 * 16 * 4), (32, 32, 32 * 32 * 4)]
    assert tray.argb_pixmaps(DATA_DIR / "missing.png") == []


def test_registers_with_the_panel(panel, item):
    conn, items = panel
    t, _calls = item
    assert t.start()
    assert spin(lambda: t.registered)
    assert items == [t.name]
    sni = "org.kde.StatusNotifierItem"
    assert get(conn, t.name, tray.ITEM_PATH, sni, "Title") == "Understeer"
    assert get(conn, t.name, tray.ITEM_PATH, sni, "Status") == "Active"
    assert get(conn, t.name, tray.ITEM_PATH, sni, "Menu") == tray.MENU_PATH
    sizes = [p[0] for p in get(conn, t.name, tray.ITEM_PATH, sni, "IconPixmap")]
    assert sizes == list(tray.ICON_SIZES)
    assert get(conn, t.name, tray.ITEM_PATH, sni, "ToolTip")[2:] == \
        ("Understeer", "Applies presets")


def test_menu_opens_and_quits(panel, item):
    conn, _items = panel
    t, calls = item
    t.start()
    spin(lambda: t.registered)
    menu = "com.canonical.dbusmenu"
    _revision, (root, _props, children) = call(
        conn, t.name, tray.MENU_PATH, menu, "GetLayout",
        GLib.Variant("(iias)", (0, -1, [])))
    labels = [c[1].get("label") for c in children]
    assert root == 0 and labels == ["Open Understeer", None, "Quit"]
    call(conn, t.name, tray.MENU_PATH, menu, "Event",
         GLib.Variant("(isvu)", (tray.Tray.QUIT, "clicked", GLib.Variant("i", 0), 0)))
    assert spin(lambda: calls["quit"] == 1)
    call(conn, t.name, tray.MENU_PATH, menu, "Event",
         GLib.Variant("(isvu)", (tray.Tray.OPEN, "clicked", GLib.Variant("i", 0), 0)))
    assert spin(lambda: calls["open"] == [None])


def test_click_shows_the_window_with_the_token(panel, item):
    conn, _items = panel
    t, calls = item
    t.start()
    spin(lambda: t.registered)
    sni = "org.kde.StatusNotifierItem"
    call(conn, t.name, tray.ITEM_PATH, sni, "ProvideXdgActivationToken",
         GLib.Variant("(s)", ("token-1",)))
    call(conn, t.name, tray.ITEM_PATH, sni, "Activate", GLib.Variant("(ii)", (0, 0)))
    assert spin(lambda: calls["open"] == ["token-1"])


def test_registers_when_the_panel_comes_later(bus, item):
    """E.g. Understeer started at login before the panel, or the panel
    restarted."""
    t, _calls = item
    t.start()                       # no panel yet: waits for it
    spin(lambda: False, 0.3)
    assert not t.registered
    conn = connect(bus)
    items = []

    def register(_c, _s, _p, _i, _m, params, invocation):
        items.append(params.unpack()[0])
        invocation.return_value(None)
    info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
    conn.register_object("/StatusNotifierWatcher", info, register, None, None)
    Gio.bus_own_name_on_connection(conn, tray.WATCHER, Gio.BusNameOwnerFlags.NONE,
                                   None, None)
    assert spin(lambda: t.registered) and items == [t.name]


def test_stop_releases_the_name(panel, item):
    conn, _items = panel
    t, _calls = item
    t.start()
    spin(lambda: t.registered)
    t.stop()
    assert not t.running

    def gone():
        return not call(conn, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "NameHasOwner",
                        GLib.Variant("(s)", (t.name,)))[0]
    assert spin(gone)


@pytest.fixture
def with_presets(bus):
    state = {"presets": [("Daily", "a", False), ("GT3_Race", "b", True)],
             "applied": []}

    def texts():
        return {"title": "Understeer", "tooltip": "", "open": "Open", "quit": "Quit"}
    t = tray.Tray(ICON, texts, lambda token: None, lambda: None,
                  presets=lambda: state["presets"],
                  apply_preset=state["applied"].append, connection=connect(bus))
    yield t, state
    t.stop()


def layout(conn, t):
    return call(conn, t.name, tray.MENU_PATH, "com.canonical.dbusmenu",
                "GetLayout", GLib.Variant("(iias)", (0, -1, [])))


def click(conn, t, item_id):
    call(conn, t.name, tray.MENU_PATH, "com.canonical.dbusmenu", "Event",
         GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("i", 0), 0)))


def test_menu_lists_the_presets(panel, with_presets):
    conn, _items = panel
    t, state = with_presets
    t.start()
    spin(lambda: t.registered)
    _revision, (_root, _props, children) = layout(conn, t)
    labels = [c[1].get("label") for c in children]
    assert labels == ["Open", None, "Daily", "GT3__Race", None, "Quit"]
    presets = [c for c in children if c[1].get("toggle-type") == "radio"]
    assert [c[1]["toggle-state"] for c in presets] == [0, 1]
    click(conn, t, presets[0][0])
    assert spin(lambda: state["applied"] == ["a"])


def test_menu_changes_with_the_presets(panel, with_presets):
    conn, _items = panel
    t, state = with_presets
    t.start()
    spin(lambda: t.registered)
    revision, (_root, _props, children) = layout(conn, t)
    first = [c[0] for c in children if c[1].get("toggle-type") == "radio"][0]
    updates = []
    conn.signal_subscribe(None, "com.canonical.dbusmenu", "LayoutUpdated",
                          tray.MENU_PATH, None, Gio.DBusSignalFlags.NONE,
                          lambda *args: updates.append(args[5].unpack()))
    # The list changes while the menu is open: a click still means the
    # preset that was shown
    state["presets"] = [("New", "c", False)] + state["presets"]
    t.update_menu()
    assert spin(lambda: updates) and updates[0][0] > revision
    click(conn, t, first)
    assert spin(lambda: state["applied"] == ["a"])
    _revision, (_root, _props, children) = layout(conn, t)
    assert [c[1].get("label") for c in children][2:5] == ["New", "Daily", "GT3__Race"]


def test_menu_without_presets(panel, item):
    conn, _items = panel
    t, _calls = item
    t.start()
    spin(lambda: t.registered)
    _revision, (_root, _props, children) = layout(conn, t)
    assert [c[1].get("label") for c in children] == ["Open Understeer", None, "Quit"]


def test_folders_are_submenus(panel, with_presets):
    conn, _items = panel
    t, state = with_presets
    state["presets"] = [("Daily", "a", False),
                        ("BeamNG", [("Drift", "b", True), ("Climb", "c", False)]),
                        ("Rally", [("Stage", "d", False)])]
    t.start()
    spin(lambda: t.registered)
    _revision, (_root, _props, children) = layout(conn, t)
    # A folder has no check mark: KDE would show a choice, no submenu
    assert all(c[1].get("toggle-type", "") == "" for c in children
               if c[1].get("children-display") == "submenu")
    top = [(c[1].get("label"), c[1].get("children-display"), c[1].get("toggle-state"))
           for c in children]
    assert top == [("Open", None, None), (None, None, None), ("Daily", "", 0),
                   ("BeamNG", "submenu", None), ("Rally", "submenu", None),
                   (None, None, None), ("Quit", None, None)]
    beamng = children[3]
    assert [(c[1]["label"], c[1]["toggle-state"]) for c in beamng[2]] == \
        [("Drift", 1), ("Climb", 0)]
    # The panel may ask for one submenu only
    _revision, (folder_id, props, kids) = call(
        conn, t.name, tray.MENU_PATH, "com.canonical.dbusmenu", "GetLayout",
        GLib.Variant("(iias)", (beamng[0], -1, [])))
    assert folder_id == beamng[0] and props["label"] == "BeamNG"
    assert [k[1]["label"] for k in kids] == ["Drift", "Climb"]
    # Only one level asked for: the children without theirs
    _revision, (_root, _props, shallow) = call(
        conn, t.name, tray.MENU_PATH, "com.canonical.dbusmenu", "GetLayout",
        GLib.Variant("(iias)", (0, 1, [])))
    assert all(c[2] == [] for c in shallow)
    click(conn, t, beamng[2][1][0])
    assert spin(lambda: state["applied"] == ["c"])
    click(conn, t, beamng[0])               # the folder itself: nothing
    spin(lambda: False, 0.2)
    assert state["applied"] == ["c"]


def test_submenus_are_read_again(panel, with_presets):
    """KDE reads only the level named in LayoutUpdated and asks AboutToShow
    before a submenu opens; both must lead to fresh check marks."""
    conn, _items = panel
    t, state = with_presets
    state["presets"] = [("BeamNG", [("Drift", "b", True), ("Climb", "c", False)])]
    t.start()
    spin(lambda: t.registered)
    _revision, (_root, _props, children) = layout(conn, t)
    folder = children[2][0]
    menu = "com.canonical.dbusmenu"
    assert call(conn, t.name, tray.MENU_PATH, menu, "AboutToShow",
                GLib.Variant("(i)", (folder,))) == (True,)
    assert call(conn, t.name, tray.MENU_PATH, menu, "AboutToShowGroup",
                GLib.Variant("(ai)", ([0, folder],))) == ([0, folder], [])
    updates = []
    conn.signal_subscribe(None, menu, "LayoutUpdated", tray.MENU_PATH, None,
                          Gio.DBusSignalFlags.NONE,
                          lambda *args: updates.append(args[5].unpack()[1]))
    state["presets"] = [("BeamNG", [("Drift", "b", False), ("Climb", "c", True)])]
    t.update_menu()
    assert spin(lambda: sorted(updates) == [0, folder])
    _revision, (_id, _props, kids) = call(
        conn, t.name, tray.MENU_PATH, menu, "GetLayout",
        GLib.Variant("(iias)", (folder, 1, [])))
    assert [(k[1]["label"], k[1]["toggle-state"]) for k in kids] == \
        [("Drift", 0), ("Climb", 1)]


def test_ids_stay_with_their_item(panel, with_presets):
    """A folder that goes away and comes back keeps its id, and no id is
    ever a preset once and a folder later: KDE keeps items by id."""
    conn, _items = panel
    t, state = with_presets
    t.start()
    spin(lambda: t.registered)

    def kinds():
        _revision, (_root, _props, children) = layout(conn, t)
        found = {}
        for c in children:
            if c[1].get("children-display") == "submenu":
                found[c[0]] = ("folder", c[1]["label"])
                for k in c[2]:
                    found[k[0]] = ("preset", k[1]["label"])
            elif c[1].get("toggle-type") == "radio":
                found[c[0]] = ("preset", c[1]["label"])
        return found

    seen = {}
    for presets in (
            [("A", [("One", "1", False)]), ("B", [("Two", "2", True)])],
            [("B", [("One", "1", False), ("Two", "2", True)])],        # A emptied
            [("A", [("Two", "2", True)]), ("B", [("One", "1", False)])],  # back
            [("Loose", "3", False), ("A", [("Two", "2", True)])]):
        state["presets"] = presets
        t.update_menu()
        for item_id, what in kinds().items():
            assert seen.setdefault(item_id, what) == what, (item_id, what)
    _revision, (_root, _props, children) = layout(conn, t)
    folder = [c for c in children if c[1].get("children-display") == "submenu"][0]
    assert folder[1]["toggle-type"] == ""               # never a choice
    preset = [c for c in children if c[1].get("toggle-type") == "radio"][0]
    assert preset[1]["children-display"] == ""          # never a submenu
