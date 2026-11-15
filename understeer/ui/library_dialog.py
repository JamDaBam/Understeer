"""The community preset library: a dialog listing the presets of the
online catalog, each with a read-only page of its details and values.

Browsing only: nothing here applies, imports or saves a preset, and
nothing is written to the wheelbase. Everything in the catalog comes from
others, so it is only ever shown as plain text, never as markup.
"""
import logging

from gi.repository import Adw, GLib, Gtk, Pango

from .. import community, compatibility, library_search
from ..i18n import N_, _
from ..settings import SETTINGS_BY_ID, format_value
from .dates import date_text
from .names import cap_row, label_factory
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


LEVEL_TEXT = {
    compatibility.SAME: N_("Same wheelbase model"),
    compatibility.DIFFERENT: N_("Different wheelbase model"),
    compatibility.UNKNOWN: N_("Compatibility unknown"),
}
REASON_TEXT = {
    compatibility.NO_DEVICE: N_("No wheelbase is connected, so the model "
                                "can't be compared."),
    compatibility.UNKNOWN_DEVICE: N_("Understeer doesn't know the model of "
                                     "the connected wheelbase."),
    compatibility.NO_MODEL: N_("The preset doesn't say which wheelbase it "
                               "was made on."),
    compatibility.UNKNOWN_MODEL: N_("The preset names a wheelbase Understeer "
                                    "can't match to exactly one model: "
                                    "{name}."),
}


def compatibility_details(entry, wheelbase):
    """(title, explanation) of how the preset's wheelbase compares to the
    connected one."""
    result = compatibility.classify(entry.meta, wheelbase)
    title = _(LEVEL_TEXT[result.level])
    if result.level == compatibility.SAME:
        return title, _(
            "Made on the same model as your wheelbase ({model}), recognised "
            "by its USB product ID. That doesn't mean every value suits it: "
            "variants of a model can differ, and the values aren't checked "
            "against your wheelbase.").format(model=wheelbase.model)
    if result.level == compatibility.DIFFERENT:
        lines = [_("Made on a {preset}; your wheelbase is a {model}.").format(
            preset=result.preset_model, model=wheelbase.model)]
        drives = {"belt": _("Belt"), "direct_drive": _("Direct drive")}
        if result.same_drive is not None:
            lines.append(_("Drive of the preset's wheelbase: {preset}. Yours: "
                           "{yours}.").format(preset=drives[entry.meta.drive],
                                              yours=drives[wheelbase.drive]))
        lines.append(_("Another model can need other values."))
        return title, " ".join(lines)
    return title, _(REASON_TEXT[result.reason]).format(
        name=entry.meta.wheelbase or "")


class LibraryMixin:
    """Part of TunerWindow: the Community Library dialog."""

    _library_waiting = None     # callbacks while the catalog loads
    _library_views = ()         # update(), per open dialog, for a new device

    def _library_device_changed(self):
        """The wheelbase was connected, removed or changed."""
        for update in list(self._library_views):
            update()

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
        state = {"loaded": community.read_cache(), "busy": False,
                 "filters": library_search.NONE, "shown": 0, "device": None,
                 "choices": {"game": [], "wheelbase": [], "tag": []},
                 "updating": False}

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
        search = Gtk.SearchEntry(placeholder_text=_("Search presets"),
                                 hexpand=True)
        search.update_property([Gtk.AccessibleProperty.LABEL],
                               [_("Search presets")])
        pickers = {}
        for key, everything in (("game", _("All games")),
                                ("wheelbase", _("All wheelbases")),
                                ("tag", _("All tags"))):
            picker = Gtk.DropDown.new_from_strings([everything])
            picker.set_factory(label_factory(chars=24))
            picker.set_list_factory(label_factory(in_list=True))
            picker.update_property([Gtk.AccessibleProperty.LABEL], [everything])
            pickers[key] = picker
        mine = Gtk.CheckButton(label=_("Only my wheelbase"))
        reset = Gtk.Button(label=_("Reset Filters"))
        reset.add_css_class("flat")
        filter_row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                 column_spacing=6, row_spacing=6,
                                 max_children_per_line=5)
        for w in (*pickers.values(), mine, reset):
            filter_row.append(w)
        filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        filter_box.append(search)
        filter_box.append(filter_row)
        filter_group = Adw.PreferencesGroup()
        filter_group.add(filter_box)
        status = Gtk.Label(wrap=True, xalign=0)
        status.add_css_class("dim-label")
        status_group = Adw.PreferencesGroup()
        status_group.add(status)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        no_match = Adw.StatusPage(icon_name="edit-find-symbolic",
                                  title=_("No Matching Presets"))
        reset_too = Gtk.Button(label=_("Reset Filters"), halign=Gtk.Align.CENTER)
        reset_too.add_css_class("pill")
        no_match.set_child(reset_too)
        no_match.add_css_class("compact")
        rows.set_placeholder(no_match)
        rows.set_filter_func(lambda row: getattr(row, "shown", True))
        rows_group = Adw.PreferencesGroup(
            description=_("Read only: looking at a preset changes neither "
                          "the wheelbase nor your presets."))
        rows_group.add(rows)
        list_page.add(filter_group)
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
            if library_search.active(state["filters"]):
                parts.append(_("Shown: {n} of {total}").format(
                    n=state["shown"], total=len(loaded.catalog.entries)))
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
            state["error"] = error
            status.set_label(status_text(error))
            stack.set_visible_child_name("list")

        def chosen(key):
            index = pickers[key].get_selected()
            choices = state["choices"][key]
            return choices[index - 1] if 0 < index <= len(choices) else None

        def read_filters():
            return library_search.Filters(
                search.get_text(), chosen("game"), chosen("wheelbase"),
                chosen("tag"), mine.get_active() and mine.get_sensitive())

        def apply_filters(*_args):
            if state["updating"]:
                return
            filters = state["filters"] = read_filters()
            shown = 0
            row = rows.get_first_child()
            while row is not None:
                if isinstance(row, Gtk.ListBoxRow) and hasattr(row, "entry"):
                    row.shown = library_search.matches(
                        row.entry, filters, state["device"], row.text)
                    shown += row.shown
                row = row.get_next_sibling()
            state["shown"] = shown
            rows.invalidate_filter()
            reset.set_sensitive(library_search.active(filters)
                                or mine.get_active())
            pickers["wheelbase"].set_sensitive(
                not filters.only_mine and bool(state["choices"]["wheelbase"]))
            if state["loaded"] is not None and state["loaded"].catalog.entries:
                status.set_label(status_text(state.get("error")))

        def set_choices(key, values, keep):
            state["choices"][key] = values
            everything = pickers[key].get_model().get_string(0)
            pickers[key].set_model(Gtk.StringList.new([everything, *values]))
            folded = [library_search.fold(v) for v in values]
            pickers[key].set_selected(
                folded.index(library_search.fold(keep)) + 1
                if keep is not None and library_search.fold(keep) in folded else 0)
            pickers[key].set_sensitive(bool(values))

        def update_device():
            state["device"] = compatibility.connected(self.device_path)
            known = state["device"] is not None and state["device"].model
            # A tick stays while the wheelbase is away; it filters again
            # once one is back (read_filters ignores it meanwhile)
            mine.set_sensitive(bool(known))
            mine.set_tooltip_text(
                _("Only presets made on the same model as your {model}").format(
                    model=state["device"].model) if known else
                _(REASON_TEXT[compatibility.NO_DEVICE if state["device"] is None
                              else compatibility.UNKNOWN_DEVICE]))
            row = rows.get_first_child()
            while row is not None:
                if hasattr(row, "entry"):
                    level = compatibility.classify(row.entry.meta,
                                                   state["device"]).level
                    row.level_label.set_label(_(LEVEL_TEXT[level]))
                row = row.get_next_sibling()
            apply_filters()

        def fill():
            while (row := rows.get_row_at_index(0)) is not None:
                rows.remove(row)
            entries = [] if state["loaded"] is None else sorted(
                state["loaded"].catalog.entries,
                key=lambda e: (e.name.casefold(), e.id))
            for entry in entries:
                rows.append(self._library_row(entry, nav, lambda: state["device"]))
            # The choices of this catalog; a choice no longer there is let go
            available = library_search.options(entries)
            keep = library_search.keep(state["filters"], available)
            state["updating"] = True
            set_choices("game", available.games, keep.game)
            set_choices("wheelbase", available.wheelbases, keep.wheelbase)
            set_choices("tag", available.tags, keep.tag)
            state["updating"] = False
            update_device()

        def reset_filters(*_args):
            state["updating"] = True
            search.set_text("")
            for picker in pickers.values():
                picker.set_selected(0)
            mine.set_active(False)
            state["updating"] = False
            apply_filters()

        search.connect("search-changed", apply_filters)
        for picker in pickers.values():
            picker.connect("notify::selected", apply_filters)
        mine.connect("toggled", apply_filters)
        reset.connect("clicked", reset_filters)
        reset_too.connect("clicked", reset_filters)
        self._library_views = [*self._library_views, update_device]
        dialog.connect("closed", lambda d: setattr(
            self, "_library_views",
            [v for v in self._library_views if v is not update_device]))

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

    def _library_row(self, entry, nav, wheelbase):
        """A row of the list: name, game and wheelbase, the beginning of the
        description, how its wheelbase compares; opens the details.
        wheelbase(): the connected one (compatibility.Device or None)."""
        row = cap_row(Adw.ActionRow(use_markup=False, activatable=True),
                      title_lines=1, subtitle_lines=2)
        row.set_title(entry.name)       # after use_markup, others' text
        row.entry, row.text, row.shown = entry, library_search.haystack(entry), True
        meta = entry.meta
        line = " · ".join(t for t in (meta.game, meta.wheelbase) if t)
        if meta.author:
            by = _("by {author}").format(author=meta.author)
            line = f"{line} · {by}" if line else by
        first = (meta.description or "").split("\n", 1)[0]
        row.set_subtitle("\n".join(t for t in (line, first) if t))
        row.level_label = Gtk.Label(wrap=True, max_width_chars=18,
                                    justify=Gtk.Justification.RIGHT, xalign=1,
                                    valign=Gtk.Align.CENTER,
                                    css_classes=["caption", "dim-label"])
        row.add_suffix(row.level_label)
        if is_demo(entry):
            badge = Gtk.Label(label=_("Example"), valign=Gtk.Align.CENTER,
                              css_classes=["example-badge"])
            row.add_suffix(badge)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda r: nav.push(
            self._library_details(entry, wheelbase())))
        return row

    def _library_details(self, entry, wheelbase=None):
        """A page with everything about one preset; read only. wheelbase:
        the connected one, to compare the preset's with."""
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
            text = Gtk.Label(label=meta.description, wrap=True, xalign=0)
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
        title, explanation = compatibility_details(entry, wheelbase)
        group = Adw.PreferencesGroup(title=_("Compatibility"))
        group.add(_plain_row(title, subtitle=explanation))
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
