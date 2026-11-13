# Understeer

A modern, GTK4/Libadwaita graphical utility for adjusting Fanatec wheelbases and hardware settings on Linux. This application hooks into the settings exposed by the `hid-fanatecff` kernel driver via `sysfs`.

![Understeer screenshot](docs/screenshot.png)

## Features

- **Live tuning:** Changes reach the wheelbase while you move a control, so you feel them immediately. Undo single settings or everything since loading. Switching the slot or changing a value on the wheelbase shows up in the app right away.
- **Everything on one view:** *Tuning* shows the live steering angle and pedals on top and all settings below in three tabs: steering & force feedback, wheel feel, pedals & paddles. Rarely changed signal settings are folded away. *Presets* and *Games* are one click away in the header bar; everything else is in the *Preferences* (main menu or Ctrl+,).
- **Live input display:** See the current steering angle and how far each pedal is pressed, including the maximum reached. Useful to find the brake force you need for 100 %.
- **Wheel & force feedback:** Steering angle with quick select, force feedback strength, force/spring/damper effects, scaling, intensity and interpolation.
- **Wheel feel:** Natural damper, friction and inertia, wheel vibration and drift mode.
- **Pedals:** Load cell brake force, brake level LED and FullForce, plus a rumble test for ClubSport V3 / CSL Elite pedals.
- **Steering wheel display test:** Not every steering wheel has an LED display, and the driver can't tell. *Preferences → Wheelbase* shows a short text on it (`888` lights every segment) and clears it, so you can see whether yours has one and Understeer can write to it. Nothing is written until you click *Show* or *Clear*, and the text stays when Understeer closes; a game that uses the display replaces it. Shown are digits, letters except K, M, V, W and X, `- _ [ ]`, and a point or comma after a character lights its point. Needs write access to the `display` file of hid-fanatecff (its udev rule allows it for its group, usually `games`).
- **Standard or Advanced tuning menu:** *Preferences → Wheelbase* shows which tuning menu the wheelbase has on its display and switches it: Advanced has all settings and the tuning slots, Standard a short menu without them. Understeer changes the settings in both, so this is only needed to use the tuning slots. In Standard mode the slot menu offers the two setups of that menu, *Auto* and *Custom* (e.g. A SET and C SET), which can be named under *Slot Names in the Standard Menu*; *Slot Names* names Auto and slots 1–5 of the Advanced menu. Understeer remembers the slot last used in each mode and goes back to it after switching, also when the menu is switched on the wheelbase; in Advanced mode the slot menu shows *Auto* while the wheelbase is on that setup. Switching the menu on the wheelbase is followed right away. Shown when the driver offers it (hid-fanatecff 0.1.1 or newer).
- **Preset name on the wheel display:** Optional, off by default (*Preferences → Wheelbase → Show Preset Name on Wheel Display*). After a preset has been written, applied by hand, from the tray menu or when a game starts, the steering wheel display shows the first three characters of its name that it can show: accents are dropped, spaces and characters it can't show (K, M, V, W, X, ...) are left out, e.g. *Rally 1* → `Ral`, *F1 2024* → `F12`. After switching slots it shows the name of the preset active in the new slot, or is cleared if there is none (switching in its own menu, the wheelbase uses the display itself and may cover the name); a name with nothing it can show clears it too. Nothing is written for a preset that waits for *Apply* or wasn't written, or when the same text is what Understeer wrote last. A game that uses the display replaces the name.
- **Tuning slots & presets:** Switch between the 5 tuning slots of the wheelbase, name them like on its display, and keep named presets for your games. Apply a preset with one click (its values are then what Undo goes back to), save your changes back into it, rename it with the pencil next to its name, and look at its values first in its ⋮ menu (what differs from your current settings is marked); before a preset is overwritten, Understeer lists every setting that changes in it (e.g. 70 % → 85 %). Quitting or applying a preset with unsaved changes to the active preset asks whether to save them first. Balanced sim racing defaults are built in. A preset belongs to the slot it was applied in: after switching slots, here or on the wheelbase, the preset last applied in the new slot is active again (or none), so another slot's values are never offered for saving into it. *Remove “…” from Slot* in the *Presets* menu takes the active preset out of the slot again: its values stay on the wheelbase and the preset stays as it is.
- **Deleting and the trash:** *Select* on the *Presets* and *Games* page lets you pick several tiles and delete them at once; one is deleted from its ⋮ menu. Nothing is gone right away: the message offers *Undo*, and deleted presets and games stay in the trash for 30 days. *Trash* next to *Select* opens it, on each page for its own kind; there you can restore them or delete them for good. A game comes back with its preset and picture; a game that uses a deleted preset gets it back once the preset is restored.
- **Folders for presets:** Several presets for one game, e.g. for drifting, crawling and rally in BeamNG.drive? Make a folder with *New Folder…* on the *Presets* page and drag presets onto it, or drop a preset on *New Folder…* to make one with it. The *Presets* menu and the tray menu show each folder as a submenu.
- **Share presets:** Found the perfect setup? Share it with others. *Share* in the ⋮ menu of a preset saves it as a file or copies it as readable text, ready for Discord or a forum post, or as plain JSON. Got presets from someone else? *Paste Text…* on the *Presets* page takes anything you copied, even a whole forum thread: Understeer finds every preset in it, shows their values and lets you pick which ones to add. If you already have one with the same name, you see what differs and choose to replace it or keep both. Only known settings within their ranges are taken, so pasted text can't do anything else. *Import File…* does the same with files.
- **Moving to another computer:** *Preferences → Data* exports presets, games with their pictures and all settings into one ZIP file and imports them on the other computer.
- **Plug and play:** Switch the wheelbase on or plug it in while Understeer is open, it is found by itself.
- **Presets per game:** Give each game a preset on the *Games* page. While Understeer runs, it applies the preset as soon as the game starts, or once the wheelbase is switched on if that comes later. Steam games (native, Flatpak or Snap Steam, all libraries) are found automatically and shown with their cover. Other games (Lutris, Heroic, Wine, native) are recognised by their program: pick it from the running programs or choose its file. Covers come from Steam's local cache; downloading missing ones is optional and off by default. Any game can get its own picture, also chosen from [SteamGridDB](https://www.steamgriddb.com) with your free personal API key.
- **In the background:** Under *Preferences → Games*, Understeer can keep running when you close its window, with an icon in the system tray (KDE Plasma, Cinnamon, XFCE and others; GNOME with the AppIndicator extension), and start at login. Then the presets of your games are applied without opening it first, and a notification tells you whether that worked (can be switched off). Click the icon to open it. Its menu applies one of your presets without opening the window and quits Understeer, as does *Quit* in the main menu (Ctrl+Q).
- **Your hardware:** *Preferences → Wheelbase* shows your wheelbase, the steering wheel on it and their firmware versions (the wheelbase firmware needs hid-fanatecff 0.2.3, the steering wheel and its firmware its current `master`, see [Prerequisites](#prerequisites)). Changing the steering wheel shows up right away. *Device Details* there show the device name, USB vendor and product ID and the kernel driver (version and build); where something is missing it says whether the installed driver doesn't offer it, it isn't available right now (e.g. no steering wheel on the wheelbase) or it is unknown (e.g. a wheel Understeer has no name for). Firmware versions are only shown as the driver reports them. The copy button next to *Hardware* copies all of it as a table for a GitHub issue, without serial numbers, USB ports or anything about you.
- **Your look:** Every setting is a tile with its value in large type, in two sizes (*Full* or *Compact*). Under *Preferences → General* you choose light, dark or system colours, one of four themes (Standard, Carbon, Midnight, Pit Lane), an accent colour and the corner radius (3 px by default).
- **Translations:** Available in English and German, chosen automatically or under *Preferences → General*. More languages can be added easily (see below).

![Presets page with presets without a folder and in a folder](docs/presets.png)

![Choosing presets to delete them at once](docs/select.png)

![Games page with a preset for each game and BeamNG.drive running](docs/games.png)

## Prerequisites

This application requires the [hid-fanatecff](https://github.com/gotzl/hid-fanatecff) kernel driver to be installed and active on your system. Without it, your system will not expose the hardware tuning paths required by this interface.

Understeer needs hid-fanatecff 0.1 or newer; Standard/Advanced mode of the tuning menu needs 0.1.1 and the firmware version of the wheelbase 0.2.3. The steering wheel, its firmware and its LEDs need the current `master` of hid-fanatecff (October 2026 or newer); there is no numbered release with them yet. Understeer works with older versions too and leaves out what they don't offer.

Understeer is developed and tested with a CSL DD (firmware 1537) and a Podium Steering Wheel GT (firmware 8). Other Fanatec hardware should work as far as hid-fanatecff supports it, but hasn't been tried by me.

The AppImage uses Python 3 with PyGObject, GTK 4 and Libadwaita 1.5 or newer from your system, as found for example in Ubuntu 24.04, Fedora 40 and Debian 13.

## Installation

Download `Understeer-<version>-x86_64.AppImage` from the [latest release](https://github.com/JamDaBam/Understeer/releases/latest), make it executable (`chmod +x Understeer-*-x86_64.AppImage`) and run it.

The AppImage contains embedded update information, so AppImage managers such as [Gear Lever](https://github.com/mijorus/gearlever) detect new releases automatically. Understeer itself can also look for new versions (it asks once; *Preferences → General → Look for New Versions*): at start and every hour it asks GitHub for the newest version number and shows a note at the top when there is a newer one. *Update Now* there downloads the new AppImage, checks it against the SHA-256 checksum GitHub gives for it, puts it in place of the old file and restarts.

## Hardware Permissions

Understeer writes changes to the wheelbase live while you move a control, which needs write access to the device. Without it, the app collects your changes and writes them with a single password prompt when you click **Apply**.

### Recommended: join the `games` group

The `hid-fanatecff` driver already installs a udev rule (`fanatec.rules`) that gives the `games` group write access to the tuning settings. Usually you only need to add your user to that group; no udev changes are needed:

```bash
getent group games                # check that the group exists
sudo usermod -aG games "$USER"    # add yourself to it
```

Log out and back in (or reboot), then check with `id -nG` that `games` is listed. Start Understeer again and the permission banner should be gone.

To undo it later: `sudo gpasswd -d "$USER" games`, then log out and back in.

### Alternative: a separate udev rule

If the driver's rule is not installed or you do not want to use the group, the **Set Up** button in the app's banner (or under *Preferences → Wheelbase*) installs the rule below for you. It asks for your password once. You can also add it manually:

```bash
sudo tee /etc/udev/rules.d/99-fanatec-tuner.rules > /dev/null << 'EOF'
ACTION!="add|change", GOTO="fanatec_tuner_end"

SUBSYSTEM=="ftec_tuning", RUN+="/bin/sh -c 'find /sys%p -type f -exec chmod 666 {} +'"
SUBSYSTEM=="hid", DRIVER=="fanatec", RUN+="/bin/sh -c 'chmod 666 /sys%p/rumble 2>/dev/null'"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{idVendor}=="0eb7", MODE="0644"

LABEL="fanatec_tuner_end"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=ftec_tuning --subsystem-match=hid --subsystem-match=input
```


The live input display (current steering angle and pedal travel) reads the wheel's input device. On most desktops your user can already read it; the rule's last line makes sure of that.

## Reporting Problems

The easiest way: choose **Report a Problem…** in the main menu (also under *Preferences → Data*, *About Understeer → Report an Issue* and on every error message). Write what happened, then click **Open on GitHub**. Understeer opens a [new issue](https://github.com/JamDaBam/Understeer/issues/new?template=bug_report.yml) with the versions, your wheelbase and steering wheel (USB IDs, firmware, driver build), its access rights and the end of the log already filled in. Your user name and home folder are left out, and nothing is sent until you submit the issue on GitHub (you need a GitHub account). The report is also put on the clipboard. Ideas are welcome too: on GitHub, choose *Suggest an idea* under [New issue](https://github.com/JamDaBam/Understeer/issues/new/choose).

Understeer writes a log file for every session to `~/.local/state/Understeer/understeer.log`; the one of the previous start is kept as `understeer.previous.log`. It records the wheelbase, the driver version, the access rights of the tuning settings and every value written, including the reason when the driver rejects one. If you are asked for the whole file, open its folder under *Preferences → Data → Log File* and drag it into the issue.

## Translations

Understeer uses your system language when a translation is available and falls back to English otherwise. You can also pick a language under *Preferences → General*. Translations are plain gettext `.po` files in [`locale/`](locale/), so tools like Poedit or Weblate can edit them. `locale/en.po` keeps the English text unchanged; it makes sure English is used when it comes first in your language list (for example `LANGUAGE=en_US:de`).

To add a language:

1. Copy `locale/understeer.pot` to `locale/<code>.po`, for example `locale/fr.po`.
2. Fill in every `msgstr`. Keep placeholders such as `{name}` or `{n}` unchanged.
3. Add the language's own name to `LANGUAGE_NAMES` in [`understeer/i18n.py`](understeer/i18n.py), for example `"fr": "Français"`, so it shows up in the language menu.
4. Test it with `LANGUAGE=fr python3 -m understeer`.

After changing text in the code, run `python3 tools/update_translations.py`. It updates the template and adds new strings to every `.po` file while keeping existing translations.

## Development

Run the app from a checkout with `python3 -m understeer` (needs PyGObject with GTK 4 and Libadwaita 1.5 or newer). The code lives in the `understeer/` package:

| Module | Contents |
|---|---|
| `settings.py` | The settings shown in the app (sysfs attribute, range, tab, description) |
| `device.py` | Finding the wheelbase, reading and writing sysfs, the mode of the tuning menu, permissions and udev rule |
| `display.py` | Which text the steering wheel display can show, and writing or clearing it |
| `capabilities.py` | Which hardware functions the wheelbase, wheel and pedals offer (`python3 -m understeer.capabilities` shows them) |
| `inputs.py` | Live steering angle and pedal travel from `/dev/input/event*` |
| `config.py` | Presets and app settings in `~/.config/Understeer/` |
| `sharing.py` | Sharing presets as a file or text, and finding presets in pasted text |
| `trash.py` | The trash for deleted presets and games |
| `names.py` | Long names cut short for menus and messages |
| `games.py` | Installed Steam games, their covers and which games are running |
| `steamgriddb.py` | Searching covers on SteamGridDB |
| `log.py` | Session log file |
| `themes.py` | Themes, accent colours and corner radius as a stylesheet |
| `report.py` | Problem reports: a filled-in GitHub issue |
| `backup.py` | Export and import of presets, games and settings |
| `background.py` | Starting at login, in the background |
| `releases.py` | The release notes in the metainfo file, for *What's New* and GitHub |
| `updates.py` | Looking for a new version and updating the AppImage |
| `i18n.py` | Loading translations from `locale/` |
| `ui/window.py` | The main window: device sync, writing, slots, games watch |
| `ui/live_input.py`, `ui/presets.py`, `ui/preferences.py` | Parts of the main window: live input, presets, preferences with export and import |
| `ui/` (others) | Games page, cover search, setting tiles and other widgets |
| `app.py` | Application start |

Everything outside `ui/`, `inputs.py` and `app.py` works without GTK.

Run the tests with `python3 -m pytest` (needs pytest, for example `sudo apt install python3-pytest`). They use a fake sysfs and temporary folders, so no wheelbase or password is needed. One test runs the whole app on an invisible screen; it needs Xvfb (`sudo apt install xvfb`) and is skipped without it.
GitHub runs them on every push and pull request, and a release is only built when they pass.

To release a version, first add it with its changes (in English and German, see the comment there) to `<releases>` in `io.github.JamDaBam.Understeer.metainfo.xml`, then push a tag like `v1.9.0`. The app opens these notes as *What's New* after an update (also in the main menu), and the release build takes the GitHub release notes from there; without them it stops.

## Credits & License

- Understeer started as a fork of [SimPower (FanatecLinux)](https://github.com/BenryBoi/FanatecLinux) by BenryBoi and has since been rewritten as its own project. Thanks for the starting point!
- The tuning settings come from the [hid-fanatecff](https://github.com/gotzl/hid-fanatecff) kernel driver by gotzl and its contributors.
- Game covers come from Steam and, if you enter your own API key, from [SteamGridDB](https://www.steamgriddb.com).

Understeer is free software under the [GNU General Public License v3.0](LICENSE).

*Disclaimer: This software is provided as-is. I am not responsible for any damage to hardware or personal injury resulting from misconfigured settings. Use at your own risk.*
