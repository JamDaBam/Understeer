"""The tray icon while Understeer runs in the background.

GTK 4 has no tray icon, so this speaks the StatusNotifierItem protocol on
the session D-Bus directly, with a com.canonical.dbusmenu menu for the
right click. KDE Plasma, Cinnamon, XFCE, Budgie and MATE show it, GNOME
with the AppIndicator extension (Ubuntu has it). Without a tray the icon
is just not shown; starting Understeer again brings the window back.
"""
import logging
import os
import warnings

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from ..names import menu_label  # noqa: E402

log = logging.getLogger("understeer.tray")

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ICON_SIZES = (16, 22, 24, 32, 48, 64)

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/></method>
    <method name="ProvideXdgActivationToken">
      <arg name="token" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>"""


def argb_pixmaps(path, sizes=ICON_SIZES):
    """The picture at path as SNI pixmaps: [(w, h, ARGB32 big-endian)]."""
    try:
        source = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except GLib.Error as e:
        log.warning("Tray icon %s not readable: %s", path, e)
        return []
    pixmaps = []
    for size in sizes:
        pb = source.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        data, stride = pb.get_pixels(), pb.get_rowstride()
        rgba = b"".join(data[y * stride:y * stride + size * 4] for y in range(size))
        argb = bytearray(len(rgba))
        argb[0::4], argb[1::4] = rgba[3::4], rgba[0::4]
        argb[2::4], argb[3::4] = rgba[1::4], rgba[2::4]
        pixmaps.append((size, size, bytes(argb)))
    return pixmaps


class Tray:
    """A StatusNotifierItem with a menu: open the window, apply a preset,
    or quit.

    texts() returns {"title", "tooltip", "open", "quit"} in the current
    language; call relocalize() after it changed. activate(token) shows the
    window (token: the Wayland activation token from the panel, or None),
    quit() ends Understeer. presets() returns the menu entries: (name, key,
    active) for a preset, (folder, [presets]) for a folder, shown as a
    submenu; apply_preset(key) applies one. Call update_menu() when they
    changed.
    """
    OPEN, QUIT = 1, 3
    FIRST_PRESET = 100

    def __init__(self, icon_path, texts, activate, quit, presets=None,
                 apply_preset=None, connection=None):
        self.icon_path = icon_path
        self.texts = texts
        self.on_activate = activate
        self.on_quit = quit
        self.presets = presets or (lambda: [])
        self.on_apply_preset = apply_preset
        self.connection = connection
        self._pixmaps = None
        self._token = None
        self._revision = 1
        self._preset_keys = {}  # menu id -> key, of the menu last given out
        self._folder_ids = []   # submenus in it
        self._id_of = {}        # ("preset", key) or ("folder", name): id
        self._ids = []          # registration ids to undo in stop()
        self._owner = self._watch = 0
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.registered = False     # the panel took the item

    # -- start / stop --------------------------------------------------------
    def start(self):
        """Put the icon up. Returns False without a session bus."""
        if self._owner:
            return True
        try:
            if self.connection is None:
                self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            for path, xml, call, prop in (
                    (ITEM_PATH, ITEM_XML, self._item_call, self._item_property),
                    (MENU_PATH, MENU_XML, self._menu_call, self._menu_property)):
                info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                with warnings.catch_warnings():
                    # Its successor needs GLib 2.84; Ubuntu 24.04 has 2.80.
                    warnings.simplefilter("ignore", DeprecationWarning)
                    self._ids.append(self.connection.register_object(
                        path, info, call, prop, None))
        except GLib.Error as e:
            log.warning("No tray icon, the session bus is not available: %s", e)
            self.stop()
            return False
        self._owner = Gio.bus_own_name_on_connection(
            self.connection, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self._watch = Gio.bus_watch_name_on_connection(
            self.connection, WATCHER, Gio.BusNameWatcherFlags.NONE,
            self._watcher_appeared, self._watcher_vanished)
        return True

    def stop(self):
        for reg_id in self._ids:
            self.connection.unregister_object(reg_id)
        self._ids = []
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
        if self._owner:
            Gio.bus_unown_name(self._owner)
        self._owner = self._watch = 0
        self.registered = False

    @property
    def running(self):
        return bool(self._owner)

    def _watcher_appeared(self, connection, _name, _owner):
        """The panel is there (again, e.g. after it restarted): register."""
        def done(conn, result):
            try:
                conn.call_finish(result)
                self.registered = True
                log.info("Tray icon shown")
            except GLib.Error as e:
                log.warning("The panel did not take the tray icon: %s", e)
        connection.call(WATCHER, "/StatusNotifierWatcher", WATCHER,
                        "RegisterStatusNotifierItem",
                        GLib.Variant("(s)", (self.name,)), None,
                        Gio.DBusCallFlags.NONE, -1, None, done)

    def _watcher_vanished(self, _connection, _name):
        if self.registered:
            log.info("The panel is gone, tray icon hidden")
        self.registered = False

    def relocalize(self):
        """Texts changed (language): tell the panel to read them again."""
        if not self._owner:
            return
        for signal in ("NewTitle", "NewToolTip"):
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", signal, None)
        self.update_menu()

    def update_menu(self):
        """The presets or the active one changed: the panel reads the menu
        again. KDE reads only the level a signal names, so each submenu
        gets one too, or its check marks stay as they were."""
        if not self._owner:
            return
        self._revision += 1
        for parent in [0, *self._folder_ids]:
            self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                       GLib.Variant("(ui)", (self._revision, parent)))

    def _emit(self, path, interface, signal, params):
        try:
            self.connection.emit_signal(None, path, interface, signal, params)
        except GLib.Error as e:
            log.debug("Signal %s not sent: %s", signal, e)

    # -- the item ------------------------------------------------------------
    def _item_property(self, _conn, _sender, _path, _iface, name):
        texts = self.texts()
        if name in ("IconPixmap",):
            if self._pixmaps is None:
                self._pixmaps = argb_pixmaps(self.icon_path)
            return GLib.Variant("a(iiay)", self._pixmaps)
        if name in ("OverlayIconPixmap", "AttentionIconPixmap"):
            return GLib.Variant("a(iiay)", [])
        if name == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)",
                                ("", [], texts["title"], texts["tooltip"]))
        values = {"Category": ("s", "ApplicationStatus"),
                  "Id": ("s", "io.github.JamDaBam.Understeer"),
                  "Title": ("s", texts["title"]),
                  "Status": ("s", "Active"),
                  "WindowId": ("i", 0),
                  "IconName": ("s", ""),        # the pixmap, as the AppImage's
                  "OverlayIconName": ("s", ""),  # icon isn't in the icon theme
                  "AttentionIconName": ("s", ""),
                  "AttentionMovieName": ("s", ""),
                  "ItemIsMenu": ("b", False),
                  "Menu": ("o", MENU_PATH)}
        if name in values:
            return GLib.Variant(*values[name])
        return None

    def _item_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "ProvideXdgActivationToken":
            self._token = params.unpack()[0]
        elif method in ("Activate", "SecondaryActivate"):
            self._open()
        invocation.return_value(None)

    def _open(self):
        token, self._token = self._token, None
        GLib.idle_add(lambda: self.on_activate(token) and False)

    # -- the menu ------------------------------------------------------------
    def _stable_id(self, what):
        """The menu id of a preset or folder: the same while Understeer
        runs and never given to anything else. KDE keeps an item by its id
        and changes only the properties sent, so an id that was a preset
        and then a folder stayed a choice without a submenu."""
        if what not in self._id_of:
            self._id_of[what] = self.FIRST_PRESET + len(self._id_of)
        return self._id_of[what]

    @staticmethod
    def _label(text):
        # Short, so a long name can't make the menu very wide; dbusmenu
        # reads "_" as a mnemonic too
        return GLib.Variant("s", menu_label(text))

    @staticmethod
    def _toggle(on):
        return {"toggle-type": GLib.Variant("s", "radio"),
                "toggle-state": GLib.Variant("i", 1 if on else 0)}

    def _items(self):
        """The menu as ({id: properties}, {id: [child ids]} with 0 for the
        top, {id: preset key}): open, the presets and folders, quit."""
        texts = self.texts()
        items = {0: {"children-display": GLib.Variant("s", "submenu")},
                 self.OPEN: {"label": GLib.Variant("s", texts["open"])},
                 2: {"type": GLib.Variant("s", "separator")}}
        children = {0: [self.OPEN, 2]}
        keys = {}
        try:
            entries = list(self.presets())
        except Exception:                   # never break the whole menu
            log.exception("Presets for the tray menu not readable")
            entries = []

        def add_preset(parent, name, key, active):
            item_id = self._stable_id(("preset", key))
            keys[item_id] = key
            items[item_id] = {"label": self._label(name),
                              "children-display": GLib.Variant("s", ""),
                              **self._toggle(active)}
            children[parent].append(item_id)

        for entry in entries:
            if len(entry) == 3:
                add_preset(0, *entry)
                continue
            folder, presets = entry             # a folder: a submenu
            folder_id = self._stable_id(("folder", folder.casefold()))
            children[0].append(folder_id)
            children[folder_id] = []
            for preset in presets:
                add_preset(folder_id, *preset)
            # No check mark on a folder: KDE then shows a choice instead
            # of a submenu
            items[folder_id] = {
                "label": self._label(folder),
                "children-display": GLib.Variant("s", "submenu"),
                "toggle-type": GLib.Variant("s", "")}
        if entries:
            items[4] = {"type": GLib.Variant("s", "separator")}
            children[0].append(4)
        items[self.QUIT] = {"label": GLib.Variant("s", texts["quit"])}
        children[0].append(self.QUIT)
        return items, children, keys

    def _layout(self, parent=0, depth=-1):
        items, children, self._preset_keys = self._items()  # what a click means
        self._folder_ids = [i for i in children if i]
        if parent not in items:
            parent = 0

        def node(item_id, depth):
            kids = children.get(item_id, [])
            props = dict(items[item_id])
            if item_id and not kids and "toggle-type" not in props:
                props.pop("children-display", None)     # e.g. Open, Quit
            below = [] if depth == 0 else [
                GLib.Variant("(ia{sv}av)", node(k, depth - 1)) for k in kids]
            return (item_id, props, below)
        return node(parent, depth)

    def _menu_property(self, _conn, _sender, _path, _iface, name):
        values = {"Version": ("u", 3), "TextDirection": ("s", "ltr"),
                  "Status": ("s", "normal"), "IconThemePath": ("as", [])}
        return GLib.Variant(*values[name]) if name in values else None

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method in ("GetGroupProperties", "GetProperty"):
            items = self._items()[0]
        if method == "GetLayout":
            invocation.return_value(GLib.Variant(
                "(u(ia{sv}av))", (self._revision, self._layout(args[0], args[1]))))
        elif method == "GetGroupProperties":
            ids = args[0] or [i for i in items if i]
            invocation.return_value(GLib.Variant(
                "(a(ia{sv}))", ([(i, items[i]) for i in ids if i in items],)))
        elif method == "GetProperty":
            value = items.get(args[0], {}).get(args[1])
            if value is None:
                invocation.return_dbus_error(
                    "com.canonical.dbusmenu.Error", "no such property")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            self._event(args[0], args[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _data, _time in args[0]:
                self._event(item_id, event_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        # Always read a (sub)menu again before it opens: the active preset
        # may have changed, also from the window
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (True,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", (list(args[0]), [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod",
                                         method)

    def _event(self, item_id, event_id):
        if event_id != "clicked":
            return
        if item_id == self.OPEN:
            self._open()
        elif item_id == self.QUIT:
            GLib.idle_add(lambda: self.on_quit() and False)
        elif item_id in self._preset_keys and self.on_apply_preset:
            key = self._preset_keys[item_id]
            GLib.idle_add(lambda: self.on_apply_preset(key) and False)
