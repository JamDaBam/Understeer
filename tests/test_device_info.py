import os

import pytest

from understeer import device_info as di
from understeer.capabilities import Capabilities, Feature

BASE = "0003:0EB7:0020.001A"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def wheelbase(tmp_path):
    """A tuning folder whose device/ is the HID device, as in sysfs."""
    def make(name=BASE, product="00000020", hid_name="Fanatec FANATEC Wheel",
             files=None):
        tuning = tmp_path / "ftec_tuning" / name
        hid = tuning / "device"
        hid.mkdir(parents=True)
        uevent = ["DRIVER=fanatec", f"HID_ID=0003:00000EB7:{product}",
                  "HID_PHYS=usb-0000:06:00.3-3/input0", "HID_UNIQ=SERIAL123"]
        if hid_name:
            uevent.append(f"HID_NAME={hid_name}")
        write(hid / "uevent", "\n".join(uevent) + "\n")
        for file, text in (files or {}).items():
            write(hid / file, text)
        return str(tuning)
    return make


@pytest.fixture
def module(tmp_path):
    def make(version="0.2.3\n", srcversion="B4DFCE6022652E2ABDAE624\n"):
        folder = tmp_path / "module" / "hid_fanatec"
        folder.mkdir(parents=True)
        if version is not None:
            write(folder / "version", version)
        if srcversion is not None:
            write(folder / "srcversion", srcversion)
        return str(folder)
    return make


def texts(info):
    return {key: di.item_text(item) for key, item in info.items()}


def test_complete_information(wheelbase, module):
    path = wheelbase(files={"fw_version": "1537\n", "rim_id": "16\n",
                            "fw_version_rim": "8\n"})
    info = di.collect(path, module())
    assert list(info) == [key for key, _name in di.ITEMS]
    assert all(item.known for item in info.values())
    assert texts(info) == {
        "device_name": "Fanatec FANATEC Wheel",
        "vendor_id": "0x0EB7", "product_id": "0x0020",
        "wheelbase": "CSL DD", "wheelbase_firmware": "1537",
        "rim": "Podium Steering Wheel GT", "rim_firmware": "8",
        "driver": "hid_fanatec", "driver_version": "0.2.3",
        "driver_build": "B4DFCE6022652E2ABDAE624"}


def test_old_driver_without_firmware_and_wheel(wheelbase, module):
    """hid-fanatecff 0.1: no fw_version, no rim_id or wheel_id."""
    info = di.collect(wheelbase(), module(version="0.1\n"))
    for key in ("wheelbase_firmware", "rim", "rim_firmware"):
        assert info[key] == di.Item(di.UNSUPPORTED)
    assert di.item_text(info["rim"]) == "not supported by the installed driver"
    assert info["wheelbase"].value == "CSL DD"


def test_driver_before_october_2026_reports_wheel_id(wheelbase, module):
    info = di.collect(wheelbase(files={"fw_version": "1537",
                                       "wheel_id": "0x0a\n"}), module())
    assert info["rim"].value == "ClubSport Steering Wheel Formula V2"
    assert info["rim_firmware"] == di.Item(di.UNSUPPORTED)   # no fw_version_rim


def test_no_wheel_reported(wheelbase, module):
    info = di.collect(wheelbase(files={"rim_id": "0", "fw_version_rim": "0"}),
                      module())
    assert info["rim"] == di.Item(di.UNAVAILABLE, note=di.NO_WHEEL)
    assert info["rim_firmware"] == di.Item(di.UNAVAILABLE, note=di.NO_WHEEL)
    assert di.item_text(info["rim"]) == "not available (no steering wheel reported)"


def test_unknown_wheel_and_wheelbase(wheelbase, module):
    """Hardware Understeer has no name for: shown with its id, not guessed."""
    path = wheelbase(name="0003:0EB7:0999.0002", product="00000999",
                     files={"rim_id": "99", "fw_version_rim": "3"})
    info = di.collect(path, module())
    assert info["wheelbase"] == di.Item(di.UNKNOWN, "0x0EB7:0x0999", di.NOT_LISTED)
    assert info["product_id"].value == "0x0999"
    assert info["rim"] == di.Item(di.UNKNOWN, "ID 0x63", di.NOT_LISTED)
    assert info["rim_firmware"].value == "3"
    assert di.item_text(info["rim"]) == "unknown (ID 0x63, not in Understeer's list)"


def test_firmware_never_invented(wheelbase, module):
    info = di.collect(wheelbase(files={"fw_version": "garbage"}), module())
    assert info["wheelbase_firmware"] == di.Item(di.UNKNOWN, note=di.INVALID)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_unreadable_firmware(wheelbase, module, tmp_path):
    path = wheelbase(files={"fw_version": "1537"})
    os.chmod(os.path.join(path, "device", "fw_version"), 0)
    info = di.collect(path, module())
    assert info["wheelbase_firmware"] == di.Item(di.UNAVAILABLE,
                                                 note=di.NOT_READABLE)


def test_missing_uevent_name_and_ids_from_folder(wheelbase, module, tmp_path):
    path = wheelbase(hid_name=None)
    os.remove(os.path.join(path, "device", "uevent"))
    info = di.collect(path, module())
    assert info["device_name"] == di.Item(di.UNKNOWN, note=di.NOT_READABLE)
    assert info["product_id"].value == "0x0020"    # from the folder name
    assert info["wheelbase"].value == "CSL DD"


def test_driver_without_version(wheelbase, module):
    """Built from git: no version file."""
    info = di.collect(wheelbase(), module(version=None))
    assert info["driver_version"] == di.Item(di.UNKNOWN, note=di.NOT_REPORTED)
    assert info["driver_build"].known


def test_nothing_connected_and_driver_not_loaded(tmp_path):
    info = di.collect(None, str(tmp_path / "missing"))
    assert all(item.state == di.UNAVAILABLE for item in info.values())
    assert info["vendor_id"].note == di.NOT_CONNECTED
    assert info["driver"].note == di.NOT_LOADED


def test_disconnect_and_reconnect(wheelbase, module, tmp_path):
    path = wheelbase(files={"fw_version": "1537"})
    mod = module()
    assert di.collect(path, mod)["wheelbase"].known
    os.rename(path, str(tmp_path / "gone"))         # unplugged
    info = di.collect(path, mod)
    assert info["wheelbase"] == di.Item(di.UNAVAILABLE, note=di.NOT_CONNECTED)
    assert info["driver"].known                      # the module stays loaded
    os.rename(str(tmp_path / "gone"), path)          # plugged in again
    assert di.collect(path, mod)["wheelbase_firmware"].value == "1537"


def test_report_text_for_github(wheelbase, module):
    path = wheelbase(files={"fw_version": "1537", "rim_id": "0"})
    caps = Capabilities(tuning=path, wheelbase=path + "/device", features={
        "display": Feature(("x",), False, True)})
    text = di.report_text(di.collect(path, module()), caps,
                          version="2.2.0", kernel="7.3.1")
    lines = text.splitlines()
    assert lines[:4] == ["### Device information", "", "| | |", "|---|---|"]
    assert "| Wheelbase model | CSL DD |" in lines
    assert "| Wheelbase firmware | 1537 |" in lines
    assert ("| Steering wheel model | not available "
            "(no steering wheel reported) |") in lines
    assert "- Understeer: 2.2.0" in lines
    assert "- Linux kernel: 7.3.1" in lines
    assert any(l.startswith("- Hardware functions: ") and "display w" in l
               for l in lines)
    # Nothing that identifies the person or the computer
    assert "SERIAL123" not in text and "usb-0000" not in text


def test_report_text_disconnected_and_escaped(tmp_path):
    info = di.collect(None, str(tmp_path / "missing"))
    text = di.report_text(info, Capabilities(), version="v", kernel="k")
    assert "| USB vendor ID | not available (no wheelbase connected) |" in text
    assert "Hardware functions" not in text
    item = di.Item(di.VALUE, "a|b")
    info["device_name"] = item
    assert "| Device name | a\\|b |" in di.report_text(info, version="v",
                                                         kernel="k")


def test_report_text_leaves_out_home(tmp_path, monkeypatch):
    info = di.collect(None, str(tmp_path / "missing"))
    info["device_name"] = di.Item(di.VALUE, "/home/jo/x")
    monkeypatch.setattr(di, "sanitize",
                        lambda t: t.replace("/home/jo", "~"))
    assert "| Device name | ~/x |" in di.report_text(info, version="v",
                                                      kernel="k")


def test_report_lines_for_problem_reports(wheelbase, module):
    path = wheelbase(files={"fw_version": "1537", "rim_id": "0"})
    lines = dict(di.report_lines(di.collect(path, module())))
    assert lines["Wheelbase model"] == "CSL DD"
    assert lines["Steering wheel model"] == "not available (no steering wheel reported)"
    # Already in the system details
    assert not {"Kernel driver", "Driver version",
                "Driver build (srcversion)"} & set(lines)
