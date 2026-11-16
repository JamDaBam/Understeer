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


def test_script_prints_the_outputs():
    done = run("v2.4.0-beta.1")
    assert done.returncode == 0
    assert done.stdout == ("version=2.4.0-beta.1\nkind=beta\n"
                           "flags=--prerelease --latest=false\n")
    assert run("v2.4.0").stdout == "version=2.4.0\nkind=stable\nflags=\n"


@pytest.mark.parametrize("args", [["v2.4.0-rc1"], ["v2.4.0\nkind=stable"],
                                  [], ["v2.4.0", "v2.4.1"]])
def test_script_fails_and_prints_nothing(args):
    """Nothing reaches $GITHUB_OUTPUT, and the step fails."""
    done = run(*args)
    assert done.returncode != 0
    assert done.stdout == ""


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
    assert steps(workflow) == ["Check the tag", "Release notes", "Build AppImage",
                               "Check the files to publish", "Publish release"]


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
