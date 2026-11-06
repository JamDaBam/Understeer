"""The main window."""
import json
import os
import pathlib

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config
from ..device import (can_write, find_device_path, find_rumble_path,
                      install_udev_rule, join_group, model_id, permission_hint,
                      read_int, write_values)
from ..i18n import _
from ..inputs import AXIS_ROLES, InputMonitor
from ..settings import MAX_SLOT, PAGES, SETTINGS, SIM_DEFAULTS, SLOTS
from .setting_row import SettingRow

LIVE_DELAY_MS = 120


class TunerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Understeer")
        self.set_default_size(860, 720)

        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)

        self.rows = {}           # attr id -> SettingRow
        self.pending = {}        # attr id -> value waiting to be written
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
        self._refresh_device_state()
        self._start_inputs()
        self.connect("close-request", lambda w: self.inputs.stop() and False)

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
        if page_key == "steering":
            page.add(self._build_live_steering())
        elif page_key == "pedals":
            page.add(self._build_live_pedals())
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

    # -----------------------------------------------------------------------
    # Live input display
    # -----------------------------------------------------------------------
    def _build_live_steering(self):
        grp = Adw.PreferencesGroup(title=_("Live Input"))
        self.steer_row = Adw.ActionRow(title=_("Current Angle"))
        self.steer_adj = Gtk.Adjustment(lower=-1, upper=1)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                          adjustment=self.steer_adj)
        scale.set_size_request(240, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_draw_value(False)
        scale.set_has_origin(False)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        scale.set_can_target(False)     # display only
        scale.set_focusable(False)
        self.steer_label = Gtk.Label(xalign=1, width_chars=9)
        self.steer_label.add_css_class("numeric")
        self.steer_row.add_suffix(scale)
        self.steer_row.add_suffix(self.steer_label)
        grp.add(self.steer_row)
        return grp

    def _build_live_pedals(self):
        grp = Adw.PreferencesGroup(
            title=_("Live Input"),
            description=_("Press a pedal to see which bar moves and choose "
                          "what it is. Max shows the highest value since the "
                          "last reset."))
        reset = Gtk.Button(label=_("Reset Max"))
        reset.add_css_class("flat")
        reset.connect("clicked", lambda b: self._reset_peaks())
        grp.set_header_suffix(reset)
        self.axes_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.axes_list.add_css_class("boxed-list")
        grp.add(self.axes_list)
        return grp

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
        self.axes_list.remove_all()
        self.axis_widgets = {}
        steering = self.inputs.steering_axis()
        axes = [a for a in self.inputs.axes.values() if a is not steering]
        if not axes:
            self.axes_list.append(Adw.ActionRow(title=self._input_problem()))
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
            row = Adw.ActionRow(
                title=_(label) if label else
                _("Axis {name}").format(name=axis.name),
                subtitle=f"{axis.device} · {axis.name}")
            bar = Gtk.LevelBar(min_value=0, max_value=1)
            for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                           Gtk.LEVEL_BAR_OFFSET_FULL):
                bar.remove_offset_value(offset)
            bar.set_size_request(150, -1)
            bar.set_valign(Gtk.Align.CENTER)
            value = Gtk.Label(xalign=1, width_chars=6)
            value.add_css_class("numeric")
            peak = Gtk.Label(xalign=1, width_chars=10)
            peak.add_css_class("numeric")
            peak.add_css_class("dim-label")
            choose = Gtk.DropDown.new_from_strings(
                [_(l) for _k, l in AXIS_ROLES])
            choose.set_selected(role_keys.index(role) if role in role_keys else 0)
            choose.set_valign(Gtk.Align.CENTER)
            choose.set_size_request(140, -1)    # keeps the bars aligned
            choose.set_tooltip_text(_("What this axis is"))
            choose.connect("notify::selected", lambda d, _p, k=axis.key:
                           self._set_axis_role(k, role_keys[d.get_selected()]))
            for w in (bar, value, peak, choose):
                row.add_suffix(w)
            self.axes_list.append(row)
            self.axis_widgets[axis.key] = (bar, value, peak)

        if hidden:
            show = Adw.SwitchRow(
                title=_("Show hidden axes ({n})").format(n=hidden),
                active=self.show_hidden_axes)
            show.connect("notify::active", lambda r, _p: self._toggle_hidden(
                r.get_active()))
            self.axes_list.append(show)
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
            self.steer_row.set_subtitle(_("Turn the wheel to see the angle"))
        else:
            self.steer_label.set_label("—")
            self.steer_row.set_subtitle(self._input_problem())
        for key, (bar, value, peak) in self.axis_widgets.items():
            a = self.inputs.axes.get(key)
            if a:
                f = a.fraction()
                bar.set_value(f)
                value.set_label(f"{f * 100:.0f} %")
                peak.set_label(_("Max {n} %").format(n=f"{a.peak * 100:.0f}"))
        return GLib.SOURCE_REMOVE

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
                          "~/.config/Understeer/."))
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

    def _rebuild_presets_list(self):
        # PreferencesGroup has no row removal, so replace the whole group.
        if self.presets_list_grp is not None:
            self.presets_page.remove(self.presets_list_grp)
        grp = Adw.PreferencesGroup(title=_("Saved Presets"))
        self.presets_list_grp = grp
        self.presets_page.add(grp)

        presets = config.list_presets()
        if not presets:
            grp.add(Adw.ActionRow(
                title=_("No presets saved yet"),
                subtitle=_("Saved presets appear here and in the Presets "
                           "menu at the top.")))
            return
        for name, path in presets:
            active = self.active_preset is not None and path == self.active_preset
            row = Adw.ActionRow(
                title=name,
                subtitle=_("Active preset") if active else path.name)
            save_btn = Gtk.Button(label=_("Save Here"))
            save_btn.set_tooltip_text(_("Save current settings to this preset"))
            save_btn.set_valign(Gtk.Align.CENTER)
            save_btn.connect("clicked", lambda b, p=path:
                             self._confirm_overwrite(p))
            row.add_suffix(save_btn)
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
        """Select the slot the wheel base reports in the slot menu."""
        slot = read_int(self._attr_file("SLOT"))
        if not slot or not 1 <= slot <= MAX_SLOT:
            return
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

    def _on_slot_name_changed(self, n, text):
        if self._filling_slot_names or self.model is None:
            return
        config.save_slot_name(self.model, n, text)
        if text.strip():
            self.slot_names[n] = text.strip()
        else:
            self.slot_names.pop(n, None)
        self._update_slot_dropdown()

    def _update_slot_dropdown(self):
        labels = [self.slot_names.get(n) or _("Slot {n}").format(n=n)
                  for n in range(1, self.slot_count + 1)]
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
            self.toasts.add_toast(Adw.Toast.new(
                _("Setting up permissions failed")))
            return
        self._update_banner()     # keeps pending changes, unlike a reload
        self.toasts.add_toast(Adw.Toast.new(_(
            "Added to “{group}”. Log out and back in to tune live.").format(
                group=group)))

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
        for attr_id, row in self.rows.items():
            value = read_int(self._attr_file(attr_id))
            row.set_supported(value is not None)
            if value is not None:
                row.set(value)
            row.set_baseline(row.get())
        self._show_device_slot()
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
        self._update_preset_indicator()
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
            self.toasts.add_toast(Adw.Toast.new(
                _("The driver can only switch to slots 1–{n}. Choose this "
                  "slot on the wheel base.").format(n=SLOTS)
                if slot > SLOTS else _("Could not switch slot")))
            self._show_device_slot()        # back to the slot that is active
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
        if not install_udev_rule():
            self.toasts.add_toast(Adw.Toast.new(
                _("Setting up permissions failed")))
            return
        GLib.timeout_add(1000, self._recheck_permissions)

    def _recheck_permissions(self):
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self._refresh_device_state()
        self._start_inputs()
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

    def _read_preset(self, path):
        """Return (name, values) of a preset file; raises on bad files."""
        name, values = config.read_preset(path)
        return name, {k: v for k, v in values.items() if k in self.rows}

    def _set_active_preset(self, path, values):
        self.active_preset = pathlib.Path(path) if path else None
        self.active_values = dict(values)
        self._presets_changed()

    def _apply_preset(self, path):
        path = pathlib.Path(path)
        try:
            name, values = self._read_preset(path)
        except (OSError, json.JSONDecodeError) as e:
            self._error(_("Could not load preset"), str(e))
            return
        self.active_preset, self.active_values = path, dict(values)
        self._rebuild_preset_menu()
        self._rebuild_presets_list()
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
        path = config.preset_path(name)
        if path.exists():
            self._confirm_overwrite(path, new_name=name)
            return
        if self._write_preset(path, name):
            self.toasts.add_toast(Adw.Toast.new(
                _("Preset “{name}” saved").format(name=name)))

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
                self.toasts.add_toast(Adw.Toast.new(
                    _("Preset “{name}” saved").format(name=name)))

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
        toast = Adw.Toast.new(_("Saved to “{name}”").format(name=name))
        if old_text is not None:
            toast.set_button_label(_("Undo"))

            def undo(t):
                try:
                    path.write_text(old_text)
                except OSError as e:
                    self._error(_("Could not save preset"), str(e))
                    return
                self._set_active_preset(path, old_values)

            toast.connect("button-clicked", undo)
        self.toasts.add_toast(toast)

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

    def _error(self, heading, body):
        d = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        d.add_response("ok", _("OK"))
        d.present()
