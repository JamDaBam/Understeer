"""Sharing a saved preset with the community: a dialog to enter what is
shown publicly with it, check it with submission.prepare() and review the
prepared entry before confirming it.

After the confirmation, the form on GitHub can be opened in the browser
with the submission filled in (github_form); the user submits it there,
or not. Nothing is sent from Understeer, nothing is copied unless the user
asks for it, nothing is written, the saved preset stays as it is, and the
wheelbase is never read or written. The values are those of the saved
preset file, never the current tuning. Everything the user types is shown
as plain text, never as markup.
"""
import logging
import pathlib

from gi.repository import Adw, GLib, Gtk, Pango

from .. import community, config, github_form, submission
from ..i18n import N_, _
from ..settings import SETTINGS_BY_ID, format_value
from .names import label_factory

log = logging.getLogger("understeer.window")

FIELDS = {"name": N_("Preset Name"), "game": N_("Game"),
          "wheelbase": N_("Wheelbase"), "author": N_("Author"),
          "description": N_("Description"), "tags": N_("Tags")}


# The fields' labels as the form on GitHub shows them, in English like the
# form itself, so they can be found there
GITHUB_LABELS = {"title": "Title", "preset-name": "Preset name",
                 "game": "Game", "wheelbase": "Wheelbase model",
                 "author": "Author", "description": "Description",
                 "tags": "Tags (optional)",
                 "catalog-id": "Proposed catalog ID (optional)",
                 "preset-json": "Catalog Entry (JSON)"}


def open_uri(parent, uri, done):
    """Open uri with the default browser, through the desktop portal or
    GIO, never a shell; then done(None), or done(error text)."""
    def finished(launcher, result):
        try:
            ok = launcher.launch_finish(result)
        except GLib.Error as e:
            done(e.message)
            return
        done(None if ok else _("The browser didn't start."))
    Gtk.UriLauncher.new(uri).launch(parent, None, finished)


def _field(field):
    return _(FIELDS[field]) if field in FIELDS else _("Preset")


def _setting(attr_id):
    setting = SETTINGS_BY_ID.get(attr_id)
    return _(setting["label"]) if setting else attr_id


def problem_text(problem):
    """A problem of submission.prepare() in words for the user."""
    field, code, detail = problem
    name = _field(field)
    if code == "required":
        if field == "wheelbase":
            return _("Choose the wheelbase the preset was made on.")
        return _("{field} is required.").format(field=name)
    if code == "too_long":
        if field == "name" and detail == submission.sharing.MAX_NAME_BYTES:
            return _("{field} is too long.").format(field=name)
        return _("{field} is too long: at most {n} characters.").format(
            field=name, n=detail)
    if code == "too_many_lines":
        return _("{field} has too many lines: at most {n}.").format(
            field=name, n=detail)
    if code == "line_breaks":
        return _("{field} must be one line.").format(field=name)
    if code == "control_characters":
        return _("{field} contains invisible or control characters.").format(
            field=name)
    if code == "not_text":
        return _("{field} must be text.").format(field=name)
    if code == "web_address":
        return _("{field} must not contain a web address.").format(field=name)
    if code == "email_address":
        return _("{field} must not contain an e-mail address.").format(field=name)
    if code == "file_path":
        return _("{field} must not contain a file path.").format(field=name)
    if code == "unknown_wheelbase":
        return _("Choose one of the listed wheelbases.")
    if field == "tags" and code == "too_many":
        return _("At most {n} tags.").format(n=detail)
    if field == "tags":
        return _("Tags are short words of a–z, 0–9 and -, separated by commas.")
    if code == "unsupported_setting":
        return _("The preset has a setting this version can't share: "
                 "{setting}.").format(setting=detail)
    if code == "invalid_value":
        return _("{setting} has a value that isn't valid.").format(
            setting=_setting(detail))
    if code == "adjusted_value":
        attr_id, shared, kept = detail
        setting = SETTINGS_BY_ID[attr_id]
        return _("{setting} is {shared}, which the wheelbase keeps as {kept}. "
                 "Save the preset with {kept} first.").format(
                     setting=_(setting["label"]),
                     shared=format_value(setting, shared),
                     kept=format_value(setting, kept))
    if code == "no_settings":
        return _("The preset has no settings.")
    if code == "too_many_settings":
        return _("The preset has too many settings.")
    return _("The preset can't be prepared for sharing.")


def warning_text(problem):
    """A warning of submission.prepare() in words for the user."""
    field, code, _detail = problem
    if code == "looks_like_serial":
        return _("{field} contains something that looks like a serial "
                 "number. Make sure it isn't one of yours.").format(
                     field=_field(field))
    return _("Check {field} once more.").format(field=_field(field))


def split_tags(text):
    """The tags typed into one line, separated by commas."""
    return [t.strip() for t in text.split(",") if t.strip()]


def _plain_label(text, *css, **props):
    label = Gtk.Label(label=text, wrap=True, xalign=0, css_classes=list(css),
                      **props)
    label.set_use_markup(False)
    return label


def _wide_factory(chars):
    """Labels for the chosen entry of a drop-down that ask for room for
    chars characters, so a name of that length shows whole; in a narrower
    window it still ends in "…"."""
    factory = Gtk.SignalListItemFactory()
    factory.connect("setup", lambda _f, item: item.set_child(Gtk.Label(
        xalign=1, width_chars=chars, ellipsize=Pango.EllipsizeMode.END)))
    factory.connect("bind", lambda _f, item: item.get_child().set_label(
        item.get_item().get_string()))
    return factory


def _plain_row(title, value):
    """An Adw.ActionRow with the user's text as it is, not as markup."""
    row = Adw.ActionRow(use_markup=False, title_lines=0, subtitle_lines=0)
    row.set_title(title)
    row.set_subtitle(value)
    row.add_css_class("property")
    return row


class ShareMixin:
    """Part of TunerWindow: sharing a saved preset with the community."""

    prepared_submission = None      # the last confirmed one, in memory only

    def _show_share_community(self, path):
        """The dialog for the saved preset at path; returns it, or None
        when the preset can't be read."""
        path = pathlib.Path(path)
        try:
            name, values = config.read_preset(path)     # as saved, not cleaned
        except (OSError, ValueError) as e:
            self._error(_("Could not load preset"), str(e))
            return None
        values = dict(values)                           # never the file's own
        dialog = self.fit_dialog(Adw.Dialog(title=_("Share with Community")))
        nav = Adw.NavigationView()
        dialog.set_child(nav)

        page = Adw.PreferencesPage(description=_(
            "Everything you enter here is meant to be public. Nothing is "
            "sent: you review it first, and the preset stays as it is."))
        errors = Adw.PreferencesGroup(title=_("Please Correct"), visible=False)
        page.add(errors)
        required = Adw.PreferencesGroup(title=_("Required"))
        rows = {"name": Adw.EntryRow(title=_(FIELDS["name"]), text=name),
                "game": Adw.EntryRow(title=_(FIELDS["game"])),
                "author": Adw.EntryRow(title=_(FIELDS["author"]))}
        bases = sorted(submission.WHEELBASE_DRIVES)
        choices = [_("Not chosen")] + bases
        rows["wheelbase"] = Adw.ComboRow(
            title=_(FIELDS["wheelbase"]),
            subtitle=_("The one you made the preset on"),
            model=Gtk.StringList.new(choices))
        # The default rows cut the long names ("CSL Elite Wheel Base
        # (PS4)"): the chosen one gets the width of the longest, the list
        # as wide as its entries
        rows["wheelbase"].set_factory(_wide_factory(max(map(len, choices))))
        rows["wheelbase"].set_list_factory(label_factory(in_list=True))
        rows["author"].set_tooltip_text(_(
            "The name shown with the preset. Nothing is taken from your "
            "system."))
        for key in ("name", "game", "wheelbase", "author"):
            rows[key].update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                      [_("Required")])
            required.add(rows[key])
        page.add(required)
        about = Adw.PreferencesGroup(
            title=_(FIELDS["description"]),
            description=_("Required. What the preset is for, plain text."))
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                            top_margin=8, bottom_margin=8, left_margin=8,
                            right_margin=8, height_request=110)
        text.update_property([Gtk.AccessibleProperty.LABEL,
                              Gtk.AccessibleProperty.DESCRIPTION],
                             [_(FIELDS["description"]), _("Required")])
        text.add_css_class("card")
        about.add(text)
        page.add(about)
        optional = Adw.PreferencesGroup(
            title=_("Optional"),
            description=_("Tags: short words separated by commas, e.g. "
                          "gt3, endurance."))
        rows["tags"] = Adw.EntryRow(title=_(FIELDS["tags"]))
        optional.add(rows["tags"])
        page.add(optional)
        description = text.get_buffer()

        review = Gtk.Button(label=_("Review"))
        review.add_css_class("suggested-action")
        header = Adw.HeaderBar()
        header.pack_end(review)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        nav.add(Adw.NavigationPage(title=_("Share with Community"),
                                   child=toolbar, tag="form"))
        shown_errors = []

        def entered():
            chosen = rows["wheelbase"].get_selected()
            return dict(
                author=rows["author"].get_text(), game=rows["game"].get_text(),
                wheelbase=bases[chosen - 1] if chosen > 0 else None,
                description=description.get_text(
                    description.get_start_iter(), description.get_end_iter(),
                    False),
                tags=split_tags(rows["tags"].get_text()))

        def show_errors(problems):
            for row in shown_errors:
                errors.remove(row)
            shown_errors.clear()
            for key, row in rows.items():
                row.remove_css_class("error")
            text.remove_css_class("error")
            for problem in problems:
                row = Adw.ActionRow(use_markup=False, title_lines=0)
                row.set_title(problem_text(problem))
                row.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic"))
                row.add_css_class("error")
                errors.add(row)
                shown_errors.append(row)
                if problem.field in rows:
                    rows[problem.field].add_css_class("error")
                elif problem.field == "description":
                    text.add_css_class("error")
            errors.set_visible(bool(problems))
            if problems:
                page.scroll_to_top()

        def on_review(_button):
            fields = entered()
            taken = self._share_taken_ids()
            try:
                result = submission.prepare(rows["name"].get_text(), values,
                                            taken_ids=taken, **fields)
            except submission.Invalid as e:
                log.info("Preset %s not ready to share: %s", path.name,
                         ", ".join(f"{p.field} {p.code}" for p in e.problems))
                show_errors(e.problems)
                return
            show_errors(())
            log.info("Reviewing preset %s for sharing", path.name)
            nav.push(self._share_preview(dialog, nav, path, result))

        review.connect("clicked", on_review)
        log.info("Sharing preset %s: entering its details", path.name)
        dialog.present(self)
        return dialog

    @staticmethod
    def _share_taken_ids():
        """The ids of the cached catalog, for the proposed id to avoid; read
        from the cache only, nothing is fetched."""
        loaded = community.read_cache()
        if loaded is None:
            return set()
        return {e.id for e in loaded.catalog.entries}

    def _share_preview(self, dialog, nav, path, result):
        """The page with the prepared entry, built from the checked result
        only, and the confirmation."""
        entry = result.entry
        meta, preset = entry["meta"], entry["preset"]
        page = Adw.PreferencesPage()
        notice = Adw.PreferencesGroup()
        notice.add(_plain_label(_(
            "This is meant to be shared publicly, as shown here. Nothing has "
            "been published or sent yet."), "heading"))
        page.add(notice)
        if result.warnings:
            group = Adw.PreferencesGroup(title=_("Please Check"))
            for warning in result.warnings:
                row = Adw.ActionRow(use_markup=False, title_lines=0)
                row.set_title(warning_text(warning))
                row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
                row.add_css_class("warning")
                group.add(row)
            page.add(group)
        public = Adw.PreferencesGroup(title=_("Public Information"))
        for title, value in (
                (_(FIELDS["name"]), preset["name"]),
                (_(FIELDS["game"]), meta["game"]),
                (_(FIELDS["wheelbase"]), meta["wheelbase"]),
                (_(FIELDS["author"]), meta["author"]),
                (_(FIELDS["description"]), meta["description"]),
                (_(FIELDS["tags"]), ", ".join(meta.get("tags", ()))
                 or _("None"))):
            public.add(_plain_row(title, value))
        page.add(public)
        ident = Adw.PreferencesGroup(title=_("Proposed Catalog ID"))
        ident.add(_plain_row(entry["id"], _(
            "Only a proposal: it is not reserved and may already be taken by "
            "another submission. The catalog is checked again when the "
            "submission is accepted, and the ID can still change.")))
        page.add(ident)
        settings = Adw.PreferencesGroup(
            title=_("Settings"),
            description=_("{n} settings, with exactly these values.").format(
                n=len(preset["values"])))
        page.add(settings)
        for group in self._value_groups(preset["values"]):
            page.add(group)
        confirm_group = Adw.PreferencesGroup()
        tick = Gtk.CheckButton(child=_plain_label(_(
            "I have checked everything above and want to share it publicly")))
        confirm_group.add(tick)
        page.add(confirm_group)

        confirm = Gtk.Button(label=_("Confirm"), sensitive=False)
        confirm.add_css_class("suggested-action")
        tick.connect("toggled", lambda t: confirm.set_sensitive(t.get_active()))
        title = Gtk.Label(label=_("Review"), ellipsize=Pango.EllipsizeMode.END,
                          css_classes=["title"])
        header = Adw.HeaderBar(title_widget=title)
        header.pack_end(confirm)

        def on_confirm(_button):
            if not tick.get_active():
                return
            self.prepared_submission = result
            log.info("Preset %s prepared for sharing as %s", path.name,
                     entry["id"])
            nav.push(self._share_done(dialog, result))

        confirm.connect("clicked", on_confirm)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        return Adw.NavigationPage(title=_("Review"), child=toolbar,
                                  tag="preview")

    def _share_done(self, dialog, result):
        """The last page: what happens on GitHub, the button that opens the
        form there with the submission filled in, and copy buttons for
        fields too long for the link. Nothing is sent from here: the user
        submits on GitHub, or doesn't."""
        try:
            plan = github_form.for_submission(result)
        except ValueError as e:
            log.error("Prepared submission not usable: %s", e)
            status = Adw.StatusPage(icon_name="dialog-error-symbolic",
                                    title=_("The preset can't be prepared "
                                            "for sharing."))
            return Adw.NavigationPage(title=_("Share with Community"),
                                      child=status, tag="done", can_pop=False)
        page = Adw.PreferencesPage()
        about = Adw.PreferencesGroup(title=_("Continue on GitHub"))
        for text in (
                _("GitHub opens in your default browser with the form filled "
                  "in. You may need to sign in there."),
                _("Nothing has been submitted yet. Check every field there, "
                  "tick the boxes yourself and submit the issue: only then "
                  "is it public."),
                _("A maintainer reviews it and may accept it, ask for "
                  "changes or decline it."),
                _("The link holds the public details, so it can show up in "
                  "your browser history.")):
            about.add(_plain_label(text))
        page.add(about)
        state = {"launching": False, "opened": False}
        status = _plain_label("", visible=False)

        def tell(text, css):
            status.set_label(text)
            for c in ("success", "error", "dim-label"):
                status.remove_css_class(c)
            status.add_css_class(css)
            status.set_visible(True)

        def copy(text, field_label):
            try:
                self.get_clipboard().set(text)
            except (GLib.Error, TypeError) as e:
                log.warning("Could not copy %s: %s", field_label, e)
                tell(_("Could not copy: {error}").format(error=str(e)), "error")
                return
            log.info("Copied %s of a prepared submission", field_label)
            tell(_("Copied: {field}").format(field=field_label), "dim-label")

        if plan.left_out:
            group = Adw.PreferencesGroup(
                title=_("Too Long for the Link"),
                description=_("These don't fit into the link and stay empty "
                              "on GitHub. Copy each one and paste it into "
                              "its field there:"))
            for field in plan.left_out:
                label = GITHUB_LABELS[field]
                row = Adw.ActionRow(use_markup=False)
                row.set_title(label)
                button = Gtk.Button(label=_("Copy"), valign=Gtk.Align.CENTER)
                button.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_("Copy {field}").format(field=label)])
                button.connect("clicked", lambda b, f=field, l=label:
                               copy(plan.values[f], l))
                row.add_suffix(button)
                group.add(row)
            page.add(group)
            log.info("Submission link without %s (too long)",
                     ", ".join(plan.left_out))

        actions = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        go = Gtk.Button(label=_("Continue on GitHub…"), halign=Gtk.Align.CENTER)
        go.add_css_class("pill")
        go.add_css_class("suggested-action")
        copy_link = Gtk.Button(label=_("Copy Link"), halign=Gtk.Align.CENTER,
                               visible=False)
        copy_link.add_css_class("flat")
        copy_link.connect("clicked", lambda b: copy(plan.url, _("Link")))
        close = Gtk.Button(label=_("Close"), halign=Gtk.Align.CENTER)
        close.add_css_class("flat")
        close.connect("clicked", lambda b: dialog.close())
        for w in (go, status, copy_link, close):
            box.append(w)
        actions.add(box)
        page.add(actions)

        def opened(error):
            state["launching"] = False
            if error is not None:
                log.warning("Could not open the submission form: %s", error)
                tell(_("The browser could not be opened: {error}").format(
                    error=error), "error")
                copy_link.set_visible(True)     # to open it by hand
                go.set_sensitive(True)
                return
            state["opened"] = True
            log.info("Submission form opened in the browser")
            tell(_("GitHub opened. Review the information and submit the "
                   "issue there."), "success")

        def on_go(_button):
            if state["launching"] or state["opened"]:
                return                          # opened once is enough
            state["launching"] = True
            go.set_sensitive(False)
            open_uri(self, plan.url, opened)

        go.connect("clicked", on_go)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(Adw.HeaderBar(show_back_button=False))
        return Adw.NavigationPage(title=_("Share with Community"), child=toolbar,
                                  tag="done", can_pop=False)
