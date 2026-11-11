"""The trash of a page: deleted presets on the Presets page, deleted games
on the Games page, each opened with a button next to Select. A small
dialog with a list that scrolls when it gets long."""
import logging

from gi.repository import Adw, GLib, Gtk

from .. import trash
from ..i18n import _

log = logging.getLogger("understeer.window")

ROW_HEIGHT = 56
ROWS_SHOWN = 6          # more scroll, with half a row showing there are more


class TrashMixin:
    """Part of TunerWindow: the trash dialogs and their buttons."""

    def _trash_button(self, kind):
        """The button that opens the trash of kind ("preset" or "game")."""
        button = Gtk.Button(child=Adw.ButtonContent(
            icon_name="user-trash-symbolic", label=_("Trash")))
        button.add_css_class("flat")
        button.set_tooltip_text(
            _("Deleted presets, to put back for {days} days").format(days=trash.KEEP_DAYS)
            if kind == "preset" else
            _("Deleted games, to put back for {days} days").format(days=trash.KEEP_DAYS))
        button.connect("clicked", lambda b: self._show_trash(kind))
        return button

    def _show_trash(self, kind):
        dialog = Adw.Dialog(title=_("Trash"), content_width=480)
        header = Adw.HeaderBar()
        empty = Gtk.Button(label=_("Empty"))
        empty.add_css_class("destructive-action")
        empty.connect("clicked", lambda b: self._ask_empty_trash(kind))
        header.pack_start(empty)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         margin_top=12, margin_bottom=18, margin_start=18,
                         margin_end=18)
        about = Gtk.Label(wrap=True, xalign=0, label=(
            _("Deleted presets stay here for {days} days, then they are "
              "deleted for good.") if kind == "preset" else
            _("Deleted games stay here for {days} days, with their preset "
              "and picture, then they are deleted for good.")).format(
                  days=trash.KEEP_DAYS))
        about.add_css_class("dim-label")
        column.append(about)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow(
            child=rows, propagate_natural_height=True,
            max_content_height=ROWS_SHOWN * ROW_HEIGHT + ROW_HEIGHT // 2,
            hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_overflow(Gtk.Overflow.HIDDEN)    # keep the round corners
        scroll.add_css_class("suggestions")
        column.append(scroll)

        view = Adw.ToolbarView(content=column)
        view.add_top_bar(header)
        dialog.set_child(view)
        dialog.kind, dialog.rows, dialog.empty_button = kind, rows, empty
        self._trash_dialog = dialog
        dialog.connect("closed", lambda d: setattr(self, "_trash_dialog", None))
        self._fill_trash(dialog)
        dialog.present(self)
        return dialog

    def _fill_trash(self, dialog):
        rows = dialog.rows
        while (row := rows.get_first_child()) is not None:
            rows.remove(row)
        items = trash.items(dialog.kind)
        dialog.empty_button.set_sensitive(bool(items))
        if not items:
            row = Adw.ActionRow(title=_("The trash is empty"))
            row.add_css_class("dim-label")
            rows.append(row)
            return
        for item in items:
            when = GLib.DateTime.new_from_unix_local(int(item["deleted"])).format("%x")
            parts = [_("Deleted on {date}").format(date=when)]
            if item.get("folder"):
                parts.insert(0, _("Folder: {folder}").format(folder=item["folder"]))
            row = Adw.ActionRow(title=item["name"], subtitle=" · ".join(parts),
                                use_markup=False)
            back = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=_("Restore"))
            back.add_css_class("flat")
            back.connect("clicked", lambda b, i=item: self._restore_from_trash(i))
            gone = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=_("Delete for Good"))
            gone.add_css_class("flat")
            gone.connect("clicked", lambda b, i=item: self._delete_from_trash(i))
            row.add_suffix(back)
            row.add_suffix(gone)
            rows.append(row)

    def _trash_changed(self):
        """The trash changed: show it anew if it is open."""
        dialog = getattr(self, "_trash_dialog", None)
        if dialog is not None:
            self._fill_trash(dialog)

    def _restore_from_trash(self, item):
        if item["kind"] == "game":
            if self.games_page.restore(item["id"]):
                self._notify(_("“{name}” restored").format(name=item["name"]))
            return
        try:
            path = trash.restore_preset(item["id"])
        except (trash.Error, OSError) as e:
            self._error(_("Could not restore presets"), str(e))
            return
        self._presets_changed()
        self._trash_changed()
        name = self._preset_names.get(path, item["name"])
        self._notify(_("“{name}” restored").format(name=name))

    def _delete_from_trash(self, item):
        trash.remove(item["id"])
        log.info("%s %r deleted for good", item["kind"], item["name"])
        self._trash_changed()

    def _ask_empty_trash(self, kind):
        dialog = Adw.AlertDialog(
            heading=_("Empty Trash?"),
            body=_("The presets in it are deleted for good.") if kind == "preset"
            else _("The games in it are deleted for good."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("empty", _("Empty Trash"))
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "empty":
                trash.empty(kind)
                log.info("Trash of %ss emptied", kind)
                self._trash_changed()

        dialog.connect("response", on_response)
        dialog.present(self)
        return dialog
