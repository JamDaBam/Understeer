"""The community preset library: a dialog listing the presets of the
online catalog, each with a read-only page of its details and values.

Browsing only: nothing here applies, imports or saves a preset, and
nothing is written to the wheelbase. Everything in the catalog comes from
others, so it is only ever shown as plain text, never as markup.
"""
import logging

from gi.repository import Adw, GLib, Gtk, Pango

from .. import community
from ..i18n import _
from ..settings import SETTINGS_BY_ID, format_value
from .dates import date_text
from .names import cap_row
from .tasks import in_thread

log = logging.getLogger("understeer.window")

DEMO_TAGS = {"demo", "example"}


def is_demo(entry):
    return bool(DEMO_TAGS & set(entry.meta.tags))


def error_text(error):
    """What went wrong, for the user."""
    kind = getattr(error, "kind", None)
    if kind == "offline":
        return _("No connection to the catalog. Check the internet connection.")
    if kind == "timeout":
        return _("The catalog took too long to answer.")
    if kind == "http":
        return _("The catalog server answered with error {code}.").format(
            code=error.code)
    if kind == "newer":
        return _("The catalog needs a newer version of Understeer.")
    if kind == "redirect":
        return _("The catalog was moved to another address, which is not "
                 "followed.")
    return _("The downloaded catalog is not valid.")


def _plain_row(title, value=None, subtitle=None):
    """An Adw.ActionRow showing others' text as it is, not as markup."""
    row = cap_row(Adw.ActionRow(use_markup=False), title_lines=0,
                  subtitle_lines=0)
    row.set_title(title)            # after use_markup, or it is read as markup
    if subtitle:
        row.set_subtitle(subtitle)
    if value is not None:
        label = Gtk.Label(label=value, wrap=True, xalign=1,
                          max_width_chars=30, selectable=False)
        label.set_use_markup(False)
        row.add_suffix(label)
    return row


class LibraryMixin:
    """Part of TunerWindow: the Community Library dialog."""

    _library_waiting = None     # callbacks while the catalog loads

    def _load_library(self, done):
        """Download the catalog in a thread, then done(loaded, error); a
        download already under way is waited for instead of a second one."""
        if self._library_waiting is not None:
            self._library_waiting.append(done)
            return
        self._library_waiting = [done]

        def finished(loaded, error):
            waiting, self._library_waiting = self._library_waiting, None
            for callback in waiting:
                callback(loaded, error)
        in_thread(community.refresh, finished, (community.Error,))

    def _show_library(self):
        dialog = self.fit_dialog(Adw.Dialog(title=_("Community Library")))
        nav = Adw.NavigationView()
        dialog.set_child(nav)
        state = {"loaded": community.read_cache(), "busy": False}

        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text=_("Load the catalog again. Newly "
                                            "shared presets can take a few "
                                            "minutes to show up."))
        spinner = Gtk.Spinner(visible=False)
        header = Adw.HeaderBar()
        header.pack_start(refresh)
        header.pack_start(spinner)

        stack = Gtk.Stack()
        loading = Adw.StatusPage(title=_("Loading the Catalog…"),
                                 child=Gtk.Spinner(spinning=True,
                                                   width_request=32,
                                                   height_request=32))
        failed = Adw.StatusPage(icon_name="network-offline-symbolic",
                                title=_("Community Library Unavailable"))
        retry = Gtk.Button(label=_("Try Again"), halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        failed.set_child(retry)
        empty = Adw.StatusPage(icon_name="view-list-symbolic",
                               title=_("No Community Presets Yet"))
        list_page = Adw.PreferencesPage()
        status = Gtk.Label(wrap=True, xalign=0)
        status.add_css_class("dim-label")
        status_group = Adw.PreferencesGroup()
        status_group.add(status)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        rows_group = Adw.PreferencesGroup(
            description=_("Read only: looking at a preset changes neither "
                          "the wheelbase nor your presets."))
        rows_group.add(rows)
        list_page.add(status_group)
        list_page.add(rows_group)
        for name, child in (("loading", loading), ("failed", failed),
                            ("empty", empty), ("list", list_page)):
            stack.add_named(child, name)
        toolbar = Adw.ToolbarView(content=stack)
        toolbar.add_top_bar(header)
        nav.add(Adw.NavigationPage(title=_("Community Library"), child=toolbar,
                                   tag="list"))

        def status_text(error=None):
            loaded = state["loaded"]
            parts = []
            if error is None:
                parts.append(_("Catalog of {date}.").format(
                    date=date_text(loaded.when)))
            elif getattr(error, "kind", None) in ("offline", "timeout"):
                parts.append(_("Offline: showing the catalog of {date}.").format(
                    date=date_text(loaded.when)))
            else:
                parts.append(error_text(error) + " " + _(
                    "Showing the catalog of {date}.").format(
                        date=date_text(loaded.when)))
            skipped = len(loaded.catalog.skipped)
            if skipped:
                parts.append(_("Presets left out because they can't be "
                               "read: {n}").format(n=skipped))
            return " ".join(parts)

        def show(error=None):
            loaded = state["loaded"]
            refresh.set_sensitive(not state["busy"])
            spinner.set_visible(state["busy"])
            spinner.set_spinning(state["busy"])
            if loaded is None:
                if state["busy"]:
                    stack.set_visible_child_name("loading")
                else:
                    failed.set_description(GLib.markup_escape_text(
                        error_text(error)))
                    stack.set_visible_child_name("failed")
                return
            if not loaded.catalog.entries:
                empty.set_description(GLib.markup_escape_text(status_text(error)))
                stack.set_visible_child_name("empty")
                return
            status.set_label(status_text(error))
            stack.set_visible_child_name("list")

        def fill():
            while (row := rows.get_row_at_index(0)) is not None:
                rows.remove(row)
            if state["loaded"] is None:
                return
            entries = sorted(state["loaded"].catalog.entries,
                             key=lambda e: (e.name.casefold(), e.id))
            for entry in entries:
                rows.append(self._library_row(entry, nav))

        def load():
            state["busy"] = True
            show()

            def done(loaded, error):
                state["busy"] = False
                if error is None:
                    state["loaded"] = loaded
                    fill()
                else:
                    log.info("Community catalog not loaded: %s", error)
                show(error)
            self._load_library(done)

        refresh.connect("clicked", lambda b: load())
        retry.connect("clicked", lambda b: load())
        fill()
        # Load when the cache is old, or wait for a download under way
        # (e.g. started before the dialog was closed and opened again)
        if community.due(state["loaded"]) or self._library_waiting is not None:
            load()
        else:
            show()
        log.info("Showing the community library")
        dialog.present(self)
        return dialog

    def _library_row(self, entry, nav):
        """A row of the list: name, game and wheelbase, the beginning of the
        description; opens the details."""
        row = cap_row(Adw.ActionRow(use_markup=False, activatable=True),
                      title_lines=1, subtitle_lines=2)
        row.set_title(entry.name)       # after use_markup, others' text
        meta = entry.meta
        line = " · ".join(t for t in (meta.game, meta.wheelbase) if t)
        if meta.author:
            by = _("by {author}").format(author=meta.author)
            line = f"{line} · {by}" if line else by
        first = (meta.description or "").split("\n", 1)[0]
        row.set_subtitle("\n".join(t for t in (line, first) if t))
        if is_demo(entry):
            badge = Gtk.Label(label=_("Example"), valign=Gtk.Align.CENTER,
                              css_classes=["example-badge"])
            row.add_suffix(badge)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda r: nav.push(self._library_details(entry)))
        return row

    def _library_details(self, entry):
        """A page with everything about one preset; read only."""
        meta = entry.meta
        page = Adw.PreferencesPage()
        if is_demo(entry):
            note = Adw.PreferencesGroup()
            label = Gtk.Label(wrap=True, xalign=0, label=_(
                "Example: this entry only shows what the library looks like. "
                "It is not a recommended setup."))
            label.add_css_class("warning")
            note.add(label)
            page.add(note)
        if meta.description:
            group = Adw.PreferencesGroup()
            text = Gtk.Label(label=meta.description, wrap=True, xalign=0,
                             selectable=True)
            text.set_use_markup(False)
            group.add(text)
            page.add(group)
        drives = {"belt": _("Belt"), "direct_drive": _("Direct drive")}
        about = [(title, value) for title, value in (
            (_("Author"), meta.author), (_("Game"), meta.game),
            (_("Wheelbase"), meta.wheelbase),
            (_("Drive"), drives.get(meta.drive)),
            (_("Torque"), _("{n} Nm").format(n=meta.torque_nm)
             if meta.torque_nm else None),
            (_("Tags"), ", ".join(meta.tags) if meta.tags else None)) if value]
        if about:
            group = Adw.PreferencesGroup(title=_("About This Preset"))
            for title, value in about:
                group.add(_plain_row(title, value))
            page.add(group)
        if entry.unsupported or entry.adjusted:
            notes = Adw.PreferencesGroup(title=_("Notes"))
            if entry.unsupported:
                notes.add(_plain_row(
                    _("Settings this version doesn't know"),
                    subtitle=", ".join(entry.unsupported) + " — " +
                    _("they are left out.")))
            for attr_id, shared, kept in entry.adjusted:
                setting = SETTINGS_BY_ID[attr_id]
                notes.add(_plain_row(
                    _(setting["label"]),
                    subtitle=_("Shared as {shared}, kept as {kept} in the "
                               "steps of this setting.").format(
                                   shared=format_value(setting, shared),
                                   kept=format_value(setting, kept))))
            page.add(notes)
        for group in self._value_groups(entry.values):
            page.add(group)
        # The header shows the name in a label of its own: a page title is
        # shown as markup there, and the name is others' text.
        title = Gtk.Label(label=entry.name, ellipsize=Pango.EllipsizeMode.END,
                          css_classes=["title"])
        title.set_use_markup(False)
        header = Adw.HeaderBar(title_widget=title)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        log.info("Showing community preset %s", entry.id)
        return Adw.NavigationPage(title=entry.name, child=toolbar)
