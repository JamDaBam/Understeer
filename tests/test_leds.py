import os
import pathlib

import pytest

from understeer import capabilities as caps_mod
from understeer import device, leds
from understeer.leds import (BASE, BOTH, LEFT_FLAG, REV, RIGHT_FLAG, RIM,
                             State)
from test_capabilities import BASE as BASE_HID, ELITE, FakeSysfs


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


def led_files(fake, max_brightness=None, index="red green blue"):
    """Add what the kernel's LED class gives every LED (and RGB LED)."""
    for folder in (fake.root / "class/leds").iterdir():
        rgb = (folder / "multi_intensity").exists()
        top = max_brightness or (255 if rgb else 1)
        fake.file(folder / "max_brightness", mode=0o444, text=f"{top}\n")
        if rgb:
            fake.file(folder / "multi_index", mode=0o444, text=index + "\n")
            (folder / "multi_intensity").write_text("0 0 0\n")


def text(led, name="brightness"):
    return (fake_path(led) / name).read_text().strip()


def fake_path(led):
    return pathlib.Path(led.folder)


def take_wheel_off(fake):
    """The driver drops the wheel's LED folders; here they are moved away
    (nothing is deleted)."""
    gone = fake.root / "gone"
    gone.mkdir(exist_ok=True)
    for folder in (fake.root / "class/leds").glob(f"{BASE_HID}:[0-9a-f]*"):
        folder.rename(gone / folder.name)


def found(fake, **kw):
    led_files(fake, **kw)
    return leds.find(caps_mod.detect())


def formula(fake):
    """CSL DD with a ClubSport Formula V2: 9 RGB rev LEDs, 3+3 RGB flags."""
    fake.wheelbase()
    fake.rim(BASE_HID, 0x0A, leds=9, rgb=True, flags=True)
    return found(fake)


def test_rev_leds_of_base_and_rim_told_apart(sysfs):
    sysfs.wheelbase(base_leds=0)
    sysfs.rim(BASE_HID, 0x01, leds=9)
    found_leds = found(sysfs)
    assert [(l.location, l.kind, l.number) for l in found_leds] == \
        [(RIM, REV, n) for n in range(1, 10)]
    assert not any(l.rgb for l in found_leds)
    assert all(l.readable and l.writable for l in found_leds)


def test_flags_left_and_right_and_rgb_channels(sysfs):
    found_leds = formula(sysfs)
    kinds = [l.kind for l in found_leds]
    assert kinds == [REV] * 9 + [LEFT_FLAG] * 3 + [RIGHT_FLAG] * 3
    assert all(l.channels == ("red", "green", "blue") for l in found_leds)
    assert all(l.max_brightness == 255 for l in found_leds)
    assert leds.colors(found_leds) == ["red", "green", "blue", "yellow",
                                       "cyan", "magenta", "white"]


def test_colours_follow_the_channels(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE_HID, 0x0A, leds=1, rgb=True)
    found_leds = found(sysfs, index="red green")
    assert leds.colors(found_leds) == ["red", "green", "yellow"]
    assert leds.lit(found_leds[0], "yellow").intensity == (255, 255)


def test_no_colours_without_rgb(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE_HID, 0x01, leds=9)
    assert leds.colors(found(sysfs)) == []


def test_wheelbase_leds_on_and_off(sysfs):
    sysfs.wheelbase(name=ELITE, base_leds=9)
    found_leds = found(sysfs)
    assert {l.location for l in found_leds} == {BASE}
    led = found_leds[3]
    assert leds.write_state(led, leds.lit(led)) == leds.OK
    assert text(led) == "1"
    assert leds.is_on(leds.read_state(led))
    assert leds.write_state(led, leds.off(led)) == leds.OK
    assert text(led) == "0"


def test_old_driver_shared_leds_listed_once(sysfs):
    hid, _t = sysfs.wheelbase(name=ELITE, base_leds=9)
    sysfs.file(hid / "wheel_id", text="0x01\n")
    found_leds = found(sysfs)
    assert len(found_leds) == 9
    assert {l.location for l in found_leds} == {BOTH}


def test_rgb_colour_written_before_brightness(sysfs):
    led = formula(sysfs)[0]
    assert leds.write_state(led, leds.lit(led, "magenta")) == leds.OK
    assert text(led, "multi_intensity") == "255 0 255"
    assert text(led) == "255"
    state = leds.read_state(led)
    assert state == State(255, (255, 0, 255)) and leds.is_on(state)


def test_rgb_dark_at_full_brightness_is_off(sysfs):
    led = formula(sysfs)[0]
    (fake_path(led) / "brightness").write_text("255\n")
    assert not leds.is_on(leds.read_state(led))   # as the driver starts


def test_single_rgb_stripe_uses_its_scale(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE_HID, 0x05, leds=1, rgb=True)
    led = found(sysfs, max_brightness=1)[0]
    assert leds.lit(led, "cyan") == State(1, (0, 1, 1))
    leds.write_state(led, State(255, (255, 255, 255)))
    assert text(led) == "1"


def test_all_off(sysfs):
    found_leds = formula(sysfs)
    leds.write_all([(l, leds.lit(l)) for l in found_leds])
    assert leds.all_off(found_leds) == leds.OK
    assert not any(leds.is_on(leds.read_state(l)) for l in found_leds)


def test_snapshot_and_restore(sysfs):
    found_leds = formula(sysfs)
    leds.write_state(found_leds[2], leds.lit(found_leds[2], "blue"))
    before = leds.snapshot(found_leds)
    leds.write_all([(l, leds.lit(l, "red")) for l in found_leds])
    assert leds.restore(found_leds, before) == leds.OK
    assert leds.read_state(found_leds[2]) == State(255, (0, 0, 255))
    assert not leds.is_on(leds.read_state(found_leds[0]))


def test_sequence_lights_each_group_and_restores(sysfs):
    found_leds = formula(sysfs)
    leds.write_state(found_leds[0], leds.lit(found_leds[0], "green"))
    seq = leds.Sequence(found_leds)
    # all off, 9 rev + off, left on/off, right on/off, 7 colours, all off
    assert len(seq.steps) == 1 + 10 + 4 + 7 + 1
    assert [len(s) for s in seq.steps[1:10]] == list(range(1, 10))
    seen = []
    while seq.advance():
        seen.append(sum(leds.is_on(leds.read_state(l)) for l in found_leds))
    assert seq.result == leds.OK and not seq.running
    assert seq.position == len(seq.steps)
    assert seen[:3] == [0, 1, 2]
    assert seq.finish() == leds.OK
    assert leds.read_state(found_leds[0]) == State(255, (0, 255, 0))


def test_sequence_cancelled(sysfs):
    found_leds = formula(sysfs)
    seq = leds.Sequence(found_leds)
    seq.advance()
    seq.advance()
    seq.cancel()
    assert not seq.advance()
    assert seq.position == 2
    seq.finish()
    assert not any(leds.is_on(leds.read_state(l)) for l in found_leds)


def test_sequence_stops_when_unplugged(sysfs):
    found_leds = formula(sysfs)
    seq = leds.Sequence(found_leds)
    seq.advance()
    take_wheel_off(sysfs)              # wheel taken off
    assert not seq.advance()
    assert seq.result == leds.UNAVAILABLE
    assert seq.finish() == leds.OK          # nothing left to set back
    assert not any(os.path.exists(l.folder) for l in found_leds)


def test_no_folder_made_for_gone_led(sysfs):
    led = formula(sysfs)[0]
    take_wheel_off(sysfs)
    assert leds.write_state(led, leds.lit(led)) == leds.UNAVAILABLE
    assert not os.path.exists(led.folder)


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write anyway")
def test_read_only_leds(sysfs):
    sysfs.wheelbase()
    sysfs.rim(BASE_HID, 0x01, leds=9)
    led_files(sysfs)
    for folder in (sysfs.root / "class/leds").iterdir():
        os.chmod(folder / "brightness", 0o444)
    found_leds = leds.find(caps_mod.detect())
    assert found_leds and not any(l.writable for l in found_leds)
    assert all(l.readable for l in found_leds)
    assert leds.sequence_steps(found_leds) == []
    assert leds.all_off(found_leds) == leds.OK          # nothing to write
    assert leds.write_state(found_leds[0], leds.lit(found_leds[0])) == leds.DENIED
    assert text(found_leds[0]) == "0"


def test_unsupported_devices(sysfs):
    assert leds.find(None) == []
    assert leds.find(caps_mod.detect()) == []           # nothing connected
    sysfs.wheelbase()                                   # no wheel, no LEDs
    found_leds = found(sysfs)
    assert found_leds == [] and leds.colors(found_leds) == []
    assert leds.sequence_steps(found_leds) == []
    seq = leds.Sequence(found_leds)
    assert not seq.running and not seq.advance()
