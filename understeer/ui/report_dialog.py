"""Reporting a problem: a short description, the rest is filled in."""
import logging

from gi.repository import Adw, Gdk, Gtk, Pango

from .. import log as session_log
from .. import report
from ..i18n import _
from .files import show_in_folder
from .layout import margins

log = logging.getLogger("understeer.report")

MAX_HEIGHT = 640    # content taller than this scrolls, e.g. an opened log


class ReportDialog:
    """Dialog that opens a filled-in GitHub issue.

    The user writes what happened; system details and the end of the log
    are added. GitHub shows the issue before it is sent, nothing is sent
    from here.
    """

    def __init__(self, window, title="", what=""):
        """window: the main window, for its details and as parent."""
        self.window = window
        self.system = report.system_info(window.report_details())
        self.log = report.log_excerpt()

        # As big as the other big dialogs (window.fit_dialog); the content
        # scrolls, the buttons sit in a bar below and stay in view.
        self.dialog = Adw.Dialog(title=_("Report a Problem"))
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay()
        view.set_content(self.toasts)
        self.dialog.set_child(view)

        column = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18), 18)
        clamp = Adw.Clamp(maximum_size=600, child=column)
        scroll = Gtk.ScrolledWindow(child=clamp, propagate_natural_height=True,
                                    max_content_height=MAX_HEIGHT,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.toasts.set_child(scroll)

        intro = Adw.PreferencesGroup(
            description=_("Describe briefly what happened. Understeer adds the "
                          "technical details and opens a filled-in issue on "
                          "GitHub, where you see everything before you send "
                          "it."))
        self.title_row = Adw.EntryRow(title=_("Title"), text=title)
        intro.add(self.title_row)
        column.append(intro)

        what_group = Adw.PreferencesGroup(
            title=_("What happened?"),
            description=_("What did you do, what did you expect, and what "
                          "happened instead?"))
        self.what = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 top_margin=10, bottom_margin=10,
                                 left_margin=12, right_margin=12,
                                 accepts_tab=False)
        self.what.get_buffer().set_text(what)
        self.what.update_property([Gtk.AccessibleProperty.LABEL],
                                  [_("What happened?")])
        frame = Gtk.ScrolledWindow(child=self.what, min_content_height=110,
                                   hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        frame.set_overflow(Gtk.Overflow.HIDDEN)     # keep the round corners
        what_group.add(frame)
        column.append(what_group)

        sent = Adw.PreferencesGroup(
            title=_("Added to the Report"),
            description=_("Your user name and home folder are left out."))
        system_row = Adw.ExpanderRow(title=_("System Details"),
                                     subtitle=_("Versions, wheelbase and "
                                                "access rights"))
        system_row.add_row(self._text_row(self.system))
        sent.add(system_row)

        self.log_row = Adw.ExpanderRow(
            title=_("Log Excerpt"),
            subtitle=_("The last entries of this session"),
            show_enable_switch=True, enable_expansion=bool(self.log))
        self.log_row.set_sensitive(bool(self.log))
        self.log_row.add_row(self._text_row(self.log or "—"))
        folder = Gtk.Button(icon_name="folder-open-symbolic",
                            valign=Gtk.Align.CENTER)
        folder.add_css_class("flat")
        folder.set_tooltip_text(_("Show the whole log file, e.g. to drag it "
                                  "into the issue"))
        folder.connect("clicked", lambda b: show_in_folder(
            window, session_log.LOG_FILE))
        self.log_row.add_suffix(folder)
        sent.add(self.log_row)
        column.append(sent)

        bar = margins(Gtk.Box(spacing=12, halign=Gtk.Align.CENTER), 12,
                      ("top", "bottom"))
        copy = Gtk.Button(label=_("Copy Only"))
        copy.add_css_class("pill")
        copy.set_tooltip_text(_("Copy the filled-in report, e.g. for a comment "
                                "on an existing issue, an e-mail or a forum post"))
        copy.connect("clicked", lambda b: self._copy(_("Report copied")))
        send = Gtk.Button(label=_("Open on GitHub"))
        send.add_css_class("pill")
        send.add_css_class("suggested-action")
        send.set_tooltip_text(_("Needs a GitHub account"))
        send.connect("clicked", lambda b: self._open())
        bar.append(copy)
        bar.append(send)
        view.add_bottom_bar(bar)

        window.fit_dialog(self.dialog)
        self.dialog.present(window)
        # Start where the user has to type.
        if title:
            buffer = self.what.get_buffer()
            buffer.place_cursor(buffer.get_end_iter())
            self.what.grab_focus()
        else:
            self.title_row.grab_focus()

    @staticmethod
    def _text_row(text):
        label = margins(Gtk.Label(label=text, xalign=0, wrap=True, selectable=True,
                                  wrap_mode=Pango.WrapMode.WORD_CHAR,
                                  css_classes=["monospace", "caption"]), 12)
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
        Gtk.UriLauncher.new(url).launch(self.window, None, None)
