"""The guard of isolation.py, which keeps the UI smoke test away from real
hardware, the real home, the network and privileged programs.

An audit hook can't be removed once installed, so every test that
installs it does so in a child process of its own. Nothing protected is
ever used for real here: what the guard does with real hardware, other
processes or the network is asked of its rules only (Guard._check, which
needs no hook and touches nothing it refuses), and ui_smoke.py itself is
only read, never run here; operations that really
run do so on files below tmp_path, a stand-in for the real home among
them, or are harmless if the guard failed (starting "true", connecting
to a closed port on 127.0.0.1).
"""
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import types

import pytest

import isolation
import test_ui

TESTS = pathlib.Path(__file__).resolve().parent
SMOKE = TESTS / "ui_smoke.py"


@pytest.fixture
def place(tmp_path):
    """Folders as the smoke test has them: scratch (writable), a stand-in
    for the real home (denied) with a repository in it (read only)."""
    scratch, home = tmp_path / "scratch", tmp_path / "home"
    repo = home / "repo"
    for folder in (scratch, repo):
        folder.mkdir(parents=True)
    (repo / "code.py").write_text("print('hi')\n")
    (home / "secret").write_text("private\n")
    return {"scratch": scratch, "home": home, "repo": repo}


def rules(place, **kwargs):
    return isolation.Guard(writable=[place["scratch"]], readable=[place["repo"]],
                           denied=[place["home"]], **kwargs)


def run_guarded(place, code, cwd=None, commands=(), loopback=False):
    """Run code with the guard installed, in a child process. attempt(name,
    fn) runs fn and notes "done", "refused" or the error. Returns
    (results, violations)."""
    script = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, {tests!r})
        import isolation
        guard = isolation.Guard(writable=[{scratch!r}], readable=[{repo!r}],
                                denied=[{home!r}], commands={commands!r},
                                loopback={loopback!r}).install()
        results = {{}}
        def attempt(name, fn):
            try:
                fn()
                results[name] = "done"
            except PermissionError as e:
                results[name] = "refused" if "isolation guard" in str(e) else repr(e)
            except Exception as e:
                results[name] = repr(e)
        SCRATCH, HOME, REPO = {scratch!r}, {home!r}, {repo!r}
    """).format(tests=str(TESTS), scratch=str(place["scratch"]),
                home=str(place["home"]), repo=str(place["repo"]),
                commands=[(list(a), str(c)) for a, c in commands],
                loopback=loopback)
    script += textwrap.dedent(code) + textwrap.dedent("""
        print(json.dumps({"results": results, "violations": guard.violations,
                          "no_bytecode": sys.dont_write_bytecode}))
    """)
    out = subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=cwd or place["scratch"],
                         capture_output=True, text=True, timeout=60,
                         env={"PATH": os.environ.get("PATH", "")})
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout.strip().splitlines()[-1])
    return data["results"], data["violations"], data["no_bytecode"]


# -- in a child process, with the hook installed --------------------------------
def test_allowed_work_goes_on(place):
    results, violations, no_bytecode = run_guarded(place, """
        attempt("write scratch", lambda: open(SCRATCH + "/a", "w").write("x"))
        attempt("read scratch", lambda: open(SCRATCH + "/a").read())
        attempt("read repo", lambda: open(REPO + "/code.py").read())
        attempt("list repo", lambda: os.listdir(REPO))
        attempt("read python", lambda: open(os.__file__).read())
        attempt("write /dev/null", lambda: open("/dev/null", "w").write("x"))
        attempt("own pid", lambda: os.kill(os.getpid(), 0))
    """)
    assert set(results.values()) == {"done"}, results
    assert violations == []
    assert no_bytecode                      # nothing written next to the code


def test_a_refusal_counts_also_when_caught(place):
    results, violations, _ = run_guarded(place, """
        try:
            open(HOME + "/secret").read()
        except OSError:
            pass                            # as code that goes on would
        attempt("read home", lambda: open(HOME + "/secret").read())
    """)
    assert results == {"read home": "refused"}
    assert len(violations) == 2 and all("secret" in v for v in violations)


def test_the_repository_is_read_only(place):
    results, violations, _ = run_guarded(place, """
        attempt("write", lambda: open(REPO + "/code.py", "a").write("x"))
        attempt("new file", lambda: open(REPO + "/new.py", "x"))
        attempt("os.open", lambda: os.open(REPO + "/n", os.O_WRONLY | os.O_CREAT))
        attempt("remove", lambda: os.remove(REPO + "/code.py"))
        attempt("bytecode", lambda: os.mkdir(REPO + "/__pycache__"))
    """)
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == 5
    assert os.listdir(place["repo"]) == ["code.py"]
    assert (place["repo"] / "code.py").read_text() == "print('hi')\n"


def test_every_way_of_writing_a_path_is_checked(place):
    (place["scratch"] / "real").mkdir()
    results, violations, _ = run_guarded(place, """
        import pathlib
        target = HOME + "/x"
        os.symlink(HOME, SCRATCH + "/link")         # a link may be made ...
        attempt("absolute", lambda: open(target, "w"))
        attempt("bytes", lambda: open(os.fsencode(target), "w"))
        attempt("pathlib", lambda: pathlib.Path(target).write_text("x"))
        attempt("dot dot", lambda: open(SCRATCH + "/../home/x", "w"))
        attempt("two slashes", lambda: open("/" + target, "w"))
        attempt("through a link", lambda: open(SCRATCH + "/link/x", "w"))  # ... not used
        attempt("relative", lambda: open("../home/x", "w"))
        os.chdir(SCRATCH + "/real")
        attempt("relative below", lambda: open("../../home/x", "w"))
        attempt("relative inside", lambda: open("y", "w"))
    """)
    assert results.pop("relative inside") == "done"
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == 8
    assert not (place["home"] / "x").exists()


def test_changing_files_outside_is_refused(place):
    home, scratch = place["home"], place["scratch"]
    (home / "dir").mkdir()
    (scratch / "mine").write_text("x")
    results, violations, _ = run_guarded(place, """
        import shutil, tempfile
        S, H = SCRATCH + "/mine", HOME + "/secret"
        attempt("remove", lambda: os.remove(H))
        attempt("unlink", lambda: os.unlink(H))
        attempt("rename out", lambda: os.rename(S, HOME + "/moved"))
        attempt("rename in", lambda: os.rename(H, SCRATCH + "/got"))
        attempt("replace", lambda: os.replace(S, H))
        attempt("mkdir", lambda: os.mkdir(HOME + "/new"))
        attempt("rmdir", lambda: os.rmdir(HOME + "/dir"))
        attempt("chmod", lambda: os.chmod(H, 0o777))
        attempt("chown", lambda: os.chown(H, os.getuid(), -1))
        attempt("link", lambda: os.link(H, SCRATCH + "/hard"))
        attempt("symlink", lambda: os.symlink(S, HOME + "/sym"))
        attempt("truncate", lambda: os.truncate(H, 0))
        attempt("utime", lambda: os.utime(H))
        attempt("copy to", lambda: shutil.copyfile(S, HOME + "/copy"))
        attempt("move", lambda: shutil.move(H, SCRATCH + "/moved"))
        attempt("mkstemp", lambda: tempfile.mkstemp(dir=HOME))
        attempt("mkdtemp", lambda: tempfile.mkdtemp(dir=HOME))
    """)
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == len(results)
    assert sorted(os.listdir(home)) == ["dir", "repo", "secret"]
    assert (home / "secret").read_text() == "private\n"
    assert sorted(os.listdir(scratch)) == ["mine"]


def test_descriptors_are_handled_conservatively(place):
    (place["scratch"] / "f").write_text("x")
    results, violations, _ = run_guarded(place, """
        fd = os.open(SCRATCH, os.O_RDONLY)
        attempt("remove with dir_fd", lambda: os.remove("f", dir_fd=fd))
        f = os.open(SCRATCH + "/f", os.O_RDONLY)
        attempt("fchmod", lambda: os.chmod(f, 0o600))
        attempt("ftruncate path", lambda: os.truncate(f, 0))
        attempt("fdopen", lambda: open(f).read())
    """)
    assert results == {"remove with dir_fd": "refused", "fchmod": "refused",
                       "ftruncate path": "refused", "fdopen": "done"}
    assert len(violations) == 3
    assert (place["scratch"] / "f").read_text() == "x"


def test_no_folder_descriptor_outside(place):
    """Python's "open" event leaves dir_fd out, so os.open("x", dir_fd=fd)
    is checked as if relative to the current folder. A descriptor of a
    folder outside the writable ones is therefore never handed out, so a
    dir_fd can only point into them."""
    results, violations, _ = run_guarded(place, """
        attempt("repo folder", lambda: os.open(REPO, os.O_RDONLY))
        attempt("repo folder, O_DIRECTORY",
                lambda: os.open(REPO, os.O_RDONLY | os.O_DIRECTORY))
        os.chdir(REPO)
        attempt("current folder", lambda: os.open(".", os.O_RDONLY))
        os.chdir(SCRATCH)
        attempt("scratch folder", lambda: os.close(os.open(SCRATCH, os.O_RDONLY)))
        attempt("file in repo", lambda: os.close(os.open(REPO + "/code.py", os.O_RDONLY)))
    """)
    assert results == {"repo folder": "refused", "repo folder, O_DIRECTORY": "refused",
                       "current folder": "refused", "scratch folder": "done",
                       "file in repo": "done"}
    assert len(violations) == 3
    assert os.listdir(place["repo"]) == ["code.py"]


def test_programs(place):
    scratch = place["scratch"]
    results, violations, _ = run_guarded(place, """
        import subprocess
        attempt("allowed", lambda: subprocess.run(["true"], cwd=SCRATCH, check=True))
        attempt("other args", lambda: subprocess.run(["true", "x"], cwd=SCRATCH))
        attempt("other folder", lambda: subprocess.run(["true"], cwd=REPO))
        attempt("executable", lambda: subprocess.run(["x"], executable="true", cwd=SCRATCH))
        attempt("shell", lambda: subprocess.run("true", shell=True, cwd=SCRATCH))
        attempt("os.system", lambda: os.system("true"))
        attempt("posix_spawn", lambda: os.posix_spawn("/bin/true", ["true"], {}))
        attempt("exec", lambda: os.execv("/bin/true", ["true"]))
    """, commands=[(["true"], scratch)])
    assert results.pop("allowed") == "done"
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == len(results)


def test_sockets_in_a_child(place):
    results, violations, _ = run_guarded(place, """
        import socket
        def connect(ex=False):
            with socket.socket() as s:
                (s.connect_ex if ex else s.connect)(("127.0.0.1", 9))
        attempt("connect", connect)
        attempt("connect_ex", lambda: connect(True))
        attempt("lookup", lambda: socket.getaddrinfo("localhost", 80))
    """)
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == 3


# -- R1: a link followed by ".." resolves as the kernel does ------------------------
@pytest.fixture
def linked(place):
    """scratch/out links out of the test folder (to home/dir), scratch/in
    stays inside it (to scratch/sub/deep)."""
    scratch, home = place["scratch"], place["home"]
    (home / "dir").mkdir()
    (scratch / "sub" / "deep").mkdir(parents=True)
    (scratch / "sub" / "file").write_text("mine\n")
    os.symlink(home / "dir", scratch / "out")
    os.symlink(scratch / "sub" / "deep", scratch / "in")
    return place


def test_a_link_then_dot_dot_out_of_the_test_folder_is_refused(linked):
    """scratch/out/../x is home/x: the link first, then "..". Checked as
    written alone it would look like scratch/x."""
    results, violations, _ = run_guarded(linked, """
        attempt("read", lambda: open(SCRATCH + "/out/../secret").read())
        attempt("write", lambda: open(SCRATCH + "/out/../planted", "w").write("x"))
        attempt("write below a missing folder",
                lambda: open(SCRATCH + "/out/../missing/x", "w"))
        attempt("mkdir", lambda: os.mkdir(SCRATCH + "/out/../made"))
        attempt("relative", lambda: open("out/../secret").read())
        attempt("folder descriptor", lambda: os.open(SCRATCH + "/out/..", os.O_RDONLY))
    """)
    assert set(results.values()) == {"refused"}, results
    assert len(violations) == 6
    assert sorted(os.listdir(linked["home"])) == ["dir", "repo", "secret"]


def test_a_link_then_dot_dot_inside_the_test_folder_is_allowed(linked):
    results, violations, _ = run_guarded(linked, """
        attempt("read", lambda: open(SCRATCH + "/in/../file").read())
        attempt("write", lambda: open(SCRATCH + "/in/../new", "w").write("x"))
        attempt("folder descriptor",
                lambda: os.close(os.open(SCRATCH + "/in/..", os.O_RDONLY)))
    """)
    assert set(results.values()) == {"done"}, results
    assert violations == []
    assert (linked["scratch"] / "sub" / "new").read_text() == "x"


def test_a_folder_given_with_dot_dot_is_refused(linked):
    """What such a folder means depends on the links in it: refused rather
    than guessed."""
    out = linked["scratch"] / "out" / ".."
    for kwargs in ({"writable": [out]},
                   {"writable": [linked["scratch"]], "readable": [out]},
                   {"writable": [linked["scratch"]], "denied": [out]}):
        with pytest.raises(ValueError):
            isolation.Guard(**kwargs)


def test_a_test_folder_through_a_link_and_dot_dot_is_seen(linked, tmp_path):
    """check_environment resolves the folders the same way."""
    (tmp_path / "t").mkdir()
    env = test_ui.isolated_env(tmp_path / "t", "99")
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={env['XDG_RUNTIME_DIR']}/dbus-abc"
    env["XDG_CACHE_HOME"] = str(tmp_path / "t" / "out" / ".." / "cache")
    os.symlink(linked["home"] / "dir", tmp_path / "t" / "out")
    assert any("XDG_CACHE_HOME is not below" in p
               for p in isolation.check_environment(tmp_path / "t", env))


# -- R2: the real home is the user's, whatever HOME says ----------------------------
def user(home):
    """A stand-in for pwd.getpwuid that tells home as the user's home."""
    return lambda _uid: types.SimpleNamespace(pw_dir=str(home))


def test_one_home_when_both_agree(place):
    env = {"UNDERSTEER_TEST_REAL_HOME": str(place["home"])}
    assert isolation.protected_homes(env, getpwuid=user(place["home"])) == \
        [str(place["home"])]


def test_both_homes_when_home_was_changed(place, tmp_path):
    other = tmp_path / "other-home"
    other.mkdir()
    env = {"UNDERSTEER_TEST_REAL_HOME": str(other)}
    assert isolation.protected_homes(env, getpwuid=user(place["home"])) == \
        [str(other), str(place["home"])]


def test_both_homes_are_protected_the_repository_stays_readable(place, tmp_path):
    other = tmp_path / "other-home"
    other.mkdir()
    (other / "f").write_text("x")
    homes = isolation.protected_homes({"UNDERSTEER_TEST_REAL_HOME": str(other)},
                                      getpwuid=user(place["home"]))
    guard = isolation.Guard(writable=[place["scratch"]], readable=[place["repo"]],
                            denied=homes)
    assert guard._check("open", (str(other / "f"), "r", os.O_RDONLY))
    assert guard._check("open", (str(place["home"] / "secret"), "r", os.O_RDONLY))
    assert guard._check("open", (str(place["repo"] / "code.py"), "r", os.O_RDONLY)) is None
    assert guard._check("open", (str(place["repo"] / "code.py"), "a", os.O_APPEND))


def broken_user(_uid):
    raise KeyError("no such user")


@pytest.mark.parametrize("environ, getpwuid", [
    ({}, None),                                          # not told
    ({"UNDERSTEER_TEST_REAL_HOME": ""}, None),
    ({"UNDERSTEER_TEST_REAL_HOME": "relative/home"}, None),
    ({"UNDERSTEER_TEST_REAL_HOME": "/h"}, broken_user),  # user unknown
    ({"UNDERSTEER_TEST_REAL_HOME": "/h"}, user("")),
    ({"UNDERSTEER_TEST_REAL_HOME": "/h"}, user("relative")),
], ids=["missing", "empty", "relative", "no-user", "user-empty", "user-relative"])
def test_an_unknown_home_is_refused(place, environ, getpwuid):
    with pytest.raises(ValueError):
        isolation.protected_homes(environ, getpwuid=getpwuid or user(place["home"]))


def env_with_bus(tmp_path):
    env = test_ui.isolated_env(tmp_path, "99")
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={env['XDG_RUNTIME_DIR']}/dbus-abc"
    return env


@pytest.mark.parametrize("home, problem", [
    ("{tmp}", "inside a protected home"),                # the test folder in it
    ("{tmp}/home/.config", "inside a protected home"),   # one test folder
    ("{tmp}/realhome", "a protected home inside the test folder"),
    (None, "the real home is not known"),
])
def test_test_folders_and_protected_homes_dont_overlap(tmp_path, home, problem):
    env = env_with_bus(tmp_path)
    getpwuid = broken_user if home is None else user(home.format(tmp=tmp_path))
    problems = isolation.check_environment(tmp_path, env, getpwuid=getpwuid)
    assert any(problem in p for p in problems), problems


def test_separate_folders_pass(tmp_path):
    elsewhere = tmp_path.parent / (tmp_path.name + "-home")
    elsewhere.mkdir()
    (tmp_path / "t").mkdir()
    env = env_with_bus(tmp_path / "t")
    assert isolation.check_environment(tmp_path / "t", env,
                                       getpwuid=user(elsewhere)) == []


def test_the_smoke_test_protects_both_homes():
    text = smoke_source()
    homes = text.index("isolation.protected_homes(")
    assert homes < text.index(".install()", homes)
    assert "denied=_homes" in text


# -- the rules alone: nothing protected is touched --------------------------------
REAL_THINGS = [
    ("open", ("/dev/input/event0", "rb", os.O_RDONLY)),
    ("open", ("/dev/hidraw0", "rb", os.O_RDONLY)),
    ("open", ("/dev/uinput", None, os.O_WRONLY)),
    ("open", ("/dev/bus/usb/001/002", "rb", os.O_RDONLY)),
    ("open", ("/sys/class/ftec_tuning/0003:0EB7:0020.0005/FF", "r", os.O_RDONLY)),
    ("open", ("/sys/class/ftec_tuning/0003:0EB7:0020.0005/FF", "w", os.O_WRONLY)),
    ("open", ("/sys/bus/hid/devices/0003:0EB7:183B.0001/rumble", "w", os.O_WRONLY)),
    ("open", ("/sys/module/hid_fanatec/version", "r", os.O_RDONLY)),
    ("glob.glob", ("/sys/class/input/event*", False)),
    ("glob.glob", ("/sys/bus/hid/devices/*/rumble", False)),
    ("os.scandir", ("/sys/class/leds",)),
    ("os.listdir", ("/proc",)),
    ("os.scandir", ("/proc",)),
    ("open", ("/proc/1/cmdline", "rb", os.O_RDONLY)),
    ("open", ("/run/user/1000/bus", "r", os.O_RDONLY)),
    ("open", ("/run/udev/data/c13:64", "r", os.O_RDONLY)),
    ("open", ("/etc/udev/rules.d/99-fanatec-tuner.rules", "w", os.O_WRONLY)),
    ("open", ("/etc/passwd", "a", os.O_WRONLY | os.O_APPEND)),
    ("os.chmod", ("/sys/class/ftec_tuning/x/FF", 0o666, -1)),
]


@pytest.mark.parametrize("event, args", REAL_THINGS)
def test_real_hardware_and_system_are_refused(place, event, args):
    assert rules(place)._check(event, args)


@pytest.mark.parametrize("event, args", [
    ("open", ("/dev/null", "w", os.O_WRONLY)),
    ("open", ("/dev/urandom", "rb", os.O_RDONLY)),
    ("open", ("/proc/self/status", "r", os.O_RDONLY)),
    ("open", (sys.executable, "rb", os.O_RDONLY)),
    ("open", (os.__file__, "r", os.O_RDONLY)),
])
def test_harmless_system_reads_are_allowed(place, event, args):
    assert rules(place)._check(event, args) is None


@pytest.mark.parametrize("argv", [
    ["pkexec", "/bin/sh", "-c", "echo 1 > /sys/x"], ["sudo", "/bin/sh", "-c", "x"],
    ["su", "-c", "x"], ["udevadm", "control", "--reload-rules"],
    ["usermod", "-aG", "games", "me"], ["git", "describe", "--tags"],
    ["/bin/sh", "-c", "true"],
])
def test_no_program_without_permission(place, argv):
    assert "program not allowed" in rules(place)._check(
        "subprocess.Popen", (None, argv, str(place["scratch"]), None))


def test_an_allowed_command_is_exact(place):
    guard = rules(place, commands=[(["git", "describe"], place["repo"])])
    assert guard._check("subprocess.Popen",
                        (None, ["git", "describe"], str(place["repo"]), None)) is None
    for argv, cwd in ((["git", "describe", "--dirty"], place["repo"]),
                      (["git", "describe"], place["scratch"]),
                      (["git", "pull"], place["repo"])):
        assert guard._check("subprocess.Popen", (None, argv, str(cwd), None))
    assert guard._check("subprocess.Popen",
                        ("git", ["git", "describe"], str(place["repo"]), None)) is None
    assert guard._check("subprocess.Popen",
                        ("/usr/bin/pkexec", ["git", "describe"], str(place["repo"]), None))


@pytest.mark.parametrize("event", sorted(isolation._ALWAYS_REFUSED))
def test_other_ways_of_starting_programs(place, event):
    assert rules(place)._check(event, ("x",))


@pytest.mark.parametrize("event, args", [
    ("socket.connect", (None, ("192.0.2.1", 443))),
    ("socket.connect", (None, ("2001:db8::1", 443, 0, 0))),
    ("socket.sendto", (None, ("192.0.2.1", 53))),
    ("socket.sendmsg", (None, ("192.0.2.1", 53))),
    ("socket.bind", (None, ("0.0.0.0", 8080))),
    ("socket.getaddrinfo", ("example.org", 443, 0, 0, 0)),
    ("socket.gethostbyname", ("example.org",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)),
    ("http.client.connect", (None, "api.github.com", 443)),
    ("urllib.Request", ("https://api.github.com/repos/x", None, {}, "GET")),
    ("urllib.Request", ("ftp://example.org/x", None, {}, None)),
    ("socket.connect", (None, "\0/tmp/dbus-abstract")),
    ("socket.connect", (None, "/run/user/1000/bus")),
    ("socket.connect", (None, "/run/dbus/system_bus_socket")),
    ("socket.connect", (None, "/tmp/.X11-unix/X0")),
])
def test_network_is_refused(place, event, args):
    assert rules(place, loopback=True)._check(event, args)


def test_loopback_only_where_allowed(place):
    for loopback in (False, True):
        guard = rules(place, loopback=loopback)
        for address in (("127.0.0.1", 80), ("::1", 80, 0, 0)):
            assert (guard._check("socket.connect", (None, address)) is None) == loopback
        assert (guard._check("socket.getaddrinfo", ("localhost", 80)) is None) == loopback


def test_unix_sockets_only_in_the_test_folder(place):
    guard = rules(place)
    assert guard._check("socket.connect", (None, str(place["scratch"] / "bus"))) is None
    assert guard._check("socket.connect", (None, str(place["home"] / "bus")))


def test_signals_only_to_itself(place):
    guard = rules(place)
    assert guard._check("os.kill", (os.getpid(), 0)) is None
    assert guard._check("os.kill", (1, 15))
    assert guard._check("os.killpg", (1, 15))


def test_a_path_that_cant_be_checked_is_refused(place):
    guard = rules(place)
    assert "can't be checked" in guard._check("os.remove", (object(), -1))
    assert guard._check("socket.connect", (None, object()))


def test_a_broken_check_refuses(place, monkeypatch):
    guard = rules(place)

    def broken(*_args):
        raise ValueError("bug")
    monkeypatch.setattr(guard, "_check", broken)
    with pytest.raises(PermissionError):
        guard._hook("open", ("/x", "r", 0))
    assert guard.violations == ["open: could not check (ValueError: bug)"]


def test_refuse_records_and_raises(place):
    guard = rules(place)
    with pytest.raises(PermissionError, match="isolation guard"):
        guard.refuse("something")
    assert guard.violations == ["something"]


# -- the environment of the smoke test ---------------------------------------------
def test_the_smoke_test_environment_is_isolated(tmp_path, monkeypatch):
    for name, value in {"LD_PRELOAD": "/x.so", "http_proxy": "http://p:3128",
                        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                        "APPIMAGE": "/x.AppImage", "WAYLAND_DISPLAY": "wayland-0",
                        "PYTHONSTARTUP": "/x.py", "SSH_AUTH_SOCK": "/run/x",
                        "XDG_CURRENT_DESKTOP": "KDE", "GTK_MODULES": "x"}.items():
        monkeypatch.setenv(name, value)
    env = test_ui.isolated_env(tmp_path, "99")
    assert set(env) - {"PATH", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "CI", "SMOKE_TRACE"} \
        == {"DISPLAY", "GDK_BACKEND", "GSK_RENDERER", "NO_AT_BRIDGE", "LANG",
            "ADW_DISABLE_PORTAL", "GDK_DEBUG", "GSETTINGS_BACKEND", "HOME",
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
            "XDG_RUNTIME_DIR", "TMPDIR", "DBUS_SYSTEM_BUS_ADDRESS", "PYTHONPATH",
            "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
            "UNDERSTEER_TEST_REAL_HOME"}
    assert os.stat(env["XDG_RUNTIME_DIR"]).st_mode & 0o777 == 0o700
    # As dbus-run-session adds it, with the bus configuration of test_ui.py
    env["DBUS_SESSION_BUS_ADDRESS"] = \
        f"unix:path={env['XDG_RUNTIME_DIR']}/dbus-abc,guid=0"
    assert isolation.check_environment(tmp_path, env) == []
    assert "<servicedir>" not in test_ui.BUS_CONFIG
    assert "standard_session_servicedirs" not in test_ui.BUS_CONFIG
    assert "unix:dir={folder}" in test_ui.BUS_CONFIG


@pytest.mark.parametrize("change, problem", [
    ({"UNDERSTEER_TEST_REAL_HOME": None}, "UNDERSTEER_TEST_REAL_HOME"),
    ({"HOME": "/elsewhere/home"}, "HOME is not below"),
    ({"XDG_RUNTIME_DIR": "/elsewhere/run"}, "XDG_RUNTIME_DIR is not below"),
    ({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/elsewhere/bus"}, "no isolated session bus"),
    ({"DBUS_SESSION_BUS_ADDRESS": None}, "no isolated session bus"),
    ({"DBUS_SYSTEM_BUS_ADDRESS": None}, "system bus"),
    ({"GSETTINGS_BACKEND": "dconf"}, "GSETTINGS_BACKEND"),
    ({"APPIMAGE": "/x.AppImage"}, "APPIMAGE is set"),
    ({"https_proxy": "http://p"}, "https_proxy is set"),
    ({"LD_PRELOAD": "x.so"}, "LD_PRELOAD is set"),
])
def test_a_wrong_environment_is_refused(tmp_path, change, problem):
    env = test_ui.isolated_env(tmp_path, "99")
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={env['XDG_RUNTIME_DIR']}/dbus-abc"
    for name, value in change.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    assert any(problem in p for p in isolation.check_environment(tmp_path, env))


def test_a_runtime_folder_others_can_enter_is_refused(tmp_path):
    env = test_ui.isolated_env(tmp_path, "99")
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={env['XDG_RUNTIME_DIR']}/dbus-abc"
    os.chmod(env["XDG_RUNTIME_DIR"], 0o755)
    assert "XDG_RUNTIME_DIR is not private (0700)" in \
        isolation.check_environment(tmp_path, env)


# -- ui_smoke.py sets it all up in the right order ----------------------------------
def smoke_source():
    return SMOKE.read_text()


def test_the_guard_comes_before_understeer():
    text = smoke_source()
    guard = text.index("isolation.Guard(")
    install = text.index(".install()", guard)
    first = min(text.index(s) for s in ("from understeer", "import understeer")
                if s in text)
    check = text.index("isolation.check_environment(scratch)")
    # Not isolated: it stops before the guard, the fake sysfs and Understeer
    assert check < text.index("sys.exit(100)", check) < install < first
    assert install < text.index("dev.mkdir(")       # before the fake sysfs


@pytest.mark.parametrize("line", [
    "device.SYSFS_GLOB = str(scratch",
    "device.RUMBLE_GLOB = str(scratch",
    "capabilities.HID_GLOB = str(scratch",
    "capabilities.LEDS_GLOB = str(scratch",
    "capabilities.WHEEL_GLOB = str(scratch",
    "_inputs.find_input_devices = lambda: []",
    'games.PROC = scratch / "proc"',
    "device_info.collect = lambda device_path, module_dir=str(FAKE_MODULE)",
    "_session_log._driver = lambda module=str(FAKE_MODULE)",
    "_session_log.version = lambda:",
    "device.run_privileged = _privileged",
    "_updates.fetch_latest = _refused(",
    "_updates.fetch_beta = _refused(",
    "_updates.download = _refused(",
    "_updates.restart = _refused(",
    "games.download_cover = _refused(",
    "steamgriddb.search = fake_search",
    "community.download = library_offline",
    "Gtk.UriLauncher.launch = _launch_refused(",
    "Gtk.FileLauncher.launch = _launch_refused(",
    "Gtk.FileLauncher.open_containing_folder = _launch_refused(",
    'print(f"guard violations: {len(violations)}")',
])
def test_the_smoke_test_replaces(line):
    text = smoke_source()
    assert line in text
    # Set up front, before the first step, not only inside one
    assert text.index(line) < text.index("def steps(") or "violations" in line


def test_the_ui_test_needs_its_own_bus():
    text = (TESTS / "test_ui.py").read_text()
    assert '_missing("dbus-run-session not installed")' in text
    assert '"--config-file=' in text or "--config-file=" in text
    assert "guard violations: 0" in text
