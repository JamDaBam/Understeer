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


def test_join_group(privileged):
    user = pwd.getpwuid(os.getuid()).pw_name
    assert device.join_group("games")
    privileged.assert_called_once_with(f"usermod -aG games {user}")
