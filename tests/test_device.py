import grp
import os
import pwd
import stat
from unittest import mock

import pytest

from understeer import device


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    """Empty fake sysfs; returns a function that adds a device folder."""
    monkeypatch.setattr(device, "SYSFS_GLOB", str(tmp_path / "ftec_tuning" / "*"))
    monkeypatch.setattr(device, "RUMBLE_GLOB", str(tmp_path / "hid" / "*" / "rumble"))

    def make(name, **attrs):
        d = tmp_path / "ftec_tuning" / name
        d.mkdir(parents=True)
        for k, v in attrs.items():
            (d / k).write_text(f"{v}\n")
        return d
    return make


@pytest.fixture
def privileged(monkeypatch):
    """Records privileged calls instead of asking for a password."""
    priv = mock.Mock(return_value=True)
    monkeypatch.setattr(device, "run_privileged", priv)
    return priv


@pytest.fixture
def no_write_access(monkeypatch):
    real_open = open

    def deny(path, mode="r", *args, **kwargs):
        if "w" in mode:
            raise PermissionError(path)
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr("builtins.open", deny)


# -- finding the device -----------------------------------------------------
def test_nothing_connected(sysfs):
    assert device.find_device_path() is None
    assert device.find_rumble_path() is None


def test_prefers_device_with_ff(sysfs, monkeypatch):
    pedals = sysfs("pedals", brF=50)
    wheel = sysfs("wheel", FF=70)
    # pedals listed first, so only the FF check can pick the wheel
    monkeypatch.setattr(device.glob, "glob", lambda p: [str(pedals), str(wheel)])
    assert device.find_device_path() == str(wheel)


def test_takes_device_without_ff(sysfs):
    pedals = sysfs("pedals", brF=50)
    assert device.find_device_path() == str(pedals)


def test_rumble(sysfs, tmp_path):
    r = tmp_path / "hid" / "0003:0EB7:183B.0001" / "rumble"
    r.parent.mkdir(parents=True)
    r.write_text("0")
    assert device.find_rumble_path() == str(r)


@pytest.mark.parametrize("folder, model", [
    ("0003:0EB7:0020.0005", "0EB7:0020"),
    ("0003:0eb7:0020.001A", "0EB7:0020"),       # same model after reconnect
    ("/sys/class/ftec_tuning/0003:0EB7:0006.0002/", "0EB7:0006"),
    ("something-else", "something-else"),
])
def test_model_id(folder, model):
    assert device.model_id(folder) == model


@pytest.mark.parametrize("folder, expected", [
    ("0003:0EB7:0020.0005", (2520, 2530)),      # CSL DD: Auto reads 2530
    ("0003:0EB7:0007.0001", (2520, 2530)),      # Podium DD2
    ("0003:0EB7:0E03.0001", (1080, 1090)),      # CSL Elite
    ("0003:0EB7:0004.0001", (900, None)),       # ClubSport V2.5: no Auto
    ("something-else", (1080, 1090)),           # the driver's default
])
def test_steering_range(folder, expected):
    assert device.steering_range(folder) == expected


# -- reading and writing ----------------------------------------------------
def test_read_int(sysfs):
    d = sysfs("w", FF=" 70 ", BAD="abc")
    assert device.read_int(d / "FF") == 70
    assert device.read_int(d / "BAD") is None
    assert device.read_int(d / "MISSING") is None


def test_write_values(sysfs, privileged):
    d = sysfs("w", FF=70, SEN=900)
    assert device.write_values({str(d / "FF"): 55, str(d / "SEN"): 1080.0}) == []
    assert (d / "FF").read_text() == "55"
    assert (d / "SEN").read_text() == "1080"
    privileged.assert_not_called()


def test_missing_attribute_fails(tmp_path, privileged):
    path = str(tmp_path / "nowhere" / "FF")
    assert device.write_values({path: 1}) == [path]
    privileged.assert_not_called()


def test_denied_writes_need_one_password(sysfs, privileged, no_write_access):
    d = sysfs("w", FF=70, SEN=900)
    assert device.write_values({str(d / "FF"): 55, str(d / "SEN"): 900}) == []
    privileged.assert_called_once()
    script = privileged.call_args.args[0]
    assert f"echo 55 > {d / 'FF'}" in script
    assert f"echo 900 > {d / 'SEN'}" in script


def test_password_refused(sysfs, privileged, no_write_access):
    d = sysfs("w", FF=70)
    privileged.return_value = False
    assert device.write_values({str(d / "FF"): 55}) == [str(d / "FF")]


def test_paths_with_spaces_are_quoted(tmp_path, privileged, no_write_access):
    path = str(tmp_path / "odd dir" / "FF")
    device.write_values({path: 1})
    assert f"echo 1 > '{path}'" in privileged.call_args.args[0]


# -- permissions ------------------------------------------------------------
GROUP_WRITABLE = stat.S_IFREG | 0o664


@pytest.mark.parametrize("mode, gid, members, user_gid, expected", [
    (GROUP_WRITABLE, 60, [], 1000, ("join", "games")),
    (GROUP_WRITABLE, 60, ["racer"], 1000, ("relogin", "games")),
    (GROUP_WRITABLE, 60, [], 60, ("relogin", "games")),       # primary group
    (stat.S_IFREG | 0o644, 60, [], 1000, ("udev", None)),     # group can't write
    (GROUP_WRITABLE, 0, [], 1000, ("udev", None)),            # root group
])
def test_permission_hint(monkeypatch, mode, gid, members, user_gid, expected):
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):     # only fake our device file
        if str(path).startswith("/sys/fake/"):
            return mock.Mock(st_mode=mode, st_gid=gid)
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(device.os, "stat", fake_stat)
    monkeypatch.setattr(device.grp, "getgrgid",
                        lambda g: grp.struct_group(("games", "x", gid, members)))
    monkeypatch.setattr(device.pwd, "getpwuid", lambda u: pwd.struct_passwd(
        ("racer", "x", 1000, user_gid, "", "/", "/bin/sh")))
    assert device.permission_hint("/sys/fake") == expected


def test_permission_hint_missing_file(tmp_path):
    assert device.permission_hint(str(tmp_path)) == ("udev", None)


def test_install_udev_rule(privileged):
    assert device.install_udev_rule()
    script = privileged.call_args.args[0]
    assert device.UDEV_RULE_PATH in script
    assert 'SUBSYSTEM=="ftec_tuning"' in script
    assert "udevadm control --reload-rules" in script
    # only the devices of the rule, not all devices of the system
    assert "--subsystem-match=ftec_tuning" in script


def test_join_group(privileged):
    user = pwd.getpwuid(os.getuid()).pw_name
    assert device.join_group("games")
    privileged.assert_called_once_with(f"usermod -aG games {user}")


def test_wheel_info(sysfs, tmp_path):
    d = sysfs("0003:0EB7:0020.0005", FF=50)
    hid = tmp_path / "hid-device"
    hid.mkdir()
    (d / "device").symlink_to(hid)
    assert device.wheel_info(str(d)) == {           # older driver
        "base": "CSL DD", "base_firmware": None, "rim_id": None,
        "rim_firmware": None}
    (hid / "fw_version").write_text("1234\n")
    (hid / "rim_id").write_text("0\n")             # no wheel reported yet
    (hid / "fw_version_rim").write_text("7\n")
    info = device.wheel_info(str(d))
    assert (info["base_firmware"], info["rim_id"], info["rim_firmware"]) == (1234, None, None)
    (hid / "rim_id").write_text("10\n")
    info = device.wheel_info(str(d))
    assert (info["rim_id"], info["rim_firmware"]) == (10, 7)
    assert device.RIMS[info["rim_id"]] == "ClubSport Steering Wheel Formula V2"


def test_wheel_info_unknown_base(sysfs):
    assert device.wheel_info(str(sysfs("0003:0EB7:0999.0001")))["base"] is None
    assert device.wheel_info(str(sysfs("odd-name")))["base"] is None


# -- Standard / Advanced tuning menu ----------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("1\n", 1), ("6\n", 6), ("auto\n", 0), ("auto", 0), ("", None), ("x\n", None)])
def test_read_slot(tmp_path, text, expected):
    """The driver writes the number of the slot, or "auto" for the Auto
    setup of the wheelbase."""
    path = tmp_path / "SLOT"
    path.write_text(text)
    assert device.read_slot(str(path)) == expected


def test_read_slot_missing(tmp_path):
    assert device.read_slot(str(tmp_path / "SLOT")) is None


@pytest.mark.parametrize("text, expected", [
    ("1\n", True), ("0\n", False), ("1", True),
    ("2\n", None), ("advanced\n", None), ("", None)])
def test_read_advanced_mode(tmp_path, text, expected):
    """The driver writes 1 or 0; anything else is not a mode."""
    path = tmp_path / "advanced_mode"
    path.write_text(text)
    assert device.read_advanced_mode(str(path)) is expected


def test_read_advanced_mode_missing(tmp_path):
    assert device.read_advanced_mode(str(tmp_path / "advanced_mode")) is None


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def mode_file(sysfs):
    """advanced_mode of a wheelbase in Advanced mode, next to its settings."""
    return sysfs("0003:0EB7:0020.0005", advanced_mode=1, FF=70, SEN=900, SLOT=2
                 ) / "advanced_mode"


def test_mode_is_read_when_attached(mode_file):
    mode = device.TuningMode()
    assert mode.path is None and mode.advanced is None and mode.poll() is None
    mode.attach(str(mode_file))
    assert mode.advanced is True and not mode.switching
    assert mode.poll() is None          # nothing happened since


@pytest.mark.parametrize("start, wanted", [(0, True), (1, False)])
def test_switch_mode(mode_file, privileged, start, wanted):
    """Standard to Advanced and back: the number is written, and the switch
    counts once the file shows the new mode."""
    mode_file.write_text(f"{start}\n")
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    assert mode.switch(wanted) and mode.switching
    assert mode.advanced is (not wanted)        # not before it is read back
    assert mode_file.read_text() == str(int(wanted))
    assert mode.poll() == mode.SWITCHED
    assert mode.advanced is wanted and not mode.switching
    assert mode.poll() is None                  # told once
    privileged.assert_not_called()


def test_switch_leaves_other_settings_alone(mode_file, privileged):
    folder = mode_file.parent
    before = {p.name: p.read_text() for p in folder.iterdir()
              if p.name != "advanced_mode"}
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    assert mode.switch(False) and mode.poll() == mode.SWITCHED
    assert {p.name: p.read_text() for p in folder.iterdir()
            if p.name != "advanced_mode"} == before
    assert sorted(before) == ["FF", "SEN", "SLOT"]


def test_wheelbase_does_not_switch(mode_file, monkeypatch):
    """The driver accepts every write; a wheelbase that keeps its mode is
    noticed when the time is up, not before."""
    monkeypatch.setattr(device, "write_values", lambda writes: [])
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert mode.switch(False)
    clock.now += device.MODE_SWITCH_SECONDS - 0.1
    assert mode.poll() is None and mode.switching
    clock.now += 0.2
    assert mode.poll() == mode.FAILED
    assert mode.advanced is True and not mode.switching
    assert mode.poll() is None


def test_asks_again_while_waiting(mode_file, monkeypatch):
    """A wheelbase that has just switched ignores the request, so it is
    repeated every MODE_RETRY_SECONDS until the switch is read back."""
    asked = []
    monkeypatch.setattr(device, "write_values",
                        lambda writes: asked.append(dict(writes)) or [])
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert mode.switch(False) and asked == [{str(mode_file): 0}]
    clock.now += device.MODE_RETRY_SECONDS - 0.1
    assert mode.poll() is None and len(asked) == 1      # not yet
    clock.now += 0.2
    assert mode.poll() is None and asked[1:] == [{str(mode_file): 0}]
    assert mode.poll() is None and len(asked) == 2      # once per second
    clock.now += device.MODE_RETRY_SECONDS
    assert mode.poll() is None and len(asked) == 3
    mode_file.write_text("0\n")         # now the wheelbase switched
    clock.now += device.MODE_RETRY_SECONDS
    assert mode.poll() == mode.SWITCHED and len(asked) == 3
    clock.now += device.MODE_RETRY_SECONDS
    assert mode.poll() is None and len(asked) == 3      # nothing more to ask


def test_busy_after_switching(mode_file):
    """The wheelbase ignores a request right after it has switched, also
    after a switch made on the wheelbase itself."""
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert not mode.busy
    assert mode.switch(False) and mode.busy             # switching
    assert mode.poll() == mode.SWITCHED and mode.busy   # ... and just after
    clock.now += device.MODE_SETTLE_SECONDS - 0.1
    assert mode.busy
    clock.now += 0.2
    assert not mode.busy
    mode_file.write_text("1\n")                         # on the wheelbase
    assert mode.poll() == mode.CHANGED and mode.busy
    clock.now += device.MODE_SETTLE_SECONDS
    assert not mode.busy


def test_not_busy_after_a_failed_switch(mode_file, monkeypatch):
    monkeypatch.setattr(device, "write_values", lambda writes: [])
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert mode.switch(False)
    clock.now += device.MODE_SWITCH_SECONDS
    assert mode.poll() == mode.FAILED and not mode.busy


def test_does_not_ask_a_file_that_is_gone(mode_file, monkeypatch):
    asked = []
    monkeypatch.setattr(device, "write_values",
                        lambda writes: asked.append(dict(writes)) or [])
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert mode.switch(False)
    mode_file.unlink()                  # the wheelbase was switched off
    clock.now += device.MODE_RETRY_SECONDS
    assert mode.poll() is None and len(asked) == 1
    clock.now += device.MODE_SWITCH_SECONDS
    assert mode.poll() == mode.FAILED and mode.advanced is True


def test_wheelbase_switches_late(mode_file, monkeypatch):
    monkeypatch.setattr(device, "write_values", lambda writes: [])
    clock = Clock()
    mode = device.TuningMode(clock)
    mode.attach(str(mode_file))
    assert mode.switch(False)
    clock.now += 1
    assert mode.poll() is None
    mode_file.write_text("0\n")         # the wheelbase reports its settings
    assert mode.poll() == mode.SWITCHED and mode.advanced is False


def test_mode_changed_on_the_wheelbase(mode_file):
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    mode_file.write_text("0\n")
    assert mode.poll() == mode.CHANGED and mode.advanced is False
    assert mode.poll() is None
    mode_file.write_text("1\n")
    assert mode.poll() == mode.CHANGED and mode.advanced is True


def test_mode_read_for_the_first_time_is_no_change(mode_file):
    mode_file.write_text("\n")          # nothing to read yet
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    assert mode.advanced is None
    mode_file.write_text("1\n")
    assert mode.poll() is None and mode.advanced is True


def test_mode_file_missing(tmp_path, privileged):
    """An older driver, or the wheelbase unplugged meanwhile."""
    mode = device.TuningMode()
    mode.attach(str(tmp_path / "advanced_mode"))
    assert mode.advanced is None and mode.poll() is None
    assert not mode.switch(True) and not mode.switching
    mode.attach(None)
    assert not mode.switch(True) and mode.poll() is None
    privileged.assert_not_called()


def test_mode_file_gone_keeps_last_mode(mode_file):
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    mode_file.unlink()
    assert mode.poll() is None and mode.advanced is True


def test_mode_file_read_only(mode_file, privileged, no_write_access):
    """Without write access and with the password refused nothing is
    switched, and nothing is waited for."""
    privileged.return_value = False
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    assert not mode.switch(False)
    assert not mode.switching and mode.advanced is True
    assert mode_file.read_text() == "1\n"
    assert mode.poll() is None


def test_attach_again_forgets_a_switch(mode_file, tmp_path, monkeypatch):
    """A wheelbase plugged in again gets a new folder; a switch asked of
    the old one is over."""
    monkeypatch.setattr(device, "write_values", lambda writes: [])
    mode = device.TuningMode()
    mode.attach(str(mode_file))
    assert mode.switch(False)
    other = tmp_path / "advanced_mode"
    other.write_text("0\n")
    mode.attach(str(other))
    assert not mode.switching and mode.advanced is False
    assert mode.poll() is None
