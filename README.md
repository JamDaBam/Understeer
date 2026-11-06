# Understeer

A modern, GTK4/Libadwaita graphical utility for adjusting Fanatec wheel bases and hardware settings on Linux. This application hooks into the settings exposed by the `hid-fanatecff` kernel driver via `sysfs`.

![Understeer screenshot](docs/screenshot.png)

## Features

- **Live tuning:** Changes reach the wheel base while you move a control, so you feel them immediately. Undo single settings or everything since loading. Switching the slot or changing a value on the wheel base shows up in the app right away.
- **Live input display:** See the current steering angle and how far each pedal is pressed, including the maximum reached. Useful to find the brake force you need for 100 %.
- **Wheel & force feedback:** Steering angle with quick select, force feedback strength, force/spring/damper effects, scaling, intensity and interpolation.
- **Wheel feel:** Natural damper, friction and inertia, wheel vibration and drift mode.
- **Pedals:** Load cell brake force, brake level LED and FullForce, plus a rumble test for ClubSport V3 / CSL Elite pedals.
- **Tuning slots & presets:** Switch between the 5 tuning slots of the wheel base, name them like on its display, and keep named presets for your games. Apply a preset with one click or save your changes back into it.
- **Presets per game:** Give each game a preset on the *Games* page. While Understeer is open, it applies the preset as soon as the game starts. Steam games (native, Flatpak or Snap Steam, all libraries) are found automatically and shown with their cover. Other games (Lutris, Heroic, Wine, native) are recognised by their program: pick it from the running programs or choose its file. Covers come from Steam's local cache; downloading missing ones is optional and off by default. Any game can get its own picture.
- **Tiles in two sizes:** Every setting is a tile with its value in large type. Choose *Full* or *Compact* under *Tools → Appearance*; light and dark mode follow your system.
- **Translations:** Available in English and German, chosen automatically or under *Tools → Appearance*. More languages can be added easily (see below).

Understeer is based on [SimPower (FanatecLinux)](https://github.com/BenryBoi/FanatecLinux) by BenryBoi.

## Prerequisites

This application requires the [hid-fanatecff](https://github.com/gotzl/hid-fanatecff) kernel driver to be installed and active on your system. Without it, your system will not expose the hardware tuning paths required by this interface.

## Installation

Download `Understeer-<version>-x86_64.AppImage` from the [latest release](https://github.com/JamDaBam/Understeer/releases/latest), make it executable (`chmod +x Understeer-*-x86_64.AppImage`) and run it.

The AppImage contains embedded update information, so AppImage managers such as [Gear Lever](https://github.com/mijorus/gearlever) detect new releases automatically.

## Hardware Permissions

Understeer writes changes to the wheel base live while you move a control, which needs write access to the device. Without it, the app collects your changes and writes them with a single password prompt when you click **Apply**.

### Recommended: join the `games` group

The `hid-fanatecff` driver already installs a udev rule (`fanatec.rules`) that gives the `games` group write access to the tuning settings. Usually you only need to add your user to that group; no udev changes are needed:

```bash
getent group games                # check that the group exists
sudo usermod -aG games "$USER"    # add yourself to it
```

Log out and back in (or reboot), then check with `id -nG` that `games` is listed. Start Understeer again and the permission banner should be gone.

To undo it later: `sudo gpasswd -d "$USER" games`, then log out and back in.

### Alternative: a separate udev rule

If the driver's rule is not installed or you do not want to use the group, the **Set Up** button in the app's banner (or under *Tools*) installs the rule below for you. It asks for your password once. You can also add it manually:

```bash
sudo tee /etc/udev/rules.d/99-fanatec-tuner.rules > /dev/null << 'EOF'
ACTION!="add|change", GOTO="fanatec_tuner_end"

SUBSYSTEM=="ftec_tuning", RUN+="/bin/sh -c 'find /sys%p -type f -exec chmod 666 {} +'"
SUBSYSTEM=="hid", DRIVER=="fanatec", RUN+="/bin/sh -c 'chmod 666 /sys%p/rumble 2>/dev/null'"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{idVendor}=="0eb7", MODE="0644"

LABEL="fanatec_tuner_end"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```


The live input display (current steering angle and pedal travel) reads the wheel's input device. On most desktops your user can already read it; the rule's last line makes sure of that.

## Reporting Problems

Understeer writes a log file for every session to `~/.local/state/Understeer/understeer.log`; the one of the previous start is kept as `understeer.previous.log`. It records the wheel base, the driver version, the access rights of the tuning settings and every value written, including the reason when the driver rejects one. Open it under *Tools → System → Log File*, or with **Show Log** on an error message, and attach it to your issue.

## Translations

Understeer uses your system language when a translation is available and falls back to English otherwise. You can also pick a language under *Tools → Appearance*. Translations are plain gettext `.po` files in [`locale/`](locale/), so tools like Poedit or Weblate can edit them. `locale/en.po` keeps the English text unchanged; it makes sure English is used when it comes first in your language list (for example `LANGUAGE=en_US:de`).

To add a language:

1. Copy `locale/understeer.pot` to `locale/<code>.po`, for example `locale/fr.po`.
2. Fill in every `msgstr`. Keep placeholders such as `{name}` or `{n}` unchanged.
3. Add the language's own name to `LANGUAGE_NAMES` in [`understeer/i18n.py`](understeer/i18n.py), for example `"fr": "Français"`, so it shows up in the language menu.
4. Test it with `LANGUAGE=fr python3 -m understeer`.

After changing text in the code, run `python3 tools/update_translations.py`. It updates the template and adds new strings to every `.po` file while keeping existing translations.

## Development

Run the app from a checkout with `python3 -m understeer` (needs PyGObject with GTK 4 and Libadwaita). The code lives in the `understeer/` package:

| Module | Contents |
|---|---|
| `settings.py` | The settings shown in the app (sysfs attribute, range, page, description) |
| `device.py` | Finding the wheel base, reading and writing sysfs, permissions and udev rule |
| `inputs.py` | Live steering angle and pedal travel from `/dev/input/event*` |
| `config.py` | Presets and app settings in `~/.config/Understeer/` |
| `games.py` | Installed Steam games, their covers and which games are running |
| `log.py` | Session log file |
| `i18n.py` | Loading translations from `locale/` |
| `ui/` | GTK window and widgets |
| `app.py` | Application start |

Everything outside `ui/`, `inputs.py` and `app.py` works without GTK.

Run the tests with `python3 -m pytest` (needs pytest, for example `sudo apt install python3-pytest`). They use a fake sysfs and temporary folders, so no wheel base or password is needed.

*Disclaimer: This software is provided as-is. I am not responsible for any damage to hardware or personal injury resulting from misconfigured settings. Use at your own risk.*
