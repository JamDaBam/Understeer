"""Reporting a problem: a short description, the rest is filled in."""
import logging

from gi.repository import Adw, Gdk, Gio, Gtk

from .. import log as session_log
from .. import report
from ..i18n import _

log = logging.getLogger("understeer.report")


class ReportDialog:
    """Window that opens a filled-in GitHub issue.

    The user writes what happened; system details and the end of the log
    are added. GitHub shows the issue before it is sent, nothing is sent
    from here.
    """

    def __init__(self, window, title="", what="", parent=None):
        """window: the main window, for its details; parent: the window the
        dialog belongs to, if not that one (e.g. the preferences)."""
        self.window = window
        self.system = report.system_info(window.report_details())
        self.log = report.log_excerpt()

        self.dialog = Adw.Window(transient_for=parent or window, modal=True,
                                 default_width=600, default_height=720,
                                 title=_("Report a Problem"))
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay()
        view.set_content(self.toasts)
        self.dialog.set_content(view)

        page = Adw.PreferencesPage()
        self.toasts.set_child(page)

        intro = Adw.PreferencesGroup(
            description=_("Describe briefly what happened. Understeer adds the "
                          "technical details and opens a filled-in issue on "
                          "GitHub. You see everything there before you send "
                          "it; you need a GitHub account."))
        self.title_row = Adw.EntryRow(title=_("Title"), text=title)
        intro.add(self.title_row)
        page.add(intro)

        what_group = Adw.PreferencesGroup(title=_("What happened?"))
        self.what = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 top_margin=10, bottom_margin=10,
                                 left_margin=12, right_margin=12,
                                 accepts_tab=False)
        self.what.get_buffer().set_text(what)
        self.what.update_property([Gtk.AccessibleProperty.LABEL],
                                  [_("What happened?")])
        frame = Gtk.ScrolledWindow(child=self.what, min_content_height=120,
                                   hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        frame.set_overflow(Gtk.Overflow.HIDDEN)     # keep the round corners
        what_group.add(frame)
        hint = Gtk.Label(label=_("What did you do, what did you expect, and "
                                 "what happened instead?"),
                         xalign=0, wrap=True, margin_top=6)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        what_group.add(hint)
        page.add(what_group)

        sent = Adw.PreferencesGroup(
            title=_("Added to the Report"),
            description=_("Your user name and home folder are left out."))
        system_row = Adw.ExpanderRow(title=_("System Details"),
                                     subtitle=_("Versions, wheel base and "
                                                "access rights"))
        system_row.add_row(self._text_row(self.system))
        sent.add(system_row)

        self.log_row = Adw.ExpanderRow(
            title=_("Log Excerpt"),
            subtitle=_("The last entries of this session"),
            show_enable_switch=True, enable_expansion=bool(self.log))
        self.log_row.set_sensitive(bool(self.log))
        self.log_row.add_row(self._text_row(self.log or "—"))
        sent.add(self.log_row)

        full_log = Adw.ActionRow(
            title=_("Whole Log File"),
            subtitle=_("If asked for it, drag the file into the issue."))
        folder = Gtk.Button(label=_("Show Folder"), valign=Gtk.Align.CENTER)
        folder.connect("clicked", lambda b: Gtk.FileLauncher.new(
            Gio.File.new_for_path(str(session_log.LOG_FILE)))
            .open_containing_folder(self.dialog, None, None))
        full_log.add_suffix(folder)
        sent.add(full_log)
        page.add(sent)

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6)
        copy = Gtk.Button(label=_("Copy Details"))
        copy.add_css_class("pill")
        copy.set_tooltip_text(_("Copy the whole report, e.g. to paste it "
                                "somewhere else"))
        copy.connect("clicked", lambda b: self._copy(_("Report copied")))
        send = Gtk.Button(label=_("Open on GitHub"))
        send.add_css_class("pill")
        send.add_css_class("suggested-action")
        send.connect("clicked", lambda b: self._open())
        buttons.append(copy)
        buttons.append(send)
        actions = Adw.PreferencesGroup()
        actions.add(buttons)
        page.add(actions)

        self.dialog.present()
        # Start where the user has to type.
        if title:
            buffer = self.what.get_buffer()
            buffer.place_cursor(buffer.get_end_iter())
            self.what.grab_focus()
        else:
            self.title_row.grab_focus()

    @staticmethod
    def _text_row(text):
        label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True,
                          wrap_mode=2)      # Pango.WrapMode.WORD_CHAR
        label.add_css_class("monospace")
        label.add_css_class("caption")
        for side in ("top", "bottom", "start", "end"):
            getattr(label, f"set_margin_{side}")(12)
        row = Gtk.ListBoxRow(activatable=False, child=label)
        return row

    def _parts(self):
        buffer = self.what.get_buffer()
        what = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(),
                               False).strip()
        log_text = self.log if self.log_row.get_enable_expansion() else ""
        return self.title_row.get_text().strip(), what, self.system, log_text

    def _copy(self, message):
        Gdk.Display.get_default().get_clipboard().set(
            report.report_text(*self._parts()))
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))

    def _open(self):
        url = report.issue_url(*self._parts())
        log.info("Opening a GitHub issue (%d characters)", len(url))
        # Also on the clipboard, in case the browser cuts the address.
        self._copy(_("GitHub opened in the browser. The report is also on "
                     "the clipboard."))
        Gtk.UriLauncher.new(url).launch(self.dialog, None, None)
