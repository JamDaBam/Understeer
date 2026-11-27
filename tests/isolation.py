"""A fail-closed guard for the UI smoke test: a Python audit hook that
refuses, and counts, what a test must never touch.

It is an extra safety net on top of the isolation itself (fake sysfs,
stubs, an isolated HOME and D-Bus): the test redirects everything it
needs, and the guard catches what slipped through. It is no sandbox of
the operating system.

What it refuses, as far as Python's audit events tell about it:
- reading or writing real hardware and system state: all of /sys, /proc
  except the own process, /dev except a few harmless nodes, /run/user,
  /run/udev, /run/dbus, /etc/udev, and the folders given as denied
  (the real home and runtime folder);
- writing anywhere but the given writable folders (the repository is
  read only);
- starting any program but the exact commands allowed, os.system,
  os.exec*, os.posix_spawn, os.spawn*, os.fork;
- signals to other processes;
- network: every IPv4/IPv6 connection, DNS lookup and urllib/http.client
  request (loopback only where allowed), and Unix sockets outside the
  writable folders (also abstract ones).

Each refusal raises PermissionError and is recorded in violations, also
when the code that got the error catches it.

Limits (no audit event, so not seen at all):
- os.stat, os.lstat, os.access, os.path.exists/isdir/isfile, os.readlink,
  os.mkfifo, os.mknod: existence checks and a few rare calls;
- everything done in C: GTK, GIO, GLib, cairo, D-Bus, file and URI
  launchers, and ctypes;
- child processes, e.g. dbus-daemon and the programs it starts: the hook
  lives in this process only;
- os.open() with dir_fd: the event leaves dir_fd out, so a relative path
  is taken as relative to the current folder. That's why opening a folder
  (getting a descriptor that could be a dir_fd) is refused outside the
  writable folders: a dir_fd can then only lead into them. Other calls
  with a dir_fd and a relative path are refused, as their target can't
  be told;
- calls on a file descriptor (fchmod, ftruncate, ...): writing calls are
  refused, an open() of a descriptor is let through (it was opened
  through a checked open() before).
Paths are checked as written (made absolute and normalised), then as the
kernel resolves them: from the path as given, relative to the current
folder, each link in turn before a ".." after it; both must pass. A path
refused as written is not resolved, so checking never looks at a refused
place; a path that can't be resolved (other than not existing yet, e.g.
a loop of links or no permission) is refused. Folders handed to the
guard must not contain "..", as what they mean depends on links. A glob
pattern is checked by the folder before its first wildcard.
"""
import errno
import ipaddress
import os
import pwd
import sys
import threading

# Device nodes that tell nothing about the machine and change nothing
HARMLESS_DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
                    "/dev/full")
SYSTEM_DENIED = ("/sys", "/run/user", "/run/udev", "/run/dbus", "/etc/udev",
                 "/var/run/dbus", "/var/run/udev")

_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)

# event: (indexes of paths written, indexes of paths read, indexes of dir_fds)
_PATH_EVENTS = {
    "os.remove": ((0,), (), (1,)),
    "os.rmdir": ((0,), (), (1,)),
    "os.mkdir": ((0,), (), (2,)),
    "os.chmod": ((0,), (), (2,)),
    "os.chown": ((0,), (), (3,)),
    "os.rename": ((0, 1), (), (2, 3)),     # also os.replace
    "os.link": ((0, 1), (), (2, 3)),
    "os.symlink": ((1,), (), (2,)),
    "os.truncate": ((0,), (), ()),
    "os.utime": ((0,), (), (3,)),
    "os.setxattr": ((0,), (), ()),
    "os.removexattr": ((0,), (), ()),
    "os.chflags": ((0,), (), ()),
    "os.lchflags": ((0,), (), ()),
    "os.lchmod": ((0,), (), ()),
    "os.getxattr": ((), (0,), ()),
    "os.listxattr": ((), (0,), ()),
    "os.listdir": ((), (0,), ()),
    "os.scandir": ((), (0,), ()),
    "os.walk": ((), (0,), ()),
    "os.fwalk": ((), (0,), (4,)),
    "os.chdir": ((), (0,), ()),
    "glob.glob": ((), (0,), ()),
    "glob.glob/2": ((), (0, 2), (3,)),
    "shutil.copyfile": ((1,), (0,), ()),
    "shutil.copymode": ((1,), (0,), ()),
    "shutil.copystat": ((1,), (0,), ()),
    "shutil.copytree": ((1,), (0,), ()),
    "shutil.move": ((0, 1), (), ()),
    "shutil.rmtree": ((0,), (), (1,)),
    "shutil.chown": ((0,), (), ()),
    "shutil.make_archive": ((0,), (2,), ()),
    "shutil.unpack_archive": ((1,), (0,), ()),
    "tempfile.mkstemp": ((0,), (), ()),
    "tempfile.mkdtemp": ((0,), (), ()),
}
_ALWAYS_REFUSED = {"os.system", "os.exec", "os.posix_spawn", "os.spawn",
                   "os.fork", "os.forkpty", "pty.spawn", "webbrowser.open"}
_NAME_LOOKUPS = {"socket.getaddrinfo": 0, "socket.gethostbyname": 0,
                 "socket.gethostbyname_ex": 0, "socket.gethostbyaddr": 0}
_SOCKET_ADDRESSES = ("socket.connect", "socket.sendto", "socket.sendmsg",
                     "socket.bind")


def _inside(path, root):
    """Whether path is root or below it, by whole folder names."""
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")


def _normal(path):
    """path made absolute and normalised, without looking at the disk.
    normpath() keeps two leading slashes, which still mean /."""
    path = os.path.normpath(os.path.join(os.getcwd(), path))
    return "/" + path.lstrip("/") if path.startswith("//") else path


def _resolved(path):
    """path as the kernel finds it: relative to the current folder, links
    resolved in the order written (a link before the ".." after it), not
    normalised beforehand. Only a part that doesn't exist yet is let be;
    any other trouble raises OSError (and so refuses, in the hook)."""
    strict = getattr(os.path, "ALLOW_MISSING", None)
    if strict is None:
        raise RuntimeError("The test isolation guard requires os.path.ALLOW_MISSING. "
                           "Please use a Python version that provides it.")
    return os.path.realpath(os.path.join(os.getcwd(), path), strict=strict)


def _clean_root(root):
    return _resolved(os.fsdecode(os.fspath(root)))


def _roots(paths):
    """Each folder as written and with links resolved."""
    found = []
    for p in paths:
        text = os.fsdecode(os.fspath(p))
        if ".." in text.split("/"):
            raise ValueError(f"a folder for the guard with '..' in it: {text}")
        for form in (_normal(text), _clean_root(text)):
            if form not in found:
                found.append(form)
    return found


class Guard:
    """The audit hook. writable: folders that may be written (and read);
    readable: folders inside denied ones that may still be read, e.g. the
    repository inside the real home; denied: more folders not to touch;
    commands: allowed programs as exact (argv, cwd) pairs; loopback:
    whether connections to 127.0.0.1 and ::1 are allowed."""

    def __init__(self, writable, readable=(), denied=(), commands=(),
                 loopback=False):
        self.writable = _roots(writable)
        self.readable = _roots(readable)
        # The system's own as written, so that setting up never looks at them
        self.denied = [*SYSTEM_DENIED, *_roots(denied)]
        self.commands = [(tuple(map(str, argv)), _clean_root(cwd))
                         for argv, cwd in commands]
        self.loopback = loopback
        self.pid = os.getpid()
        self.violations = []
        self._busy = threading.local()

    def install(self):
        """Start guarding this process, for good: an audit hook can't be
        removed again. Also stops Python from writing bytecode files."""
        sys.dont_write_bytecode = True
        sys.addaudithook(self._hook)
        return self

    # -- the hook ------------------------------------------------------------
    def _hook(self, event, args):
        if getattr(self._busy, "on", False):
            return                  # what checking itself does
        self._busy.on = True
        try:
            why = self._check(event, args)
        except Exception as e:      # a broken check refuses, never lets through
            why = f"could not check ({type(e).__name__}: {e})"
        finally:
            self._busy.on = False
        if why:
            self.refuse(f"{event}: {why}")

    def refuse(self, what):
        """Record a violation and raise PermissionError. Also for stubs
        that stand in for something a test must never reach."""
        self.violations.append(what)
        raise PermissionError(errno.EACCES, f"isolation guard: {what}")

    def _check(self, event, args):
        if event == "open":
            path, mode, flags = (list(args) + [None, None])[:3]
            if isinstance(path, int):
                return None         # an open file descriptor
            writes = (isinstance(flags, int) and flags & _WRITE_FLAGS) or (
                isinstance(mode, str) and any(c in mode for c in "wax+"))
            why = self._path(path, write=bool(writes))
            if why or writes:
                return why
            # The event leaves out dir_fd: so no descriptor of a folder that
            # isn't writable, which a dir_fd could write through unseen
            real = _resolved(os.fsdecode(os.fspath(path)))
            if os.path.isdir(real) and not any(_inside(real, w) for w in self.writable):
                return f"a descriptor of a folder outside the test folders: {real}"
            return None
        if event in _PATH_EVENTS:
            written, read, dir_fds = _PATH_EVENTS[event]
            given = [args[i] for i in dir_fds if i < len(args)]
            dir_fd = next((d for d in given if d not in (None, -1)), None)
            for i, write in [(i, True) for i in written] + [(i, False) for i in read]:
                if i >= len(args) or args[i] is None:
                    continue
                path = args[i]
                if event.startswith("glob.") and isinstance(path, (str, bytes)):
                    path = _glob_base(os.fsdecode(path))
                why = self._path(path, write, dir_fd)
                if why:
                    return why
            return None
        if event in _ALWAYS_REFUSED:
            return "starting programs this way is not allowed"
        if event == "subprocess.Popen":
            return self._command(*args[:3])
        if event in ("os.kill", "os.killpg"):
            return None if args and args[0] == self.pid else \
                f"signal to another process ({args[0]})"
        if event in _NAME_LOOKUPS:
            return self._host(args[_NAME_LOOKUPS[event]])
        if event == "socket.getnameinfo":
            return self._address(args[0])
        if event in _SOCKET_ADDRESSES:
            return self._address(args[1] if len(args) > 1 else None)
        if event == "http.client.connect":
            return self._host(args[1])
        if event == "urllib.Request":
            return self._url(args[0])
        return None

    # -- what is allowed -------------------------------------------------------
    def _path(self, path, write, dir_fd=None):
        if isinstance(path, int):
            return "a write through a file descriptor" if write else None
        try:
            text = os.fsdecode(os.fspath(path))
        except TypeError:
            return f"a path that can't be checked: {path!r}"
        if not os.path.isabs(text) and dir_fd not in (None, -1):
            return f"a path relative to a folder descriptor: {text!r}"
        plain = _normal(text)
        # As written first: a path refused as it stands is never even
        # looked at (resolving links reads the file system)
        why = self._place(plain, write)
        if why:
            return why
        real = _resolved(text)          # from text: links before ".."
        return self._place(real, write) if real != plain else None

    def _place(self, path, write):
        if any(_inside(path, w) for w in self.writable):
            return None
        if write:
            return None if path == "/dev/null" else \
                f"writing outside the test folders: {path}"
        return self._denied(path)

    def _denied(self, path):
        if _inside(path, "/proc"):
            parts = path.split("/")         # "", "proc", pid, ...
            if len(parts) > 2 and parts[2] in ("self", "thread-self", str(self.pid)):
                return None
            return f"processes: {path}"
        if _inside(path, "/dev"):
            return None if path in HARMLESS_DEVICES else f"a device: {path}"
        if any(_inside(path, r) for r in self.readable):
            return None
        for root in self.denied:
            if _inside(path, root):
                return f"not part of the test: {path}"
        return None

    def _command(self, executable, argv, cwd):
        argv = tuple(map(os.fsdecode, argv)) if isinstance(argv, (list, tuple)) \
            else (os.fsdecode(argv),)
        here = _clean_root(cwd if cwd is not None else os.getcwd())
        # Popen tells argv[0] as the program when none was given apart
        program = None if executable is None else os.fsdecode(executable)
        if program in (None, argv[0] if argv else None) and \
                (argv, here) in self.commands:
            return None
        return f"program not allowed: {list(argv)} in {here}"

    def _host(self, host):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if self.loopback and host in ("localhost", "127.0.0.1", "::1"):
            return None
        return f"network: {host!r}"

    def _address(self, address):
        if isinstance(address, (str, bytes)):           # a Unix socket
            text = os.fsdecode(address)
            if text.startswith("\0") or not text:
                return f"an abstract Unix socket: {text!r}"
            return self._path(text, write=True) and f"a Unix socket outside the test: {text}"
        if isinstance(address, tuple) and address:
            host = address[0]
            if isinstance(host, bytes):
                host = host.decode("ascii", "replace")
            try:
                loop = ipaddress.ip_address(str(host).split("%")[0]).is_loopback
            except ValueError:
                loop = False
            if self.loopback and loop:
                return None
            return f"network: {address!r}"
        return f"a socket address that can't be checked: {address!r}"

    def _url(self, url):
        from urllib.parse import urlsplit
        parts = urlsplit(str(url))
        if parts.scheme in ("http", "https", "ftp"):
            return self._host(parts.hostname or "")
        if parts.scheme in ("file", "data", ""):
            return None if parts.scheme != "file" else self._path(parts.path, False)
        return f"URL scheme {parts.scheme!r}"


def _glob_base(pattern):
    """The folder a glob pattern reads first: the part before the first
    wildcard, up to its last slash."""
    for i, c in enumerate(pattern):
        if c in "*?[":
            return os.path.dirname(pattern[:i]) or "."
    return pattern


def protected_homes(environ=os.environ, getpwuid=pwd.getpwuid):
    """The homes a test must not touch, each once: the HOME of whoever
    started it (UNDERSTEER_TEST_REAL_HOME, set by test_ui.py) and the
    user's home in the user database, which no changed HOME can hide.
    Raises ValueError if either is missing or no absolute path."""
    try:
        user = getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError) as e:
        raise ValueError(f"the user's home is not known: {e}") from e
    homes = []
    for source, home in (("UNDERSTEER_TEST_REAL_HOME (start through test_ui.py)",
                          environ.get("UNDERSTEER_TEST_REAL_HOME")),
                         ("the user database", user)):
        if not isinstance(home, str) or not os.path.isabs(home):
            raise ValueError(f"no absolute home from {source}: {home!r}")
        home = _clean_root(home)
        if home not in homes:
            homes.append(home)
    return homes


def check_environment(scratch, environ=os.environ, getpwuid=pwd.getpwuid):
    """What is wrong with the environment for an isolated run below the
    folder scratch, as a list of texts; empty if nothing is."""
    scratch = _clean_root(scratch)
    problems = []
    try:
        homes = protected_homes(environ, getpwuid)
    except ValueError as e:
        problems.append(f"the real home is not known: {e}")
        homes = []
    folders = [scratch]
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                 "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
        value = environ.get(name)
        if not value or not _inside(_clean_root(value), scratch):
            problems.append(f"{name} is not below the test folder: {value!r}")
        if value:
            folders.append(_clean_root(value))
    # Writable wins over denied in the guard: neither may hold the other
    for home in homes:
        for folder in folders:
            if _inside(folder, home):
                problems.append(f"a test folder is inside a protected home: {folder}")
        if _inside(home, scratch):
            problems.append(f"a protected home inside the test folder: {home}")
    runtime = environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime) and \
            os.stat(runtime).st_mode & 0o777 != 0o700:
        problems.append("XDG_RUNTIME_DIR is not private (0700)")
    bus = environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not bus.startswith("unix:path=") or not _inside(
            _clean_root(bus[len("unix:path="):].split(",")[0]), scratch):
        problems.append(f"no isolated session bus: {bus!r}")
    system_bus = environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
    if not system_bus.startswith("unix:path=") or not _inside(
            _clean_root(system_bus[len("unix:path="):].split(",")[0]), scratch):
        problems.append(f"the system bus is not cut off: {system_bus!r}")
    if environ.get("GSETTINGS_BACKEND") != "memory":
        problems.append("GSETTINGS_BACKEND is not memory")
    for name in ("WAYLAND_DISPLAY", "APPIMAGE", "APPDIR", "LD_PRELOAD",
                 "PYTHONSTARTUP", "PYTHONHOME", "http_proxy", "https_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy",
                 "SSH_AUTH_SOCK", "GIO_EXTRA_MODULES", "GTK_MODULES"):
        if environ.get(name):
            problems.append(f"{name} is set")
    return problems
