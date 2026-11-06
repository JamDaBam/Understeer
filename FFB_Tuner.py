#!/usr/bin/env python3
"""
SimPower — a GNOME-aligned GUI for adjusting Fanatec force-feedback
settings exposed by the hid-fanatecff kernel driver via sysfs.

Changes are written to the wheel base live while you move a control.
All user-visible text goes through _() and is translated from
locale/<lang>.po (see README for adding a language).
"""

import glob
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
LOCALE_DIRS = [
    _SCRIPT_DIR / "locale",                            # source checkout
    _SCRIPT_DIR.parent / "share" / "simpower" / "locale",  # AppImage layout
]


def _unquote_po(s):
    s = s.strip()
    if not (s.startswith('"') and s.endswith('"')):
        return ""
    return (s[1:-1].replace('\\n', '\n').replace('\\t', '\t')
            .replace('\\"', '"').replace('\\\\', '\\'))


def parse_po(text):
    """Minimal .po reader (msgid/msgstr with continuation lines)."""
    catalog, msgid, msgstr, field = {}, None, None, None

    def flush():
        if msgid and msgstr:
            catalog[msgid] = msgstr

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("msgid "):
            flush()
            msgid, msgstr, field = _unquote_po(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, field = _unquote_po(line[7:]), "str"
        elif line.startswith('"'):
            if field == "id":
                msgid += _unquote_po(line)
            elif field == "str":
                msgstr += _unquote_po(line)
        elif not line or line.startswith("#"):
            field = None if not line else field
    flush()
    return catalog


def _wanted_languages():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            langs = []
            for entry in value.split(":"):
                entry = entry.split(".")[0].split("@")[0]
                if entry and entry not in ("C", "POSIX"):
                    langs += [entry, entry.split("_")[0]]
            return langs
    return []


def load_catalog():
    for lang in _wanted_languages():
        for d in LOCALE_DIRS:
            po = d / f"{lang}.po"
            if po.is_file():
                try:
                    return parse_po(po.read_text(encoding="utf-8"))
                except OSError:
                    pass
    return {}


_CATALOG = load_catalog()


def _(text):
    return _CATALOG.get(text, text)


def N_(text):
    """Mark a string for translation; it is translated where it is shown."""
    return text


import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio  # noqa: E402

# ---------------------------------------------------------------------------
# sysfs paths and storage
# ---------------------------------------------------------------------------
SYSFS_GLOB  = "/sys/class/ftec_tuning/*"
RUMBLE_GLOB = "/sys/bus/hid/devices/*/rumble"
CONFIG_DIR  = pathlib.Path.home() / ".config" / "SimPower"

UDEV_RULE = """ACTION!="add|change", GOTO="fanatec_tuner_end"

SUBSYSTEM=="ftec_tuning", RUN+="/bin/sh -c 'find /sys%p -type f -exec chmod 666 {} +'"
SUBSYSTEM=="hid", DRIVER=="fanatec", RUN+="/bin/sh -c 'chmod 666 /sys%p/rumble 2>/dev/null'"

LABEL="fanatec_tuner_end"
"""
UDEV_RULE_PATH = "/etc/udev/rules.d/99-fanatec-tuner.rules"

# ---------------------------------------------------------------------------
# Settings
#
# Each entry describes one sysfs attribute. "kind" is "slider" or "choice".
# Sliders may have a "unit" and a "fmt" function for special values;
# choices list (value, label) pairs.
# ---------------------------------------------------------------------------
PAGES = [
    ("steering", N_("Steering"),       "input-gaming-symbolic"),
    ("feel",     N_("Wheel Feel"),     "emblem-system-symbolic"),
    ("pedals",   N_("Pedals"),         "media-playback-start-symbolic"),
    ("paddles",  N_("Paddles"),        "view-dual-symbolic"),
    ("presets",  N_("Presets"),        "document-save-symbolic"),
    ("tools",    N_("Tools"),          "applications-utilities-symbolic"),
]


def _fmt_drift(v):
    if v == 0:
        return _("Off")
    if v < 0:
        return _("Counter-steer {n}").format(n=-v)
    return _("Centering +{n}").format(n=v)


def _fmt_brake_led(v):
    if v == 0:
        return _("Off")
    if v >= 101:
        return _("Always on")
    return f"{v} %"


SETTINGS = [
    # --- Steering --------------------------------------------------------
    dict(id="SEN", page="steering", group=N_("Rotation"),
         label=N_("Steering Angle"), lo=90, hi=1080, unit="°",
         desc=N_("Total wheel rotation in degrees. Auto uses the maximum "
                 "range of the wheel base."),
         quick=[540, 720, 900, 1080]),
    dict(id="FF", page="steering", group=N_("Force Feedback"),
         label=N_("Force Feedback Strength"), lo=0, hi=100, unit="%",
         desc=N_("Master force feedback output of the wheel base.")),
    dict(id="FOR", page="steering", group=N_("Force Feedback"),
         label=N_("Force Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of constant-force effects, the core of the game's "
                 "force feedback.")),
    dict(id="SPR", page="steering", group=N_("Force Feedback"),
         label=N_("Spring Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of centering spring effects sent by the game.")),
    dict(id="DPR", page="steering", group=N_("Force Feedback"),
         label=N_("Damper Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of damper effects sent by the game.")),
    dict(id="FFS", page="steering", group=N_("Signal"),
         label=N_("Force Feedback Scaling"), kind="choice",
         choices=[(0, N_("Linear")), (1, N_("Peak"))],
         desc=N_("Linear keeps a constant taper. Peak scales to the strongest "
                 "force (default).")),
    dict(id="FEI", page="steering", group=N_("Signal"),
         label=N_("Force Effect Intensity"), lo=0, hi=100, unit="%",
         desc=N_("Filters the force feedback signal. Higher values give "
                 "sharper details.")),
    dict(id="INT", page="steering", group=N_("Signal"),
         label=N_("Interpolation Filter"), lo=0, hi=20,
         desc=N_("Smooths between force feedback updates. 0 turns it off.")),
    # --- Wheel feel ------------------------------------------------------
    dict(id="NDP", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Damper"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical damping added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NFR", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Friction"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical friction added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NIN", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Inertia"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical inertia added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="SHO", page="feel", group=N_("Effects"),
         label=N_("Wheel Vibration"), lo=0, hi=100, unit="%",
         desc=N_("Strength of the rumble on curbs and impacts.")),
    dict(id="DRI", page="feel", group=N_("Effects"),
         label=N_("Drift Mode"), lo=-5, hi=3, fmt=_fmt_drift,
         desc=N_("Negative values help with counter-steering, positive values "
                 "add centering force. 0 turns it off.")),
    # --- Pedals ----------------------------------------------------------
    dict(id="brF", page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Force"), lo=0, hi=100, unit="%",
         desc=N_("Pedal stiffness. Higher values need more force for full "
                 "braking. Requires a load cell brake.")),
    dict(id="BLI", page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Level Indicator"), lo=0, hi=101, fmt=_fmt_brake_led,
         desc=N_("Brake force at which the LED lights up.")),
    dict(id="FUL", page="pedals", group=N_("Vibration"),
         label=N_("FullForce"), lo=0, hi=100, unit="%",
         desc=N_("Pedal vibration routed through the wheel motor. Requires a "
                 "game with FullForce support.")),
    # --- Paddles ---------------------------------------------------------
    dict(id="ACP", page="paddles", group=N_("Analogue Paddles"),
         label=N_("Paddle Mode"), kind="choice",
         choices=[(1, N_("Independent (CbP)")),
                  (2, N_("Clutch / Handbrake (CH)")),
                  (3, N_("Brake / Throttle (Bt)")),
                  (4, N_("Analogue Axes (AnA)"))],
         desc=N_("How the analogue clutch paddles behave. Read-only on wheels "
                 "with a physical mode switch.")),
]
SETTINGS_BY_ID = {s["id"]: s for s in SETTINGS}

SIM_DEFAULTS = {"SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0,
                "SHO": 80, "FFS": 1, "FEI": 30, "INT": 0}

SLOTS = 5
LIVE_DELAY_MS = 120


# ---------------------------------------------------------------------------
# Device access
# ---------------------------------------------------------------------------
def find_device_path():
    candidates = [p for p in glob.glob(SYSFS_GLOB) if os.path.isdir(p)]
    valid = [p for p in candidates if os.path.exists(os.path.join(p, "FF"))]
    return valid[0] if valid else (candidates[0] if candidates else None)


def find_rumble_path():
    matches = glob.glob(RUMBLE_GLOB)
    return matches[0] if matches else None


def can_write(device_path):
    """sysfs dirs are rarely writable even when their files are, so test FF."""
    attr = os.path.join(device_path, "FF")
    return os.access(attr if os.path.exists(attr) else device_path, os.W_OK)


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_values(writes):
    """Write {path: int}. Paths without permission go through one pkexec call.

    Returns the list of paths that could not be written.
    """
    denied, failed = {}, []
    for path, value in writes.items():
        try:
            with open(path, "w") as f:
                f.write(str(int(value)))
        except PermissionError:
            denied[path] = value
        except OSError:
            failed.append(path)
    if denied:
        script = "; ".join(f"echo {int(v)} > {shlex.quote(p)}"
                           for p, v in denied.items())
        if not run_privileged(script):
            failed += list(denied)
    return failed


def run_privileged(script):
    tool = "pkexec" if shutil.which("pkexec") else "sudo"
    try:
        subprocess.run([tool, "/bin/sh", "-c", script], check=True,
                       capture_output=True, text=True)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


# ---------------------------------------------------------------------------
# One row per setting
# ---------------------------------------------------------------------------
class SettingRow:
    """Wraps the widgets of one setting and exposes get/set of its value."""

    def __init__(self, setting, on_change):
        self.setting = setting
        self.on_change = on_change
        self.baseline = None
        self._silent = False
        kind = setting.get("kind", "slider")

        if kind == "choice":
            self.values = [v for v, _l in setting["choices"]]
            row = Adw.ComboRow()
            row.set_model(Gtk.StringList.new(
                [_(label) for _v, label in setting["choices"]]))
            row.connect("notify::selected", lambda *a: self._changed())
            self.combo = row
        else:
            row = Adw.ActionRow()
            lo, hi = setting["lo"], setting["hi"]
            self.adj = Gtk.Adjustment(value=max(lo, 0), lower=lo, upper=hi,
                                      step_increment=1, page_increment=10)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=self.adj)
            scale.set_size_request(240, -1)
            scale.set_valign(Gtk.Align.CENTER)
            scale.set_draw_value(False)
            if lo < 0 < hi:
                # No fill from the left: 0 ("off") sits in the middle.
                scale.set_has_origin(False)
                scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            self.value_label = Gtk.Label(xalign=1)
            self.value_label.set_width_chars(9)
            self.value_label.add_css_class("numeric")
            row.add_suffix(scale)
            row.add_suffix(self.value_label)
            self.adj.connect("value-changed", lambda *a: self._changed())

        row.set_title(_(setting["label"]))
        row.set_subtitle(setting["id"])
        row.set_tooltip_text(_(setting["desc"]))

        self.revert_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.revert_btn.set_tooltip_text(_("Undo changes to this setting"))
        self.revert_btn.set_valign(Gtk.Align.CENTER)
        self.revert_btn.add_css_class("flat")
        self.revert_btn.connect("clicked", lambda b: self.revert())
        row.add_suffix(self.revert_btn)

        info = Gtk.MenuButton(icon_name="help-about-symbolic")
        info.set_valign(Gtk.Align.CENTER)
        info.add_css_class("flat")
        info.set_tooltip_text(_("About this setting"))
        pop_label = Gtk.Label(label=_(setting["desc"]), wrap=True,
                              max_width_chars=40)
        for side in ("top", "bottom", "start", "end"):
            getattr(pop_label, f"set_margin_{side}")(8)
        popover = Gtk.Popover()
        popover.set_child(pop_label)
        info.set_popover(popover)
        row.add_suffix(info)

        self.row = row
        self._update_label()
        self.revert_btn.set_opacity(0)
        self.revert_btn.set_sensitive(False)

    # -- value access ------------------------------------------------------
    def get(self):
        if hasattr(self, "combo"):
            return self.values[self.combo.get_selected()]
        return int(round(self.adj.get_value()))

    def set(self, value, silent=True):
        self._silent = silent
        try:
            if hasattr(self, "combo"):
                if value in self.values:
                    self.combo.set_selected(self.values.index(value))
            else:
                self.adj.set_value(value)
        finally:
            self._silent = False
        self._update_label()

    def set_baseline(self, value):
        self.baseline = value
        self._update_label()

    def revert(self):
        if self.baseline is not None and self.baseline != self.get():
            self.set(self.baseline, silent=False)
            self.on_change(self)

    def set_supported(self, supported):
        self.row.set_sensitive(supported)
        self.row.set_subtitle(
            self.setting["id"] if supported else
            _("Not supported by your hardware") + f"  ·  {self.setting['id']}")

    # -- internals ---------------------------------------------------------
    def _changed(self):
        self._update_label()
        if not self._silent:
            self.on_change(self)

    def _update_label(self):
        if hasattr(self, "value_label"):
            v = self.get()
            fmt = self.setting.get("fmt")
            if fmt:
                text = fmt(v)
            elif self.setting["id"] == "SEN" and v == 0:
                text = _("Auto")
            else:
                unit = self.setting.get("unit", "")
                text = f"{v}{unit}" if unit == "°" else f"{v} {unit}".strip()
            self.value_label.set_label(text)
        modified = self.baseline is not None and self.get() != self.baseline
        self.revert_btn.set_opacity(1 if modified else 0)
        self.revert_btn.set_sensitive(modified)


# ===========================================================================
class TunerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("SimPower")
        self.set_default_size(860, 720)

        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)

        self.rows = {}           # attr id -> SettingRow
        self.pending = {}        # attr id -> value waiting to be written
        self._live_timer = 0
        self._status = ""

        self._build_actions()
        self._build_ui()
        self._refresh_device_state()

    # -----------------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------------
    def _build_ui(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.split = Adw.NavigationSplitView()
        self.toasts.set_child(self.split)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        # Sidebar
        side_view = Adw.ToolbarView()
        side_view.add_top_bar(Adw.HeaderBar())
        self.sidebar = Gtk.ListBox()
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-selected", self._on_page_selected)
        side_scroll = Gtk.ScrolledWindow(vexpand=True)
        side_scroll.set_child(self.sidebar)
        side_view.set_content(side_scroll)
        self.split.set_sidebar(Adw.NavigationPage.new(side_view, "SimPower"))

        # Content
        content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="SimPower")
        header.set_title_widget(self.window_title)
        content_view.add_top_bar(header)

        self.slot_dropdown = Gtk.DropDown.new_from_strings(
            [_("Slot {n}").format(n=i) for i in range(1, SLOTS + 1)])
        self.slot_dropdown.set_tooltip_text(
            _("Tuning slot on the wheel base that is being edited"))
        self._slot_handler = self.slot_dropdown.connect(
            "notify::selected", lambda *a: self._on_slot_selected())
        header.pack_start(self.slot_dropdown)

        self.preset_button = Gtk.MenuButton(
            label=_("Presets"), always_show_arrow=True)
        self.preset_button.set_tooltip_text(_("Apply or save a preset"))
        header.pack_start(self.preset_button)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append(_("Reload from Device"), "win.reload")
        menu.append(_("Save as Preset…"), "win.save-preset")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.undo_btn.set_tooltip_text(
            _("Undo all changes since the values were loaded"))
        self.undo_btn.set_action_name("win.undo-all")
        header.pack_end(self.undo_btn)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", lambda b: self._on_banner_button())
        content_view.add_top_bar(self.banner)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_view.set_content(self.stack)

        # Shown instead of live writing when every write would ask for a password
        self.pending_bar = Gtk.ActionBar()
        self.pending_label = Gtk.Label()
        self.pending_bar.pack_start(self.pending_label)
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda b: self._flush_pending())
        discard_btn = Gtk.Button(label=_("Discard"))
        discard_btn.set_action_name("win.undo-all")
        self.pending_bar.pack_end(apply_btn)
        self.pending_bar.pack_end(discard_btn)
        self.pending_bar.set_revealed(False)
        content_view.add_bottom_bar(self.pending_bar)

        self.content_page = Adw.NavigationPage.new(content_view, "")
        self.split.set_content(self.content_page)

        builders = {"presets": self._build_presets_page,
                    "tools": self._build_tools_page}
        for key, title, icon in PAGES:
            page = builders.get(key, lambda k=key: self._build_settings_page(k))()
            self.stack.add_named(page, key)
            self.sidebar.append(self._sidebar_row(key, _(title), icon))

        self._rebuild_preset_menu()
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))

    def _sidebar_row(self, key, title, icon):
        box = Gtk.Box(spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(6)
        box.append(Gtk.Image.new_from_icon_name(icon))
        box.append(Gtk.Label(label=title, xalign=0))
        row = Gtk.ListBoxRow()
        row.set_child(box)
        row.page_key = key
        row.page_title = title
        return row

    def _on_page_selected(self, listbox, row):
        if row is None:
            return
        self.stack.set_visible_child_name(row.page_key)
        self.content_page.set_title(row.page_title)
        self.split.set_show_content(True)

    def _build_settings_page(self, page_key):
        page = Adw.PreferencesPage()
        groups = {}
        for s in SETTINGS:
            if s["page"] != page_key:
                continue
            if s["group"] not in groups:
                grp = Adw.PreferencesGroup(title=_(s["group"]))
                page.add(grp)
                groups[s["group"]] = grp
            row = SettingRow(s, self._on_setting_changed)
            self.rows[s["id"]] = row
            groups[s["group"]].add(row.row)
            if s.get("quick"):
                groups[s["group"]].add(self._quick_angle_row(s))
        return page

    def _quick_angle_row(self, setting):
        row = Adw.ActionRow(title=_("Quick Select"))
        box = Gtk.Box()
        box.add_css_class("linked")
        box.set_valign(Gtk.Align.CENTER)
        for value in setting["quick"]:
            b = Gtk.Button(label=f"{value}°")
            b.connect("clicked", lambda b, v=value:
                      self._set_setting(setting["id"], v))
            box.append(b)
        auto = Gtk.Button(label=_("Auto"))
        auto.set_tooltip_text(_("Use the maximum range of the wheel base"))
        auto.connect("clicked", lambda b: self._set_angle_auto())
        box.append(auto)
        row.add_suffix(box)
        return row

    # -----------------------------------------------------------------------
    # Presets page and menu
    # -----------------------------------------------------------------------
    def _build_presets_page(self):
        self.presets_page = Adw.PreferencesPage()
        save_grp = Adw.PreferencesGroup(
            title=_("Save Preset"),
            description=_("Saves all current settings under a name in "
                          "~/.config/SimPower/."))
        save_row = Adw.ActionRow(title=_("Current Settings"))
        save_btn = Gtk.Button(label=_("Save as Preset…"))
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.set_action_name("win.save-preset")
        save_row.add_suffix(save_btn)
        save_grp.add(save_row)
        self.presets_page.add(save_grp)
        self.presets_list_grp = None
        self._rebuild_presets_list()
        return self.presets_page

    def _preset_files(self):
        if not CONFIG_DIR.exists():
            return []
        result = []
        for f in sorted(CONFIG_DIR.glob("*.json")):
            try:
                name = json.loads(f.read_text()).get("name", f.stem)
            except (OSError, json.JSONDecodeError):
                name = f.stem
            result.append((name, f))
        return result

    def _rebuild_presets_list(self):
        # PreferencesGroup has no row removal, so replace the whole group.
        if self.presets_list_grp is not None:
            self.presets_page.remove(self.presets_list_grp)
        grp = Adw.PreferencesGroup(title=_("Saved Presets"))
        self.presets_list_grp = grp
        self.presets_page.add(grp)

        presets = self._preset_files()
        if not presets:
            grp.add(Adw.ActionRow(
                title=_("No presets saved yet"),
                subtitle=_("Saved presets appear here and in the Presets "
                           "menu at the top.")))
            return
        for name, path in presets:
            row = Adw.ActionRow(title=name, subtitle=path.name)
            apply_btn = Gtk.Button(label=_("Apply"))
            apply_btn.add_css_class("suggested-action")
            apply_btn.set_valign(Gtk.Align.CENTER)
            apply_btn.connect("clicked", lambda b, p=path: self._apply_preset(p))
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_tooltip_text(_("Delete"))
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.connect("clicked", lambda b, p=path, n=name:
                            self._confirm_delete_preset(p, n))
            row.add_suffix(apply_btn)
            row.add_suffix(del_btn)
            grp.add(row)

    def _rebuild_preset_menu(self):
        menu = Gio.Menu()
        presets = Gio.Menu()
        for name, path in self._preset_files():
            item = Gio.MenuItem.new(name, None)
            item.set_action_and_target_value(
                "win.apply-preset", GLib.Variant.new_string(str(path)))
            presets.append_item(item)
        if presets.get_n_items():
            menu.append_section(None, presets)
        manage = Gio.Menu()
        manage.append(_("Save as Preset…"), "win.save-preset")
        manage.append(_("Manage Presets"), "win.show-presets")
        menu.append_section(None, manage)
        self.preset_button.set_menu_model(menu)

    def _presets_changed(self):
        self._rebuild_presets_list()
        self._rebuild_preset_menu()

    # -----------------------------------------------------------------------
    # Tools page
    # -----------------------------------------------------------------------
    def _build_tools_page(self):
        page = Adw.PreferencesPage()

        defaults = Adw.PreferencesGroup(title=_("Quick Setup"))
        row = Adw.ActionRow(
            title=_("Sim Racing Defaults"),
            subtitle=_("Balanced steering values for sim racing: 900°, "
                       "70 % force feedback, no spring or damper."))
        btn = Gtk.Button(label=_("Apply"))
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda b: self._apply_values(
            SIM_DEFAULTS, _("Sim racing defaults applied")))
        row.add_suffix(btn)
        defaults.add(row)
        page.add(defaults)

        rumble = Adw.PreferencesGroup(
            title=_("Pedal Rumble Test"),
            description=_("Sends a single rumble command to ClubSport Pedals "
                          "V3 or CSL Elite/LC pedals."))
        self.rumble_adj = {}
        for key, title in [("a", _("Throttle Motor")),
                           ("b", _("Brake Motor")),
                           ("dur", _("Duration (×10 ms)"))]:
            r = Adw.SpinRow.new_with_range(0, 255, 1)
            r.set_title(title)
            r.set_value(20 if key == "dur" else 128)
            self.rumble_adj[key] = r
            rumble.add(r)
        fire = Adw.ActionRow(title=_("Send Rumble"))
        fire_btn = Gtk.Button(label=_("Send"))
        fire_btn.set_valign(Gtk.Align.CENTER)
        fire_btn.connect("clicked", lambda b: self._send_rumble())
        fire.add_suffix(fire_btn)
        rumble.add(fire)
        self.rumble_group = rumble
        page.add(rumble)

        system = Adw.PreferencesGroup(title=_("System"))
        self.perm_row = Adw.ActionRow(
            title=_("Write Permissions"),
            subtitle=_("Installs a udev rule so settings can be changed "
                       "without a password prompt."))
        perm_btn = Gtk.Button(label=_("Set Up"))
        perm_btn.set_valign(Gtk.Align.CENTER)
        perm_btn.connect("clicked", lambda b: self._setup_permissions())
        self.perm_row.add_suffix(perm_btn)
        system.add(self.perm_row)

        reset = Adw.ActionRow(
            title=_("Reset All Tuning Slots"),
            subtitle=_("Clears all 5 tuning slots stored on the wheel base."))
        reset_btn = Gtk.Button(label=_("Reset…"))
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", lambda b: self._confirm_reset())
        reset.add_suffix(reset_btn)
        system.add(reset)
        page.add(system)
        return page

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------
    def _build_actions(self):
        def add(name, cb, param=None):
            action = Gio.SimpleAction.new(name, param)
            action.connect("activate", cb)
            self.add_action(action)
            return action

        add("reload", lambda *a: self._load_from_device())
        add("save-preset", lambda *a: self._ask_preset_name())
        add("show-presets", lambda *a: self._select_page("presets"))
        add("apply-preset", lambda a, p: self._apply_preset(p.get_string()),
            GLib.VariantType.new("s"))
        self.undo_action = add("undo-all", lambda *a: self._undo_all())

    def _select_page(self, key):
        i = 0
        while (row := self.sidebar.get_row_at_index(i)) is not None:
            if row.page_key == key:
                self.sidebar.select_row(row)
                return
            i += 1

    # -----------------------------------------------------------------------
    # Device state
    # -----------------------------------------------------------------------
    def _refresh_device_state(self):
        if not self.device_path:
            self.banner.set_title(
                _("No Fanatec wheel base found. Check that it is switched on "
                  "and the hid-fanatecff driver is loaded."))
            self.banner.set_button_label(_("Retry"))
            self.banner.set_revealed(True)
        elif not self.writable:
            self.banner.set_title(
                _("Changes need a password each time. Set up write "
                  "permissions to tune live."))
            self.banner.set_button_label(_("Set Up"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        self.slot_dropdown.set_sensitive(bool(self.device_path))
        self.perm_row.set_visible(bool(self.device_path) and not self.writable)
        self.rumble_group.set_sensitive(bool(self.rumble_path))
        self._load_from_device()

    def _on_banner_button(self):
        if self.device_path:
            self._setup_permissions()
        else:
            self.device_path = find_device_path()
            self.rumble_path = find_rumble_path()
            self.writable = bool(self.device_path) and can_write(self.device_path)
            self._refresh_device_state()

    def _attr_file(self, attr_id):
        return os.path.join(self.device_path, attr_id)

    def _load_from_device(self):
        """Read every setting from the device and make it the undo baseline."""
        self._cancel_pending()
        if not self.device_path:
            for row in self.rows.values():
                row.set_baseline(row.get())
            self._set_status(_("No device"))
            return
        for attr_id, row in self.rows.items():
            value = read_int(self._attr_file(attr_id))
            row.set_supported(value is not None)
            if value is not None:
                row.set(value)
            row.set_baseline(row.get())
        slot = read_int(self._attr_file("SLOT"))
        if slot and 1 <= slot <= SLOTS:
            with self.slot_dropdown.handler_block(self._slot_handler):
                self.slot_dropdown.set_selected(slot - 1)
        self._set_status(_("In sync with device"))

    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------
    def _on_setting_changed(self, row):
        if not self.device_path or not row.row.get_sensitive():
            return
        self.pending[row.setting["id"]] = row.get()
        if self.writable:
            self._set_status(_("Writing…"))
            if self._live_timer:
                GLib.source_remove(self._live_timer)
            self._live_timer = GLib.timeout_add(LIVE_DELAY_MS, self._flush_live)
        else:
            self._update_pending_bar()

    def _flush_live(self):
        self._live_timer = 0
        self._flush_pending()
        return GLib.SOURCE_REMOVE

    def _flush_pending(self):
        if not self.pending:
            return
        writes = {self._attr_file(a): v for a, v in self.pending.items()}
        self.pending = {}
        failed = write_values(writes)
        self._update_pending_bar()
        if failed:
            names = ", ".join(os.path.basename(p) for p in failed)
            self._set_status(_("Write failed: {names}").format(names=names))
            self.toasts.add_toast(Adw.Toast.new(
                _("Could not write: {names}").format(names=names)))
        else:
            self._set_status(_("In sync with device"))

    def _cancel_pending(self):
        if self._live_timer:
            GLib.source_remove(self._live_timer)
            self._live_timer = 0
        self.pending = {}
        self._update_pending_bar()

    def _update_pending_bar(self):
        n = len(self.pending)
        waiting = n > 0 and not self.writable
        self.pending_bar.set_revealed(waiting)
        self.pending_label.set_label(_("Pending changes: {n}").format(n=n))
        if waiting:
            self._set_status(_("Changes not applied yet"))
        elif self.device_path and not self._live_timer:
            self._set_status(_("In sync with device"))

    def _set_status(self, text):
        self.window_title.set_subtitle(text)

    def _set_setting(self, attr_id, value):
        row = self.rows.get(attr_id)
        if row and row.get() != value:
            row.set(value, silent=False)

    def _apply_values(self, values, message=None, pending_message=None):
        """Set several settings at once and write them in one go."""
        for attr_id, value in values.items():
            row = self.rows.get(attr_id)
            if row is None or not row.row.get_sensitive():
                continue
            row.set(value)
            if self.device_path:
                self.pending[attr_id] = row.get()
        if self.writable or not self.device_path:
            self._flush_pending()
        else:
            self._update_pending_bar()
            message = pending_message or message
        if message:
            self.toasts.add_toast(Adw.Toast.new(message))

    def _undo_all(self):
        self._cancel_pending()
        changed = {a: r.baseline for a, r in self.rows.items()
                   if r.baseline is not None and r.get() != r.baseline}
        if changed:
            self._apply_values(changed, _("Changes undone"))

    def _set_angle_auto(self):
        if not self.device_path:
            return
        if not write_values({self._attr_file("SEN"): 0}):
            GLib.timeout_add(150, self._reload_value, "SEN")

    def _reload_value(self, attr_id):
        value = read_int(self._attr_file(attr_id))
        if value is not None:
            self.rows[attr_id].set(value)
        return GLib.SOURCE_REMOVE

    def _on_slot_selected(self):
        if not self.device_path:
            return
        self._flush_pending()
        slot = self.slot_dropdown.get_selected() + 1
        if write_values({self._attr_file("SLOT"): slot}):
            self.toasts.add_toast(Adw.Toast.new(_("Could not switch slot")))
            return
        # Switching loads the slot's stored values into all attributes.
        GLib.timeout_add(150, lambda: self._load_from_device() and False)

    def _send_rumble(self):
        if not self.rumble_path:
            return
        a = int(self.rumble_adj["a"].get_value())
        b = int(self.rumble_adj["b"].get_value())
        dur = int(self.rumble_adj["dur"].get_value())
        if write_values({self.rumble_path: (a << 16) | (b << 8) | dur}):
            self.toasts.add_toast(Adw.Toast.new(_("Could not send rumble")))

    def _setup_permissions(self):
        script = (f"printf %s {shlex.quote(UDEV_RULE)} > {UDEV_RULE_PATH} && "
                  "udevadm control --reload-rules && udevadm trigger")
        if not run_privileged(script):
            self.toasts.add_toast(Adw.Toast.new(
                _("Setting up permissions failed")))
            return
        GLib.timeout_add(1000, self._recheck_permissions)

    def _recheck_permissions(self):
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self._refresh_device_state()
        if self.writable:
            self.toasts.add_toast(Adw.Toast.new(
                _("Permissions set up — changes now apply live")))
        return GLib.SOURCE_REMOVE

    # -----------------------------------------------------------------------
    # Presets
    # -----------------------------------------------------------------------
    def _collect_values(self):
        return {a: r.get() for a, r in self.rows.items()
                if r.row.get_sensitive()}

    def _apply_preset(self, path):
        path = pathlib.Path(path)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        values = {k: v for k, v in data.get("values", {}).items()
                  if k in self.rows}
        name = data.get("name", path.stem)
        self._apply_values(
            values, _("Preset “{name}” applied").format(name=name),
            _("Preset “{name}” loaded — click Apply to write it").format(name=name))

    def _ask_preset_name(self):
        entry = Gtk.Entry(placeholder_text=_("e.g. GT3 Race"),
                          activates_default=True)
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Save as Preset"),
            body=_("Saves all current settings under this name."))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "save":
                self._save_preset(entry.get_text().strip())

        dialog.connect("response", on_response)
        dialog.present()

    def _save_preset(self, name):
        if not name:
            self._error(_("No Name"), _("Enter a name for the preset."))
            return
        safe = re.sub(r"[/\\]", "_", name)
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / f"{safe}.json").write_text(json.dumps(
                {"name": name, "values": self._collect_values()}, indent=2))
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return
        self._presets_changed()
        self.toasts.add_toast(Adw.Toast.new(
            _("Preset “{name}” saved").format(name=name)))

    def _confirm_delete_preset(self, path, name):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete “{name}”?").format(name=name),
            body=_("The preset file will be removed. This cannot be undone."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "delete":
                try:
                    pathlib.Path(path).unlink(missing_ok=True)
                except OSError as e:
                    self._error(_("Could not delete preset"), str(e))
                self._presets_changed()

        dialog.connect("response", on_response)
        dialog.present()

    # -----------------------------------------------------------------------
    # Reset
    # -----------------------------------------------------------------------
    def _confirm_reset(self):
        if not self.device_path:
            return
        dialog = Adw.MessageDialog(
            transient_for=self, heading=_("Reset All Tuning Slots?"),
            body=_("All 5 tuning slots stored on the wheel base will be "
                   "cleared. This cannot be undone."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response != "reset":
                return
            path = self._attr_file("RESET")
            if not os.path.exists(path):
                self._error(_("Not Available"),
                            _("This wheel base does not support resetting."))
            elif not write_values({path: 1}):
                GLib.timeout_add(300, lambda: self._load_from_device() and False)

        dialog.connect("response", on_response)
        dialog.present()

    def _error(self, heading, body):
        d = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        d.add_response("ok", _("OK"))
        d.present()


# ===========================================================================
class TunerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.benryboi.FanatecLinux",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = TunerWindow(application=self)
        win.present()


if __name__ == "__main__":
    app = TunerApplication()
    sys.exit(app.run(sys.argv))
