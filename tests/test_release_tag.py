"""Which tags are released, and how: stable, or as a prerelease that never
becomes the latest release. Nothing here creates a tag or a release."""
import hashlib
import importlib.util
import os
import shutil
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


def test_a_prepared_next_beta_waits_for_its_date(metainfo):
    """While the next beta is prepared without its date, it can't be
    released yet, and the dated releases below it still can."""
    path = metainfo(release_xml(
        entry("2.4.0-beta.2", None, "development"),
        entry("2.4.0-beta.1", "2026-11-17", "development"),
        entry("2.3.3", "2026-11-13")))
    with pytest.raises(ValueError, match="no date yet"):
        release_tag.check_metainfo("v2.4.0-beta.2", path)
    done = run("--metainfo", str(path), "v2.4.0-beta.2")
    assert done.returncode == 1 and done.stdout == ""
    assert "has no date yet" in done.stderr
    release_tag.check_metainfo("v2.4.0-beta.1", path)
    release_tag.check_metainfo("v2.3.3", path)
    assert run("--metainfo", str(path), "v2.4.0-beta.1").stdout == (
        "version=2.4.0-beta.1\nkind=beta\nflags=--prerelease --latest=false\n")
    assert run("--metainfo", str(path), "v2.3.3").stdout == \
        "version=2.3.3\nkind=stable\nflags=\n"


def test_script_refuses_an_unready_release(metainfo):
    """Fails with a message, and nothing reaches $GITHUB_OUTPUT."""
    path = str(metainfo(release_xml(entry("2.4.0-beta.1", None, "development"))))
    done = run("--metainfo", path, "v2.4.0-beta.1")
    assert done.returncode == 1 and done.stdout == ""
    assert "has no date yet" in done.stderr


def test_the_shipped_metainfo():
    """2.3.3 and 2.4.0-beta.1 to beta.4 are dated and ready; each beta is
    published as a prerelease that doesn't become the latest release."""
    release_tag.check_metainfo("v2.3.3")
    for beta in ("2.4.0-beta.1", "2.4.0-beta.2", "2.4.0-beta.3", "2.4.0-beta.4"):
        release_tag.check_metainfo(f"v{beta}")
        done = run(f"v{beta}")
        assert done.returncode == 0, done.stderr
        assert done.stdout == (
            f"version={beta}\nkind=beta\nflags=--prerelease --latest=false\n")
    assert run("v2.3.3").stdout == "version=2.3.3\nkind=stable\nflags=\n"


LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def shipped_releases():
    import xml.etree.ElementTree as ET
    root = ET.parse(ROOT / "io.github.JamDaBam.Understeer.metainfo.xml").getroot()
    return root.findall("releases/release")


def test_the_shipped_beta_4_is_dated_and_ready(metainfo):
    """2.4.0-beta.4 is in the metainfo file as the newest release, a test
    version with notes in English and German and the day it is released:
    it is released as a beta, a prerelease that doesn't become the latest.
    The same entry without its date still can't be released. The releases
    below it stay as they are."""
    entries = shipped_releases()
    versions = [e.get("version") for e in entries]
    assert versions[:5] == ["2.4.0-beta.4", "2.4.0-beta.3", "2.4.0-beta.2",
                            "2.4.0-beta.1", "2.3.3"]
    assert len(versions) == len(set(versions))          # each exactly once
    beta4 = entries[0]
    assert beta4.get("type") == "development"
    assert beta4.get("date") == "2026-11-28"
    description = beta4.find("description")
    for tag in ("p", "ul/li"):
        english = [e.text for e in description.findall(tag) if e.get(LANG) is None]
        german = [e.text for e in description.findall(tag) if e.get(LANG) == "de"]
        assert english and len(english) == len(german)
        assert all(text and text.strip() for text in english + german)
    release_tag.check_metainfo("v2.4.0-beta.4")
    done = run("v2.4.0-beta.4")
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "version=2.4.0-beta.4\nkind=beta\nflags=--prerelease --latest=false\n")

    # The same file without the day: nothing to publish
    shipped = (ROOT / "io.github.JamDaBam.Understeer.metainfo.xml").read_text(
        encoding="utf-8")
    dated = '<release version="2.4.0-beta.4" date="2026-11-28" type="development">'
    assert shipped.count(dated) == 1
    path = metainfo(shipped.replace(
        dated, '<release version="2.4.0-beta.4" type="development">'))
    with pytest.raises(ValueError, match="no date yet"):
        release_tag.check_metainfo("v2.4.0-beta.4", path)
    done = run("--metainfo", str(path), "v2.4.0-beta.4")
    assert done.returncode == 1 and done.stdout == ""
    assert "has no date yet" in done.stderr


def test_the_shipped_beta_3_is_dated_and_ready():
    """2.4.0-beta.3, the release below beta.4, is a test
    version with notes in English and German and the day it was released."""
    entries = shipped_releases()
    versions = [e.get("version") for e in entries]
    assert versions[1:5] == ["2.4.0-beta.3", "2.4.0-beta.2", "2.4.0-beta.1", "2.3.3"]
    assert len(versions) == len(set(versions))          # each exactly once
    beta3 = entries[1]
    assert beta3.get("type") == "development"
    assert beta3.get("date") == "2026-11-22"
    description = beta3.find("description")
    for tag in ("p", "ul/li"):
        english = [e.text for e in description.findall(tag) if e.get(LANG) is None]
        german = [e.text for e in description.findall(tag) if e.get(LANG) == "de"]
        assert english and len(english) == len(german)
        assert all(text and text.strip() for text in english + german)
    release_tag.check_metainfo("v2.4.0-beta.3")
    done = run("v2.4.0-beta.3")
    assert done.returncode == 0, done.stderr
    assert done.stdout == (
        "version=2.4.0-beta.3\nkind=beta\nflags=--prerelease --latest=false\n")


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
                               "Check that the tagged commit is on main",
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
    assert 'stable) test "${#flags[@]}" -eq 0' in publish
    # the asset names stay as they were: Understeer-<version>-x86_64.AppImage
    assert 'files=("Understeer-$VERSION-x86_64.AppImage")' in publish
    assert 'files+=("Understeer-$VERSION-x86_64.AppImage.zsync")' in publish


TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def permissions(text):
    """{None: the workflow's permissions, job: its own} as written, from
    the indentation (the CI has no YAML library). A value on the same line,
    e.g. "permissions: write-all", is kept as text."""
    found, job, in_jobs = {}, None, False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if in_jobs and m:
            job = m.group(1)
            continue
        m = re.match(r"^( *)permissions:\s*(.*?)\s*(#.*)?$", line)
        if not m:
            continue
        key = job if in_jobs else None
        assert key not in found, f"permissions twice for {key}"
        if m.group(2):
            found[key] = m.group(2)
            continue
        scopes, indent = {}, len(m.group(1))
        for inner in lines[i + 1:]:
            if not inner.strip() or inner.strip().startswith("#"):
                continue
            if len(inner) - len(inner.lstrip()) <= indent:
                break
            name, value = inner.split("#")[0].split(":")
            scopes[name.strip()] = value.strip()
        found[key] = scopes
    return found


def job_names(text):
    return re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text.split("\njobs:\n", 1)[1],
                      re.M)


def test_release_workflow_has_least_privilege(workflow):
    perms = permissions(workflow)
    assert perms == {None: {"contents": "read"},          # the default
                     "tag": {"contents": "read"},
                     "test": {"contents": "read"},
                     "build": {"contents": "write"}}    # gh release create
    # every job says what it gets, so none inherits more by accident
    assert set(job_names(workflow)) == {"tag", "test", "build"}
    assert "secrets." not in workflow and "pull_request" not in workflow
    assert "write-all" not in workflow and "read-all" not in workflow


def test_the_test_workflow_only_reads():
    text = TESTS_WORKFLOW.read_text(encoding="utf-8")
    assert permissions(text) == {None: {"contents": "read"}}
    assert "write" not in text and "secrets." not in text


def test_the_permission_reader():
    text = ("on: push\npermissions:\n  contents: read  # c\n\njobs:\n"
            "  a:\n    permissions: write-all\n    steps: []\n"
            "  b:\n    permissions:\n      contents: write\n"
            "      packages: read\n    runs-on: x\n")
    assert permissions(text) == {None: {"contents": "read"}, "a": "write-all",
                                 "b": {"contents": "write", "packages": "read"}}
    assert job_names(text) == ["a", "b"]


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



# -- which builds get AppImage update information ----------------------------

BUILD = ROOT / "build.sh"
UPDATE_INFO = ("gh-releases-zsync|JamDaBam|Understeer|latest|"
               "Understeer-*x86_64.AppImage.zsync")


@pytest.mark.parametrize("version, kind", [
    ("2.3.3", "stable"), ("v2.3.3", "stable"), ("1.10.0", "stable"),
    ("2.4.0-beta.1", "beta"), ("v2.4.0-beta.1", "beta"), ("3.0.0-beta.10", "beta"),
    ("2.3.3-8-g37781ef", "development"), ("v2.3.3-8-g37781ef", "development"),
    ("2.3.3-8-g37781ef-dirty", "development"), ("2.3.3-dirty", "development"),
    ("2.4.0-beta.1-2-gabc", "development"), ("37781ef", "development"),
    ("dev", "development"), ("unknown", "development"), ("", "development"),
    ("2.4.0-rc1", "development"), ("2.4", "development"), ("v", "development"),
    ("vv2.3.3", "development"), ("2.3.3 ", "development"),
    ("2.3.3\n", "development"), ("٢.٣.٣", "development"), (None, "development"),
])
def test_build_kind(version, kind):
    """Nothing unknown counts as stable."""
    assert release_tag.build_kind(version) == kind


def test_build_kind_from_the_command_line():
    for version, kind in (("v2.3.3", "stable"), ("2.4.0-beta.1", "beta"),
                          ("2.3.3-8-gabc", "development")):
        done = run("--build-kind", version)
        assert (done.returncode, done.stdout) == (0, kind + "\n")


# The build tools as pinned in build.sh, and stand-ins for the tests: the
# test copy of build.sh pins these instead, so nothing real is downloaded
# or run.
TOOL_URL = ("https://github.com/AppImage/appimagetool/releases/download/"
            "continuous/appimagetool-x86_64.AppImage")
TOOL_SHA256 = "e8c1169c4af66c3184d7ee110713d4be04befe671fa358e2c1591ee840553b03"
RUNTIME_URL = ("https://github.com/AppImage/type2-runtime/releases/download/"
               "continuous/runtime-x86_64")
RUNTIME_SHA256 = "617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"
FAKE_TOOL = (b'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done '
             b'> args.txt\n')
FAKE_RUNTIME = b"\x7fELF a stand-in for the runtime\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Build:
    """Steps 5 and 6 of build.sh in a folder of their own, with a wget that
    serves files from server/ (and notes each call) and an appimagetool
    stand-in that notes its arguments in args.txt."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "build"
        self.server = tmp_path / "server"
        self.log = tmp_path / "wget.log"
        for d in (self.dir, self.server, tmp_path / "bin"):
            d.mkdir()
        (self.dir / "tools").mkdir()
        shutil.copy(SCRIPT, self.dir / "tools" / "release_tag.py")
        (self.dir / "understeer").symlink_to(ROOT / "understeer")
        (self.dir / "AppDir" / "usr" / "share" / "understeer").mkdir(parents=True)
        wget = tmp_path / "bin" / "wget"
        wget.write_text(
            '#!/bin/sh\necho "$@" >> "$WGET_LOG"\nout=; url=\n'
            'while [ $# -gt 0 ]; do case "$1" in -O) out="$2"; shift 2;; '
            '-*) shift;; *) url="$1"; shift;; esac; done\n'
            'if [ -n "$WGET_FAIL" ]; then printf partial > "$out"; exit 4; fi\n'
            'cat "$FAKE_SERVER/$(basename "$url")" > "$out"\n')
        wget.chmod(0o755)
        self.path = f"{tmp_path / 'bin'}:{os.environ['PATH']}"
        self.serve(FAKE_TOOL, FAKE_RUNTIME)

    def serve(self, tool, runtime):
        (self.server / "appimagetool-x86_64.AppImage").write_bytes(tool)
        (self.server / "runtime-x86_64").write_bytes(runtime)

    def cache(self, tool=FAKE_TOOL, runtime=FAKE_RUNTIME):
        if tool is not None:
            (self.dir / "appimagetool-x86_64.AppImage").write_bytes(tool)
        if runtime is not None:
            (self.dir / "runtime-x86_64").write_bytes(runtime)

    def run(self, version="v2.3.3", wget_fails=False,
            tool_sha=sha256(FAKE_TOOL), runtime_sha=sha256(FAKE_RUNTIME)):
        text = BUILD.read_text(encoding="utf-8")
        step = text[text.index('echo "=== Step 5'):]
        step = step.replace(TOOL_SHA256, tool_sha).replace(RUNTIME_SHA256,
                                                            runtime_sha)
        env = {**os.environ, "VERSION": version, "PATH": self.path,
               "WGET_LOG": str(self.log), "FAKE_SERVER": str(self.server),
               "WGET_FAIL": "1" if wget_fails else ""}
        return subprocess.run(["bash", "-e", "-c", step], cwd=self.dir, env=env,
                              capture_output=True, text=True, timeout=30)

    @property
    def args(self):
        path = self.dir / "args.txt"
        return path.read_text().splitlines() if path.exists() else None

    @property
    def downloads(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def file(self, name):
        return self.dir / name


@pytest.fixture
def build(tmp_path):
    return Build(tmp_path)


def build_step(build, version):
    """A build with both tools already there and right."""
    build.cache()
    done = build.run(version)
    assert done.returncode == 0, done.stderr
    return build.args, done.stdout


def test_a_stable_build_keeps_its_update_information(build):
    args, _out = build_step(build, "v2.3.3")
    assert args == ["--runtime-file", "runtime-x86_64", "-u", UPDATE_INFO,
                    "AppDir", "Understeer-2.3.3-x86_64.AppImage"]
    assert (build.dir / "AppDir/usr/share/understeer/VERSION").read_text() \
        == "2.3.3\n"


@pytest.mark.parametrize("version, name, kind", [
    ("v2.4.0-beta.1", "2.4.0-beta.1", "beta"),
    ("2.3.3-8-g37781ef-dirty", "2.3.3-8-g37781ef-dirty", "development"),
    ("dev", "dev", "development"),
])
def test_other_builds_have_no_update_information(build, version, name, kind):
    args, out = build_step(build, version)
    assert args == ["--runtime-file", "runtime-x86_64", "AppDir",
                    f"Understeer-{name}-x86_64.AppImage"]
    assert "-u" not in args and UPDATE_INFO not in args
    assert f"No update information in a {kind} build" in out


# -- getting the pinned build tools ----------------------------------------------

def test_missing_tools_are_downloaded_checked_and_used(build):
    done = build.run("v2.4.0-beta.1")
    assert done.returncode == 0, done.stderr
    # each from its fixed address, into a temporary file first
    calls = [line.split() for line in build.downloads]
    assert [c[-1] for c in calls] == [RUNTIME_URL, TOOL_URL]
    assert [c[2].startswith(n + ".download.") for c, n in zip(
        calls, ("runtime-x86_64", "appimagetool-x86_64.AppImage"))] == [True, True]
    assert build.file("appimagetool-x86_64.AppImage").read_bytes() == FAKE_TOOL
    assert build.file("runtime-x86_64").read_bytes() == FAKE_RUNTIME
    assert os.access(build.file("appimagetool-x86_64.AppImage"), os.X_OK)
    assert not list(build.dir.glob("*.download.*"))     # moved into place
    assert build.args[:2] == ["--runtime-file", "runtime-x86_64"]


def test_checked_tools_are_not_downloaded_again(build):
    build_step(build, "v2.3.3")
    assert build.downloads == []


@pytest.mark.parametrize("which", ["tool", "runtime"])
def test_a_changed_upstream_file_stops_the_build(build, which):
    """Upstream replaced the file: the build fails on purpose, nothing of
    the new file is used, and the pinned value stays as it is."""
    changed = b"#!/bin/sh\necho changed > args.txt\n"
    if which == "tool":
        build.serve(changed, FAKE_RUNTIME)
    else:
        build.serve(FAKE_TOOL, changed)
    done = build.run()
    assert done.returncode != 0
    assert "is not the expected file" in done.stderr
    assert "update the SHA-256 in build.sh" in done.stderr
    assert build.args is None                           # nothing ran
    name = "appimagetool-x86_64.AppImage" if which == "tool" else "runtime-x86_64"
    assert not build.file(name).exists()                # not put in place
    assert len(list(build.dir.glob(name + ".download.*"))) == 1
    assert TOOL_SHA256 in BUILD.read_text() and RUNTIME_SHA256 in BUILD.read_text()


def test_a_failed_download_leaves_no_usable_tool(build):
    done = build.run(wget_fails=True)
    assert done.returncode != 0
    assert "could not download" in done.stderr
    assert build.args is None
    assert not build.file("runtime-x86_64").exists()
    assert not build.file("appimagetool-x86_64.AppImage").exists()
    # the incomplete file has another name, so no later build takes it
    assert [p.read_bytes() for p in build.dir.glob("*.download.*")] == [b"partial"]
    done = build.run()                                  # the next try works
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tool, runtime", [
    (FAKE_TOOL + b"#", FAKE_RUNTIME),                   # changed tool
    (FAKE_TOOL[:-5], FAKE_RUNTIME),                     # cut short
    (b"", FAKE_RUNTIME),                                # empty
    (FAKE_TOOL, FAKE_RUNTIME + b"x"),                   # changed runtime
    (FAKE_TOOL, b""),
], ids=["changed tool", "tool cut short", "empty tool", "changed runtime",
        "empty runtime"])
def test_a_wrong_cached_tool_stops_the_build_and_stays(build, tool, runtime):
    build.cache(tool, runtime)
    done = build.run()
    assert done.returncode != 0
    assert "is not the expected file" in done.stderr
    assert "move it away" in done.stderr
    assert build.args is None                           # nothing ran
    assert build.downloads == []                        # nor replaced
    assert build.file("appimagetool-x86_64.AppImage").read_bytes() == tool
    assert build.file("runtime-x86_64").read_bytes() == runtime


@pytest.mark.parametrize("name", ["appimagetool-x86_64.AppImage", "runtime-x86_64"])
def test_a_link_or_folder_at_a_tool_path_stops_the_build(build, tmp_path, name):
    build.cache()
    target = tmp_path / "elsewhere"
    target.write_bytes((build.dir / name).read_bytes())  # even a right one
    (build.dir / name).unlink()
    (build.dir / name).symlink_to(target)
    done = build.run()
    assert done.returncode != 0 and "not a regular file" in done.stderr
    assert build.args is None and build.downloads == []
    (build.dir / name).unlink()
    (build.dir / name).mkdir()
    done = build.run()
    assert done.returncode != 0 and "not a regular file" in done.stderr
    assert build.args is None


@pytest.mark.parametrize("name", ["appimagetool-x86_64.AppImage", "runtime-x86_64"])
def test_a_broken_link_is_no_missing_tool(build, tmp_path, name):
    """A link to nowhere is refused, not taken as missing and downloaded
    through (which would write the download where the link points)."""
    build.cache()
    (build.dir / name).unlink()
    (build.dir / name).symlink_to(tmp_path / "nowhere")
    done = build.run()
    assert done.returncode != 0 and "not a regular file" in done.stderr
    assert build.args is None and build.downloads == []
    assert not (tmp_path / "nowhere").exists()


def test_the_pinned_tools_in_build_sh():
    text = BUILD.read_text(encoding="utf-8")
    assert f'TOOL_URL="{TOOL_URL}"' in text
    assert f'TOOL_SHA256="{TOOL_SHA256}"' in text
    assert f'RUNTIME_URL="{RUNTIME_URL}"' in text
    assert f'RUNTIME_SHA256="{RUNTIME_SHA256}"' in text
    # one way to download, only when missing; no -N that replaces a file
    assert text.count("wget ") == 1 and "wget -N" not in text
    # every run of the tool gets the checked runtime, and is checked first
    runs = [line for line in text.splitlines() if '"./$TOOL"' in line]
    assert len(runs) == 2 and all('--runtime-file "$RUNTIME"' in r for r in runs)
    check = text.index('verified "$RUNTIME" "$RUNTIME_SHA256" && '
                       'verified "$TOOL" "$TOOL_SHA256"')
    assert check < text.index('"./$TOOL"')
    # the expected values are only read, never written by the script
    assert text.count("TOOL_SHA256=") == 1 and text.count("RUNTIME_SHA256=") == 1


def test_build_sh_text():
    text = BUILD.read_text(encoding="utf-8")
    assert f'UPDATE_INFO="{UPDATE_INFO}"' in text
    assert text.count('-u "$UPDATE_INFO"') == 1
    assert 'BUILD_KIND="$(python3 tools/release_tag.py --build-kind "$VERSION")"' \
        in text
    assert subprocess.run(["bash", "-n", str(BUILD)]).returncode == 0


# -- the workflow's last two steps, with stand-ins for readelf and gh ----------

def run_block(text, name):
    """The script of a workflow step's run: | block."""
    lines = text.split(f"- name: {name}\n", 1)[1].splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "run: |")
    indent = len(lines[start + 1]) - len(lines[start + 1].lstrip())
    block = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        block.append(line[indent:])
    return "\n".join(block) + "\n"


@pytest.fixture
def release_dir(tmp_path):
    """A folder with stand-ins: readelf shows what the fake AppImage holds,
    gh notes what it was asked to publish."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Like the real one: each line of the fake AppImage is a string in its
    # section; "NO SECTION" has none, "EMPTY SECTION" one without strings,
    # "NOT ELF" is no ELF file.
    (bin_dir / "readelf").write_text(
        '#!/bin/sh\n[ "$1" = "-p" ] && [ "$2" = ".upd_info" ] || exit 2\n'
        'if [ "$(cat "$3")" = "NOT ELF" ]; then\n'
        '  echo "readelf: Error: Not an ELF file" >&2; exit 1; fi\n'
        'if [ "$(cat "$3")" = "NO SECTION" ]; then echo "readelf: Warning: Section \'.upd_info\' '
        'was not dumped because it does not exist" >&2; exit 0; fi\n'
        'echo; echo "String dump of section \'.upd_info\':"\n'
        '[ "$(cat "$3")" = "EMPTY SECTION" ] && exit 0\n'
        'while IFS= read -r line || [ -n "$line" ]; do printf "  [     0]  %s\\n" "$line"; done < "$3"\n'
        'echo\n')
    (bin_dir / "gh").write_text(
        '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > gh.txt\n')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    (tmp_path / "release-notes.md").write_text("notes")
    return tmp_path


def run_steps(release_dir, workflow, tag, files):
    version, kind = release_tag.release(tag)
    for name, content in files.items():
        (release_dir / name).write_text(content)
    env = {**os.environ, "PATH": f"{release_dir / 'bin'}:{os.environ['PATH']}",
           "GITHUB_REF_NAME": tag, "VERSION": version, "KIND": kind,
           "FLAGS": " ".join(release_tag.FLAGS[kind]), "GH_TOKEN": "x"}
    for step in ("Check the files to publish", "Publish release"):
        done = subprocess.run(["bash", "-e", "-o", "pipefail", "-c",
                               run_block(workflow, step)], cwd=release_dir,
                              env=env, capture_output=True, text=True, timeout=30)
        if done.returncode:
            return step, done.stdout + done.stderr
    return None, (release_dir / "gh.txt").read_text().splitlines()


def test_stable_release_publishes_both_files(release_dir, workflow):
    app = "Understeer-2.3.3-x86_64.AppImage"
    failed, gh = run_steps(release_dir, workflow, "v2.3.3", {
        app: UPDATE_INFO + "\n", app + ".zsync": "zsync"})
    assert failed is None, gh
    # exactly the arguments of before this change
    assert gh == ["release", "create", "v2.3.3", app, app + ".zsync",
                  "--title", "v2.3.3", "--notes", "notes", "--generate-notes"]


def test_beta_release_publishes_the_appimage_only(release_dir, workflow):
    app = "Understeer-2.4.0-beta.1-x86_64.AppImage"
    failed, gh = run_steps(release_dir, workflow, "v2.4.0-beta.1",
                           {app: "NO SECTION"})     # no .upd_info strings
    assert failed is None, gh
    assert gh == ["release", "create", "v2.4.0-beta.1", app,
                  "--title", "v2.4.0-beta.1", "--notes", "notes",
                  "--generate-notes", "--prerelease", "--latest=false"]
    assert not any(a.endswith(".zsync") for a in gh)


STABLE_APP = "Understeer-2.3.3-x86_64.AppImage"
BETA_APP = "Understeer-2.4.0-beta.1-x86_64.AppImage"
ZSYNC = {STABLE_APP + ".zsync": "zsync"}
WRONG = "Not the expected update information"


@pytest.mark.parametrize("tag, files, message", [
    ("v2.3.3", {}, f"Missing: {STABLE_APP}"),
    ("v2.3.3", {STABLE_APP: UPDATE_INFO}, f"Missing: {STABLE_APP}.zsync"),
    ("v2.3.3", {STABLE_APP: UPDATE_INFO, STABLE_APP + ".zsync": ""},
     f"Missing: {STABLE_APP}.zsync"),                        # empty .zsync
    # the update information of a stable release must be exactly ours
    ("v2.3.3", {STABLE_APP: "NO SECTION", **ZSYNC}, WRONG),  # no section
    ("v2.3.3", {STABLE_APP: "nothing like it", **ZSYNC}, WRONG),
    ("v2.3.3", {STABLE_APP: "gh-releases-zsync|JamDaBam|Understeer|latest|"
                "Other-*.zsync", **ZSYNC}, WRONG),           # right start only
    ("v2.3.3", {STABLE_APP: "gh-releases-zsync|JamDaBam|Understeer|latest-all|"
                "Understeer-*x86_64.AppImage.zsync", **ZSYNC}, WRONG),
    ("v2.3.3", {STABLE_APP: "gh-releases-zsync|someone|Understeer|latest|"
                "Understeer-*x86_64.AppImage.zsync", **ZSYNC}, WRONG),
    ("v2.3.3", {STABLE_APP: UPDATE_INFO + "x", **ZSYNC}, WRONG),
    ("v2.3.3", {STABLE_APP: "x" + UPDATE_INFO, **ZSYNC}, WRONG),
    ("v2.3.3", {STABLE_APP: UPDATE_INFO + "\nanother string", **ZSYNC}, WRONG),
    # a beta has no update information of any kind and no .zsync
    ("v2.4.0-beta.1", {}, f"Missing: {BETA_APP}"),
    ("v2.4.0-beta.1", {BETA_APP: "NO SECTION", BETA_APP + ".zsync": "old"},
     "A beta has no"),                          # a stale or unexpected .zsync
    ("v2.4.0-beta.1", {BETA_APP: UPDATE_INFO}, "Update information in the beta"),
    ("v2.4.0-beta.1", {BETA_APP: "zsync|https://example.org/U.AppImage.zsync"},
     "Update information in the beta"),
    ("v2.4.0-beta.1", {BETA_APP: "gh-releases-direct|JamDaBam|Understeer|"
                       "latest|*.AppImage"}, "Update information in the beta"),
])
def test_nothing_is_published_with_wrong_files(release_dir, workflow, tag,
                                               files, message):
    failed, output = run_steps(release_dir, workflow, tag, files)
    assert failed == "Check the files to publish"
    assert message in output
    assert not (release_dir / "gh.txt").exists()


@pytest.mark.parametrize("tag, files", [
    ("v2.3.3", {STABLE_APP: "NOT ELF", **ZSYNC}),
    ("v2.4.0-beta.1", {BETA_APP: "NOT ELF"}),
])
def test_an_appimage_readelf_cant_read_is_refused(release_dir, workflow, tag,
                                                  files):
    failed, output = run_steps(release_dir, workflow, tag, files)
    assert failed == "Check the files to publish"
    assert "Not an ELF file" in output
    assert not (release_dir / "gh.txt").exists()


def test_the_check_reads_readelf_as_it_prints(tmp_path, release_dir, workflow):
    """The fake readelf prints like the real one (checked by hand on an
    AppImage of 2.3.3): an empty line, the heading, then "  [     0]  …"."""
    app = release_dir / STABLE_APP
    app.write_text(UPDATE_INFO)
    out = subprocess.run([str(release_dir / "bin" / "readelf"), "-p",
                          ".upd_info", str(app)], capture_output=True,
                         text=True).stdout
    assert out == ("\nString dump of section '.upd_info':\n"
                   f"  [     0]  {UPDATE_INFO}\n\n")


@pytest.mark.parametrize("content", ["NO SECTION", "EMPTY SECTION"])
def test_a_beta_without_update_information_is_published(release_dir, workflow,
                                                         content):
    failed, gh = run_steps(release_dir, workflow, "v2.4.0-beta.1",
                           {BETA_APP: content})
    assert failed is None, gh
    assert gh[3] == BETA_APP and not any(a.endswith(".zsync") for a in gh)


def test_a_stable_release_with_an_empty_section_is_refused(release_dir, workflow):
    failed, output = run_steps(release_dir, workflow, "v2.3.3",
                               {STABLE_APP: "EMPTY SECTION", **ZSYNC})
    assert failed == "Check the files to publish" and WRONG in output


# -- external Actions pinned to full commit SHAs ---------------------------------

CHECKOUT = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"  # v5.1.0
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", re.M)
PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@[0-9a-f]{40}")


def action_refs(text):
    """[(reference, rest of the line)] of every uses: in a workflow."""
    return USES.findall(text)


def unpinned(text):
    """External Actions not pinned to a full commit SHA; local workflows
    (./...) are part of this repository and need none."""
    return [ref for ref, _rest in action_refs(text)
            if not ref.startswith("./") and not PINNED.fullmatch(ref)]


@pytest.mark.parametrize("path", [WORKFLOW, TESTS_WORKFLOW], ids=["release", "tests"])
def test_actions_are_pinned_to_commits(path):
    text = path.read_text(encoding="utf-8")
    assert unpinned(text) == []
    external = [(ref, rest) for ref, rest in action_refs(text)
                if not ref.startswith("./")]
    assert external and all(ref == CHECKOUT for ref, _ in external)
    assert all(rest.strip() == "# v5.1.0" for _, rest in external)


def test_the_local_test_workflow_stays_local(workflow):
    refs = [ref for ref, _ in action_refs(workflow)]
    assert refs.count("./.github/workflows/tests.yml") == 1
    assert TESTS_WORKFLOW.exists()


@pytest.mark.parametrize("ref", [
    "actions/checkout@v5", "actions/checkout@v5.1.0", "actions/checkout@main",
    "actions/checkout@releases/v5", "actions/checkout@fbc6f39",
    "actions/checkout@" + "F" * 40, "actions/checkout@" + "f" * 39,
    "actions/checkout@" + "f" * 41, "actions/checkout", "docker://alpine:3",
])
def test_a_mutable_reference_is_found(workflow, ref):
    """Putting a tag, branch or short SHA back makes the check fail."""
    changed = workflow.replace(CHECKOUT, ref, 1)
    assert unpinned(changed) == [ref]


# -- only commits on main are released --------------------------------------------

ON_MAIN = "Check that the tagged commit is on main"
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def upstream(tmp_path_factory):
    """A repository like the one on GitHub: main with an older commit, a
    feature merged into it, a branch never merged and one with unrelated
    history, and release tags (lightweight and annotated) on each."""
    repo = tmp_path_factory.mktemp("upstream")
    git(repo, "init", "-q", "-b", "main")

    def commit(name):
        (repo / name).write_text(name)
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", name)
        return git(repo, "rev-parse", "HEAD")

    old = commit("one")
    commit("two")
    git(repo, "checkout", "-q", "-b", "feature")
    merged = commit("feature")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    git(repo, "checkout", "-q", "-b", "wip")
    unmerged = commit("wip")
    git(repo, "checkout", "-q", "--orphan", "other")
    unrelated = commit("other")
    git(repo, "checkout", "-q", "main")
    git(repo, "tag", "v2.3.3", old)                               # older main
    git(repo, "tag", "v2.4.0")                                    # main now
    git(repo, "tag", "-a", "-m", "beta", "v2.4.0-beta.1", merged)  # annotated
    git(repo, "tag", "-a", "-m", "beta", "v2.4.0-beta.3")         # annotated
    git(repo, "tag", "v2.4.0-beta.2", unmerged)
    git(repo, "tag", "-a", "-m", "x", "v2.5.0", unrelated)
    return repo


def checkout(tmp_path, upstream, tag, *clone_args):
    """What actions/checkout makes of a tag push with fetch-depth: 0: a
    clone of the repository with the tag checked out."""
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", "--no-checkout", *clone_args,
        f"file://{upstream}", str(work))
    git(work, "checkout", "-q", "--detach", f"refs/tags/{tag}")
    return work


def check_on_main(workflow, cwd, upstream, tag, path=None, sha=None):
    """sha: GITHUB_SHA of the push; by default the tag's commit, as GitHub
    sets it for a tag push."""
    if sha is None:
        sha = subprocess.run(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
                             cwd=upstream, env=GIT_ENV, capture_output=True,
                             text=True).stdout.strip()
    env = {**GIT_ENV, "GITHUB_REF_NAME": tag, "GITHUB_SERVER_URL": "file://",
           "GITHUB_REPOSITORY": str(upstream)[1:], "GITHUB_SHA": sha}
    if path:
        env["PATH"] = f"{path}:{env['PATH']}"
    return subprocess.run(["bash", "-e", "-o", "pipefail", "-c",
                           run_block(workflow, ON_MAIN)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("tag", [
    "v2.4.0",          # the commit main is at
    "v2.3.3",          # an older commit of main
    "v2.4.0-beta.1",   # a feature commit merged into main, annotated tag
    "v2.4.0-beta.3",   # annotated tag on main
])
def test_a_tag_on_main_is_released(workflow, tmp_path, upstream, tag):
    work = checkout(tmp_path, upstream, tag)
    done = check_on_main(workflow, work, upstream, tag)
    assert done.returncode == 0, done.stderr
    assert "is in the history of main" in done.stdout


@pytest.mark.parametrize("tag", [
    "v2.4.0-beta.2",   # a branch never merged (a beta)
    "v2.5.0",          # unrelated history (a stable tag)
])
def test_a_tag_off_main_is_refused(workflow, tmp_path, upstream, tag):
    work = checkout(tmp_path, upstream, tag)
    done = check_on_main(workflow, work, upstream, tag)
    assert done.returncode == 1
    assert "is not in the history of main" in done.stderr


def test_missing_main_is_refused(workflow, tmp_path, upstream):
    work = checkout(tmp_path, upstream, "v2.4.0")
    git(work, "update-ref", "-d", "refs/remotes/origin/main")
    done = check_on_main(workflow, work, upstream, "v2.4.0")
    assert done.returncode == 1 and "origin/main is missing" in done.stderr


def test_a_shallow_clone_is_refused(workflow, tmp_path, upstream):
    """Like the default checkout (depth 1): too little history to tell."""
    work = checkout(tmp_path, upstream, "v2.3.3", "--depth", "1",
                    "--no-single-branch")
    done = check_on_main(workflow, work, upstream, "v2.3.3")
    assert done.returncode == 1 and "shallow" in done.stderr


def test_another_remote_is_refused(workflow, tmp_path, upstream):
    work = checkout(tmp_path, upstream, "v2.4.0")
    git(work, "remote", "set-url", "origin", "file:///somewhere/else")
    done = check_on_main(workflow, work, upstream, "v2.4.0")
    assert done.returncode == 1 and "not file://" in done.stderr


def test_the_tag_must_be_what_is_checked_out(workflow, tmp_path, upstream):
    work = checkout(tmp_path, upstream, "v2.4.0")
    done = check_on_main(workflow, work, upstream, "v2.3.3")
    assert done.returncode == 1 and "the tag points to" in done.stderr
    done = check_on_main(workflow, work, upstream, "v9.9.9")
    assert done.returncode == 1 and "there is no tag v9.9.9" in done.stderr


@pytest.mark.parametrize("tag", ["v2.4.0-beta.1", "v2.4.0-beta.3"])
def test_an_annotated_tag_may_come_as_its_tag_object(workflow, tmp_path,
                                                     upstream, tag):
    """GITHUB_SHA naming the tag object instead of its commit still matches."""
    work = checkout(tmp_path, upstream, tag)
    tag_object = git(upstream, "rev-parse", f"refs/tags/{tag}")
    assert tag_object != git(upstream, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    done = check_on_main(workflow, work, upstream, tag, sha=tag_object)
    assert done.returncode == 0, done.stderr


def test_the_checkout_must_be_the_pushed_commit(workflow, tmp_path, upstream):
    """The tag points to the checked-out commit, but the push that started
    the run was for another one (e.g. the tag was moved): refused."""
    work = checkout(tmp_path, upstream, "v2.4.0")
    other = git(upstream, "rev-parse", "refs/tags/v2.3.3^{commit}")
    done = check_on_main(workflow, work, upstream, "v2.4.0", sha=other)
    assert done.returncode == 1 and "the push was for" in done.stderr


@pytest.mark.parametrize("sha", ["", "HEAD", "--help", "abc123", "A" * 40,
                                 "f" * 40, "0" * 40 + "\n", "v2.4.0"])
def test_a_bad_github_sha_is_refused(workflow, tmp_path, upstream, sha):
    work = checkout(tmp_path, upstream, "v2.4.0")
    done = check_on_main(workflow, work, upstream, "v2.4.0", sha=sha)
    assert done.returncode == 1
    assert "no commit id" in done.stderr or "is not here" in done.stderr


def test_git_failures_are_refused(workflow, tmp_path, upstream):
    # no repository at all
    empty = tmp_path / "empty"
    empty.mkdir()
    done = check_on_main(workflow, empty, upstream, "v2.4.0")
    assert done.returncode == 1 and "Not released" in done.stderr
    # git fails in the ancestry check itself (not "no" but an error)
    work = checkout(tmp_path, upstream, "v2.4.0")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True,
                          text=True).stdout.strip()
    (bin_dir / "git").write_text(
        f'#!/bin/sh\n[ "$1" = merge-base ] && exit 128\nexec "{real}" "$@"\n')
    (bin_dir / "git").chmod(0o755)
    done = check_on_main(workflow, work, upstream, "v2.4.0", path=bin_dir)
    assert done.returncode == 1 and "could not tell" in done.stderr


def test_the_main_check_is_in_the_tag_job(workflow):
    tag_job, rest = workflow.split("\n  test:", 1)
    assert f"- name: {ON_MAIN}" in tag_job and ON_MAIN not in rest
    assert "fetch-depth: 0" in tag_job and "fetch-depth" not in rest
    # the jobs after it can't start without it
    assert re.search(r"^  test:\n    needs: tag\b", workflow, re.M)
    assert re.search(r"^  build:\n    needs: \[tag, test\]", workflow, re.M)
    assert "continue-on-error" not in workflow and "if:" not in workflow
