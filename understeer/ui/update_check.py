"""Telling about a newer Understeer: a banner that stays until updated,
and updating the AppImage from there.

Only the AppImage looks, at start and then every hour (at most once an
hour, also when started often), and only after the user agreed once.
"""
import logging
import os
import threading
import time

from gi.repository import Adw, GLib, Gtk

from .. import config, updates
from .. import log as session_log
from ..i18n import _
from ..releases import plain_version

log = logging.getLogger("understeer.window")

TICK_SECONDS = 600      # how often to see whether a look is due


class UpdateCheckMixin:
    """Part of TunerWindow. Settings: "update_check" (None: not asked yet),
    "update_checked" (time of the last look), "update_latest" (what GitHub
    answered then, so the banner shows right away at the next start)."""

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
        if not getattr(self, "_update_tick", 0):
            self._update_tick = GLib.timeout_add_seconds(
                TICK_SECONDS, lambda: self._look_for_update() or True)

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
        if on:
            config.set_setting("update_checked", None)      # look right away
            self._start_update_check()
        else:
            self._show_update_banner()

    def _look_for_update(self):
        if not config.get_setting("update_check") or not updates.due(
                config.get_setting("update_checked")):
            return
        config.set_setting("update_checked", time.time())

        def work():
            try:
                latest, error = updates.fetch_latest(), None
            except updates.Error as e:
                latest, error = None, e
            GLib.idle_add(lambda: self._update_found(latest, error) and False)

        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, latest, error):
        if error is not None:
            log.info("Could not look for a new version: %s", error)
            return
        log.info("Newest version on GitHub: %s", latest["version"])
        config.set_setting("update_latest", latest)
        self._show_update_banner()

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
        restart ("installed")."""
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

        def work():
            try:
                updates.install(updates.download(latest["asset"], appimage),
                                appimage)
                error = None
            except updates.Error as e:
                error = e
            GLib.idle_add(lambda: self._updated(error) and False)

        threading.Thread(target=work, daemon=True).start()

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
