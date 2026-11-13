import pathlib
import re
import urllib.parse

from understeer import report

ROOT = pathlib.Path(__file__).resolve().parent.parent


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_sanitize_removes_home_and_user():
    text = "log in /home/jo/.local/state, user jo in groups jo, games; jogger"
    assert report.sanitize(text, home="/home/jo", user="jo") == \
        "log in ~/.local/state, user <user> in groups <user>, games; jogger"


def test_log_excerpt_keeps_end_and_earlier_problems(tmp_path):
    lines = [f"2026 INFO    understeer: line {i}" for i in range(100)]
    lines[10] = "2026 WARNING understeer.device: Could not write FF=70"
    (tmp_path / "u.log").write_text("\n".join(lines))
    text = report.log_excerpt(tmp_path / "u.log", lines=5).splitlines()
    assert text[0].endswith("Could not write FF=70")
    assert text[1] == "..."
    assert text[-1].endswith("line 99") and len(text) == 7


def test_log_excerpt_without_file(tmp_path):
    assert report.log_excerpt(tmp_path / "missing.log") == ""


def test_issue_url_fills_the_form():
    url = report.issue_url("Slots", "Slot 2 & 3 don't switch", "Understeer 1.5.0",
                           "a\nb")
    assert url.startswith(report.REPO_URL + "/issues/new?")
    assert params(url) == {"template": "bug_report.yml", "title": "Slots",
                           "what-happened": "Slot 2 & 3 don't switch",
                           "system": "Understeer 1.5.0", "log": "a\nb"}


def test_issue_url_drops_old_log_lines_first():
    log = "\n".join(f"log line {i:04d} " + "x" * 60 for i in range(400))
    url = report.issue_url("t", "what", "sys", log, max_len=3000)
    assert len(url) <= 3000
    p = params(url)
    assert p["what-happened"] == "what"
    assert p["log"].startswith("...") and p["log"].endswith("x" * 60)
    assert "log line 0399" in p["log"] and "log line 0000" not in p["log"]


def test_issue_url_shortens_a_huge_description():
    url = report.issue_url("t", "y" * 20000, "sys", "", max_len=3000)
    assert len(url) <= 3000 and params(url)["what-happened"].endswith(" …")


def test_template_has_the_fields_the_url_fills():
    template = (ROOT / ".github" / "ISSUE_TEMPLATE" / report.TEMPLATE).read_text()
    ids = set(re.findall(r"^\s+id:\s*(\S+)", template, re.M))
    assert ids == {"what-happened", "system", "health", "log"}


def test_health_check_in_the_report():
    url = report.issue_url("T", "what", "sys", health="Brake: (!) at 4.0 %")
    assert "health=Brake%3A%20%28%21%29%20at%204.0%20%25" in url
    assert "health=" not in report.issue_url("T", "what", "sys")
    text = report.report_text("T", "what", "sys", health="Brake: ok")
    assert "### Health check\n\n```\nBrake: ok\n```" in text
    assert "Health check" not in report.report_text("T", "what", "sys")


def test_report_text():
    text = report.report_text("Title", "It broke", "Understeer 1.5.0", "end")
    assert text.startswith("## Title") and "```\nUndersteer 1.5.0\n```" in text
    assert text.endswith("```\nend\n```")
