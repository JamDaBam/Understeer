"""The main window."""
import json
import logging
import os
import pathlib

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config, games
from .. import log as session_log
from ..device import (can_write, describe_access, find_device_path,
                      find_rumble_path, install_udev_rule, join_group, model_id,
                      permission_hint, read_int, write_values)
from .. import i18n
from ..i18n import _
from ..inputs import AXIS_ROLES, InputMonitor
from ..settings import MAX_SLOT, PAGES, SETTINGS, SIM_DEFAULTS, SLOTS
from .games_page import GamesPage
from .live import WheelView
from .setting_tile import SettingTile

LIVE_DELAY_MS = 120
LAYOUTS = ["full", "compact"]
WATCH_MS = 250       # how often to look for changes made on the wheel base
GAMES_WATCH_MS = 2000   # how often to look for started games
TOAST_MS = 3000         # how long a message stays
TOAST_BUTTON_MS = 5000  # ... with a button such as Undo, to have time to click

log = logging.getLogger("understeer.window")


class TunerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Understeer")
        self.set_default_size(1060, 780)
        layout = config.get_setting("layout")
        self.layout = layout if layout in LAYOUTS else "full"

        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)

        self.rows = {}           # attr id -> SettingTile
        self.pending = {}        # attr id -> value waiting to be written
        self.device_values = {}  # attr id (and "SLOT") -> last value read or written
        self._toast = None       # shown message, replaced by the next one
        self._toast_timer = 0
        self._toast_action = None   # what its button does
        self._live_timer = 0
        self._status = ""

        self.model = None           # wheel base model, e.g. "0EB7:0020"
        self.slot_names = {}        # slot number -> name chosen by the user
        self.slot_count = SLOTS     # more if the wheel base reported more
        self._filling_slot_names = False

        self.active_preset = None   # path of the last applied/saved preset
        self.active_values = {}     # values stored in that preset

        self.inputs = InputMonitor(self._on_input)
        self.axis_roles = config.load_axis_roles()
        self.show_hidden_axes = False
        self.axis_widgets = {}   # axis key -> (level bar, value label, max label)
        self._input_timer = 0

        self._build_actions()
        self._build_ui()
        self._apply_layout()
        self._refresh_device_state()
        self._start_inputs()
        self._watch = GLib.timeout_add(WATCH_MS, self._check_device)
        self._running_games = set()
        self._games_watch = GLib.timeout_add(GAMES_WATCH_MS, self._check_games)
        GLib.idle_add(lambda: self._check_games() and False)
        self.connect("close-request", self._on_close)

    def _on_close(self, window):
        GLib.source_remove(self._watch)
        GLib.source_remove(self._games_watch)
        self.inputs.stop()
        return False

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
        self.split.set_sidebar(Adw.NavigationPage.new(side_view, "Understeer"))

        # Content
        content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Understeer")
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
        menu.append(_("Rename Slots…"), "win.rename-slots")
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
                    "games": self._build_games_page,
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

    # -----------------------------------------------------------------------
    # Page layout helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _scroll_page():
        """Scrollable page with a centred column; returns (page, column)."""
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(column, f"set_margin_{side}")(12)
        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900)
        clamp.set_child(column)
        page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  vexpand=True)
        page.set_child(clamp)
        return page, column

    @staticmethod
    def _tile_grid():
        """Grid of tiles that wraps to the window width."""
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           homogeneous=True, min_children_per_line=1,
                           max_children_per_line=5, column_spacing=12,
                           row_spacing=12, valign=Gtk.Align.START)
        return grid

    @staticmethod
    def _section(column, title, description=None, suffix=None):
        head = Gtk.Box(spacing=12)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("section-title")
        head.append(label)
        if suffix is not None:
            suffix.set_valign(Gtk.Align.END)
            head.append(suffix)
        column.append(head)
        if description:
            desc = Gtk.Label(label=description, xalign=0, wrap=True)
            desc.add_css_class("dim-label")
            desc.set_margin_start(6)
            desc.set_margin_bottom(6)
            column.append(desc)

    def _build_settings_page(self, page_key):
        page, column = self._scroll_page()
        if page_key == "steering":
            self._build_live_steering(column)
        elif page_key == "pedals":
            self._build_live_pedals(column)
        grids = {}
        for s in SETTINGS:
            if s["page"] != page_key:
                continue
            if s["group"] not in grids:
                self._section(column, _(s["group"]))
                grids[s["group"]] = self._tile_grid()
                column.append(grids[s["group"]])
            tile = SettingTile(s, self._on_setting_changed)
            self.rows[s["id"]] = tile
            if s.get("quick"):
                tile.add_extra(self._quick_angle_buttons(s))
            grids[s["group"]].append(tile.row)
        return page

    # -----------------------------------------------------------------------
    # Live input display
    # -----------------------------------------------------------------------
    def _build_live_steering(self, column):
        self._section(column, _("Live Input"))
        tile = Gtk.Box(spacing=24)
        tile.add_css_class("card")
        tile.add_css_class("tile")
        tile.add_css_class("live-tile")
        self.wheel_view = WheelView("understeer-steering-symbolic")
        tile.append(self.wheel_view)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       hexpand=True, valign=Gtk.Align.CENTER)
        caption = Gtk.Label(label=_("Current Angle"), xalign=0)
        caption.add_css_class("dim-label")
        self.steer_label = Gtk.Label(xalign=0)
        self.steer_label.add_css_class("live-value")
        self.steer_adj = Gtk.Adjustment(lower=-1, upper=1)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                          adjustment=self.steer_adj)
        scale.set_draw_value(False)
        scale.set_has_origin(False)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        scale.set_can_target(False)     # display only
        scale.set_focusable(False)
        self.steer_status = Gtk.Label(xalign=0, wrap=True)
        self.steer_status.add_css_class("dim-label")
        for w in (caption, self.steer_label, scale, self.steer_status):
            info.append(w)
        tile.append(info)
        column.append(tile)

    def _build_live_pedals(self, column):
        reset = Gtk.Button(label=_("Reset Max"))
        reset.add_css_class("flat")
        reset.connect("clicked", lambda b: self._reset_peaks())
        self._section(column, _("Live Input"),
                      _("Press a pedal to see which bar moves and choose "
                        "what it is. Max shows the highest value since the "
                        "last reset."), suffix=reset)
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        tile.add_css_class("card")
        tile.add_css_class("tile")
        tile.add_css_class("live-tile")
        self.axes_empty = Gtk.Label(xalign=0, wrap=True)
        self.axes_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                     homogeneous=True, min_children_per_line=2,
                                     max_children_per_line=8,
                                     column_spacing=18, row_spacing=18)
        self.hidden_box = Gtk.Box(spacing=12)
        self.hidden_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.hidden_switch.connect("notify::active", lambda sw, _p:
                                   self._toggle_hidden(sw.get_active()))
        self.hidden_label = Gtk.Label(xalign=0)
        self.hidden_box.append(self.hidden_switch)
        self.hidden_box.append(self.hidden_label)
        for w in (self.axes_empty, self.axes_flow, self.hidden_box):
            tile.append(w)
        column.append(tile)

    def _start_inputs(self):
        self.inputs.start()
        self._rebuild_axes_list()
        self._update_inputs()
        self._update_permission_row()

    def _input_problem(self):
        if self.inputs.denied:
            return _("No read access to the input device. Set up permissions "
                     "under Tools.")
        return _("No Fanatec input device found.")

    def _rebuild_axes_list(self):
        self.axes_flow.remove_all()
        self.axis_widgets = {}
        steering = self.inputs.steering_axis()
        axes = [a for a in self.inputs.axes.values() if a is not steering]
        self.axes_empty.set_label(self._input_problem())
        self.axes_empty.set_visible(not axes)
        self.axes_flow.set_visible(bool(axes))
        self.hidden_box.set_visible(False)
        if not axes:
            return

        role_keys = [k for k, _l in AXIS_ROLES]
        hidden = 0
        for axis in axes:
            role = self.axis_roles.get(axis.key, "")
            if role == "hidden":
                hidden += 1
                if not self.show_hidden_axes:
                    continue
            label = dict(AXIS_ROLES).get(role) if role and role != "hidden" else None
            pedal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pedal.add_css_class("pedal")
            name = Gtk.Label(label=_(label) if label else
                             _("Axis {name}").format(name=axis.name))
            name.add_css_class("heading")
            bar = Gtk.LevelBar(min_value=0, max_value=1,
                               orientation=Gtk.Orientation.VERTICAL,
                               inverted=True, halign=Gtk.Align.CENTER)
            for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                           Gtk.LEVEL_BAR_OFFSET_FULL):
                bar.remove_offset_value(offset)
            value = Gtk.Label()
            value.add_css_class("pedal-value")
            peak = Gtk.Label()
            peak.add_css_class("dim-label")
            choose = Gtk.DropDown.new_from_strings(
                [_(l) for _k, l in AXIS_ROLES])
            choose.set_selected(role_keys.index(role) if role in role_keys else 0)
            choose.set_tooltip_text(_("What this axis is"))
            choose.connect("notify::selected", lambda d, _p, k=axis.key:
                           self._set_axis_role(k, role_keys[d.get_selected()]))
            source = Gtk.Label(label=f"{axis.device} · {axis.name}")
            source.set_ellipsize(3)             # Pango.EllipsizeMode.END
            source.add_css_class("tile-id")
            for w in (name, bar, value, peak, choose, source):
                pedal.append(w)
            self.axes_flow.append(pedal)
            self.axis_widgets[axis.key] = (bar, value, peak)

        if hidden:
            self.hidden_label.set_label(
                _("Show hidden axes ({n})").format(n=hidden))
            self.hidden_box.set_visible(True)
        self._update_inputs()

    def _set_axis_role(self, key, role):
        if role:
            self.axis_roles[key] = role
        else:
            self.axis_roles.pop(key, None)
        config.save_axis_roles(self.axis_roles)
        # Rebuild after the dropdown's own signal has finished.
        GLib.idle_add(lambda: self._rebuild_axes_list() and False)

    def _toggle_hidden(self, show):
        if show != self.show_hidden_axes:
            self.show_hidden_axes = show
            GLib.idle_add(lambda: self._rebuild_axes_list() and False)

    def _reset_peaks(self):
        for axis in self.inputs.axes.values():
            axis.peak = axis.fraction()
        self._update_inputs()

    def _on_input(self):
        # Wheels report up to 1000 updates per second; redraw at ~30 fps.
        if not self._input_timer:
            self._input_timer = GLib.timeout_add(33, self._update_inputs)

    def _update_inputs(self):
        self._input_timer = 0
        axis = self.inputs.steering_axis()
        if axis:
            c = axis.centered()
            self.steer_adj.set_value(c)
            # The full axis range covers the configured steering angle.
            sen = self.rows["SEN"].get() if "SEN" in self.rows else 0
            v, unit = (round(c * sen / 2), "°") if sen > 0 else (round(c * 100), " %")
            self.steer_label.set_label(f"{v:+d}{unit}" if v else f"0{unit}")
            self.steer_status.set_label(_("Turn the wheel to see the angle"))
            self.wheel_view.set_angle(c * (sen / 2 if sen > 0 else 450))
        else:
            self.steer_label.set_label("—")
            self.steer_status.set_label(self._input_problem())
            self.wheel_view.set_angle(0)
        for key, (bar, value, peak) in self.axis_widgets.items():
            a = self.inputs.axes.get(key)
            if a:
                f = a.fraction()
                bar.set_value(f)
                value.set_label(f"{f * 100:.0f} %")
                peak.set_label(_("Max {n} %").format(n=f"{a.peak * 100:.0f}"))
        return GLib.SOURCE_REMOVE

    def _quick_angle_buttons(self, setting):
        box = Gtk.Box(homogeneous=True)
        box.add_css_class("linked")
        box.add_css_class("quick-select")
        box.set_tooltip_text(_("Quick Select"))
        for value in setting["quick"]:
            b = Gtk.Button(label=f"{value}°")
            b.connect("clicked", lambda b, v=value:
                      self._set_setting(setting["id"], v))
            box.append(b)
        auto = Gtk.Button(label=_("Auto"))
        auto.set_tooltip_text(_("Use the maximum range of the wheel base"))
        auto.connect("clicked", lambda b: self._set_angle_auto())
        box.append(auto)
        return box

    # -----------------------------------------------------------------------
    # Presets page and menu
    # -----------------------------------------------------------------------
    def _build_presets_page(self):
        page, column = self._scroll_page()
        self._section(column, _("Saved Presets"),
                      _("Saves all current settings under a name in "
                        "~/.config/Understeer/."))
        self.presets_flow = self._tile_grid()
        column.append(self.presets_flow)
        self._rebuild_presets_list()
        return page

    def _rebuild_presets_list(self):
        self.presets_flow.remove_all()
        presets = config.list_presets()

        new = Gtk.Button(action_name="win.save-preset")
        new.add_css_class("card")
        new.add_css_class("tile")
        new.add_css_class("new-preset")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        valign=Gtk.Align.CENTER)
        plus = Gtk.Image.new_from_icon_name("list-add-symbolic")
        plus.set_pixel_size(32)
        plus.add_css_class("tile-icon")
        plus.set_halign(Gtk.Align.CENTER)
        inner.append(plus)
        inner.append(Gtk.Label(label=_("Save as Preset…"), css_classes=["heading"]))
        if not presets:
            hint = Gtk.Label(label=_("Saved presets appear here and in the "
                                     "Presets menu at the top."),
                             wrap=True, justify=Gtk.Justification.CENTER)
            hint.add_css_class("dim-label")
            inner.append(hint)
        new.set_child(inner)
        self.presets_flow.append(new)

        for name, path in presets:
            active = self.active_preset is not None and path == self.active_preset
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            tile.add_css_class("card")
            tile.add_css_class("tile")
            tile.add_css_class("preset-tile")
            if active:
                tile.add_css_class("active")
            title = Gtk.Label(label=name, xalign=0, wrap=True)
            title.add_css_class("preset-name")
            sub = Gtk.Label(label=_("Active preset") if active else path.name,
                            xalign=0, vexpand=True, valign=Gtk.Align.START)
            sub.set_ellipsize(3)                # Pango.EllipsizeMode.END
            sub.add_css_class("dim-label")
            buttons = Gtk.Box(spacing=6)
            apply_btn = Gtk.Button(label=_("Apply"), hexpand=True)
            apply_btn.add_css_class("suggested-action")
            apply_btn.connect("clicked", lambda b, p=path: self._apply_preset(p))
            save_btn = Gtk.Button(label=_("Save Here"))
            save_btn.set_tooltip_text(_("Save current settings to this preset"))
            save_btn.connect("clicked", lambda b, p=path:
                             self._confirm_overwrite(p))
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.set_tooltip_text(_("Delete"))
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda b, p=path, n=name:
                            self._confirm_delete_preset(p, n))
            for w in (apply_btn, save_btn, del_btn):
                buttons.append(w)
            for w in (title, sub, buttons):
                tile.append(w)
            self.presets_flow.append(tile)

    def _rebuild_preset_menu(self):
        menu = Gio.Menu()
        if self.active_preset is not None:
            update = Gio.Menu()
            update.append(_("Save to “{name}”").format(
                name=self._active_name()), "win.update-preset")
            menu.append_section(None, update)
        presets = Gio.Menu()
        for name, path in config.list_presets():
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
        self.games_page.refresh()
        self._rebuild_preset_menu()
        self._update_preset_indicator()

    def _active_name(self):
        for name, path in config.list_presets():
            if path == self.active_preset:
                return name
        return self.active_preset.stem if self.active_preset else ""

    def _preset_diff(self, stored):
        """[(attr id, stored value or None, current value)] that differ."""
        return [(a, stored.get(a), v) for a, v in self._collect_values().items()
                if stored.get(a) != v]

    def _update_preset_indicator(self):
        if self.active_preset is None:
            self.preset_button.set_label(_("Presets"))
            self.update_action.set_enabled(False)
            return
        # Only settings stored in the preset count; older presets may hold
        # just a few values.
        modified = any(old is not None
                       for _a, old, _v in self._preset_diff(self.active_values))
        name = self._active_name()
        self.preset_button.set_label(f"{name} •" if modified else name)
        self.preset_button.set_tooltip_text(
            _("Preset “{name}” has unsaved changes").format(name=name)
            if modified else _("Apply or save a preset"))
        self.update_action.set_enabled(modified)

    # -----------------------------------------------------------------------
    # Tools page
    # -----------------------------------------------------------------------
    def _build_tools_page(self):
        page = Adw.PreferencesPage()

        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        layout_row = Adw.ComboRow(title=_("Layout"),
                                  subtitle=_("Size of the tiles"))
        layout_row.set_model(Gtk.StringList.new([_("Full"), _("Compact")]))
        layout_row.set_selected(LAYOUTS.index(self.layout))
        layout_row.connect("notify::selected", lambda r, _p:
                           self._set_layout(LAYOUTS[r.get_selected()]))
        appearance.add(layout_row)

        languages = [None] + i18n.available_languages()
        current = config.get_setting("language")
        language_row = Adw.ComboRow(title=_("Language"))
        language_row.set_model(Gtk.StringList.new(
            [_("System language")] + [i18n.language_name(c) for c in languages[1:]]))
        language_row.set_selected(languages.index(current)
                                  if current in languages else 0)
        language_row.connect("notify::selected", lambda r, _p:
                             self._set_language(languages[r.get_selected()]))
        appearance.add(language_row)
        page.add(appearance)

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

        self.slot_names_group = Adw.PreferencesGroup(
            title=_("Slot Names"),
            description=_("Name the slots like on the display of your wheel "
                          "base, e.g. “A” or “Set 1”. The names are saved for "
                          "this wheel base model."))
        self.slot_name_rows = {}
        self._show_slot_name_rows()
        page.add(self.slot_names_group)

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
            subtitle="")
        self.perm_btn = Gtk.Button(label=_("Set Up"))
        self.perm_btn.set_valign(Gtk.Align.CENTER)
        self.perm_btn.connect("clicked", lambda b: self._fix_permissions())
        self.perm_row.add_suffix(self.perm_btn)
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

        log_row = Adw.ActionRow(
            title=_("Log File"),
            subtitle=_("Records what happened in this session. Attach it "
                       "when you report a problem."))
        log_row.set_tooltip_text(str(session_log.LOG_FILE))
        for label, callback in ((_("Open"), self._open_log),
                                (_("Show Folder"), self._open_log_folder)):
            button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda b, cb=callback: cb())
            log_row.add_suffix(button)
        system.add(log_row)
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
        add("rename-slots", lambda *a: self._select_page("tools"))
        add("apply-preset", lambda a, p: self._apply_preset(p.get_string()),
            GLib.VariantType.new("s"))
        self.undo_action = add("undo-all", lambda *a: self._undo_all())
        self.update_action = add("update-preset",
                                 lambda *a: self._update_active_preset())
        self.update_action.set_enabled(False)

    def _apply_layout(self):
        if self.layout == "compact":
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")

    def _set_layout(self, layout):
        self.layout = layout
        config.set_setting("layout", layout)
        self._apply_layout()

    def _set_language(self, language):
        config.set_setting("language", language)
        i18n.set_language(language)
        # All text is created with the window, so build a new one; wait
        # until the language menu has finished its signal.
        GLib.idle_add(lambda: self.get_application().rebuild_window("tools")
                      and False)

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
        if self.device_path:
            log.info("Wheel base %s (model %s), %s", self.device_path,
                     model_id(self.device_path),
                     "writable" if self.writable else "no write access")
            log.info("Access: %s", describe_access(self.device_path))
            log.info("Rumble: %s", self.rumble_path or "not available")
        else:
            log.info("No wheel base found")
        self._update_banner()
        self.slot_dropdown.set_sensitive(bool(self.device_path))
        self.rumble_group.set_sensitive(bool(self.rumble_path))
        self._load_slot_names()
        self._load_from_device()

    def _load_slot_names(self):
        self.model = model_id(self.device_path) if self.device_path else None
        self.slot_names = config.load_slot_names(self.model) if self.model else {}
        self.slot_count = max(SLOTS, config.load_slot_count(self.model)
                              if self.model else 0)
        self._show_slot_name_rows()
        self._filling_slot_names = True
        try:
            for n, entry in self.slot_name_rows.items():
                entry.set_text(self.slot_names.get(n, ""))
        finally:
            self._filling_slot_names = False
        self.slot_names_group.set_sensitive(self.model is not None)
        self._update_slot_dropdown()

    def _show_slot_name_rows(self):
        """One name row per slot of the connected wheel base."""
        for n in range(1, self.slot_count + 1):
            if n not in self.slot_name_rows:
                entry = Adw.EntryRow(title=_("Slot {n}").format(n=n))
                self._filling_slot_names = True
                try:
                    entry.set_text(self.slot_names.get(n, ""))
                finally:
                    self._filling_slot_names = False
                entry.connect("changed", lambda e, n=n:
                              self._on_slot_name_changed(n, e.get_text()))
                self.slot_name_rows[n] = entry
                self.slot_names_group.add(entry)
        for n, entry in self.slot_name_rows.items():
            entry.set_visible(n <= self.slot_count)

    def _show_device_slot(self):
        """Select the slot the wheel base reports in the slot menu.

        Returns the slot number, or None if it can't be read.
        """
        slot = read_int(self._attr_file("SLOT"))
        if not slot or not 1 <= slot <= MAX_SLOT:
            return None
        if slot > self.slot_count:
            # Some wheel bases have more slots than the driver can switch
            # to (a CSL DD has 6); remember that for this model.
            self.slot_count = slot
            if self.model:
                config.save_slot_count(self.model, slot)
            self._show_slot_name_rows()
            self._update_slot_dropdown()
        with self.slot_dropdown.handler_block(self._slot_handler):
            self.slot_dropdown.set_selected(slot - 1)
        return slot

    def _on_slot_name_changed(self, n, text):
        if self._filling_slot_names or self.model is None:
            return
        config.save_slot_name(self.model, n, text)
        if text.strip():
            self.slot_names[n] = text.strip()
        else:
            self.slot_names.pop(n, None)
        self._update_slot_dropdown()

    def _slot_label(self, n):
        return self.slot_names.get(n) or _("Slot {n}").format(n=n)

    def _update_slot_dropdown(self):
        labels = [self._slot_label(n) for n in range(1, self.slot_count + 1)]
        model = self.slot_dropdown.get_model()
        # Changing the list must not switch the slot on the wheel base.
        with self.slot_dropdown.handler_block(self._slot_handler):
            selected = self.slot_dropdown.get_selected()
            model.splice(0, model.get_n_items(), labels)
            self.slot_dropdown.set_selected(selected)

    def _update_banner(self):
        if not self.device_path:
            self.banner.set_title(
                _("No Fanatec wheel base found. Check that it is switched on "
                  "and the hid-fanatecff driver is loaded."))
            self.banner.set_button_label(_("Retry"))
            self.banner.set_revealed(True)
        elif not self.writable:
            mode, group = permission_hint(self.device_path)
            if mode == "join":
                self.banner.set_title(_(
                    "Changes need a password each time. Join the “{group}” "
                    "group to tune live.").format(group=group))
                self.banner.set_button_label(_("Join Group"))
            elif mode == "relogin":
                self.banner.set_title(_(
                    "Log out and back in to finish joining the “{group}” "
                    "group. Until then, changes need a password.").format(
                        group=group))
                self.banner.set_button_label(None)
            else:
                self.banner.set_title(
                    _("Changes need a password each time. Set up write "
                      "permissions to tune live."))
                self.banner.set_button_label(_("Set Up"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self._update_permission_row()

    def _permission_mode(self):
        """("join"|"relogin"|"udev", group) or None if nothing is missing."""
        if self.device_path and not self.writable:
            return permission_hint(self.device_path)
        if self.inputs.denied:
            return "udev", None
        return None

    def _update_permission_row(self):
        mode = self._permission_mode()
        self.perm_row.set_visible(mode is not None)
        if mode is None:
            return
        kind, group = mode
        if kind == "join":
            self.perm_row.set_subtitle(_(
                "Your user is not in the “{group}” group that the "
                "hid-fanatecff driver uses for the tuning settings.").format(
                    group=group))
            self.perm_btn.set_label(_("Join Group"))
        elif kind == "relogin":
            self.perm_row.set_subtitle(_(
                "Log out and back in to finish joining the “{group}” "
                "group.").format(group=group))
        else:
            self.perm_row.set_subtitle(_(
                "Installs a udev rule so settings can be changed without a "
                "password prompt and live input can be shown."))
            self.perm_btn.set_label(_("Set Up"))
        self.perm_btn.set_visible(kind != "relogin")

    def _fix_permissions(self):
        mode = self._permission_mode()
        if mode and mode[0] == "join":
            self._join_group(mode[1])
        elif mode:
            self._setup_permissions()

    def _join_group(self, group):
        if not join_group(group):
            self._notify(_("Setting up permissions failed"))
            return
        self._update_banner()     # keeps pending changes, unlike a reload
        self._notify(_(
            "Added to “{group}”. Log out and back in to tune live.").format(
                group=group))

    def _on_banner_button(self):
        if self.device_path:
            self._fix_permissions()
        else:
            self.device_path = find_device_path()
            self.rumble_path = find_rumble_path()
            self.writable = bool(self.device_path) and can_write(self.device_path)
            self._refresh_device_state()
            self._start_inputs()

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
        self.device_values = {}
        for attr_id, row in self.rows.items():
            value = read_int(self._attr_file(attr_id))
            self.device_values[attr_id] = value
            row.set_supported(value is not None)
            if value is not None:
                row.set(value)
            row.set_baseline(row.get())
        self.device_values["SLOT"] = self._show_device_slot()
        log.info("Read from wheel base (slot %s): %s", self.device_values["SLOT"],
                 " ".join(f"{a}={v}" for a, v in self.device_values.items()
                          if a != "SLOT" and v is not None))
        missing = [a for a, v in self.device_values.items() if v is None]
        if missing:
            log.info("Not supported by this wheel base: %s", ", ".join(missing))
        self._set_status(_("In sync with device"))
        self._update_preset_indicator()

    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------
    def _on_setting_changed(self, row):
        self._update_preset_indicator()
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
        written = list(self.pending)
        writes = {self._attr_file(a): v for a, v in self.pending.items()}
        self.pending = {}
        failed = write_values(writes)
        # What the device holds now; it may round, e.g. vibration to tens.
        for attr_id in written:
            self.device_values[attr_id] = read_int(self._attr_file(attr_id))
        self._update_pending_bar()
        if failed:
            names = ", ".join(os.path.basename(p) for p in failed)
            self._set_status(_("Write failed: {names}").format(names=names))
            log.warning("Shown to the user: could not write %s", names)
            self._notify(_("Could not write: {names}").format(names=names),
                         _("Show Log"), self._open_log)
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
        self._update_preset_indicator()
        if message:
            self._notify(message)

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
        self.device_values[attr_id] = value
        if value is not None:
            self.rows[attr_id].set(value)
        return GLib.SOURCE_REMOVE

    def _notify(self, text, button=None, action=None):
        """Show a message right away.

        Adw.Toast messages queue up, so changing something quickly (slots,
        presets, ...) would show each message for its full time, one after
        another, and the latest one far too late. Instead there is only one
        message: the next one takes over its text and its time starts again.
        An optional button (e.g. Undo) calls action().
        """
        if self._toast is None:
            toast = Adw.Toast.new("")
            toast.set_use_markup(False)     # names are plain text
            toast.set_timeout(0)            # hidden by our own timer
            toast.connect("dismissed", self._on_toast_dismissed)
            toast.connect("button-clicked", self._on_toast_button)
            self._toast = toast
            self.toasts.add_toast(toast)
        self._toast.set_title(text)
        self._toast.set_button_label(button)
        self._toast_action = action if button else None
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
        self._toast_timer = GLib.timeout_add(
            TOAST_BUTTON_MS if button else TOAST_MS, self._hide_toast)

    def _hide_toast(self):
        self._toast_timer = 0
        if self._toast is not None:
            self._toast.dismiss()
        return GLib.SOURCE_REMOVE

    def _on_toast_button(self, toast):
        if toast is self._toast and self._toast_action:
            self._toast_action()

    def _on_toast_dismissed(self, toast):
        if self._toast is toast:
            self._toast = None
            self._toast_action = None
            if self._toast_timer:               # closed by the user
                GLib.source_remove(self._toast_timer)
                self._toast_timer = 0

    def _check_device(self):
        """Follow changes made on the wheel base itself.

        Switching the slot there reloads everything; a value changed in its
        tuning menu moves the matching control. Values the app wrote itself
        are already in device_values, so they don't count as changes.
        """
        if not self.device_path or self._live_timer:
            return GLib.SOURCE_CONTINUE
        slot = read_int(self._attr_file("SLOT"))
        if slot is not None and slot != self.device_values.get("SLOT"):
            discarded = bool(self.pending)
            log.info("Slot changed on the wheel base: %s -> %s%s",
                     self.device_values.get("SLOT"), slot,
                     ", unapplied changes discarded" if discarded else "")
            self._load_from_device()
            name = self._slot_label(slot)
            self._notify(
                _("Slot changed on the wheel base: {name}. Unapplied changes "
                  "were discarded.").format(name=name) if discarded else
                _("Slot changed on the wheel base: {name}").format(name=name))
            return GLib.SOURCE_CONTINUE
        if self.pending:            # don't overwrite changes waiting for Apply
            return GLib.SOURCE_CONTINUE
        changed = False
        for attr_id, row in self.rows.items():
            value = read_int(self._attr_file(attr_id))
            if value is None or value == self.device_values.get(attr_id):
                continue
            log.info("Changed on the wheel base: %s %s -> %s", attr_id,
                     self.device_values.get(attr_id), value)
            self.device_values[attr_id] = value
            row.set(value)
            changed = True
        if changed:
            self._update_preset_indicator()
        return GLib.SOURCE_CONTINUE

    def _on_slot_selected(self):
        if not self.device_path:
            return
        self._flush_pending()
        slot = self.slot_dropdown.get_selected() + 1
        log.info("Switching to slot %s", slot)
        if write_values({self._attr_file("SLOT"): slot}):
            self._notify(
                _("The driver can only switch to slots 1–{n}. Choose this "
                  "slot on the wheel base.").format(n=SLOTS)
                if slot > SLOTS else _("Could not switch slot"))
            self._show_device_slot()        # back to the slot that is active
            return
        self.device_values["SLOT"] = slot   # not a change made on the wheel
        # Switching loads the slot's stored values into all attributes.
        GLib.timeout_add(150, lambda: self._load_from_device() and False)

    def _send_rumble(self):
        if not self.rumble_path:
            return
        a = int(self.rumble_adj["a"].get_value())
        b = int(self.rumble_adj["b"].get_value())
        dur = int(self.rumble_adj["dur"].get_value())
        if write_values({self.rumble_path: (a << 16) | (b << 8) | dur}):
            self._notify(_("Could not send rumble"))

    def _setup_permissions(self):
        if not install_udev_rule():
            self._notify(_("Setting up permissions failed"))
            return
        GLib.timeout_add(1000, self._recheck_permissions)

    def _recheck_permissions(self):
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self._refresh_device_state()
        self._start_inputs()
        if self.writable:
            self._notify(_("Permissions set up — changes now apply live"))
        return GLib.SOURCE_REMOVE

    # -----------------------------------------------------------------------
    # Presets
    # -----------------------------------------------------------------------
    def _collect_values(self):
        return {a: r.get() for a, r in self.rows.items()
                if r.row.get_sensitive()}

    def _read_preset(self, path):
        """Return (name, values) of a preset file; raises on bad files."""
        name, values = config.read_preset(path)
        return name, {k: v for k, v in values.items() if k in self.rows}

    def _set_active_preset(self, path, values):
        self.active_preset = pathlib.Path(path) if path else None
        self.active_values = dict(values)
        self._presets_changed()

    def _build_games_page(self):
        self.games_page = GamesPage(self)
        return self.games_page.widget

    def _check_games(self):
        """Apply a game's preset when it starts (while the app is open)."""
        try:
            running = games.running_games()
        except OSError as e:
            log.warning("Could not look for running games: %s", e)
            return GLib.SOURCE_CONTINUE
        started = running - self._running_games
        stopped = self._running_games - running
        self._running_games = running
        self.games_page.set_running(running)
        for game in self.games_page.games:
            if game["game"] in stopped:
                log.info("Game stopped: %s (%s)", game["name"], game["game"])
            if game["game"] not in started:
                continue
            path = config.game_preset_path(game)
            log.info("Game started: %s (%s), preset %s, auto-apply %s",
                     game["name"], game["game"], game["preset"],
                     "on" if game["auto"] else "off")
            if game["auto"] and game["preset"] and path is None:
                log.warning("Preset %s of %s no longer exists", game["preset"],
                            game["name"])
            if game["auto"] and path is not None:
                self._apply_preset(path, game=game["name"])
        return GLib.SOURCE_CONTINUE

    def _apply_preset(self, path, game=None):
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        log.info("Applying preset %s (%s)%s: %s", name, path.name,
                 f" for {game}" if game else "",
                 " ".join(f"{a}={v}" for a, v in values.items()))
        self.active_preset, self.active_values = path, dict(values)
        self._rebuild_preset_menu()
        self._rebuild_presets_list()
        if game:
            self._apply_values(
                values,
                _("{game} started: preset “{name}” applied").format(
                    game=game, name=name),
                _("{game} started: preset “{name}” loaded — click Apply to "
                  "write it").format(game=game, name=name))
        else:
            self._apply_values(
                values, _("Preset “{name}” applied").format(name=name),
                _("Preset “{name}” loaded — click Apply to write it").format(
                    name=name))

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
        path = config.preset_path(name)
        if path.exists():
            self._confirm_overwrite(path, new_name=name)
            return
        if self._write_preset(path, name):
            self._notify(_("Preset “{name}” saved").format(name=name))

    def _write_preset(self, path, name):
        values = self._collect_values()
        try:
            config.write_preset(path, name, values)
        except OSError as e:
            self._error(_("Could not save preset"), str(e))
            return False
        self._set_active_preset(path, values)
        return True

    def _describe_diff(self, diff, limit=6):
        lines = []
        for attr_id, old, new in diff[:limit]:
            row = self.rows[attr_id]
            before = row.format(old) if old is not None else "—"
            lines.append(f"{_(row.setting['label'])}: {before} → {row.format(new)}")
        if len(diff) > limit:
            lines.append(_("and {n} more").format(n=len(diff) - limit))
        return "\n".join(lines)

    def _confirm_overwrite(self, path, new_name=None):
        """Ask before replacing a preset file with the current settings."""
        try:
            name, stored = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            name, stored = path.stem, {}
        name = new_name or name
        diff = self._preset_diff(stored)
        body = (_("Settings that change:") + "\n" + self._describe_diff(diff)
                if diff else _("The preset already matches the current "
                               "settings."))
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Overwrite “{name}”?").format(name=name), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("overwrite", _("Overwrite"))
        dialog.set_response_appearance("overwrite",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "overwrite" and self._write_preset(path, name):
                self._notify(_("Preset “{name}” saved").format(name=name))

        dialog.connect("response", on_response)
        dialog.present()

    def _update_active_preset(self):
        """Save to the active preset without asking; offer undo instead."""
        path = self.active_preset
        if path is None:
            return
        try:
            old_text = path.read_text()
            name, old_values = self._read_preset(path)
        except (OSError, json.JSONDecodeError):
            old_text, name, old_values = None, self._active_name(), {}
        if not self._write_preset(path, name):
            return
        def undo():
            try:
                path.write_text(old_text)
            except OSError as e:
                self._error(_("Could not save preset"), str(e))
                return
            self._set_active_preset(path, old_values)

        self._notify(_("Saved to “{name}”").format(name=name),
                     _("Undo") if old_text is not None else None, undo)

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
                if self.active_preset == pathlib.Path(path):
                    self.active_preset, self.active_values = None, {}
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

    def _open_log(self):
        """Open the session log in the default text editor."""
        if session_log.LOG_FILE.exists():
            Gtk.FileLauncher.new(Gio.File.new_for_path(
                str(session_log.LOG_FILE))).launch(self, None, None)

    def _open_log_folder(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path(
            str(session_log.LOG_FILE))).open_containing_folder(self, None, None)

    def _error(self, heading, body):
        log.error("%s: %s", heading, body)
        d = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        d.add_response("ok", _("OK"))
        d.present()
