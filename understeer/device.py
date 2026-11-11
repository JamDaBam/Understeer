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

    rim_id and the firmware versions come with hid-fanatecff from October
    2026 on; with older drivers they are None. rim_id 0 means no wheel was
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
# Auto (AUT in the tuning menu of the wheelbase).
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


def write_values(writes):
    """Write {path: int}. Paths without permission go through one pkexec call.

    Returns the list of paths that could not be written.
    """
    denied, failed, written = {}, [], []
    for path, value in writes.items():
        name = f"{os.path.basename(path)}={int(value)}"
        try:
            with open(path, "w") as f:
                f.write(str(int(value)))
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
        names = ", ".join(f"{os.path.basename(p)}={int(v)}" for p, v in denied.items())
        log.info("No write access, asking for a password to write %s", names)
        script = "; ".join(f"echo {int(v)} > {shlex.quote(p)}"
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
