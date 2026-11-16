"""Which tags are released, and how: stable, or as a prerelease that never
becomes the latest release. Nothing here creates a tag or a release."""
import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "release_tag.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

spec = importlib.util.spec_from_file_location("release_tag", SCRIPT)
release_tag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_tag)


@pytest.mark.parametrize("tag, version", [
    ("v2.4.0", "2.4.0"), ("v2.3.3", "2.3.3"), ("v1.10.0", "1.10.0"),
    ("v0.0.0", "0.0.0"), ("v10.20.30", "10.20.30"),
])
def test_stable_tags(tag, version):
    assert release_tag.release(tag) == (version, "stable")
    assert release_tag.outputs(tag) == [f"version={version}", "kind=stable",
                                        "flags="]


@pytest.mark.parametrize("tag, version", [
    ("v2.4.0-beta.1", "2.4.0-beta.1"), ("v2.4.0-beta.2", "2.4.0-beta.2"),
    ("v3.0.0-beta.10", "3.0.0-beta.10"),
])
def test_beta_tags(tag, version):
    assert release_tag.release(tag) == (version, "beta")
    assert release_tag.outputs(tag) == [
        f"version={version}", "kind=beta", "flags=--prerelease --latest=false"]


@pytest.mark.parametrize("tag", [
    # other test versions
    "v2.4.0-rc1", "v2.4.0-rc.1", "v2.4.0-alpha.1", "v2.4.0-beta",
    "v2.4.0-beta.", "v2.4.0-beta1", "v2.4.0-Beta.1", "v2.4.0-beta.1.2",
    "v2.4.0-beta.x", "v2.4.0-beta.-1", "v2.4.0+build.1", "v2.4.0-3-gabc",
    # malformed versions and empty parts
    "", "v", "2.4.0", "V2.4.0", "v2.4", "v2", "v2.4.0.1", "v2..0", ".2.4.0",
    "v.4.0", "v2.4.", "vv2.4.0", "v-2.4.0", "v2.4.x", "v٢.٤.٠", "v２.4.0",
    # whitespace and values that are no tags
    " v2.4.0", "v2.4.0 ", "v2.4.0\n", "v2.4.0\nv2.4.1", "v2.4.0;id",
    "v2.4.0$(id)", "v2.4.0`id`", "refs/tags/v2.4.0", "latest", "main",
    None, 2, ["v2.4.0"],
])
def test_other_tags_are_never_released(tag):
    with pytest.raises(ValueError):
        release_tag.release(tag)


def test_only_betas_are_prereleases_and_never_latest():
    assert release_tag.FLAGS == {"stable": (),
                                 "beta": ("--prerelease", "--latest=false")}


def run(*args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=10)


def release_xml(*releases_):
    """A metainfo file with these <release> elements (as XML text)."""
    return ("<component><releases>" + "".join(releases_)
            + "</releases></component>")


def entry(version, date="2026-11-20", kind=None, notes=True):
    attrs = f' version="{version}"'
    if date is not None:
        attrs += f' date="{date}"'
    if kind is not None:
        attrs += f' type="{kind}"'
    body = (f"<description><p>Notes of {version}</p>"
            f'<p xml:lang="de">Notizen zu {version}</p></description>'
            if notes else "")
    return f"<release{attrs}>{body}</release>"


READY = release_xml(entry("2.4.0-beta.1", kind="development"),
                    entry("2.3.3", date="2026-11-13"))


@pytest.fixture
def metainfo(tmp_path):
    def write(text=READY):
        path = tmp_path / "metainfo.xml"
        path.write_text(text, encoding="utf-8")
        return path
    return write


def test_script_prints_the_outputs(metainfo):
    path = str(metainfo())
    done = run("--metainfo", path, "v2.4.0-beta.1")
    assert done.returncode == 0, done.stderr
    assert done.stdout == ("version=2.4.0-beta.1\nkind=beta\n"
                           "flags=--prerelease --latest=false\n")
    assert run("--metainfo", path, "v2.3.3").stdout == \
        "version=2.3.3\nkind=stable\nflags=\n"


@pytest.mark.parametrize("args", [["v2.4.0-rc1"], ["v2.4.0\nkind=stable"],
                                  [], ["v2.4.0", "v2.4.1"],
                                  ["--metainfo", "x.xml"],
                                  ["--metainfo", "v2.3.3", "v2.3.3", "x"]])
def test_script_fails_and_prints_nothing(args):
    """Nothing reaches $GITHUB_OUTPUT, and the step fails."""
    done = run(*args)
    assert done.returncode != 0
    assert done.stdout == ""


# -- the release in the metainfo file ----------------------------------------

def test_ready_releases_pass(metainfo):
    path = metainfo()
    release_tag.check_metainfo("v2.4.0-beta.1", path)
    release_tag.check_metainfo("v2.3.3", path)
    release_tag.check_metainfo("v2.3.3", metainfo(release_xml(
        entry("2.3.3", kind="stable"))))


@pytest.mark.parametrize("text, tag, reason", [
    (release_xml(entry("2.3.3")), "v2.4.0-beta.1", "there is no"),
    (release_xml(entry("2.3.3")), "v2.3.4", "there is no"),
    (release_xml(entry("2.4.0-beta.1", kind="development")), "v2.4.0-beta.2",
     "there is no"),                                   # tag and version differ
    (release_xml(entry("2.4.0-beta.1", kind="development")), "v2.4.0",
     "there is no"),
    (release_xml(entry("2.3.3"), entry("2.3.3")), "v2.3.3", "2 times"),
    (release_xml(entry("2.4.0-beta.1", None, "development")), "v2.4.0-beta.1",
     "no date yet"),
    (release_xml(entry("2.3.3", None)), "v2.3.3", "no date yet"),
    (release_xml(entry("2.3.3", "")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "YYYY-MM-DD")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "TBD")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "0000-00-00")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "2026-11-31")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "2026-02-29")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "2026-13-01")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "20261113")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "2026-1-13")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "13.11.2026")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "2026-11-13T10:00")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", " 2026-11-13")), "v2.3.3", "no real day"),
    (release_xml(entry("2.3.3", "２026-11-13")), "v2.3.3", "no real day"),
    (release_xml(entry("2.4.0-beta.1")), "v2.4.0-beta.1", 'type="development"'),
    (release_xml(entry("2.4.0-beta.1", kind="stable")), "v2.4.0-beta.1",
     'type="development"'),
    (release_xml(entry("2.3.3", kind="development")), "v2.3.3",
     "is a stable release"),
    (release_xml(entry("2.3.3", kind="snapshot")), "v2.3.3",
     "is a stable release"),
    (release_xml(entry("2.3.3", notes=False)), "v2.3.3", "has no notes"),
    ("<component><releases><release", "v2.3.3", "can't be read"),
])
def test_unready_releases_fail(metainfo, text, tag, reason):
    with pytest.raises(ValueError, match=reason):
        release_tag.check_metainfo(tag, metainfo(text))


def test_a_missing_file_fails(tmp_path):
    with pytest.raises(ValueError, match="can't be read"):
        release_tag.check_metainfo("v2.3.3", tmp_path / "missing.xml")


def test_a_bad_tag_fails_before_the_file_is_read(tmp_path):
    with pytest.raises(ValueError, match="no release tag"):
        release_tag.check_metainfo("v2.4.0-rc1", tmp_path / "missing.xml")


def test_script_refuses_an_unready_release(metainfo):
    """Fails with a message, and nothing reaches $GITHUB_OUTPUT."""
    path = str(metainfo(release_xml(entry("2.4.0-beta.1", None, "development"))))
    done = run("--metainfo", path, "v2.4.0-beta.1")
    assert done.returncode == 1 and done.stdout == ""
    assert "has no date yet" in done.stderr


def test_the_shipped_metainfo():
    """2.3.3 is ready; 2.4.0-beta.1 is prepared without its date, so it
    can't be released yet."""
    release_tag.check_metainfo("v2.3.3")
    with pytest.raises(ValueError, match="no date yet"):
        release_tag.check_metainfo("v2.4.0-beta.1")
    done = run("v2.4.0-beta.1")
    assert done.returncode == 1 and done.stdout == ""
    assert run("v2.3.3").stdout == "version=2.3.3\nkind=stable\nflags=\n"


@pytest.fixture(scope="module")
def workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def steps(text):
    """The step names of the workflow, in order."""
    return re.findall(r"^\s+- name: (.+)$", text, re.M)


def test_workflow_checks_the_tag_before_anything_else(workflow):
    assert 'python3 tools/release_tag.py "$GITHUB_REF_NAME" >> "$GITHUB_OUTPUT"' \
        in workflow
    assert re.search(r"^  test:\n    needs: tag\b", workflow, re.M)
    assert re.search(r"^  build:\n    needs: \[tag, test\]", workflow, re.M)
    assert steps(workflow) == ["Check the tag and its release notes",
                               "Release notes", "Build AppImage",
                               "Check the files to publish", "Publish release"]
    # the tag job runs the check with the tag only, never another file
    tag_job = workflow.split("\n  test:", 1)[0]
    assert "--metainfo" not in tag_job


def test_workflow_publishes_with_the_flags_of_the_tag(workflow):
    publish = workflow.split("- name: Publish release", 1)[1]
    assert "FLAGS: ${{ needs.tag.outputs.flags }}" in publish
    assert '--generate-notes "${flags[@]}"' in publish
    assert 'beta) test "${flags[*]}" = "--prerelease --latest=false" ;;' in publish
    assert 'stable) test "${#flags[@]}" -eq 0 ;;' in publish
    # the asset names stay as they were: Understeer-<version>-x86_64.AppImage
    assert '"Understeer-$VERSION-x86_64.AppImage"' in publish
    assert '"Understeer-$VERSION-x86_64.AppImage.zsync"' in publish


def test_workflow_keeps_its_permissions(workflow):
    assert re.findall(r"^\s*(\w+): (read|write)$", workflow, re.M) == [
        ("contents", "write"), ("contents", "read")]
    assert "secrets." not in workflow
    assert "pull_request" not in workflow


def test_workflow_puts_no_expression_into_a_script(workflow):
    """Tag names and outputs reach the shell as variables only."""
    in_run = False
    for line in workflow.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run = True
            indent = len(line) - len(line.lstrip())
            assert "${{" not in stripped, line
            continue
        if in_run and stripped and len(line) - len(line.lstrip()) <= indent:
            in_run = False
        if in_run:
            assert "${{" not in line, line
