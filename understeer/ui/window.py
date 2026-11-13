"""The main window.

Parts of it live in their own modules as mixins: the live input display
(live_input.py), presets (presets.py) and the preferences (preferences.py).
"""
import logging
import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .. import (APP_ICON, APP_ID, capabilities, config, device_info, display,
                games, releases, report)
from .. import log as session_log
from ..device import (RIMS, TuningMode, can_write, describe_access,
                      find_device_path, find_rumble_path, install_udev_rule,
                      join_group, model_id, permission_hint, read_int,
                      read_slot, slots_in_menu, steering_range, wheel_info,
                      write_values, STANDARD_SLOTS)
from .. import i18n
from ..i18n import _
from ..inputs import InputMonitor
from ..settings import (ADVANCED_GROUPS, MAX_SLOT, SETTINGS, SETTINGS_BY_ID,
                        SLOTS, TABS, VIEWS)
from . import layout, toolkit_versions
from .games_page import GamesPage
from .live_input import LiveInputMixin
from .preferences import LAYOUTS, PRESET_ON_DISPLAY, PreferencesMixin
from .presets import PresetsMixin
from .update_check import UpdateCheckMixin
from .diagnostics_page import DiagnosticsPage
from .report_dialog import ReportDialog
from .setting_tile import SettingTile
from .dates import date_text
from .names import label_factory, name_label, short
from .trash_dialog import TrashMixin

LIVE_DELAY_MS = 120
WATCH_MS = 250       # how often to look for changes made on the wheelbase
PLUG_TICKS = 4       # ... and every 4th time (1 s) for a wheelbase plugged in or out
CAPS_TICKS = 8       # ... and every 8th time (2 s) for the hardware functions
INPUT_SETTLE_MS = 2000  # udev may give access to a new input device a bit later
GAMES_WATCH_MS = 2000   # how often to look for started games
DIALOG_MIN_WIDTH = 720  # big dialogs: About, What's New, Preferences, ...
DIALOG_MIN_HEIGHT = 680
DIALOG_MAX_WIDTH = 1000
TOAST_MS = 3000         # how long a message stays
TOAST_BUTTON_MS = 10000  # ... with a button such as Undo, to have time to click
TOAST_CHARS = 50        # a longer message goes on two lines

log = logging.getLogger("understeer.window")


class TunerWindow(LiveInputMixin, PresetsMixin, PreferencesMixin, TrashMixin,
                  UpdateCheckMixin, Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Understeer")
        self.set_default_size(1060, 780)
        layout = config.get_text("layout")
        self.layout = layout if layout in LAYOUTS else "full"

        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self.wheel = {}          # see device.wheel_info()
        self.capabilities = None  # see capabilities.detect()
        # Text Understeer last wrote to the display, None if unknown
        self._display_shown = self._display_text_sent = None
        self.tuning_mode = TuningMode()     # Standard or Advanced tuning menu
        # Steering angle of the wheelbase: largest angle, value for Auto
        self.angle_max, self.angle_auto = SETTINGS_BY_ID["SEN"]["hi"], None
        self._in_background = False     # window hidden, still watching games
        self._shut_down = False
        self._news = []                 # versions still to show after an update
        self._save_dialog = None        # asking to save the active preset

        self.rows = {}           # attr id -> SettingTile
        self.pending = {}        # attr id -> value waiting to be written
        self.device_values = {}  # attr id (and "SLOT") -> last value read or written
        self._toast = None       # shown message, replaced by the next one
        self._toast_timer = 0
        self._toast_action = None   # what its button does
        self._live_timer = 0

        self.model = None           # wheelbase model, e.g. "0EB7:0020"
        self.slot_names = {}        # slot number -> name chosen by the user
        self.standard_slot_names = {}   # ... in the Standard menu (0 Auto, 1 Custom)
        self.slot_count = SLOTS     # slots 1-5 of the Advanced menu
        self._slot_list = []        # slot numbers in the slot menu, 0 = Auto
        self._mode_slots = {}       # mode (advanced) -> slot last used in it
        self._prefs_toast = None    # the message shown in the preferences
        self._filling_slot_names = False

        self.active_preset = None   # path of the last applied/saved preset
        self.active_values = {}     # values stored in that preset
        # Which preset is active in which slot, see _follow_slot()
        self._slot_presets = {}     # (advanced, slot) -> preset path
        self._preset_slot = None    # (advanced, slot) the active preset is in
        self._preset_names = {}     # preset path -> name, see _rebuild_preset_menu

        self.inputs = InputMonitor(self._on_input, self._on_input_gone)
        self.axis_roles = config.load_axis_roles()
        self.roles_before_hidden = config.load_roles_before_hidden()
        self.show_hidden_axes = False
        self.axis_widgets = {}   # axis key -> (level bar, value label, max label)
        self._input_timer = 0
        # Pages of the Diagnostics view, see DiagnosticsPage
        self.diagnostics_page = None
        self.axis_diagnostics = self.button_diagnostics = None
        self.led_diagnostics = None
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
        GLib.idle_add(lambda: self._announce_update() and False)
        GLib.idle_add(lambda: self._start_update_check() and False)
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
        waiting for Apply, the shown values (without a wheelbase there is
        nothing to read them from again), the shown view and tab, the
        preferences window with its page, and the shown message.
        """
        self.flush_live()
        values = {a: r.get() for a, r in self.rows.items()}
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

        if not self.device_path:
            for attr_id, row in self.rows.items():
                if attr_id in values:
                    row.set(values[attr_id])
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
        app = self.get_application()
        if app is not None and hasattr(app, "tray"):
            app.tray.relocalize()           # menu texts in the new language
        log.info("Window rebuilt; kept preset %s, %d unapplied changes",
                 active[0].name if active[0] else "none", len(self.pending))

    def _on_close(self, window):
        app = self.get_application()
        if not self._shut_down and app is not None and app.runs_in_background():
            self.go_to_background()
            return True                     # keep running, without window
        if not self._shut_down and app is not None and self._unsaved_preset_changes():
            app.quit_all()                  # asks to save the preset first
            return True
        self.shut_down()
        return False

    def shut_down(self):
        """Stop watching for good, before quitting."""
        if self._shut_down:
            return
        self._shut_down = True
        self.diagnostics_page.leave()
        self.flush_live()
        self.games_page.key_entry.flush()   # an API key still being typed
        GLib.source_remove(self._watch)
        GLib.source_remove(self._games_watch)
        self.inputs.stop()

    # -----------------------------------------------------------------------
    # Running in the background
    # -----------------------------------------------------------------------
    def go_to_background(self, announce=True):
        """Hide the window but keep applying the presets of games.

        Watching the wheelbase and the games goes on; the live input
        display stops, nobody sees it.
        """
        self.diagnostics_page.leave()
        self.flush_live()
        self.games_page.key_entry.flush()
        self._in_background = True
        self.inputs.stop()
        self.set_visible(False)
        log.info("Running in the background")
        if announce and not config.get_setting("background_hint_shown"):
            config.set_setting("background_hint_shown", True)
            self._desktop_notify(
                "background", _("Understeer runs in the background"),
                _("It applies the presets of your games when they start. "
                  "Open or quit it with its icon in the system tray."))

    def _desktop_notify(self, key, title, body):
        """A notification of the desktop; clicking it opens the window."""
        note = Gio.Notification.new(title)
        note.set_body(body)
        # Without an installed .desktop file the desktop knows no icon
        if APP_ICON.is_file():
            note.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(str(APP_ICON))))
        self.get_application().send_notification(key, note)

    def show_again(self, token=None):
        """Show the window (again). token: Wayland activation token."""
        if token:
            self.set_startup_id(token)
        if self._in_background:
            self._in_background = False
            log.info("Window shown again")
            self._start_inputs()
            self.diagnostics_page.shown_changed()
            # Not over changes that wait for Apply, e.g. the preset of a
            # game that started meanwhile
            if not self.pending:
                self._load_from_device()
        self.present()
        GLib.idle_add(lambda: self._show_news() and False)   # after an update
        GLib.idle_add(lambda: self._start_update_check() and False)

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
        # Slot names are the user's: short on the button, whole in the list
        self.slot_dropdown.set_factory(label_factory(chars=16))
        self.slot_dropdown.set_list_factory(label_factory(in_list=True))
        self.slot_dropdown.set_tooltip_text(
            _("Tuning slot on the wheelbase that is being edited"))
        self._slot_handler = self.slot_dropdown.connect(
            "notify::selected", lambda *a: self._on_slot_selected())
        header.pack_start(self.slot_dropdown)

        # The active preset's name, ending in "…" when long
        self.preset_label = name_label(Gtk.Label(label=_("Presets")), chars=24)
        self.preset_button = Gtk.MenuButton(
            child=self.preset_label, always_show_arrow=True)
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
        app.append(_("What's New"), "win.whats-new")
        app.append(_("About Understeer"), "win.about")
        menu.append_section(None, app)
        quit_section = Gio.Menu()
        quit_section.append(_("Quit"), "app.quit")
        menu.append_section(None, quit_section)
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.undo_btn.set_tooltip_text(
            _("Undo all changes since the values were loaded or a preset "
              "was applied"))
        self.undo_btn.set_action_name("win.undo-all")
        header.pack_end(self.undo_btn)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", lambda b: self._on_banner_button())
        content_view.add_top_bar(self.banner)
        self._build_update_banner(content_view)

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

        old = self.diagnostics_page
        if old is not None:
            old.leave()         # no LED test goes on in the old widgets
        self.diagnostics_page = DiagnosticsPage(self, old and old.tab)
        builders = {"tuning": self._build_tuning_view,
                    "presets": self._build_presets_page,
                    "games": self._build_games_page,
                    "diagnostics": lambda: self.diagnostics_page.view}
        for key, title, icon in VIEWS:
            self.views.add_titled_with_icon(builders[key](), key, _(title), icon)
        self._build_preferences()

        self._rebuild_preset_menu()
        # Choose view and tab before the content is shown, so a rebuild
        # doesn't show the first ones for a moment.
        self.views.set_visible_child_name(view or VIEWS[0][0])
        # Choosing tiles ends on another page
        self.views.connect("notify::visible-child-name",
                           lambda *a: self._stop_choosing())
        self.views.connect("notify::visible-child-name",
                           lambda *a: self.diagnostics_page.shown_changed())
        self.diagnostics_page.shown_changed()
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

    def _build_tuning_view(self):
        """Live input on top, the settings below in tabs."""
        top = layout.margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6),
                             12, ("top", "start", "end"))
        top.append(self._build_live_strip())

        self.tabs = Gtk.Stack(vexpand=True)
        self.tabs.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        tab_row = layout.tab_row(self.tabs, [
            (key, _(title), self._build_tab(key)) for key, title in TABS])
        self.status_label = Gtk.Label(hexpand=True, xalign=1)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
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
        page, column = layout.scroll_page()
        if tab == "pedals":
            self._build_live_pedals(column)
        grids = {}
        for s in SETTINGS:
            if s["page"] != tab:
                continue
            if s["group"] not in grids:
                grids[s["group"]] = layout.tile_grid()
                if s["group"] in ADVANCED_GROUPS:
                    self._advanced_group(column, s["group"], grids[s["group"]])
                else:
                    layout.section(column, _(s["group"]))
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
        summary.set_ellipsize(Pango.EllipsizeMode.END)
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
        auto.set_tooltip_text(_("Use the maximum range of the wheelbase"))
        auto.connect("clicked", lambda b: self._set_setting(setting["id"], 0))
        box.append(auto)
        return box

    # -----------------------------------------------------------------------
    # About
    # -----------------------------------------------------------------------

    def _show_about(self):
        props = dict(
            application_name="Understeer",
            application_icon=APP_ID,
            version=session_log.version(),
            developer_name="JamDaBam",
            website=report.REPO_URL,
            issue_url=report.REPO_URL + "/issues",
            license_type=Gtk.License.GPL_3_0,
            debug_info=report.system_info(self.report_details()),
            debug_info_filename="understeer-debug.txt")
        # No release notes here: What's New in the main menu has them all.
        about = self.fit_dialog(Adw.AboutDialog(**props))
        about.add_link(_("All Versions on GitHub"),
                       report.REPO_URL + "/releases")

        def on_link(_dialog, uri):
            # "Report an Issue" opens our report window, which fills in the
            # details, instead of GitHub; on top, so closing it goes back
            # to About
            if uri != props["issue_url"]:
                return False
            ReportDialog(self)
            return True

        about.connect("activate-link", on_link)
        by = _("{name} by {author}")
        about.add_acknowledgement_section(_("Started as a fork of"), [
            by.format(name="SimPower (FanatecLinux)", author="BenryBoi")
            + " https://github.com/BenryBoi/FanatecLinux"])
        about.add_acknowledgement_section(_("Kernel driver"), [
            by.format(name="hid-fanatecff", author="gotzl")
            + " https://github.com/gotzl/hid-fanatecff"])
        about.present(self)

    def _announce_update(self):
        """After an update, show what is new: right away, or when the
        window is opened if Understeer started in the background. It only
        counts as seen once it was shown."""
        current = releases.plain_version(session_log.version())
        last = config.get_text("last_version")
        used = getattr(self.get_application(), "used_before", True)
        if releases.should_announce(current, last, used):
            log.info("Updated from %s to %s", last or "an older version", current)
            self._news = releases.versions_since(last, current)
            if self.get_visible() and not self._in_background:
                self._show_news()
        # Only released versions are remembered, not ones run from git.
        elif last != current and releases.has_notes(current):
            config.set_setting("last_version", current)

    def _show_news(self):
        """Show the news of an update that is still waiting to be seen."""
        if not self._news:
            return
        versions, self._news = self._news, []
        self._show_whats_new(versions)
        config.set_setting("last_version", versions[0])

    def _dialog_size(self):
        """The size of every big dialog: the same for all, growing with the
        window, so they are easy to read on a big screen. Libadwaita
        shrinks them in a small window."""
        width = self.get_width() or self.get_default_size()[0]
        height = self.get_height() or self.get_default_size()[1]
        return (max(DIALOG_MIN_WIDTH, min(DIALOG_MAX_WIDTH, int(width * 0.7))),
                max(DIALOG_MIN_HEIGHT, int(height * 0.85)))

    def fit_dialog(self, dialog):
        """Give a big dialog the size of _dialog_size(), before showing it."""
        width, height = self._dialog_size()
        dialog.set_content_width(width)
        dialog.set_content_height(height)
        return dialog

    def _show_whats_new(self, new=()):
        """The notes of every version, newest first, in a big dialog; the
        versions in new (those after an update) are marked as new."""
        current = releases.plain_version(session_log.version())
        versions = releases.all_versions()
        if releases.has_notes(current):         # not newer ones, e.g. from git
            versions = versions[versions.index(current):]
        language = i18n.current_language()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=18, margin_bottom=24, margin_start=24,
                         margin_end=24)
        for n, version in enumerate(versions):
            title = Gtk.Label(label=f"Understeer {version}", xalign=0,
                              css_classes=["title-2"])
            heading = Gtk.Box(spacing=12)
            heading.append(title)
            if version in new:
                badge = Gtk.Label(label=_("New"), valign=Gtk.Align.CENTER,
                                  css_classes=["new-badge"])
                heading.append(badge)
            if n:
                heading.set_margin_top(18)
            column.append(heading)
            date = releases.release_date(version)
            if date:
                column.append(Gtk.Label(label=date_text(date), xalign=0,
                                        css_classes=["dim-label"]))
            for tag, content in releases.blocks(version, language):
                for text in (content if tag != "p" else [content]):
                    line = Gtk.Box(spacing=10)
                    if tag != "p":
                        line.append(Gtk.Label(label="•", valign=Gtk.Align.START,
                                              css_classes=["accent"]))
                    line.append(Gtk.Label(label=text, xalign=0, wrap=True,
                                          hexpand=True))
                    column.append(line)
        dialog = self.fit_dialog(Adw.Dialog(title=_("What's New")))
        clamp = Adw.Clamp(child=column, maximum_size=820)
        scroll = Gtk.ScrolledWindow(child=clamp,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar = Adw.ToolbarView(content=scroll)
        toolbar.add_top_bar(Adw.HeaderBar())
        dialog.set_child(toolbar)
        log.info("Showing what's new, new: %s", ", ".join(new) or "none")
        dialog.present(self)

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
        add("whats-new", lambda *a: self._show_whats_new())
        add("report", lambda *a: ReportDialog(self))
        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.preferences", ["<Control>comma"])
        add("apply-preset", lambda a, p: self._ask_apply_preset(p.get_string()),
            GLib.VariantType.new("s"))
        for name, cb in (
                ("show-preset", self._show_preset),
                ("delete-preset", lambda p: self._delete_presets([p])),
                ("export-preset", self._export_preset),
                ("copy-preset-text", lambda p: self._copy_preset(p, False)),
                ("copy-preset-json", lambda p: self._copy_preset(p, True))):
            add(name, lambda a, p, cb=cb: cb(p.get_string()),
                GLib.VariantType.new("s"))
        add("import-preset", lambda *a: self._ask_import_preset())
        add("paste-preset", lambda *a: self._ask_paste_preset())
        self.undo_action = add("undo-all", lambda *a: self._undo_all())
        self.update_action = add("update-preset",
                                 lambda *a: self._update_active_preset())
        self.update_action.set_enabled(False)
        add("remove-preset-from-slot", lambda *a: self._remove_preset_from_slot())

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

    def _stop_choosing(self):
        for selection in (getattr(self, "preset_selection", None),
                          getattr(getattr(self, "games_page", None), "selection", None)):
            if selection is not None:
                selection.stop()

    def _select_page(self, key):
        """Show a view: "tuning", "presets" or "games"."""
        self.views.set_visible_child_name(key)

    # -----------------------------------------------------------------------
    # Device state
    # -----------------------------------------------------------------------
    def _refresh_device_state(self):
        if self.device_path:
            log.info("Wheelbase %s (model %s), %s", self.device_path,
                     model_id(self.device_path),
                     "writable" if self.writable else "no write access")
            log.info("Access: %s", describe_access(self.device_path))
            log.info("Rumble: %s", self.rumble_path or "not available")
        else:
            log.info("No wheelbase found")
        self._check_capabilities()
        self.wheel = wheel_info(self.device_path) if self.device_path else {}
        if self.wheel.get("base_firmware") is not None or self.wheel.get("rim_id"):
            log.info("Wheel: %s", self._wheel_text())
        self._apply_steering_range()
        self._show_wheel_info()
        self._update_banner()
        self.slot_dropdown.set_sensitive(bool(self.device_path))
        self.rumble_group.set_sensitive(bool(self.rumble_path))
        self._load_slot_names()
        self._load_from_device()

    def _apply_steering_range(self):
        """Let the steering angle go as far as the wheelbase allows."""
        if self.device_path:
            self.angle_max, self.angle_auto = steering_range(self.device_path)
            log.info("Steering angle up to %d°, Auto reported as %s",
                     self.angle_max, self.angle_auto)
        else:
            self.angle_max, self.angle_auto = SETTINGS_BY_ID["SEN"]["hi"], None
        if "SEN" in self.rows:
            # Without a wheelbase Auto can be chosen, e.g. for a preset
            self.rows["SEN"].set_upper(self.angle_max, not self.device_path
                                       or self.angle_auto is not None)

    def _read_value(self, attr_id):
        """A setting from the wheelbase as the app shows it: Auto of the
        steering angle as 0, the value the app writes for it."""
        value = read_int(self._attr_file(attr_id))
        if attr_id == "SEN" and value is not None and value == self.angle_auto:
            return 0
        return value

    def _load_slot_names(self):
        self.model = model_id(self.device_path) if self.device_path else None
        self.slot_names = config.load_slot_names(self.model) if self.model else {}
        self.standard_slot_names = (config.load_slot_names(
            self._standard_names_key()) if self.model else {})
        self._show_slot_name_rows()
        self._filling_slot_names = True
        try:
            for n, entry in self.slot_name_rows.items():
                entry.set_text(self.slot_names.get(n, ""))
            for n, entry in self.standard_name_rows.items():
                entry.set_text(self.standard_slot_names.get(n, ""))
        finally:
            self._filling_slot_names = False
        self.slot_names_group.set_sensitive(self.model is not None)
        self.standard_names_group.set_sensitive(self.model is not None)
        self._update_slot_dropdown()

    def _standard_names_key(self):
        """Where the names of the slots of the Standard menu are kept: next
        to those of the model, under a key of their own."""
        return f"{self.model} standard"

    def _show_slot_name_rows(self):
        """One name row per slot of the connected wheelbase, and one for
        its Auto setup (slot 0)."""
        for n in range(0, self.slot_count + 1):
            if n not in self.slot_name_rows:
                entry = Adw.EntryRow(title=_("Auto") if n == 0
                                     else _("Slot {n}").format(n=n))
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

    def _menu_slots(self):
        """The slots the slot menu lists, in its order: those of the tuning
        menu in its mode, and the one the wheelbase is on if it isn't one
        of them (Auto in Advanced mode)."""
        slots = slots_in_menu(self.tuning_mode.advanced, self.slot_count)
        current = self.device_values.get("SLOT")
        if current is not None and current not in slots:
            slots.append(current)
        return slots

    def _show_device_slot(self):
        """Select the slot the wheelbase reports in the slot menu.

        Returns the slot number, 0 for the Auto setup of the wheelbase, or
        None if the slot can't be read.
        """
        slot = read_slot(self._attr_file("SLOT"))
        if slot is None or not 0 <= slot <= MAX_SLOT:
            return None
        self.device_values["SLOT"] = slot
        self._update_slot_dropdown()
        return slot

    def _on_slot_name_changed(self, n, text, standard=False):
        if self._filling_slot_names or self.model is None:
            return
        names = self.standard_slot_names if standard else self.slot_names
        config.save_slot_name(self._standard_names_key() if standard
                              else self.model, n, text)
        if text.strip():
            names[n] = text.strip()
        else:
            names.pop(n, None)
        self._update_slot_dropdown()

    def _slot_label(self, n):
        """The name of slot n in the mode the tuning menu is in."""
        if self.tuning_mode.advanced is False and n in STANDARD_SLOTS:
            return self.standard_slot_names.get(n) or (
                _("Auto") if n == 0 else _("Custom"))
        return self.slot_names.get(n) or (
            _("Auto") if n == 0 else _("Slot {n}").format(n=n))

    def _update_slot_dropdown(self):
        """List the slots of the menu and select the one the wheelbase is
        on. Changing the list must not switch the slot on the wheelbase."""
        self._slot_list = self._menu_slots()
        labels = [self._slot_label(n) for n in self._slot_list]
        model = self.slot_dropdown.get_model()
        current = self.device_values.get("SLOT")
        with self.slot_dropdown.handler_block(self._slot_handler):
            if [model.get_string(i) for i in range(model.get_n_items())] != labels:
                model.splice(0, model.get_n_items(), labels)
            if current in self._slot_list:
                self.slot_dropdown.set_selected(self._slot_list.index(current))

    def _update_banner(self):
        if not self.device_path:
            self.banner.set_title(
                _("No Fanatec wheelbase found. Check that it is switched on "
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
        """Look for the wheelbase again, e.g. after it was switched on."""
        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self.writable = bool(self.device_path) and can_write(self.device_path)
        self._refresh_device_state()
        self._start_inputs()
        if self.device_path:
            GLib.timeout_add(INPUT_SETTLE_MS, lambda: self._start_inputs() and False)

    def _check_plugged(self):
        """Notice a wheelbase being switched on, off or plugged in again.

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
            log.info("Wheelbase %s is gone", self.device_path)
        else:
            log.info("Wheelbase found: %s", found)
        had_device = self.device_path is not None
        self._detect_device()
        if self.device_path:
            if not self._apply_running_game_preset():
                self._notify(_("Wheelbase connected"))
        elif had_device:
            self._notify(_("Wheelbase disconnected"))
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
            value = self._read_value(attr_id)
            self.device_values[attr_id] = value
            row.set_supported(value is not None)
            if value is not None:
                row.set(value)
            row.set_baseline(row.get())
        self.device_values["SLOT"] = self._show_device_slot()
        mode = self.tuning_mode
        if (self.device_values["SLOT"] is not None and mode.advanced is not None
                and not mode.switching):
            self._mode_slots[mode.advanced] = self.device_values["SLOT"]
        log.info("Read from wheelbase (slot %s): %s",
                 self.device_values["SLOT"] or "auto"
                 if self.device_values["SLOT"] is not None else None,
                 " ".join(f"{a}={v}" for a, v in self.device_values.items()
                          if a != "SLOT" and v is not None))
        missing = [a for a, v in self.device_values.items() if v is None]
        if missing:
            log.info("Not supported by this wheelbase: %s", ", ".join(missing))
        self._set_status(_("In sync with device"))
        self._follow_slot()
        self._update_preset_indicator()

    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------
    def _on_setting_changed(self, row):
        self._update_preset_indicator()
        if not self.device_path or not row.row.get_sensitive():
            return
        self._queue(row.setting["id"], row.get())
        if self.writable:
            self._set_status(_("Writing…"))
            if self._live_timer:
                GLib.source_remove(self._live_timer)
            self._live_timer = GLib.timeout_add(LIVE_DELAY_MS, self._flush_live)
        else:
            self._update_pending_bar()

    def _queue(self, attr_id, value):
        """A value to write. Where every write asks for a password, one
        the wheelbase has already is left out, so that nothing is asked
        for it, e.g. after Discard or for a preset it matches."""
        if self.writable or value != self.device_values.get(attr_id):
            self.pending[attr_id] = value
        else:
            self.pending.pop(attr_id, None)

    def _flush_live(self):
        self._live_timer = 0
        self._flush_pending()
        return GLib.SOURCE_REMOVE

    def _flush_pending(self):
        """Write the pending changes; returns the names that failed."""
        if not self.pending:
            return []
        written = list(self.pending)
        writes = {self._attr_file(a): v for a, v in self.pending.items()}
        self.pending = {}
        failed = write_values(writes)
        # What the device holds now; it may round, e.g. vibration to tens.
        for attr_id in written:
            self.device_values[attr_id] = self._read_value(attr_id)
        self._update_pending_bar()
        if failed:
            names = ", ".join(os.path.basename(p) for p in failed)
            self._set_status(_("Write failed: {names}").format(names=names))
            log.warning("Shown to the user: could not write %s", names)
            message = _("Could not write: {names}").format(names=names)
            self._notify(message, _("Report…"),
                         lambda: ReportDialog(self, title=message))
            return [os.path.basename(p) for p in failed]
        self._set_status(_("In sync with device"))
        return []

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
        """Set several settings at once and write them in one go. Returns
        what happened: "written", "pending" (to apply by hand), "failed"
        or "no device", and the names of the settings that failed."""
        for attr_id, value in values.items():
            row = self.rows.get(attr_id)
            if row is None or not row.row.get_sensitive():
                continue
            row.set(value)
            if self.device_path:
                self._queue(attr_id, row.get())
        outcome, failed = "written", []
        if not self.device_path:
            outcome = "no device"
        elif self.writable:
            failed = self._flush_pending()
            if failed:
                outcome = "failed"
                message = None          # _flush_pending told already
        else:
            self._update_pending_bar()
            if self.pending:
                message = pending_message or message
                outcome = "pending"
        self._update_preset_indicator()
        if message:
            self._notify(message)
        return outcome, failed

    def _undo_all(self):
        self._cancel_pending()
        changed = {a: r.baseline for a, r in self.rows.items()
                   if r.baseline is not None and r.get() != r.baseline}
        if changed:
            self._apply_values(changed, _("Changes undone"))

    def _notify(self, text, button=None, action=None):
        """Show a message right away.

        Adw.Toast messages queue up, so changing something quickly (slots,
        presets, ...) would show each message for its full time, one after
        another, and the latest one far too late. Instead there is only one
        message: the next one takes over its text and its time starts again.
        An optional button (e.g. Undo) calls action().

        While the preferences are open, messages show there instead.
        """
        if self._prefs_open():
            # The preferences queue their messages too: the next one
            # replaces the one shown.
            if self._prefs_toast is not None:
                self._prefs_toast.dismiss()
            toast = Adw.Toast(timeout=3, custom_title=self._toast_label(text))
            if button:
                toast.set_button_label(button)
                toast.connect("button-clicked", lambda t: action())
            toast.connect("dismissed", lambda t: setattr(
                self, "_prefs_toast", None) if self._prefs_toast is t else None)
            self._prefs_toast = toast
            self.prefs.add_toast(toast)
            return
        if self._toast is None:
            toast = Adw.Toast.new("")
            toast.set_use_markup(False)     # names are plain text
            toast.set_timeout(0)            # hidden by our own timer
            toast.connect("dismissed", self._on_toast_dismissed)
            toast.connect("button-clicked", self._on_toast_button)
            self._toast_text = self._toast_label("")
            toast.set_custom_title(self._toast_text)
            self._toast = toast
            self.toasts.add_toast(toast)
        self._toast_text.set_label(text)
        self._toast_text.set_width_chars(min(len(text), TOAST_CHARS))
        self._toast.set_button_label(button)
        self._toast_action = action if button else None
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
        self._toast_timer = GLib.timeout_add(
            TOAST_BUTTON_MS if button else TOAST_MS, self._hide_toast)

    @staticmethod
    def _toast_label(text):
        """The text of a message: one line up to TOAST_CHARS characters,
        longer ones on two, so a long name in it can't make it wider than
        the window."""
        label = name_label(Gtk.Label(label=text, justify=Gtk.Justification.CENTER),
                           lines=2, chars=TOAST_CHARS)
        label.set_width_chars(min(len(text), TOAST_CHARS))
        return label

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
        """Follow changes made on the wheelbase itself.

        Switching the slot there reloads everything; a value changed in its
        tuning menu moves the matching control. Values the app wrote itself
        are already in device_values, so they don't count as changes.
        """
        if self._check_plugged():
            return GLib.SOURCE_CONTINUE
        self._caps_tick = (getattr(self, "_caps_tick", 0) + 1) % CAPS_TICKS
        if not self._caps_tick:
            self._check_capabilities()
        if not self.device_path:
            return GLib.SOURCE_CONTINUE
        self._check_wheel()
        if self._check_tuning_mode():
            # The values were read again, or are once the slot is back;
            # until then the old slot is no change made on the wheelbase.
            return GLib.SOURCE_CONTINUE
        if self._live_timer:
            return GLib.SOURCE_CONTINUE
        slot = read_slot(self._attr_file("SLOT"))
        if slot is not None and slot != self.device_values.get("SLOT"):
            discarded = bool(self.pending)
            log.info("Slot changed on the wheelbase: %s -> %s%s",
                     self.device_values.get("SLOT"), slot,
                     ", unapplied changes discarded" if discarded else "")
            self._load_from_device()
            name = short(self._slot_label(slot))
            self._notify(
                _("Slot changed on the wheelbase: {name}. Unapplied changes "
                  "were discarded.").format(name=name) if discarded else
                _("Slot changed on the wheelbase: {name}").format(name=name))
            return GLib.SOURCE_CONTINUE
        if self.pending:            # don't overwrite changes waiting for Apply
            return GLib.SOURCE_CONTINUE
        changed = False
        for attr_id, row in self.rows.items():
            value = self._read_value(attr_id)
            if value is None or value == self.device_values.get(attr_id):
                continue
            log.info("Changed on the wheelbase: %s %s -> %s", attr_id,
                     self.device_values.get(attr_id), value)
            self.device_values[attr_id] = value
            row.set(value)
            changed = True
        if changed:
            self._update_preset_indicator()
        return GLib.SOURCE_CONTINUE

    # -----------------------------------------------------------------------
    # Wheelbase and steering wheel
    # -----------------------------------------------------------------------
    def _rim_name(self, rim_id):
        return RIMS.get(rim_id) or _("Unknown wheel (ID {id})").format(id=rim_id)

    def _wheel_text(self):
        """One line for the log and the report."""
        w = self.wheel
        parts = [f"base {w.get('base') or model_id(self.device_path)}"
                 f", firmware {w.get('base_firmware')}"]
        if w.get("rim_id"):
            parts.append(f"wheel {RIMS.get(w['rim_id'], 'unknown')} "
                         f"(id {w['rim_id']}), firmware {w.get('rim_firmware')}")
        return "; ".join(parts)

    def _check_capabilities(self):
        """Find out again which hardware functions there are and log a
        change, e.g. a wheel with other LEDs put on or pedals plugged in."""
        caps = capabilities.detect()
        if caps != self.capabilities:
            log.info("Hardware functions: %s", capabilities.summary(caps))
            self.capabilities = caps
            self._display_shown = None  # e.g. another wheelbase, or plugged in again
            if self.led_diagnostics is not None:
                self.led_diagnostics.hardware_changed()
        self._update_mode_switch()
        self._update_display_group()

    def _check_wheel(self):
        """Notice a steering wheel being put on or changed."""
        info = wheel_info(self.device_path)
        if info == self.wheel:
            return
        old_rim, self.wheel = self.wheel.get("rim_id"), info
        if info["rim_id"] != old_rim:
            log.info("Wheel: %s", self._wheel_text())
            if info["rim_id"] and old_rim:      # not when first reported
                self._notify(_("Steering wheel changed: {name}").format(
                    name=self._rim_name(info["rim_id"])))
        self._show_wheel_info()

    def _show_wheel_info(self):
        """Fill the Hardware rows in the preferences."""
        if not hasattr(self, "base_row"):
            return
        info = device_info.collect(self.device_path)

        def firmware(item):
            if item.known:
                return _("Firmware {version}").format(version=item.value)
            return _("Firmware: {state}").format(state=self._info_text(item))

        if not self.device_path:
            self.base_row.set_subtitle(_("Not connected"))
        else:
            self.base_row.set_subtitle(" · ".join((
                self._info_text(info["wheelbase"]),
                firmware(info["wheelbase_firmware"]))))
        rim = info["rim"]
        self.rim_row.set_visible(bool(self.device_path))
        self.rim_row.set_subtitle(
            self._info_text(rim) if not rim.known else " · ".join(
                (rim.value, firmware(info["rim_firmware"]))))
        for key, row in self.device_info_rows.items():
            row.set_subtitle(self._info_text(info[key]))

    @staticmethod
    def _info_text(item):
        """An item of device_info.collect() as shown: its value, or why
        there is none."""
        if item.known:
            return item.value
        state = {device_info.UNSUPPORTED: _("Not supported by the installed driver"),
                 device_info.UNAVAILABLE: _("Not available"),
                 device_info.UNKNOWN: _("Unknown")}[item.state]
        note = {device_info.NOT_CONNECTED: _("not connected"),
                device_info.NO_WHEEL: _("no steering wheel reported"),
                device_info.NOT_READABLE: _("could not be read"),
                device_info.NOT_LOADED: _("hid-fanatecff not loaded"),
                device_info.NOT_LISTED: _("not known to Understeer"),
                device_info.NOT_REPORTED: _("not reported by the driver"),
                device_info.INVALID: _("invalid value")}.get(item.note)
        details = [d for d in (item.value, note) if d]
        return f"{state} ({', '.join(details)})" if details else state

    def _copy_device_info(self):
        """Copy what is known about the hardware, read again now, as
        Markdown for a GitHub issue."""
        caps = capabilities.detect()
        text = device_info.report_text(device_info.collect(self.device_path),
                                       caps)
        Gdk.Display.get_default().get_clipboard().set(text)
        log.info("Device information copied")
        self._notify(_("Device information copied"))

    # -----------------------------------------------------------------------
    # Standard or Advanced tuning menu
    # -----------------------------------------------------------------------
    @staticmethod
    def _mode_text(advanced):
        """The mode for the log."""
        return {True: "Advanced", False: "Standard"}.get(advanced, "not readable")

    def _update_mode_switch(self):
        """Show the mode switch in the preferences if the driver offers
        the mode and it can be read, and let it be used if it can be
        written and the wheelbase is ready for it."""
        feature = self.capabilities.get(capabilities.ADVANCED_MODE)
        path = feature.paths[0] if feature and feature.readable else None
        if path != self.tuning_mode.path:
            self.tuning_mode.attach(path)
            if path:
                log.info("Tuning menu: %s",
                         self._mode_text(self.tuning_mode.advanced))
        known = self.tuning_mode.advanced is not None
        self.mode_row.set_visible(known)
        self.standard_names_group.set_visible(known)
        self.mode_row.set_sensitive(known and feature.writable
                                    and not self.tuning_mode.busy)
        self._show_tuning_mode()
        self._update_slot_dropdown()        # the slots of the mode

    def _show_tuning_mode(self):
        """Show the mode the wheelbase is in, unless it is just switching."""
        mode = self.tuning_mode
        if mode.advanced is not None and not mode.switching:
            with self.mode_switch.handler_block(self._mode_handler):
                self.mode_switch.set_active(mode.advanced)
                self.mode_switch.set_state(mode.advanced)

    def _on_mode_chosen(self, advanced):
        """Switched in the app. Only the mode is written: other changes
        are neither written along nor kept over the values of the new mode.
        Returns True: the switch shows the mode as reached only once the
        wheelbase has switched, and can't be used until then."""
        if not self.tuning_mode.path or advanced == self.tuning_mode.advanced:
            return False
        self.flush_live()
        log.info("Switching the tuning menu to %s", self._mode_text(advanced))
        if self.tuning_mode.switch(advanced):
            self._set_status(_("Writing…"))
            self.mode_row.set_sensitive(False)
        else:
            self._mode_not_switched()
        return True

    def _mode_not_switched(self):
        log.warning("Shown to the user: tuning menu not switched")
        message = _("Could not switch the tuning menu")
        self._notify(message, _("Report…"),
                     lambda: ReportDialog(self, title=message))
        self._show_tuning_mode()        # back to the mode that is active
        self._update_pending_bar()      # ... and to the status before

    def _check_tuning_mode(self):
        """Follow the mode: a switch asked for here that the wheelbase made
        or didn't make, and one made on the wheelbase itself. The values
        of the new mode are read again, as after switching the slot.
        Returns True if the mode changed."""
        event = self.tuning_mode.poll()
        if event is None:
            if self.mode_row.get_visible() and not self.mode_row.get_sensitive():
                self._update_mode_switch()      # ready for the next switch?
            return False
        mode = self.tuning_mode
        if event == mode.FAILED:
            self._mode_not_switched()
            self._update_mode_switch()
            return False
        log.info("Tuning menu %s: %s", "switched" if event == mode.SWITCHED
                 else "changed on the wheelbase", self._mode_text(mode.advanced))
        self._show_tuning_mode()
        if self._restore_slot():
            pass                    # the values follow once the slot is back
        elif self.pending:          # not over changes waiting for Apply
            self._update_pending_bar()
        else:
            self._load_from_device()
        self._update_mode_switch()
        if event == mode.CHANGED:
            self._notify(_("Tuning menu changed on the wheelbase: {mode}").format(
                mode=_("Advanced") if mode.advanced else _("Standard")))
        return True

    # -----------------------------------------------------------------------
    # Text on the display of the steering wheel
    # -----------------------------------------------------------------------
    def _display_path(self):
        """The display file if it can be written, else None."""
        caps = self.capabilities
        if caps is None or not caps.can_write(capabilities.DISPLAY):
            return None
        return caps.get(capabilities.DISPLAY).paths[0]

    def _display_problem(self):
        """Why the text in the entry can't be shown, or None."""
        problem = display.check_text(self.display_entry.get_text())
        if problem is None:
            return None
        reason, detail = problem
        if reason == display.EMPTY:
            return _("Enter up to three characters.")
        if reason == display.UNSUPPORTED:
            return _("“{character}” can't be shown on the display.").format(
                character=detail)
        if reason == display.POINT:
            return _("A point or comma has to follow a character.")
        return _("Only three characters fit on the display.")

    def _update_display_group(self):
        """Let the display be used only if the driver offers it and it can
        be written, and Show only for text the display can show."""
        if not hasattr(self, "display_entry"):
            return
        caps = self.capabilities
        feature = caps.get(capabilities.DISPLAY) if caps else None
        usable = bool(feature and feature.writable)
        problem = self._display_problem() if usable else None
        if feature is None:
            status = _("No display output: no wheelbase connected, or the "
                       "driver offers none.")
        elif not usable:
            status = _("No permission to write to the display. The udev rule "
                       "of hid-fanatecff allows it for the members of its "
                       "group, usually “games”.")
        elif problem and self.display_entry.get_text():
            status = problem
        else:
            status = _("Digits, letters except K, M, V, W and X, - _ [ ] and "
                       "a space for a dark position. A point or comma after "
                       "a character lights its point.")
        self.display_status_row.set_subtitle(status)
        failed = (not usable) or bool(problem and self.display_entry.get_text())
        for widget in (self.display_status_row, self.display_entry):
            if failed:
                widget.add_css_class("error")
            else:
                widget.remove_css_class("error")
        self.display_entry.set_sensitive(usable)
        self.display_show_btn.set_sensitive(usable and problem is None)
        self.display_clear_row.set_sensitive(usable)

    def _show_display_text(self):
        """Show was clicked (or Enter pressed in the entry)."""
        path = self._display_path()
        if path is None or self._display_problem() is not None:
            self._update_display_group()
            return
        text = self._display_text_sent = self.display_entry.get_text()
        self._display_written(display.write(path, text),
                              _("Sent to the display: {text}").format(text=text))

    def _clear_display(self):
        path = self._display_path()
        if path is None:
            self._update_display_group()
            return
        self._display_text_sent = display.CLEAR_TEXT
        self._display_written(display.clear(path), _("Display cleared"))

    def _show_preset_name(self, name):
        """After a preset was written, by hand or when a game started, or
        after switching to a slot: the name of its preset on the display,
        if switched on, so the display doesn't keep the name of the preset
        before. The display is cleared for a slot without a preset (name
        None) and a name it can't show. Never in the way of the preset or
        the slot: nothing is told if it can't be written, it is only
        logged."""
        if not config.get_setting(PRESET_ON_DISPLAY, False):
            return
        path = self._display_path()
        if path is None:
            log.info("Preset name not shown: no writable display")
            return
        text = (display.name_text(name) if name is not None else None) \
            or display.CLEAR_TEXT
        if text == self._display_shown:
            return          # Understeer wrote that last: it shows it already
        result = display.write(path, text)
        self._display_shown = text if result == display.OK else None

    def _display_written(self, result, done):
        if result == display.OK:
            self._display_shown = self._display_text_sent
            self._notify(done)
            return
        self._display_shown = None      # unknown what it shows now
        self._notify({
            display.UNAVAILABLE: _("The display is not available any more. "
                                   "Is the wheelbase still connected?"),
            display.DENIED: _("No permission to write to the display"),
        }.get(result, _("Could not write to the display")))
        self._check_capabilities()      # e.g. unplugged meanwhile

    def _restore_slot(self):
        """After switching the mode, here or on the wheelbase, go to the
        slot last used in the new mode, or else stay on the slot from
        before if the new mode has it: a CSL DD goes to its Auto setup
        when switched to Standard. Returns True if a slot is being
        selected."""
        advanced = self.tuning_mode.advanced
        offered = (STANDARD_SLOTS if advanced is False
                   else range(0, SLOTS + 1))       # what the driver can select
        before = self.device_values.get("SLOT")    # read before the switch
        target = self._mode_slots.get(advanced)
        if target not in offered:
            target = before
        if target not in offered or self.pending:
            return False
        if read_slot(self._attr_file("SLOT")) == target:
            return False
        log.info("Back to slot %s after switching the tuning menu",
                 target or "auto")
        if write_values({self._attr_file("SLOT"): target or "auto"}):
            log.warning("Could not go back to slot %s", target or "auto")
            return False
        self.device_values["SLOT"] = target     # not a change made on the wheel
        GLib.timeout_add(150, lambda: self._load_from_device() and False)
        return True

    def _on_slot_selected(self):
        if not self.device_path:
            return
        index = self.slot_dropdown.get_selected()
        if not 0 <= index < len(self._slot_list):
            return
        slot = self._slot_list[index]
        if slot == self.device_values.get("SLOT"):
            return
        self._flush_pending()
        log.info("Switching to slot %s", slot or "auto")
        if write_values({self._attr_file("SLOT"): slot or "auto"}):
            self._notify(
                _("The driver can only switch to slots 1–{n}. Choose this "
                  "slot on the wheelbase.").format(n=SLOTS)
                if slot > SLOTS else _("Could not switch slot"))
            self._update_slot_dropdown()    # back to the slot that is active
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
        old = getattr(self, "games_page", None)
        if old is not None:
            old.replaced = True     # its cover downloads end without saving
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
        dialog = Adw.AlertDialog(
            heading=_("Reset All Tuning Slots?"),
            body=_("All tuning slots stored on the wheelbase will be "
                   "cleared. This cannot be undone."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        dialog.connect("response", lambda d, r: r == "reset" and self._reset_slots())
        dialog.present(self)

    def _reset_slots(self):
        path = self._attr_file("RESET")
        if not os.path.exists(path):
            self._error(_("Not Available"),
                        _("This wheelbase does not support resetting."))
        elif write_values({path: 1}):
            self._notify(_("Could not reset the tuning slots"))
        else:
            GLib.timeout_add(300, lambda: self._load_from_device() and False)

    def _error(self, heading, body):
        log.error("%s: %s", heading, body)
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("report", _("Report…"))
        d.add_response("ok", _("OK"))
        d.set_default_response("ok")
        d.connect("response", lambda d, r: r == "report" and
                  ReportDialog(self, title=heading, what=body))
        d.present(self)

    def report_details(self):
        """What a problem report needs to know about this window."""
        details = list(toolkit_versions().items())
        if self.device_path:
            details += [("Wheelbase", f"{model_id(self.device_path)} "
                         f"({'writable' if self.writable else 'no write access'})"),
                        ("Access", describe_access(self.device_path))]
            details += device_info.report_lines(
                device_info.collect(self.device_path))
        else:
            details.append(("Wheelbase", "not found"))
        steering = self.inputs.steering_axis()
        details.append(("Input", "{} axes, steering {}{}".format(
            len(self.inputs.axes), "found" if steering else "not found",
            f", no access to {len(self.inputs.denied)} devices"
            if self.inputs.denied else "")))
        details.append(("Rumble", "not available" if not self.rumble_path else
                        "available, " + ("writable" if os.access(self.rumble_path, os.W_OK)
                                         else "not writable")))
        caps = self.capabilities or capabilities.detect()
        details += [("Hardware functions", capabilities.summary(caps)),
                    ("Driver generation", capabilities.driver_text(caps))]
        details.append(("Language / layout", "{} / {}".format(
            config.get_text("language") or "system", self.layout)))
        return details
