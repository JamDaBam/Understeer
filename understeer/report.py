"""Reporting a problem: a GitHub issue filled in with what is needed.

The issue form (.github/ISSUE_TEMPLATE/bug_report.yml) has a field for
each part; GitHub fills them from the address, so the user only adds what
happened and sends it. Nothing is sent from here. Works without GTK.
"""
import getpass
import pathlib
import re
import urllib.parse

from . import log as session_log

REPO_URL = "https://github.com/JamDaBam/Understeer"
TEMPLATE = "bug_report.yml"
# Browsers and GitHub accept longer addresses, but not reliably more.
MAX_URL = 8000
LOG_LINES = 30          # last lines of the log
PROBLEM_LINES = 20      # ... plus the last warnings and errors before them


def sanitize(text, home=None, user=None):
    """Remove the home folder and user name, which a report doesn't need."""
    home = str(home or pathlib.Path.home())
    try:
        user = user or getpass.getuser()
    except (KeyError, OSError):
        user = None
    if home and home != "/":
        text = text.replace(home, "~")
    if user and len(user) > 1:
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text)
    return text


def log_excerpt(path=None, lines=LOG_LINES, problems=PROBLEM_LINES):
    """The end of the session log, plus earlier warnings and errors."""
    try:
        all_lines = pathlib.Path(path or session_log.LOG_FILE).read_text(
            errors="replace").splitlines()
    except OSError:
        return ""
    tail = all_lines[-lines:]
    earlier = all_lines[:-lines] if len(all_lines) > lines else []
    issues = [l for l in earlier if " WARNING " in l or " ERROR " in l][-problems:]
    if issues:
        tail = issues + ["..."] + tail
    return sanitize("\n".join(tail))


def system_info(extra=()):
    """Versions and details for the report: one "name: value" per line."""
    lines = session_log.debug_info().splitlines()
    lines += [f"{name}: {value}" for name, value in extra]
    return sanitize("\n".join(lines))


def issue_url(title, what, system, log="", max_len=MAX_URL):
    """Address of a new GitHub issue with the form filled in.

    If it gets too long, the oldest log lines are left out first, then the
    end of the description.
    """
    def build(log_text, what_text):
        params = {"template": TEMPLATE, "title": title,
                  "what-happened": what_text, "system": system}
        if log_text:
            params["log"] = log_text
        return (f"{REPO_URL}/issues/new?"
                + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))

    log_lines = log.splitlines()
    url = build(log, what)
    while len(url) > max_len and log_lines:
        log_lines = log_lines[max(1, len(log_lines) // 5):]
        url = build("\n".join(["...", *log_lines]) if log_lines else "", what)
    while len(url) > max_len and what:
        what = what[:len(what) * 4 // 5]
        url = build("", what + " …")
    return url


def report_text(title, what, system, log=""):
    """The same report as Markdown, to paste anywhere."""
    parts = [f"## {title}" if title else "", what, "### System", f"```\n{system}\n```"]
    if log:
        parts += ["### Log", f"```\n{log}\n```"]
    return "\n\n".join(p for p in parts if p)
