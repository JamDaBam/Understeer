# Understeer

A modern, GTK4/Libadwaita graphical utility for adjusting Fanatec wheel bases and hardware settings on Linux. This application hooks into the settings exposed by the `hid-fanatecff` kernel driver via `sysfs`.

![Understeer screenshot](docs/screenshot.png)

## Features

- **Live tuning:** Changes reach the wheel base while you move a control, so you feel them immediately. Undo single settings or everything since loading.
- **Live input display:** See the current steering angle and how far each pedal is pressed, including the maximum reached. Useful to find the brake force you need for 100 %.
- **Wheel & force feedback:** Steering angle with quick select, force feedback strength, force/spring/damper effects, scaling, intensity and interpolation.
- **Wheel feel:** Natural damper, friction and inertia, wheel vibration and drift mode.
- **Pedals:** Load cell brake force, brake level LED and FullForce, plus a rumble test for ClubSport V3 / CSL Elite pedals.
- **Tuning slots & presets:** Switch between the 5 tuning slots of the wheel base and keep named presets for your games. Apply a preset with one click or save your changes back into it.
- **Translations:** Available in English and German, more languages can be added easily (see below).

Understeer is based on [SimPower (FanatecLinux)](https://github.com/BenryBoi/FanatecLinux) by BenryBoi.

## Prerequisites

This application requires the [hid-fanatecff](https://github.com/gotzl/hid-fanatecff) kernel driver to be installed and active on your system. Without it, your system will not expose the hardware tuning paths required by this interface.

## Installation

Download `Understeer-<version>-x86_64.AppImage` from the [latest release](https://github.com/JamDaBam/FanatecLinux/releases/latest), make it executable (`chmod +x Understeer-*-x86_64.AppImage`) and run it.

The AppImage contains embedded update information, so AppImage managers such as [Gear Lever](https://github.com/mijorus/gearlever) detect new releases automatically.

## Hardware Permissions

Understeer writes changes to the wheel base live while you move a control. This needs write access to the device. If it is missing, the app shows a banner with a **Set Up** button that installs the udev rule below for you (it asks for your password once). Without it, changes are collected and written with a single password prompt when you click **Apply**.

The live input display (current steering angle and pedal travel) reads the wheel's input device. The rule's last line makes it readable; on most desktops your user can already read it.

To set it up manually instead: by default, Linux restricts writing to device configurations in the `/sys` directory to the root user. To allow this application to save settings seamlessly without needing `sudo` or `pkexec` prompts every single time, you must add a local `udev` hardware rule.

Open a terminal and run the following combined command:

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


## Translations

Understeer uses your system language when a translation is available and falls back to English otherwise. Translations are plain gettext `.po` files in [`locale/`](locale/), so tools like Poedit or Weblate can edit them.

To add a language:

1. Copy `locale/understeer.pot` to `locale/<code>.po`, for example `locale/fr.po`.
2. Fill in every `msgstr`. Keep placeholders such as `{name}` or `{n}` unchanged.
3. Test it with `LANGUAGE=fr python3 understeer.py`.

After changing text in the code, run `python3 tools/update_translations.py`. It updates the template and adds new strings to every `.po` file while keeping existing translations.

*Disclaimer: This software is provided as-is. I am not responsible for any damage to hardware or personal injury resulting from misconfigured settings. Use at your own risk.*
