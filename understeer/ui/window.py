"""The main window.

Parts of it live in their own modules as mixins: the live input display
(live_input.py), presets (presets.py) and the preferences (preferences.py).
"""
import logging
import os

from gi.repository import Adw, Gio, GLib, Gtk

from .. import config, games, report
from .. import log as session_log
from ..device import (can_write, describe_access, find_device_path,
                      find_rumble_path, install_udev_rule, join_group, model_id,
                      permission_hint, read_int, write_values)
from .. import i18n
from ..i18n import _
from ..inputs import InputMonitor
from ..settings import (ADVANCED_GROUPS, MAX_SLOT, SETTINGS, SLOTS, TABS,
                        VIEWS)
from .games_page import GamesPage
from .live_input import LiveInputMixin
from .preferences import LAYOUTS, PreferencesMixin
from .presets import PresetsMixin
from .report_dialog import ReportDialog
from .setting_tile import SettingTile

LIVE_DELAY_MS = 120
WATCH_MS = 250       # how often to look for changes made on the wheel base
PLUG_TICKS = 4       # ... and every 4th time (1 s) for a wheel base plugged in or out
INPUT_SETTLE_MS = 2000  # udev may give access to a new input device a bit later
GAMES_WATCH_MS = 2000   # how often to look for started games
TOAST_MS = 3000         # how long a message stays
TOAST_BUTTON_MS = 5000  # ... with a button such as Undo, to have time to click

log = logging.getLogger("understeer.window")


class TunerWindow(LiveInputMixin, PresetsMixin, PreferencesMixin,
                  Adw.ApplicationWindow):
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
        self._preset_names = {}     # preset path -> name, see _rebuild_preset_menu

        self.inputs = InputMonitor(self._on_input)
        self.axis_roles = config.load_axis_roles()
        self.show_hidden_axes = False
        self.axis_widgets = {}   # axis key -> (level bar, value label, max label)
        self._input_timer = 0
        self._open_groups = set()   # advanced groups the user opened
        self._group_summaries = []  # updates their one-line values

        self._build_actions()
        self._build_ui()
        self._refresh_device_state()
        self._start_inputs()
        self._watch = GLib.timeout_add(WATCH_MS, self._check_device)
        app = self.get_application()
        self._game_watch = getattr(app, "game_watch", None) or games.StartWatch()
        self._games_watch = GLib.timeout_add(GAMES_WATCH_MS, self._check_games)
        GLib.idle_add(lambda: self._check_games() and False)
        self.connect("close-request", self._on_close)

    def flush_live(self):
        """Write a live change that is still waiting for its short delay."""
        if self._live_timer:
            GLib.source_remove(self._live_timer)
            self._flush_live()

    def relocalize(self):
        """Show all text in the language chosen now, in the same window.

        Text is set when widgets are made, so the window content is built
        again. The window itself stays, with its position and size, and so
        do the active preset, the state "Undo All" goes back to, changes
        waiting for Apply, the shown view and tab, the preferences window
        with its page, and the shown message.
        """
        self.flush_live()
        baselines = {a: r.baseline for a, r in self.rows.items()}
        pending = dict(self.pending)
        active = self.active_preset, self.active_values
        view = self.views.get_visible_child_name()
        tab = self.tabs.get_visible_child_name()

        self.rows = {}
        self.pending = {}
        self._build_ui(view, tab)
        self._refresh_device_state()
        self._rebuild_axes_list()
        self._update_inputs()
        self._update_permission_row()
        self.games_page.set_running(self._game_watch.running)

        for attr_id, row in self.rows.items():
            if baselines.get(attr_id) is not None and row.row.get_sensitive():
                row.set_baseline(baselines[attr_id])
        if pending and self.device_path and not self.writable:
            for attr_id, value in pending.items():
                if attr_id in self.rows:
                    self.rows[attr_id].set(value)
                    self.pending[attr_id] = self.rows[attr_id].get()
            self._update_pending_bar()
        if active[0] is not None:
            self._set_active_preset(*active)
        else:
            self._update_preset_indicator()
        log.info("Window rebuilt; kept preset %s, %d unapplied changes",
                 active[0].name if active[0] else "none", len(self.pending))

    def _on_close(self, window):
        GLib.source_remove(self._watch)
        GLib.source_remove(self._games_watch)
        self.inputs.stop()
        return False

    # -----------------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------------
    def _build_ui(self, view=None, tab=None):
        """Build the window content; again after the language changed.

        The message overlay holds no text and stays, so a shown message
        survives a rebuild. So does the narrow-window breakpoint, which only
        calls _set_narrow().
        """
        if not hasattr(self, "toasts"):
            self.toasts = Adw.ToastOverlay()
            self.set_content(self.toasts)
            self._narrow = False
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
            bp.connect("apply", lambda b: self._set_narrow(True))
            bp.connect("unapply", lambda b: self._set_narrow(False))
            self.add_breakpoint(bp)

        self._group_summaries = []
        content_view = Adw.ToolbarView()
        self.header = header = Adw.HeaderBar()
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

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                  primary=True)
        menu_btn.set_tooltip_text(_("Main Menu"))
        menu = Gio.Menu()
        device = Gio.Menu()
        device.append(_("Reload from Device"), "win.reload")
        device.append(_("Save as Preset…"), "win.save-preset")
        menu.append_section(None, device)
        app = Gio.Menu()
        app.append(_("Preferences"), "win.preferences")
        app.append(_("Report a Problem…"), "win.report")
        app.append(_("About Understeer"), "win.about")
        menu.append_section(None, app)
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

        self.views = Adw.ViewStack()
        content_view.set_content(self.views)
        self.switcher = Adw.ViewSwitcher(stack=self.views,
                                         policy=Adw.ViewSwitcherPolicy.WIDE)
        # In a narrow window the views move to a bar at the bottom and the
        # title shows the status instead.
        self.window_title = Adw.WindowTitle(title="Understeer")
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.views)

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
        content_view.add_bottom_bar(self.switcher_bar)

        builders = {"tuning": self._build_tuning_view,
                    "presets": self._build_presets_page,
                    "games": self._build_games_page}
        for key, title, icon in VIEWS:
            self.views.add_titled_with_icon(builders[key](), key, _(title), icon)
        self._build_preferences()

        self._rebuild_preset_menu()
        # Choose view and tab before the content is shown, so a rebuild
        # doesn't show the first ones for a moment.
        self.views.set_visible_child_name(view or VIEWS[0][0])
        if tab:
            self.tabs.set_visible_child_name(tab)
        self._set_narrow(self._narrow)
        self.toasts.set_child(content_view)
        self._apply_layout()        # the tiles are new
        for update in self._group_summaries:
            update()

    def _set_narrow(self, narrow):
        self._narrow = narrow
        self.header.set_title_widget(self.window_title if narrow else self.switcher)
        self.switcher_bar.set_reveal(narrow)
        self.status_label.set_visible(not narrow)   # the title shows it then
        self.live_strip.set_orientation(Gtk.Orientation.VERTICAL if narrow
                                        else Gtk.Orientation.HORIZONTAL)
        self.live_divider.set_orientation(Gtk.Orientation.HORIZONTAL if narrow
                                          else Gtk.Orientation.VERTICAL)

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

    def _build_tuning_view(self):
        """Live input on top, the settings below in tabs."""
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "start", "end"):
            getattr(top, f"set_margin_{side}")(12)
        top.append(self._build_live_strip())

        tab_row = Gtk.Box(spacing=2)
        tab_row.add_css_class("tab-row")
        self.tabs = Gtk.Stack(vexpand=True)
        self.tabs.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        buttons = {}
        for key, title in TABS:
            button = Gtk.ToggleButton(label=_(title))
            button.add_css_class("flat")
            button.add_css_class("tab")
            if buttons:
                button.set_group(next(iter(buttons.values())))
            button.connect("toggled", lambda b, k=key: b.get_active() and
                           self.tabs.set_visible_child_name(k))
            buttons[key] = button
            tab_row.append(button)
            self.tabs.add_named(self._build_tab(key), key)
        buttons[TABS[0][0]].set_active(True)
        self.tabs.connect("notify::visible-child-name", lambda s, _p:
                          buttons[s.get_visible_child_name()].set_active(True))
        self.status_label = Gtk.Label(hexpand=True, xalign=1)
        self.status_label.set_ellipsize(3)      # Pango.EllipsizeMode.END
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_end(6)
        tab_row.append(self.status_label)
        top.append(tab_row)

        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900)
        clamp.set_child(top)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.append(clamp)
        view.append(self.tabs)
        return view

    def _build_tab(self, tab):
        page, column = self._scroll_page()
        if tab == "pedals":
            self._build_live_pedals(column)
        grids = {}
        for s in SETTINGS:
            if s["page"] != tab:
                continue
            if s["group"] not in grids:
                grids[s["group"]] = self._tile_grid()
                if s["group"] in ADVANCED_GROUPS:
                    self._advanced_group(column, s["group"], grids[s["group"]])
                else:
                    self._section(column, _(s["group"]))
                    column.append(grids[s["group"]])
            tile = SettingTile(s, self._on_setting_changed)
            self.rows[s["id"]] = tile
            if s.get("quick"):
                tile.add_extra(self._quick_angle_buttons(s))
            grids[s["group"]].append(tile.row)
        for tile in self.rows.values():
            if tile.setting["page"] == tab and tile.setting["group"] in ADVANCED_GROUPS:
                tile.value_label.connect("notify::label", lambda *a: [
                    update() for update in self._group_summaries])
        return page

    def _advanced_group(self, column, group, grid):
        """A folded group: one line with its values, opened by a click."""
        button = Gtk.Button()
        button.add_css_class("card")
        button.add_css_class("advanced-toggle")
        button.set_margin_top(12)
        line = Gtk.Box(spacing=12)
        arrow = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        title = Gtk.Label(label=_("{group} (advanced)").format(group=_(group)))
        title.add_css_class("heading")
        summary = Gtk.Label(xalign=0, hexpand=True)
        summary.set_ellipsize(3)                # Pango.EllipsizeMode.END
        summary.add_css_class("dim-label")
        for w in (arrow, title, summary):
            line.append(w)
        button.set_child(line)
        revealer = Gtk.Revealer(child=grid)
        grid.set_margin_top(12)
        revealer.set_reveal_child(group in self._open_groups)
        arrow.set_from_icon_name("pan-down-symbolic" if group in self._open_groups
                                 else "pan-end-symbolic")

        def toggle(_button):
            show = not revealer.get_reveal_child()
            revealer.set_reveal_child(show)
            arrow.set_from_icon_name("pan-down-symbolic" if show else "pan-end-symbolic")
            (self._open_groups.add if show else self._open_groups.discard)(group)

        def update(*_args):
            tiles = [t for t in self.rows.values() if t.setting["group"] == group]
            summary.set_label(" · ".join(
                f"{_(t.setting['label'])} {t.value_label.get_label()}" for t in tiles))

        button.connect("clicked", toggle)
        button.update_summary = update
        self._group_summaries.append(update)
        column.append(button)
        column.append(revealer)

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
    # About
    # -----------------------------------------------------------------------

    def _show_about(self):
        props = dict(
            application_name="Understeer",
            application_icon="io.github.JamDaBam.Understeer",
            version=session_log.version(),
            developer_name="JamDaBam",
            website="https://github.com/JamDaBam/Understeer",
            issue_url="https://github.com/JamDaBam/Understeer/issues",
            license_type=Gtk.License.GPL_3_0,
            debug_info=report.system_info(self.report_details()),
            debug_info_filename="understeer-debug.txt")
        if hasattr(Adw, "AboutDialog"):         # Libadwaita 1.5
            about = Adw.AboutDialog(**props)
        else:
            about = Adw.AboutWindow(transient_for=self, modal=True, **props)
        about.add_acknowledgement_section(_("Started as a fork of"), [
            "SimPower (FanatecLinux) by BenryBoi "
            "https://github.com/BenryBoi/FanatecLinux"])
        about.add_acknowledgement_section(_("Kernel driver"), [
            "hid-fanatecff by gotzl https://github.com/gotzl/hid-fanatecff"])
        if isinstance(about, Adw.Window):
            about.present()
        else:
            about.present(self)

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
        add("preferences", lambda *a: self._show_preferences())
        add("about", lambda *a: self._show_about())
        add("report", lambda *a: ReportDialog(self))
        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.preferences", ["<Control>comma"])
        add("apply-preset", lambda a, p: self._apply_preset(p.get_string()),
            GLib.VariantType.new("s"))
        self.undo_action = add("undo-all", lambda *a: self._undo_all())
        self.update_action = add("update-preset",
                                 lambda *a: self._update_active_preset())
        self.update_action.set_enabled(False)

    def _apply_layout(self):
        compact = self.layout == "compact"
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        for tile in self.rows.values():
            tile.set_compact(compact)

    def _set_layout(self, layout):
        self.layout = layout
        config.set_setting("layout", layout)
        self._apply_layout()

    def _set_language(self, language):
        config.set_setting("language", language)
        i18n.set_language(language)
        # Wait until the language menu has finished its signal: it is
        # replaced as well.
        GLib.idle_add(lambda: self.relocalize() and False)

    def _select_page(self, key):
        """Show a view: "tuning", "presets" or "games"."""
        self.views.set_visible_child_name(key)

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
            self._detect_device()

    def _detect_device(self):
        """Look for the wheel base again, e.g. after it was switched on."""
        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self._refresh_device_state()
        self._start_inputs()
        if self.device_path:
            GLib.timeout_add(INPUT_SETTLE_MS, lambda: self._start_inputs() and False)

    def _check_plugged(self):
        """Notice a wheel base being switched on, off or plugged in again.

        Plugging in again gives the device folder a new name, so a gone
        folder means: look again. A new device counts only once the driver
        has made its settings (FF).
        """
        self._plug_tick = (getattr(self, "_plug_tick", 0) + 1) % PLUG_TICKS
        if self._plug_tick:
            return False
        if self.device_path and os.path.isdir(self.device_path):
            return False
        found = find_device_path()
        if self.device_path is None and (
                found is None or not os.path.exists(os.path.join(found, "FF"))):
            return False
        if found is None:
            log.info("Wheel base %s is gone", self.device_path)
        else:
            log.info("Wheel base found: %s", found)
        had_device = self.device_path is not None
        self._detect_device()
        if self.device_path:
            self._notify(_("Wheel base connected"))
        elif had_device:
            self._notify(_("Wheel base disconnected"))
        return True

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
            message = _("Could not write: {names}").format(names=names)
            self._notify(message, _("Report…"),
                         lambda: ReportDialog(self, title=message))
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
        self.status_label.set_label(text)

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

        While the preferences are open, messages show there instead.
        """
        if self.prefs.get_visible():
            toast = Adw.Toast(title=text, timeout=3, use_markup=False)
            if button:
                toast.set_button_label(button)
                toast.connect("button-clicked", lambda t: action())
            self.prefs.add_toast(toast)
            return
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
        if self._check_plugged() or not self.device_path or self._live_timer:
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
    # Games
    # -----------------------------------------------------------------------

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
        running = {g["game"] for g in self.games_page.games
                   if games.is_running(g["game"], running)}
        started, stopped = self._game_watch.update(running)
        self.games_page.set_running(self._game_watch.running)
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
        log.error("%s: %s", heading, body)
        d = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        d.add_response("report", _("Report…"))
        d.add_response("ok", _("OK"))
        d.set_default_response("ok")
        d.connect("response", lambda d, r: r == "report" and
                  ReportDialog(self, title=heading, what=body,
                               parent=self.prefs if self.prefs.get_visible() else None))
        d.present()

    def report_details(self):
        """What a problem report needs to know about this window."""
        details = [("GTK", "{}.{}.{}".format(Gtk.get_major_version(),
                                             Gtk.get_minor_version(),
                                             Gtk.get_micro_version())),
                   ("Libadwaita", "{}.{}.{}".format(Adw.get_major_version(),
                                                    Adw.get_minor_version(),
                                                    Adw.get_micro_version()))]
        if self.device_path:
            details += [("Wheel base", f"{model_id(self.device_path)} "
                         f"({'writable' if self.writable else 'no write access'})"),
                        ("Access", describe_access(self.device_path))]
        else:
            details.append(("Wheel base", "not found"))
        steering = self.inputs.steering_axis()
        details.append(("Input", "{} axes, steering {}{}".format(
            len(self.inputs.axes), "found" if steering else "not found",
            f", no access to {len(self.inputs.denied)} devices"
            if self.inputs.denied else "")))
        details.append(("Rumble", "not available" if not self.rumble_path else
                        "available, " + ("writable" if os.access(self.rumble_path, os.W_OK)
                                         else "not writable")))
        details.append(("Language / layout", "{} / {}".format(
            config.get_setting("language") or "system", self.layout)))
        return details
