import os
import shutil

import pytest

from understeer import capabilities as caps_mod
from understeer import device
from understeer.capabilities import (ADVANCED_MODE, BASE_REV_LEDS, DISPLAY,
                                     FEATURES, FLAG_LEDS, PEDAL_LOAD,
                                     PEDAL_RUMBLE, RGB_LEDS, RIM_REV_LEDS,
                                     detect, describe, summary)

BASE = "0003:0EB7:0020.0005"       # CSL DD
ELITE = "0003:0EB7:0E03.0007"      # CSL Elite: rumble for the pedals on it
PEDALS = "0003:0EB7:183B.0009"     # ClubSport Pedals V3 by USB


class FakeSysfs:
    """A small sysfs laid out like the kernel does it:

    devices/usb/<port>/<port>:1.<n>/<HID>   the HID devices, below their USB device
    bus/hid/devices/<HID>                   links to them
    class/ftec_tuning/<HID>                 tuning folder, "device" links to the HID
    class/ftec_wheel/<HID>                  the wheel on the wheelbase
    class/leds/<name>                       LEDs
    """

    def __init__(self, root):
        self.root = root
        for d in ("bus/hid/devices", "class/ftec_tuning", "class/ftec_wheel",
                  "class/leds"):
            (root / d).mkdir(parents=True)

    def hid(self, name, port="1-2", interface=0, files=()):
        path = self.root / "devices/usb" / port / f"{port}:1.{interface}" / name
        path.mkdir(parents=True)
        for f in files:
            self.file(path / f)
        (self.root / "bus/hid/devices" / name).symlink_to(path)
        return path

    @staticmethod
    def file(path, mode=0o644, text="0\n"):
        path.write_text(text)
        os.chmod(path, mode)
        return path

    def tuning(self, name, hid, files=("FF", "advanced_mode")):
        path = self.root / "class/ftec_tuning" / name
        path.mkdir()
        for f in files:
            self.file(path / f)
        (path / "device").symlink_to(hid)
        return path

    def led(self, name, rgb=False):
        path = self.root / "class/leds" / name
        path.mkdir()
        self.file(path / "brightness")
        if rgb:
            self.file(path / "multi_intensity", text="255 255 255\n")
        return path

    def rim(self, base, rim_id, leds=0, rgb=False, flags=False):
        hid = self.root / "bus/hid/devices" / base
        self.file(hid / "rim_id", text=f"{rim_id}\n")
        (self.root / "class/ftec_wheel" / base).mkdir()
        kind = "RGB:" if rgb else ""
        for n in range(1, leds + 1):
            self.led(f"{base}:{rim_id:02x}:{kind}RPM{n}", rgb)
        if flags:
            for side in ("RFLAG", "LFLAG"):
                for n in range(1, 4):
                    self.led(f"{base}:{rim_id:02x}:RGB:{side}{n}", rgb=True)

    def remove_rim(self, base):
        shutil.rmtree(self.root / "class/ftec_wheel" / base)
        for p in (self.root / "class/leds").glob(f"{base}:[0-9a-f]*"):
            shutil.rmtree(p)
        os.remove(self.root / "bus/hid/devices" / base / "rim_id")

    def wheelbase(self, name=BASE, display=True, base_leds=0, port="1-2"):
        files = ["range"] + (["display"] if display else [])
        hid = self.hid(name, port=port, files=files)
        if display:
            os.chmod(hid / "display", 0o220)    # write only, as the driver makes it
        tuning = self.tuning(name, hid)
        for n in range(1, base_leds + 1):
            self.led(f"{name}::RPM{n}")
        return hid, tuning


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    fake = FakeSysfs(tmp_path)
    monkeypatch.setattr(device, "SYSFS_GLOB", str(tmp_path / "class/ftec_tuning/*"))
    monkeypatch.setattr(device, "RUMBLE_GLOB",
                        str(tmp_path / "bus/hid/devices/*/rumble"))
    monkeypatch.setattr(caps_mod, "HID_GLOB", str(tmp_path / "bus/hid/devices/*"))
    monkeypatch.setattr(caps_mod, "LEDS_GLOB", str(tmp_path / "class/leds/*"))
    monkeypatch.setattr(caps_mod, "WHEEL_GLOB", str(tmp_path / "class/ftec_wheel/*"))
    return fake


@pytest.fixture
def no_writing(monkeypatch):
    """Fails the test if detecting writes anything."""
    real_open = open

    def guard(path, mode="r", *args, **kwargs):
        assert not any(c in mode for c in "wa+"), f"wrote {path}"
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr("builtins.open", guard)


# -- nothing there ----------------------------------------------------------
def test_nothing_connected(sysfs, no_writing):
    caps = detect()
    assert caps.wheelbase is None and caps.tuning is None
    assert caps.rim_id is None and caps.pedals == ()
    for name in FEATURES:
        assert not caps.has(name)
        assert caps.get(name) is None
        assert not caps.can_read(name) and not caps.can_write(name)


def test_sysfs_folders_missing(tmp_path, monkeypatch):
    """Without the driver loaded even the class folders are missing."""
    monkeypatch.setattr(device, "SYSFS_GLOB", str(tmp_path / "no/ftec_tuning/*"))
    monkeypatch.setattr(device, "RUMBLE_GLOB", str(tmp_path / "no/hid/*/rumble"))
    monkeypatch.setattr(caps_mod, "HID_GLOB", str(tmp_path / "no/hid/*"))
    monkeypatch.setattr(caps_mod, "LEDS_GLOB", str(tmp_path / "no/leds/*"))
    monkeypatch.setattr(caps_mod, "WHEEL_GLOB", str(tmp_path / "no/wheel/*"))
    assert detect().features == {}


# -- wheelbase --------------------------------------------------------------
def test_wheelbase_functions(sysfs, no_writing):
    hid, tuning = sysfs.wheelbase(base_leds=9)
    caps = detect()
    assert caps.tuning == str(tuning)
    assert caps.wheelbase == os.path.realpath(hid)
    assert caps.get(ADVANCED_MODE).paths == (str(tuning / "advanced_mode"),)
    assert caps.can_read(ADVANCED_MODE) and caps.can_write(ADVANCED_MODE)
    leds = caps.get(BASE_REV_LEDS).paths
    assert len(leds) == 9
    assert leds[0].endswith(f"{BASE}::RPM1/brightness")
    assert leds[-1].endswith(f"{BASE}::RPM9/brightness")     # 9 after 2..8, not after 1
    # no wheel on it
    for name in (RIM_REV_LEDS, RGB_LEDS, FLAG_LEDS):
        assert not caps.has(name)


def test_write_only_file(sysfs):
    sysfs.wheelbase()
    caps = detect()
    assert caps.has(DISPLAY)
    assert caps.can_write(DISPLAY) and not caps.can_read(DISPLAY)


def test_read_only_file(sysfs):
    _hid, tuning = sysfs.wheelbase()
    os.chmod(tuning / "advanced_mode", 0o444)
    caps = detect()
    assert caps.can_read(ADVANCED_MODE) and not caps.can_write(ADVANCED_MODE)


def test_no_permission(sysfs):
    if os.geteuid() == 0:
        pytest.skip("root may read and write everything")
    _hid, tuning = sysfs.wheelbase()
    os.chmod(tuning / "advanced_mode", 0o000)
    caps = detect()
    assert caps.has(ADVANCED_MODE)      # there, but this user may do nothing
    assert not caps.can_read(ADVANCED_MODE) and not caps.can_write(ADVANCED_MODE)
    assert "advanced_mode no access" in summary(caps)


def test_missing_function(sysfs):
    """An older driver without advanced_mode, a wheelbase without display."""
    hid = sysfs.hid(BASE, files=["range"])
    sysfs.tuning(BASE, hid, files=["FF"])
    caps = detect()
    assert caps.wheelbase is not None
    assert not caps.has(ADVANCED_MODE) and not caps.has(DISPLAY)
    assert not caps.has(BASE_REV_LEDS)


def test_wheelbase_without_tuning_menu(sysfs):
    """ClubSport V2 has no tuning folder, but a display."""
    hid = sysfs.hid("0003:0EB7:0001.0002", files=["range", "display"])
    caps = detect()
    assert caps.tuning is None
    assert caps.wheelbase == os.path.realpath(hid)
    assert caps.has(DISPLAY) and not caps.has(ADVANCED_MODE)


def test_leds_of_another_wheelbase_not_counted(sysfs):
    sysfs.wheelbase()
    sysfs.led("0003:0EB7:0E03.0001::RPM1")
    sysfs.led("input5::capslock")
    assert not detect().has(BASE_REV_LEDS)


# -- steering wheel ---------------------------------------------------------
def test_wheel_with_single_colour_leds(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE, 0x13, leds=9)       # ClubSport Steering Wheel BMW GT2 V2
    caps = detect()
    assert caps.rim_id == 0x13
    assert caps.rim.endswith(f"ftec_wheel/{BASE}")
    assert len(caps.get(RIM_REV_LEDS).paths) == 9
    assert not caps.has(RGB_LEDS) and not caps.has(FLAG_LEDS)
    assert not caps.has(BASE_REV_LEDS)  # the wheel's LEDs aren't the wheelbase's


def test_wheel_with_rgb_and_flag_leds(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE, 0x16, leds=9, rgb=True, flags=True)   # F1 Esports V2
    caps = detect()
    assert len(caps.get(RIM_REV_LEDS).paths) == 9
    assert len(caps.get(FLAG_LEDS).paths) == 6
    rgb = caps.get(RGB_LEDS).paths
    assert len(rgb) == 15 and all(p.endswith("multi_intensity") for p in rgb)


def test_wheel_without_leds(sysfs):
    """E.g. a universal hub: known to the driver, but no LEDs."""
    sysfs.wheelbase()
    sysfs.rim(BASE, 0x15)
    caps = detect()
    assert caps.rim_id == 0x15 and caps.rim is not None
    assert not caps.has(RIM_REV_LEDS)


def test_wheel_changed(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE, 0x13, leds=9)
    before = detect()
    sysfs.remove_rim(BASE)
    taken_off = detect()
    assert taken_off.rim_id is None and taken_off.rim is None
    assert not taken_off.has(RIM_REV_LEDS)
    sysfs.rim(BASE, 0x16, leds=9, rgb=True, flags=True)
    after = detect()
    assert after.rim_id == 0x16 and after.has(FLAG_LEDS)
    assert before != taken_off != after


def test_wheelbase_and_wheel_kept_apart(sysfs):
    """Only the wheelbase is switched on: no wheel functions; a wheel
    reported later adds them without touching the wheelbase's."""
    sysfs.wheelbase(base_leds=9)
    base_only = detect()
    sysfs.rim(BASE, 0x16, leds=9, rgb=True)
    both = detect()
    assert base_only.get(BASE_REV_LEDS) == both.get(BASE_REV_LEDS)
    assert not base_only.has(RIM_REV_LEDS) and both.has(RIM_REV_LEDS)


# -- drivers before October 2026 -------------------------------------------
def test_old_driver_wheel_and_leds(sysfs):
    """wheel_id instead of rim_id, no ftec_wheel folder; the 9 LEDs every
    wheelbase gets light the wheel's rev LEDs (as seen on a CSL DD)."""
    hid, _tuning = sysfs.wheelbase(base_leds=9)
    sysfs.file(hid / "wheel_id", mode=0o444, text="0x0a\n")
    caps = detect()
    assert caps.old_driver
    assert caps.rim_id == 0x0A and caps.rim is None
    assert len(caps.get(RIM_REV_LEDS).paths) == 9
    assert caps.get(RIM_REV_LEDS).paths[0].endswith(f"{BASE}::RPM1/brightness")
    assert not caps.has(BASE_REV_LEDS)
    assert "Steering wheel: id 10 (0x0a, ClubSport Steering Wheel Formula V2)" \
        in describe(caps)
    assert "before October 2026" in describe(caps)


def test_old_driver_csl_elite_leds(sysfs):
    """On a CSL Elite the same LEDs light its own rev LEDs too."""
    hid, _tuning = sysfs.wheelbase(name=ELITE, base_leds=9)
    sysfs.file(hid / "wheel_id", mode=0o444, text="0x00\n")
    caps = detect()
    assert caps.old_driver and caps.rim_id is None
    assert len(caps.get(BASE_REV_LEDS).paths) == 9
    assert len(caps.get(RIM_REV_LEDS).paths) == 9


def test_new_driver_rim_id_in_decimal(sysfs):
    hid, _tuning = sysfs.wheelbase()
    sysfs.file(hid / "rim_id", mode=0o444, text="22\n")
    caps = detect()
    assert caps.rim_id == 0x16 and not caps.old_driver
    assert "October 2026 or newer" in describe(caps)


# -- pedals -----------------------------------------------------------------
def test_usb_pedals(sysfs):
    sysfs.wheelbase()
    pedals = sysfs.hid(PEDALS, port="1-3", files=["load"])
    sysfs.file(pedals / "rumble", mode=0o220)
    caps = detect()
    assert caps.pedals == (os.path.realpath(pedals),)
    assert caps.can_read(PEDAL_LOAD) and caps.can_write(PEDAL_LOAD)
    assert caps.can_write(PEDAL_RUMBLE) and not caps.can_read(PEDAL_RUMBLE)


def test_rumble_through_wheelbase(sysfs):
    """A CSL Elite wheelbase drives the motors of the pedals on it."""
    hid, _tuning = sysfs.wheelbase(name=ELITE)
    sysfs.file(hid / "rumble", mode=0o220)
    caps = detect()
    assert caps.has(PEDAL_RUMBLE) and not caps.has(PEDAL_LOAD)
    assert caps.pedals == ()            # it is the wheelbase, not other pedals


def test_pedals_without_wheelbase(sysfs):
    sysfs.hid(PEDALS, files=["load"])
    caps = detect()
    assert caps.wheelbase is None and caps.has(PEDAL_LOAD)
    assert not caps.has(DISPLAY)


def test_one_device_per_usb_device(sysfs):
    """Several HID devices of one pair of pedals count once, the newest."""
    sysfs.hid("0003:0EB7:183B.0009", port="1-3", interface=0, files=["load"])
    newer = sysfs.hid("0003:0EB7:183B.000A", port="1-3", interface=1,
                      files=["load"])
    caps = detect()
    assert caps.pedals == (os.path.realpath(newer),)
    assert caps.get(PEDAL_LOAD).paths == (str(newer / "load"),)


def test_two_pedals_on_two_ports(sysfs):
    sysfs.hid("0003:0EB7:183B.0009", port="1-3", files=["load"])
    sysfs.hid("0003:0EB7:6204.000B", port="1-4", files=["load"])
    assert len(detect().get(PEDAL_LOAD).paths) == 2


# -- unplugging ----------------------------------------------------------
def test_unplugged(sysfs):
    hid, tuning = sysfs.wheelbase(base_leds=9)
    sysfs.rim(BASE, 0x13, leds=9)
    assert detect().has(DISPLAY)
    for p in (sysfs.root / "class/leds").iterdir():
        shutil.rmtree(p)
    shutil.rmtree(sysfs.root / "class/ftec_wheel" / BASE)
    shutil.rmtree(tuning)
    shutil.rmtree(hid)
    os.remove(sysfs.root / "bus/hid/devices" / BASE)
    caps = detect()
    assert caps.wheelbase is None and caps.features == {}


def test_unplugged_halfway(sysfs):
    """The kernel removes the HID device before the tuning folder."""
    hid, _tuning = sysfs.wheelbase()
    shutil.rmtree(hid)
    caps = detect()                     # the link to it now leads nowhere
    assert not caps.has(DISPLAY)


def test_error_while_looking(sysfs, monkeypatch):
    sysfs.wheelbase()

    def gone(*_args):
        raise OSError(19, "No such device")
    monkeypatch.setattr(caps_mod.device, "find_device_path", gone)
    assert detect() == caps_mod.Capabilities()


# -- for reading ------------------------------------------------------------
def test_summary_and_describe(sysfs):
    sysfs.wheelbase(base_leds=9)
    caps = detect()
    line = summary(caps)
    assert "advanced_mode rw" in line and "display w" in line
    assert "rim_rev_leds -" in line
    text = describe(caps)
    assert BASE in text and "Steering wheel: none" in text
    assert "base_rev_leds   rw      9" in text


def test_summary_for_reports(sysfs):
    """No paths, so a report shows nothing personal; counts for LED rows."""
    hid, _tuning = sysfs.wheelbase()
    sysfs.rim(BASE, 0x10, leds=9)
    caps = detect()
    line = summary(caps)
    assert "rim_rev_leds rw (9)" in line and "display w," in line
    assert "/" not in line
    assert caps_mod.driver_text(caps) == "October 2026 or newer (rim_id)"


def test_driver_text():
    assert caps_mod.driver_text(caps_mod.Capabilities()) == "-"
    assert caps_mod.driver_text(caps_mod.Capabilities(
        wheelbase="x", old_driver=True)) == "before October 2026 (wheel_id)"


def test_runs_as_a_module():
    """python3 -m understeer.capabilities works without a wheelbase."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-m", "understeer.capabilities"],
                         capture_output=True, text=True, check=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)))
    assert "Function" in out.stdout
