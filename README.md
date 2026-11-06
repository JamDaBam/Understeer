# SimPower

A modern, GTK4/Libadwaita graphical utility for adjusting Fanatec wheel bases and hardware settings on Linux. This application hooks into the settings exposed by the `hid-fanatecff` kernel driver via `sysfs`.

<img width="686" height="767" alt="simpower screenshot" src="https://github.com/user-attachments/assets/708d9267-5025-46a2-a4dd-a9cdba08fd47" />


## Features

- **Wheel & FFB Tuning:** Adjust steering sensitivity (SEN), master force feedback strength (FF), game force effects (FOR), spring/damper constraints, and FFB interpolation filtering.
- **Pedals & Brake Force:** Tune load-cell brake pedal stiffness (brF), brake lock indicator levels (BLI), and manage FullForce metrics.
- **ClubSport Rumble Support:** Fire manual rumble diagnostic signals directly to compatible ClubSport V3 / CSL Elite pedals.
- **Advanced Tuning & Profiles:** Cycle through the 5 independent on-device hardware tuning slots, adjust natural dampening/friction feel, and toggle analogue paddle bite-point modes.
- **Local Presets:** Snapshot your current configuration into custom, named files to easily reload setups for specific sim-racing titles (e.g., *Le Mans Ultimate*, *Assetto Corsa Competizione*, etc.).

## Prerequisites

This application requires the [hid-fanatecff](https://github.com/gotzl/hid-fanatecff) kernel driver to be installed and active on your system. Without it, your system will not expose the hardware tuning paths required by this interface.

## Installation

Download `SimPower-x86_64.AppImage` from the [latest release](https://github.com/JamDaBam/FanatecLinux/releases/latest), make it executable (`chmod +x SimPower-x86_64.AppImage`) and run it.

The AppImage contains embedded update information, so AppImage managers such as [Gear Lever](https://github.com/mijorus/gearlever) detect new releases automatically.

## Hardware Permissions

By default, Linux restricts writing to device configurations in the `/sys` directory to the root user. To allow this application to save settings seamlessly without needing `sudo` or `pkexec` prompts every single time, you must add a local `udev` hardware rule.

Open a terminal and run the following combined command:

```bash
sudo tee /etc/udev/rules.d/99-fanatec-tuner.rules > /dev/null << 'EOF'
ACTION!="add|change", GOTO="fanatec_tuner_end"

SUBSYSTEM=="ftec_tuning", RUN+="/bin/sh -c 'find /sys%p -type f -exec chmod 666 {} +'"
SUBSYSTEM=="hid", DRIVER=="fanatec", RUN+="/bin/sh -c 'chmod 666 /sys%p/rumble 2>/dev/null'"

LABEL="fanatec_tuner_end"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```


*Disclaimer: This software is provided as-is. I am not responsible for any damage to hardware or personal injury resulting from misconfigured settings. Use at your own risk.*
