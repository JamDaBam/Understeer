"""Which tags are released, and how: stable, or as a prerelease that never
becomes the latest release. Nothing here creates a tag or a release."""
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
    assert 'stable) test "${#flags[@]}" -eq 0' in publish
    # the asset names stay as they were: Understeer-<version>-x86_64.AppImage
    assert 'files=("Understeer-$VERSION-x86_64.AppImage")' in publish
    assert 'files+=("Understeer-$VERSION-x86_64.AppImage.zsync")' in publish


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


def build_step(tmp_path, version):
    """Run the last step of build.sh (making the AppImage) with a stand-in
    for appimagetool that notes its arguments; nothing is downloaded."""
    text = BUILD.read_text(encoding="utf-8")
    step = text[text.index('echo "=== Step 6'):]
    (tmp_path / "tools").mkdir()
    shutil.copy(SCRIPT, tmp_path / "tools" / "release_tag.py")
    (tmp_path / "understeer").symlink_to(ROOT / "understeer")
    (tmp_path / "AppDir" / "usr" / "share" / "understeer").mkdir(parents=True)
    tool = tmp_path / "appimagetool-x86_64.AppImage"
    tool.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done '
                    '> args.txt\n')
    tool.chmod(0o755)
    env = {**os.environ, "VERSION": version, "TOOL": tool.name}
    done = subprocess.run(["bash", "-e", "-c", step], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return (tmp_path / "args.txt").read_text().splitlines(), done.stdout


def test_a_stable_build_keeps_its_update_information(tmp_path):
    args, _out = build_step(tmp_path, "v2.3.3")
    assert args == ["-u", UPDATE_INFO, "AppDir", "Understeer-2.3.3-x86_64.AppImage"]
    assert (tmp_path / "AppDir/usr/share/understeer/VERSION").read_text() == "2.3.3\n"


@pytest.mark.parametrize("version, name, kind", [
    ("v2.4.0-beta.1", "2.4.0-beta.1", "beta"),
    ("2.3.3-8-g37781ef-dirty", "2.3.3-8-g37781ef-dirty", "development"),
    ("dev", "dev", "development"),
])
def test_other_builds_have_no_update_information(tmp_path, version, name, kind):
    args, out = build_step(tmp_path, version)
    assert args == ["AppDir", f"Understeer-{name}-x86_64.AppImage"]
    assert "-u" not in args and UPDATE_INFO not in args
    assert f"No update information in a {kind} build" in out


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
