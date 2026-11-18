"""Telling about a newer Understeer: a banner that stays until updated,
and updating the AppImage from there.

Only the AppImage looks, at start and then every hour (at most once an
hour, also when started often), and only after the user agreed once.

Test versions (betas) only if the user also switched that on: at most one
look a day. A stable version is asked at most once for each release
(2.4.0-beta.2 isn't asked about again after 2.4.0-beta.1); a beta is
told once about each newer test version (2.4.0-beta.2 while running
2.4.0-beta.1). The question only shows the beta's page on GitHub;
nothing is downloaded or installed.
"""
import logging
import os
import time

from gi.repository import Adw, GLib, Gtk

from .. import config, updates
from .. import log as session_log
from ..i18n import _
from ..releases import is_prerelease, plain_version
from . import tasks

log = logging.getLogger("understeer.window")

TICK_SECONDS = 600      # how often to see whether a look is due


class UpdateCheckMixin:
    """Part of TunerWindow. Settings: "update_check" (None: not asked yet),
    "update_checked" (time of the last look), "update_latest" (what GitHub
    answered then, so the banner shows right away at the next start).
    Betas: "beta_notify" (only True asks), "beta_checked" (time of the
    last look), "beta_latest" (the beta found then), "beta_handled" (the
    newest release whose beta was asked about, e.g. "2.4.0"),
    "beta_notified" (the newest beta asked about, e.g. "2.4.0-beta.2")."""

    def _build_update_banner(self, content_view):
        self.update_banner = Adw.Banner()
        self.update_banner.connect("button-clicked",
                                   lambda b: self._on_update_button())
        content_view.add_top_bar(self.update_banner)
        self._show_update_banner()

    def _current_version(self):
        return plain_version(session_log.version())

    def _start_update_check(self):
        """At start: ask once, then look unless that happened within the
        last hour, and every few minutes see whether a look is due again."""
        if not updates.can_check(self._current_version()):
            return
        if config.get_setting("update_check") is None:
            if self.get_visible() and not self._in_background:
                self._ask_update_check()
            return                          # asked when the window opens
        self._show_update_banner()
        self._look_for_update()
        self._look_for_beta()
        self._offer_beta()
        if not getattr(self, "_update_tick", 0):
            self._update_tick = GLib.timeout_add_seconds(
                TICK_SECONDS, self._update_tick_done)

    def _update_tick_done(self):
        self._look_for_update()
        self._look_for_beta()
        self._offer_beta()          # e.g. once a dialog in the way is gone
        return True

    def _ask_update_check(self):
        """Once: may Understeer ask GitHub for new versions?"""
        if config.get_setting("update_check") is not None or getattr(
                self, "_asking_update", False):
            return
        self._asking_update = True
        dialog = Adw.AlertDialog(
            heading=_("Look for New Versions?"),
            body=_("Understeer can ask GitHub at start and every hour whether "
                   "there is a newer version and then show a note at the top. "
                   "Only the version number is fetched; nothing about you is "
                   "sent. You can change this in the preferences."))
        dialog.add_response("no", _("Don't Look"))
        dialog.add_response("yes", _("Look"))
        dialog.set_default_response("yes")
        dialog.set_close_response("no")
        dialog.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if not self._asking_update:
                return                      # answered already
            self._asking_update = False
            self.set_update_check(response == "yes")

        dialog.connect("response", on_response)
        dialog.present(self)

    def set_update_check(self, on):
        """Switch looking for new versions on or off (also the preference)."""
        config.set_setting("update_check", bool(on))
        log.info("Looking for new versions %s", "on" if on else "off")
        row = getattr(self, "update_row", None)
        if row is not None and row.get_active() != bool(on):
            row.set_active(bool(on))
        refresh = getattr(self, "_refresh_update_row", None)
        if refresh is not None and row is not None:
            refresh()                       # the beta row depends on it
        if on:
            config.set_setting("update_checked", None)      # look right away
            self._start_update_check()
        else:
            self._withdraw_beta()
            self._show_update_banner()

    def set_beta_notify(self, on):
        """Switch asking about test versions on or off (also the
        preference). Looking for new versions stays as it is."""
        config.set_setting("beta_notify", bool(on))
        log.info("Asking about test versions %s", "on" if on else "off")
        row = getattr(self, "beta_row", None)
        if row is not None and row.get_active() != bool(on):
            row.set_active(bool(on))
        if on:
            self._look_for_beta()
            self._offer_beta()
        else:
            self._withdraw_beta()

    def _look_for_update(self):
        if not config.get_setting("update_check") or not updates.due(
                config.get_setting("update_checked")):
            return
        config.set_setting("update_checked", time.time())

        tasks.in_thread(updates.fetch_latest, self._update_found, updates.Error)

    def _update_found(self, latest, error):
        if error is not None:
            log.info("Could not look for a new version: %s", error)
            return
        log.info("Newest version on GitHub: %s", latest["version"])
        config.set_setting("update_latest", latest)
        self._show_update_banner()

    # -- test versions ---------------------------------------------------------
    def _beta_wanted(self):
        """Whether the user wants to hear of betas: both switched on, in
        an AppImage that looks for new versions at all."""
        return (config.get_setting("update_check") is True
                and config.get_setting("beta_notify") is True
                and updates.can_check(self._current_version()))

    def _look_for_beta(self):
        """Ask GitHub for the newest beta, at most once a day (the time is
        kept before asking, so a failure waits a day too) and never twice
        at once. The time is also kept here, in case the settings file
        can't be written."""
        if (not self._beta_wanted() or getattr(self, "_beta_looking", False)
                or not updates.due(config.get_setting("beta_checked"),
                                   every=updates.BETA_CHECK_EVERY)
                or not updates.due(getattr(self, "_beta_looked", None),
                                   every=updates.BETA_CHECK_EVERY)):
            return
        self._beta_looking, self._beta_looked = True, time.time()
        config.set_setting("beta_checked", self._beta_looked)
        current = self._current_version()
        tasks.in_thread(lambda: updates.fetch_beta(current), self._beta_found,
                        updates.Error)

    def _beta_found(self, beta, error):
        self._beta_looking = False
        if not self._beta_wanted():
            log.info("Test versions were switched off meanwhile")
            return                          # a late answer: forget it
        if error is not None:
            log.info("Could not look for a test version: %s", error)
            return
        log.info("Newest test version on GitHub: %s",
                 beta["version"] if beta else "none")
        config.set_setting("beta_latest", beta)
        self._offer_beta()

    def _beta_to_offer(self):
        """The beta to ask about now, {"version", "url"}, or None."""
        if not self._beta_wanted() or self._available_update():
            return None                     # a stable update comes first
        beta = updates.checked_beta(config.get_setting("beta_latest"))
        stable = config.get_setting("update_latest")
        stable = stable.get("version") if isinstance(stable, dict) else None
        # asked about: as saved, and in this session in case saving failed
        handled = (config.get_text("beta_handled"),
                   getattr(self, "_beta_handled", None))
        notified = updates.newest(
            (config.get_setting("beta_notified"),
             getattr(self, "_beta_notified", None)), updates.notified_beta)
        if beta is None or not all(updates.should_offer_beta(
                beta, self._current_version(), stable, done, notified)
                for done in handled):
            return None
        return beta

    def _remember_beta(self, beta):
        """Count beta as asked about: its release for the invitation of a
        stable version, the beta itself for a beta; kept in this session
        too, in case saving fails. Never back to an older one, e.g. from a
        question that was open while a newer one was answered."""
        self._beta_handled = updates.newest(
            (updates.beta_series(beta["version"]),
             config.get_text("beta_handled"),
             getattr(self, "_beta_handled", None)), updates.handled_series)
        config.set_setting("beta_handled", self._beta_handled)
        self._beta_notified = updates.newest(
            (beta["version"], config.get_setting("beta_notified"),
             getattr(self, "_beta_notified", None)), updates.notified_beta)
        config.set_setting("beta_notified", self._beta_notified)

    def _offer_beta(self):
        """Ask once about a new beta, only with the window open in front
        and nothing else asking; otherwise later, without counting it as
        asked."""
        beta = self._beta_to_offer()
        if (beta is None or getattr(self, "_beta_dialog", None) is not None
                or getattr(self, "_update_state", None)
                or not self.get_visible() or self._in_background
                or self.get_visible_dialog() is not None):
            return
        heading = _("Test Version Available")
        if is_prerelease(self._current_version()):
            body = _("Understeer {version} is a newer test version than the "
                     "one you use. Understeer tells you once about each newer "
                     "test version; nothing is downloaded or installed.")
        else:
            body = _("Understeer {version} is a test version of the next "
                     "release. You can try it and tell what works and what "
                     "doesn't. Understeer asks only once for each release; "
                     "nothing is downloaded or installed.")
        dialog = Adw.AlertDialog(heading=heading,
                                 body=body.format(version=beta["version"]))
        dialog.add_response("later", _("Not Now"))
        dialog.add_response("view", _("View on GitHub"))
        dialog.set_default_response("view")
        dialog.set_close_response("later")
        dialog.set_response_appearance("view", Adw.ResponseAppearance.SUGGESTED)

        def handled(d, response):
            if self._beta_dialog is not d:
                return                      # answered, withdrawn or replaced
            self._beta_dialog = None
            if not self._beta_wanted():
                log.info("Test versions were switched off meanwhile")
                return                      # not counted as asked
            if self._available_update():
                log.info("A stable update came meanwhile")
                return                      # it comes first: not counted
            self._remember_beta(beta)
            log.info("Asked about test version %s: %s", beta["version"],
                     response)
            if response == "view":
                self._open_beta_page(beta)

        dialog.connect("response", handled)
        dialog.connect("closed", lambda d: handled(d, "later"))
        self._beta_dialog = dialog
        log.info("Asking about test version %s", beta["version"])
        dialog.present(self)

    def _withdraw_beta(self, reason="Test versions switched off"):
        """Close a question about a beta when it was switched off or a
        stable update came, without counting it as answered."""
        dialog, self._beta_dialog = getattr(self, "_beta_dialog", None), None
        if dialog is not None:
            log.info("%s: question closed", reason)
            dialog.force_close()

    def _open_beta_page(self, beta):
        """Open the beta's own page on GitHub, nothing else."""
        beta = updates.checked_beta(beta)
        if beta is None:
            return
        log.info("Opening the page of test version %s", beta["url"])
        Gtk.UriLauncher.new(beta["url"]).launch(self, None, None)

    def _available_update(self):
        """{"version", "url", "asset"} of a newer version known, or None."""
        latest = config.get_setting("update_latest")
        if (not config.get_setting("update_check") or not isinstance(latest, dict)
                or not updates.can_check(self._current_version())):
            return None
        version = latest.get("version")
        if not isinstance(version, str) or not updates.is_newer(
                version, self._current_version()):
            return None
        return latest

    @staticmethod
    def _appimage():
        return os.environ.get("APPIMAGE")

    def _can_update_itself(self, latest):
        return bool(latest.get("asset")) and updates.can_install(self._appimage())

    def _show_update_banner(self):
        """The banner for the state of updating: available, being updated
        (self._update_state "updating"), or installed and waiting for a
        restart ("installed"). A stable update available withdraws a
        question about a beta: it comes first."""
        if not hasattr(self, "update_banner"):
            return
        state = getattr(self, "_update_state", None)
        latest = self._available_update()
        banner = self.update_banner
        if state == "installed":
            banner.set_title(_("Understeer {version} is installed. Restart to "
                               "use it.").format(version=self._update_version))
            banner.set_button_label(_("Restart"))
        elif state == "updating":
            banner.set_title(_("Updating to Understeer {version}…").format(
                version=self._update_version))
            banner.set_button_label(None)
        elif latest:
            banner.set_title(_("Understeer {version} is available").format(
                version=latest["version"]))
            banner.set_button_label(_("Update Now")
                                    if self._can_update_itself(latest)
                                    else _("Download"))
        banner.set_revealed(bool(state or latest))
        if latest:                          # a stable update comes first
            self._withdraw_beta("Stable update available")

    def _on_update_button(self):
        state = getattr(self, "_update_state", None)
        latest = self._available_update()
        if state == "installed":
            self._restart_for_update()
        elif state is None and latest and self._can_update_itself(latest):
            self._update_itself(latest)
        elif state is None:
            self._open_update_page()

    def _update_itself(self, latest):
        """Download, check and put the new AppImage in place, in a thread."""
        appimage = self._appimage()
        self._update_state, self._update_version = "updating", latest["version"]
        self._show_update_banner()
        log.info("Updating %s to %s", appimage, latest["version"])

        tasks.in_thread(
            lambda: updates.install(updates.download(latest["asset"], appimage),
                                    appimage),
            lambda _result, error: self._updated(error), updates.Error)

    def _updated(self, error):
        if error is None:
            self._update_state = "installed"
            self._show_update_banner()
            return
        log.warning("Update failed: %s", error)
        self._update_state = None
        self._show_update_banner()
        dialog = Adw.AlertDialog(
            heading=_("Update Failed"),
            body=_("Understeer could not update itself: {reason}. Your "
                   "version stays as it is. You can download the new one "
                   "from GitHub instead.").format(reason=error))
        dialog.add_response("close", _("Close"))
        dialog.add_response("download", _("Download"))
        dialog.set_default_response("download")
        dialog.set_close_response("close")
        dialog.set_response_appearance("download",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: r == "download"
                       and self._open_update_page())
        dialog.present(self)

    def _restart_for_update(self):
        """Quit (asking about unsaved preset changes as always) and start
        the new AppImage."""
        app = self.get_application()
        if app is not None:
            app.quit_all(restart=self._appimage())

    def _open_update_page(self):
        latest = self._available_update() or {}
        url = latest.get("url", updates.RELEASES_URL)
        if not url.startswith(updates.RELEASES_URL):
            url = updates.RELEASES_URL
        log.info("Opening the download page %s", url)
        Gtk.UriLauncher.new(url).launch(self, None, None)
