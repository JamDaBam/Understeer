"""Reading and writing the wheelbase settings in sysfs, without GTK."""
import glob
import grp
import logging
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import time

SYSFS_GLOB  = "/sys/class/ftec_tuning/*"
RUMBLE_GLOB = "/sys/bus/hid/devices/*/rumble"

log = logging.getLogger("understeer.device")

UDEV_RULE = """ACTION!="add|change", GOTO="fanatec_tuner_end"

SUBSYSTEM=="ftec_tuning", RUN+="/bin/sh -c 'find /sys%p -type f -exec chmod 666 {} +'"
SUBSYSTEM=="hid", DRIVER=="fanatec", RUN+="/bin/sh -c 'chmod 666 /sys%p/rumble 2>/dev/null'"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{idVendor}=="0eb7", MODE="0644"

LABEL="fanatec_tuner_end"
"""
UDEV_RULE_PATH = "/etc/udev/rules.d/99-fanatec-tuner.rules"


def find_device_path():
    candidates = [p for p in glob.glob(SYSFS_GLOB) if os.path.isdir(p)]
    valid = [p for p in candidates if os.path.exists(os.path.join(p, "FF"))]
    return valid[0] if valid else (candidates[0] if candidates else None)


def model_id(device_path):
    """Vendor and product id of the wheelbase, e.g. "0EB7:0020".

    The driver names the folder after the HID device ("0003:0EB7:0020.0005");
    the number after the dot changes on every reconnect, so it is left out.
    Other names are returned unchanged.
    """
    name = os.path.basename(os.path.normpath(device_path))
    m = re.fullmatch(r"[0-9A-Fa-f]{4}:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\.[0-9A-Fa-f]+",
                     name)
    return f"{m[1]}:{m[2]}".upper() if m else name


# Names for the ids hid-fanatecff reports (from its hid-ftec.h). Product
# names, so they aren't translated.
WHEEL_BASES = {          # USB product id
    0x0001: "ClubSport Wheel Base V2",
    0x0004: "ClubSport Wheel Base V2.5",
    0x0005: "CSL Elite Wheel Base (PS4)",
    0x0006: "Podium Wheel Base DD1",
    0x0007: "Podium Wheel Base DD2",
    0x0011: "CSR Elite Wheel Base",
    0x0020: "CSL DD",
    0x0E03: "CSL Elite Wheel Base",
}
RIMS = {                 # rim_id
    0x01: "ClubSport Steering Wheel BMW M3 GT2",
    0x02: "ClubSport Steering Wheel F1",
    0x03: "ClubSport Steering Wheel Porsche 918 RSR",
    0x04: "ClubSport Universal Hub",
    0x06: "ClubSport Universal Hub for Xbox One",
    0x07: "CSL Steering Wheel P1 for Xbox One",
    0x08: "CSL Steering Wheel P1 V2",
    0x09: "CSL Elite Steering Wheel McLaren GT3",
    0x0A: "ClubSport Steering Wheel Formula V2",
    0x0B: "CSL Elite Steering Wheel McLaren GT3 V2",
    0x0C: "Podium Button Module Endurance",
    0x0D: "CSL Elite Steering Wheel WRC",
    0x0E: "Podium Steering Wheel Bentley GT3",
    0x0F: "Podium Steering Wheel BMW M4 GT3",
    0x10: "Podium Steering Wheel GT",
    0x11: "CSL Universal Hub",
    0x12: "CSL Elite Steering Wheel WRC",
    0x13: "ClubSport Steering Wheel BMW GT2 V2",
    0x14: "ClubSport Steering Wheel RS",
    0x15: "ClubSport Universal Hub V2",
    0x16: "ClubSport Steering Wheel F1 Esports V2",
    0x18: "Podium Steering Wheel GT X",
}


def wheel_info(device_path):
    """What the driver tells about the wheelbase and the wheel on it:
    {"base": name or None, "base_firmware", "rim_id", "rim_firmware"}.

    The wheelbase firmware comes with hid-fanatecff 0.2.3, rim_id and the
    wheel's firmware with its master from October 2026 (no numbered release
    yet); with older drivers they are None. rim_id 0 means no wheel was
    reported yet. The files belong to the HID device, the parent of the
    tuning folder.
    """
    hid = os.path.join(device_path, "device")
    product = model_id(device_path).partition(":")[2]
    try:
        base = WHEEL_BASES.get(int(product, 16))
    except ValueError:
        base = None
    rim_id = read_int(os.path.join(hid, "rim_id"))
    return {"base": base,
            "base_firmware": read_int(os.path.join(hid, "fw_version")),
            "rim_id": rim_id or None,
            "rim_firmware": read_int(os.path.join(hid, "fw_version_rim"))
            if rim_id else None}


# The steering angle (SEN) per wheelbase, as hid-fanatecff allows it
# ("max_range" in its hid-ftec.c). Writing 0 sets the largest value; on
# most wheelbases that is one step past the largest angle and stands for
# Auto (AUT in the tuning menu of the wheelbase). The others get their
# largest angle.
DIRECT_DRIVE = {0x0006, 0x0007, 0x0020}     # Podium DD1, DD2, CSL DD
UP_TO_900 = {0x0001, 0x0004, 0x0011, 0x0197}  # ClubSport V2, V2.5, CSR Elite, Porsche 911


def steering_range(device_path):
    """(largest steering angle, the value the driver reports for Auto or
    None if the wheelbase has no Auto) of the wheelbase."""
    try:
        product = int(model_id(device_path).partition(":")[2], 16)
    except ValueError:
        product = None
    if product in DIRECT_DRIVE:
        return 2520, 2530
    if product in UP_TO_900:
        return 900, None
    return 1080, 1090           # CSL Elite and the driver's default


def find_rumble_path():
    matches = glob.glob(RUMBLE_GLOB)
    return matches[0] if matches else None


def can_write(device_path):
    """sysfs dirs are rarely writable even when their files are, so test FF."""
    attr = os.path.join(device_path, "FF")
    return os.access(attr if os.path.exists(attr) else device_path, os.W_OK)


def permission_hint(device_path):
    """How the user can get write access to the tuning settings.

    The hid-fanatecff udev rule hands the settings to the "games" group.
    Returns ("join", group) if joining that group would help,
    ("relogin", group) if the user already joined but must log in again,
    or ("udev", None) if a separate udev rule is needed.
    """
    try:
        st = os.stat(os.path.join(device_path, "FF"))
        group = grp.getgrgid(st.st_gid)
    except (OSError, KeyError):
        return "udev", None
    if group.gr_gid == 0 or not st.st_mode & stat.S_IWGRP:
        return "udev", None
    user = pwd.getpwuid(os.getuid())
    member = user.pw_name in group.gr_mem or user.pw_gid == group.gr_gid
    return ("relogin" if member else "join"), group.gr_name


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


STANDARD_SLOTS = (0, 1)     # Auto and Custom (A SET, C SET on a CSL DD)


def slots_in_menu(advanced, count):
    """The slots the tuning menu offers: in Standard mode (advanced False)
    Auto and Custom, which the driver reports as auto and 1; otherwise
    slots 1 to count. 0 stands for Auto."""
    if advanced is False:
        return list(STANDARD_SLOTS)
    return list(range(1, count + 1))


def read_slot(path):
    """The tuning slot the wheelbase is on: its number, 0 for its Auto
    setup (the driver writes "auto" then), None if path (the SLOT file)
    can't be read."""
    try:
        with open(path) as f:
            text = f.read().strip()
        return 0 if text == "auto" else int(text)
    except (OSError, ValueError):
        return None


def read_advanced_mode(path):
    """Whether the tuning menu is in Advanced mode: True, False for
    Standard, None if path (the advanced_mode file) can't be read or holds
    something else.

    hid-fanatecff writes "1" or "0" there (hid-ftecff-tuning.c); before the
    wheelbase has reported its settings it says 0.
    """
    value = read_int(path)
    return bool(value) if value in (0, 1) else None


MODE_SWITCH_SECONDS = 5     # how long the wheelbase gets to switch its menu
MODE_RETRY_SECONDS = 1      # ... and how often it is asked again meanwhile
MODE_SETTLE_SECONDS = 2.5   # after switching it takes no request for a while


class TuningMode:
    """Follows and switches the Standard/Advanced mode of the tuning menu.

    Writing to advanced_mode doesn't set the mode: if the number differs
    from the mode the driver knows, it asks the wheelbase to switch to the
    other one, and it reports success in any case. The file shows the new
    mode once the wheelbase has sent its settings again. So a switch only
    counts when it can be read back, and poll() tells how it ended.

    A CSL DD switches within 0.2 s, but ignores the request for about two
    seconds after it has switched (measured: 1.8 s ignored, 2.3 s taken).
    So busy tells while a switch is better not asked for, and poll() asks
    again until the wheelbase follows. That can't switch twice: the driver
    only passes on a number that differs from the mode the wheelbase
    reported.
    """
    SWITCHED = "switched"   # the wheelbase did what switch() asked for
    FAILED = "failed"       # ... or didn't, in MODE_SWITCH_SECONDS
    CHANGED = "changed"     # switched by someone else, e.g. on the wheelbase

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.path = None        # the advanced_mode file, None if there is none
        self.advanced = None    # mode last read: True, False or None (unknown)
        self._wanted = None     # mode a switch is waiting for
        self._deadline = self._retry_at = self._settled_at = 0

    @property
    def switching(self):
        return self._wanted is not None

    @property
    def busy(self):
        """Switching, or switched so recently that the wheelbase would
        ignore the next request."""
        return self.switching or self._clock() < self._settled_at

    def attach(self, path):
        """Follow this advanced_mode file from now on; None for none."""
        if path == self.path:
            return
        self.path = path
        self._wanted = None
        self._settled_at = 0
        self.advanced = read_advanced_mode(path) if path else None

    def switch(self, advanced):
        """Ask the wheelbase for Advanced (True) or Standard (False).
        Returns False if that could not be written; otherwise poll() tells
        later whether the wheelbase switched."""
        # Not for a file that is gone: writing would make one, or in sysfs
        # ask for a password in vain.
        if not self.path or not os.path.isfile(self.path):
            return False
        if write_values({self.path: int(bool(advanced))}):
            self._wanted = None
            return False
        self._wanted = bool(advanced)
        self._deadline = self._clock() + MODE_SWITCH_SECONDS
        self._retry_at = self._clock() + MODE_RETRY_SECONDS
        return True

    def poll(self):
        """Read the mode again. Returns SWITCHED or FAILED once for a
        switch() that ended, CHANGED if the mode changed without one, else
        None. A mode read for the first time is no change."""
        if not self.path:
            return None
        mode = read_advanced_mode(self.path)
        if self._wanted is not None:
            now = self._clock()
            if mode == self._wanted:
                self._wanted, self.advanced = None, mode
                self._settled_at = now + MODE_SETTLE_SECONDS
                return self.SWITCHED
            if now < self._deadline:
                if now >= self._retry_at and os.path.isfile(self.path):
                    self._retry_at = now + MODE_RETRY_SECONDS
                    write_values({self.path: int(self._wanted)})
                return None
            self._wanted = None
            if mode is not None:
                self.advanced = mode
            return self.FAILED
        if mode is None or mode == self.advanced:
            return None
        known, self.advanced = self.advanced is not None, mode
        if not known:
            return None
        self._settled_at = self._clock() + MODE_SETTLE_SECONDS
        return self.CHANGED


def _value_text(value):
    """What is written for value: a whole number, or "auto" for SLOT."""
    return "auto" if value == "auto" else str(int(value))


def write_values(writes):
    """Write {path: int or "auto"}. Paths without permission go through one
    pkexec call.

    Returns the list of paths that could not be written.
    """
    denied, failed, written = {}, [], []
    for path, value in writes.items():
        name = f"{os.path.basename(path)}={_value_text(value)}"
        try:
            with open(path, "w") as f:
                f.write(_value_text(value))
            written.append(name)
        except PermissionError:
            denied[path] = value
        except OSError as e:
            # The driver answers e.g. EINVAL for values it does not accept
            # and ENODEV once the wheelbase is gone.
            log.warning("Could not write %s to %s: %s", name, path,
                        e.strerror or e)
            failed.append(path)
    if written:
        log.debug("Wrote %s", ", ".join(written))
    if denied:
        names = ", ".join(f"{os.path.basename(p)}={_value_text(v)}"
                          for p, v in denied.items())
        log.info("No write access, asking for a password to write %s", names)
        script = "; ".join(f"echo {_value_text(v)} > {shlex.quote(p)}"
                           for p, v in denied.items())
        if not run_privileged(script):
            log.warning("Could not write %s with a password either", names)
            failed += list(denied)
    return failed


def run_privileged(script):
    tool = "pkexec" if shutil.which("pkexec") else "sudo"
    try:
        subprocess.run([tool, "/bin/sh", "-c", script], check=True,
                       capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        # pkexec: 126 = password dialog cancelled, 127 = not authorised
        log.warning("%s failed (exit code %s) running: %s\n%s", tool,
                    e.returncode, script, (e.stderr or "").strip())
        return False
    except OSError as e:
        log.warning("Could not run %s: %s", tool, e)
        return False


def describe_access(device_path):
    """One line about who may write the tuning settings, for the log.

    Says through what the user may write, or why not: the udev rule of
    the README and of "Set Up" makes the settings writable for everyone,
    the one of hid-fanatecff for a group (usually "games").
    """
    path = os.path.join(device_path, "FF")
    try:
        st = os.stat(path)
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        mode = stat.filemode(st.st_mode)
    except OSError as e:
        return f"settings not readable: {e}"
    if st.st_mode & stat.S_IWOTH:
        how = "writable for everyone (udev rule)"
    elif st.st_mode & stat.S_IWGRP:
        # Only the group that matters; the user's other groups are private.
        member = st.st_gid in os.getgroups() or st.st_gid == os.getgid()
        how = f"user {'in' if member else 'not in'} group {group}"
    else:
        how = f"only {owner} may write"
    result = "writable" if os.access(path, os.W_OK) else "not writable"
    return f"settings {mode} {owner}:{group}, {how}: {result}"


def install_udev_rule():
    """Install UDEV_RULE with one password prompt. Returns True on success."""
    return run_privileged(
        f"printf %s {shlex.quote(UDEV_RULE)} > {UDEV_RULE_PATH} && "
        # Only the devices the rule is about, not every device there is.
        "udevadm control --reload-rules && "
        "udevadm trigger --subsystem-match=ftec_tuning "
        "--subsystem-match=hid --subsystem-match=input")


def join_group(group):
    """Add the current user to group. Returns True on success."""
    user = pwd.getpwuid(os.getuid()).pw_name
    return run_privileged(f"usermod -aG {shlex.quote(group)} {shlex.quote(user)}")
