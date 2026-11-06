#!/usr/bin/env python3
"""
SimPower — a GNOME-aligned GUI for adjusting Fanatec force-feedback
settings exposed by the hid-fanatecff kernel driver via sysfs.
"""

import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

# ---------------------------------------------------------------------------
# sysfs paths
# ---------------------------------------------------------------------------
SYSFS_GLOB   = "/sys/class/ftec_tuning/*"
RUMBLE_GLOB  = "/sys/bus/hid/devices/*/rumble"

# ---------------------------------------------------------------------------
# Preset storage
# ---------------------------------------------------------------------------
CONFIG_DIR = pathlib.Path.home() / ".config" / "SimPower"

# ---------------------------------------------------------------------------
# Tab 1 — Wheel & FFB
# Core FFB output and steering feel attributes
# ---------------------------------------------------------------------------
WHEEL_ATTRS = [
    ("SEN", "Sensitivity",               90, 1080,
     "Wheel rotation range in degrees. 0 = auto/max base range.", "slider"),
    ("FF",  "Force Feedback Strength",    0,  100,
     "Master FFB output strength from the wheel base.", "slider"),
    ("FOR", "Force Effect Strength",      0,  120,
     "Strength of constant-force effects (core game FFB).", "slider"),
    ("SPR", "Spring Effect Strength",     0,  120,
     "Centering spring effect strength.", "slider"),
    ("DPR", "Damper Effect Strength",     0,  120,
     "Damping effect strength.", "slider"),
    ("SHO", "Wheel Vibration (Shock)",    0,  100,
     "Curb/shock rumble motor strength.", "slider"),
    ("FFS", "Force Feedback Scaling",     0,    1,
     "0 = linear FFB taper (Lin). 1 = peak scaling (default).", "toggle"),
    ("FEI", "Force Effect Intensity",     0,  100,
     "Smooths/filters the FFB signal. Higher = sharper transients.", "slider"),
    ("INT", "FFB Interpolation Filter",   0,   20,
     "Interpolation smoothing between FFB updates. 0 = off.", "slider"),
]

# ---------------------------------------------------------------------------
# Tab 2 — Pedals & Brake
# Load-cell brake tuning + CSP rumble motor
# ---------------------------------------------------------------------------
PEDAL_ATTRS = [
    ("brF", "Brake Force (Load-Cell Stiffness)", 0, 100,
     "Load-cell brake pedal stiffness. Higher = firmer pedal feel. "
     "Only active with CSL/ClubSport load-cell brake.", "slider"),
    ("BLI", "Brake Level Indicator",             0, 101,
     "Brake force % at which the LED indicator triggers. "
     "101 = always on; 0 = always off.", "slider"),
    ("FUL", "FullForce (Pedal Vibration)",        0, 100,
     "Routes pedal-load vibration through the wheel motor. "
     "Requires a FullForce-compatible game.", "slider"),
]

# ---------------------------------------------------------------------------
# Tab 3 — Advanced
# Wheel-feel naturals, drift assist, analogue paddles, slot management
# ---------------------------------------------------------------------------
ADVANCED_ATTRS = [
    ("NDP", "Natural Damper",   0, 100,
     "Mechanical-feel damper baked into the wheel base.", "slider"),
    ("NFR", "Natural Friction", 0, 100,
     "Mechanical-feel friction baked into the wheel base.", "slider"),
    ("NIN", "Natural Inertia",  0, 100,
     "Mechanical-feel inertia baked into the wheel base.", "slider"),
    ("DRI", "Drift Mode",      -5,   3,
     "Drift counter-force assist. Negative = counter-steer aid, "
     "0 = off, positive = extra centering force.", "slider"),
    ("ACP", "Analogue Paddle Mode", 1, 4,
     "Clutch paddle bite-point mode:\n"
     "  1 = CbP — both paddles independent (default)\n"
     "  2 = CH  — clutch / handbrake\n"
     "  3 = Bt  — brake / throttle\n"
     "  4 = AnA — mappable analogue axes", "slider"),
]

ACP_LABELS = {1: "CbP — Independent", 2: "CH — Clutch/Handbrake",
              3: "Bt — Brake/Throttle", 4: "AnA — Analogue Axes"}


# ---------------------------------------------------------------------------
def find_device_path():
    candidates = [p for p in glob.glob(SYSFS_GLOB) if os.path.isdir(p)]
    valid = [p for p in candidates if os.path.exists(os.path.join(p, "FF"))]
    return valid[0] if valid else (candidates[0] if candidates else None)


def find_rumble_path():
    matches = glob.glob(RUMBLE_GLOB)
    return matches[0] if matches else None


def can_write(path):
    """Return True if the sysfs attrs for this device are user-writable.

    Checking the directory itself is misleading — sysfs dirs are typically
    not writable even when the attribute files inside them are.  Test a
    known-present attribute file (FF) instead, falling back to the directory
    if the file doesn't exist yet.
    """
    attr = os.path.join(path, "FF")
    target = attr if os.path.exists(attr) else path
    return os.access(target, os.W_OK)


# ---------------------------------------------------------------------------
def build_attr_rows(pref_group, attrs, widgets, write_cb, extra_rows_cb=None):
    for attr_id, label, lo, hi, desc, kind in attrs:
        row = Adw.ActionRow()
        row.set_title(f"{label}  ({attr_id})")
        row.set_subtitle(desc)
        pref_group.add(row)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)

        if kind == "toggle":
            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            box.append(sw)
            widgets[attr_id] = sw
        else:
            adj = Gtk.Adjustment(value=max(lo, 0), lower=lo, upper=hi,
                                 step_increment=1, page_increment=10)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=adj)
            scale.set_size_request(180, -1)
            scale.set_valign(Gtk.Align.CENTER)
            scale.set_draw_value(False)
            box.append(scale)

            spin = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=0)
            spin.set_valign(Gtk.Align.CENTER)
            box.append(spin)
            widgets[attr_id] = adj

        btn = Gtk.Button(label="Set")
        btn.add_css_class("suggested-action")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda b, aid=attr_id: write_cb(aid))
        box.append(btn)

        row.add_suffix(box)

        if extra_rows_cb:
            extra_rows_cb(pref_group, attr_id)


# ===========================================================================
class TunerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("SimPower")
        self.set_default_size(700, 780)

        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()

        self.wheel_w    = {}
        self.pedal_w    = {}
        self.advanced_w = {}

        self._build_ui()
        self._load_current_values()

    # -----------------------------------------------------------------------
    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        apply_everything_btn = Gtk.Button(label="Apply All")
        apply_everything_btn.add_css_class("suggested-action")
        apply_everything_btn.set_tooltip_text(
            "Write the settings from all tabs (Wheel, Pedals, Advanced) to the device")
        apply_everything_btn.connect("clicked", lambda b: self._apply_everything())
        header.pack_start(apply_everything_btn)

        tab_bar  = Adw.TabBar()
        tab_view = Adw.TabView()
        tab_bar.set_view(tab_view)
        toolbar_view.add_top_bar(tab_bar)
        toolbar_view.set_content(tab_view)

        p = tab_view.append(self._build_wheel_page())
        p.set_title("Wheel & FFB")

        p = tab_view.append(self._build_pedal_page())
        p.set_title("Pedals & Brake")

        p = tab_view.append(self._build_advanced_page())
        p.set_title("Advanced")

        p = tab_view.append(self._build_presets_page())
        p.set_title("Presets")

    # -----------------------------------------------------------------------
    # Shared page scaffold
    # -----------------------------------------------------------------------
    def _make_page_scroll(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        scrolled.set_child(clamp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        clamp.set_child(box)
        return scrolled, box

    def _make_action_bar(self, reload_cb, apply_cb, extra_btn=None):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.set_margin_top(8)

        rb = Gtk.Button(label="Reload from Device")
        rb.connect("clicked", lambda b: reload_cb())
        bar.append(rb)

        ab = Gtk.Button(label="Apply Page")
        ab.set_tooltip_text("Write only the settings on this tab to the device")
        ab.add_css_class("pill")
        ab.connect("clicked", lambda b: apply_cb())
        bar.append(ab)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        if extra_btn:
            bar.append(extra_btn)

        return bar

    # -----------------------------------------------------------------------
    # Tab 1 — Wheel & FFB
    # -----------------------------------------------------------------------
    def _build_wheel_page(self):
        scrolled, box = self._make_page_scroll()

        self.wheel_banner = Adw.Banner()
        box.append(self.wheel_banner)
        self._update_wheel_banner()

        grp = Adw.PreferencesGroup()
        grp.set_title("Wheel Base Tuning")
        box.append(grp)

        def extra(group, attr_id):
            if attr_id == "SEN":
                row = Adw.ActionRow()
                row.set_title("Auto Sensitivity")
                row.set_subtitle(
                    "Write 0 to SEN, forcing the base to its absolute maximum range.")
                btn = Gtk.Button(label="Auto-Max")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda b: self._set_sen_auto())
                row.add_suffix(btn)
                group.add(row)

        build_attr_rows(grp, WHEEL_ATTRS, self.wheel_w,
                        self._write_wheel_attr, extra_rows_cb=extra)

        def_btn = Gtk.Button(label="Sim-Racing Defaults")
        def_btn.add_css_class("accent")
        def_btn.connect("clicked", lambda b: self._apply_sim_defaults())

        box.append(self._make_action_bar(
            self._load_current_values,
            self._apply_all_wheel,
            extra_btn=def_btn,
        ))
        return scrolled

    # -----------------------------------------------------------------------
    # Tab 2 — Pedals & Brake
    # -----------------------------------------------------------------------
    def _build_pedal_page(self):
        scrolled, box = self._make_page_scroll()

        self.pedal_banner = Adw.Banner()
        box.append(self.pedal_banner)
        self._update_pedal_banner()

        # Brake tuning group
        grp = Adw.PreferencesGroup()
        grp.set_title("Brake Tuning")
        grp.set_description(
            "brF (Brake Force) and BLI require a CSL/ClubSport load-cell brake. "
            "FUL (FullForce) routes pedal-load vibration through the wheel motor "
            "and requires a compatible game."
        )
        box.append(grp)
        build_attr_rows(grp, PEDAL_ATTRS, self.pedal_w, self._write_pedal_attr)

        # CSP rumble group
        rumble_grp = Adw.PreferencesGroup()
        rumble_grp.set_title("ClubSport Pedals Rumble Motor")
        rumble_grp.set_description(
            "One-shot rumble command for ClubSport Pedals V3 or CSL Elite/LC Pedals. "
            "Motor A = throttle pedal, Motor B = brake pedal. "
            "Duration is in ~10 ms ticks (0–255)."
        )
        box.append(rumble_grp)

        for key, title in [("rumble_a", "Motor A — Throttle Pedal"),
                            ("rumble_b", "Motor B — Brake Pedal")]:
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle("Motor strength 0–255.")
            adj = Gtk.Adjustment(value=0, lower=0, upper=255,
                                 step_increment=1, page_increment=10)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=adj)
            scale.set_size_request(180, -1)
            scale.set_valign(Gtk.Align.CENTER)
            scale.set_draw_value(False)
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=1.0, digits=0)
            spin.set_valign(Gtk.Align.CENTER)
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hb.set_valign(Gtk.Align.CENTER)
            hb.append(scale)
            hb.append(spin)
            row.add_suffix(hb)
            rumble_grp.add(row)
            self.pedal_w[key] = adj

        dur_row = Adw.ActionRow()
        dur_row.set_title("Duration")
        dur_row.set_subtitle("Duration in ~10 ms ticks (0–255).")
        dur_adj = Gtk.Adjustment(value=20, lower=0, upper=255,
                                 step_increment=1, page_increment=10)
        dur_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                              adjustment=dur_adj)
        dur_scale.set_size_request(180, -1)
        dur_scale.set_valign(Gtk.Align.CENTER)
        dur_scale.set_draw_value(False)
        dur_spin = Gtk.SpinButton(adjustment=dur_adj, climb_rate=1.0, digits=0)
        dur_spin.set_valign(Gtk.Align.CENTER)
        dur_hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dur_hb.set_valign(Gtk.Align.CENTER)
        dur_hb.append(dur_scale)
        dur_hb.append(dur_spin)
        dur_row.add_suffix(dur_hb)
        rumble_grp.add(dur_row)
        self.pedal_w["rumble_dur"] = dur_adj

        fire_row = Adw.ActionRow()
        fire_row.set_title("Send Rumble Command")
        fire_row.set_subtitle(
            "Packs Motor A, Motor B, and Duration into a 24-bit value and writes "
            "to the rumble sysfs node.")
        fire_btn = Gtk.Button(label="Fire Rumble")
        fire_btn.add_css_class("suggested-action")
        fire_btn.set_valign(Gtk.Align.CENTER)
        fire_btn.connect("clicked", lambda b: self._send_rumble())
        fire_row.add_suffix(fire_btn)
        rumble_grp.add(fire_row)

        box.append(self._make_action_bar(
            self._load_current_values,
            self._apply_all_pedal,
        ))
        return scrolled

    # -----------------------------------------------------------------------
    # Tab 3 — Advanced
    # -----------------------------------------------------------------------
    def _build_advanced_page(self):
        scrolled, box = self._make_page_scroll()

        # Natural feel group
        nat_grp = Adw.PreferencesGroup()
        nat_grp.set_title("Natural Wheel Feel")
        nat_grp.set_description(
            "Mechanical-feel effects baked into the wheel base independent of "
            "game FFB output. Set all to 0 for pure game-driven FFB."
        )
        box.append(nat_grp)

        nat_attrs = [a for a in ADVANCED_ATTRS if a[0] in ("NDP", "NFR", "NIN")]
        build_attr_rows(nat_grp, nat_attrs, self.advanced_w,
                        self._write_advanced_attr)

        # Drift & assist group
        assist_grp = Adw.PreferencesGroup()
        assist_grp.set_title("Drift & Steering Assist")
        box.append(assist_grp)

        drift_attrs = [a for a in ADVANCED_ATTRS if a[0] == "DRI"]
        build_attr_rows(assist_grp, drift_attrs, self.advanced_w,
                        self._write_advanced_attr)

        # Analogue paddles group
        paddle_grp = Adw.PreferencesGroup()
        paddle_grp.set_title("Analogue Paddles (ACP)")
        paddle_grp.set_description(
            "Controls how the clutch paddles behave. Read-only on wheels with "
            "a physical mode selector switch."
        )
        box.append(paddle_grp)

        acp_attrs = [a for a in ADVANCED_ATTRS if a[0] == "ACP"]

        def acp_extra(group, attr_id):
            if attr_id == "ACP":
                info_row = Adw.ActionRow()
                info_row.set_title("Current Mode")
                info_row.set_subtitle("Updates as you move the ACP slider above.")
                self._acp_label = Gtk.Label(label="—")
                self._acp_label.add_css_class("dim-label")
                self._acp_label.set_valign(Gtk.Align.CENTER)
                info_row.add_suffix(self._acp_label)
                group.add(info_row)
                self.advanced_w["ACP"].connect(
                    "value-changed",
                    lambda adj: self._acp_label.set_label(
                        ACP_LABELS.get(int(adj.get_value()), "—")
                    ),
                )

        build_attr_rows(paddle_grp, acp_attrs, self.advanced_w,
                        self._write_advanced_attr, extra_rows_cb=acp_extra)

        # Tuning slots group
        slot_grp = Adw.PreferencesGroup()
        slot_grp.set_title("Tuning Slots")
        slot_grp.set_description(
            "The wheel base stores up to 5 independent tuning profiles (slots). "
            "Switching slots loads that profile's values into all tuning attrs. "
            "Reset clears all slots on the device."
        )
        box.append(slot_grp)

        slot_row = Adw.ActionRow()
        slot_row.set_title("Active Slot  (SLOT)")
        slot_row.set_subtitle("Select the tuning profile slot to read/write (1–5).")
        slot_adj = Gtk.Adjustment(value=1, lower=1, upper=5,
                                  step_increment=1, page_increment=1)
        slot_spin = Gtk.SpinButton(adjustment=slot_adj, climb_rate=1.0, digits=0)
        slot_spin.set_valign(Gtk.Align.CENTER)
        slot_set = Gtk.Button(label="Switch Slot")
        slot_set.add_css_class("suggested-action")
        slot_set.set_valign(Gtk.Align.CENTER)
        slot_set.connect("clicked", lambda b: self._switch_slot(
            int(slot_adj.get_value())))
        slot_hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        slot_hb.set_valign(Gtk.Align.CENTER)
        slot_hb.append(slot_spin)
        slot_hb.append(slot_set)
        slot_row.add_suffix(slot_hb)
        slot_grp.add(slot_row)
        self.advanced_w["SLOT"] = slot_adj

        reset_row = Adw.ActionRow()
        reset_row.set_title("Reset All Tuning Slots")
        reset_row.set_subtitle(
            "Writes to the RESET sysfs node, clearing all 5 slots on the device.")
        reset_btn = Gtk.Button(label="Reset Device Tuning")
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", lambda b: self._confirm_reset())
        reset_row.add_suffix(reset_btn)
        slot_grp.add(reset_row)

        box.append(self._make_action_bar(
            self._load_current_values,
            self._apply_all_advanced,
        ))
        return scrolled

    # -----------------------------------------------------------------------
    # Tab 4 — Presets
    # -----------------------------------------------------------------------
    def _build_presets_page(self):
        scrolled, box = self._make_page_scroll()

        # ── Save current settings ──────────────────────────────────────────
        save_grp = Adw.PreferencesGroup()
        save_grp.set_title("Save Preset")
        save_grp.set_description(
            "Snapshots all current Wheel & FFB, Pedals, and Advanced values "
            "into a named preset file in ~/.config/SimPower/."
        )
        box.append(save_grp)

        name_row = Adw.ActionRow()
        name_row.set_title("Preset Name")
        name_row.set_subtitle("Enter a name, then click Save.")
        self._preset_name_entry = Gtk.Entry()
        self._preset_name_entry.set_placeholder_text("e.g. GT3 Race")
        self._preset_name_entry.set_valign(Gtk.Align.CENTER)
        self._preset_name_entry.set_size_request(200, -1)
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", lambda b: self._save_preset())
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hb.set_valign(Gtk.Align.CENTER)
        hb.append(self._preset_name_entry)
        hb.append(save_btn)
        name_row.add_suffix(hb)
        save_grp.add(name_row)

        # ── Saved presets list ─────────────────────────────────────────────
        # _presets_box holds the PreferencesGroup so we can swap it out on
        # refresh (Adw.PreferencesGroup doesn't support row removal).
        self._presets_list_box = box
        self._presets_grp = None
        self._refresh_presets_list()
        return scrolled

    # -----------------------------------------------------------------------
    # Preset helpers
    # -----------------------------------------------------------------------
    def _collect_all_values(self):
        """Return a dict of all tunable attr IDs → current widget values."""
        result = {}
        for attrs, widgets in [
            (WHEEL_ATTRS,    self.wheel_w),
            (PEDAL_ATTRS,    self.pedal_w),
            (ADVANCED_ATTRS, self.advanced_w),
        ]:
            for attr_id, *_, kind in attrs:
                w = widgets.get(attr_id)
                if w is None:
                    continue
                if kind == "toggle":
                    result[attr_id] = int(w.get_active())
                else:
                    result[attr_id] = int(w.get_value())
        # Also save current SLOT value
        slot_w = self.advanced_w.get("SLOT")
        if slot_w:
            result["SLOT"] = int(slot_w.get_value())
        return result

    def _apply_preset_values(self, values):
        """Push a dict of attr IDs → values back into all widgets."""
        for attrs, widgets in [
            (WHEEL_ATTRS,    self.wheel_w),
            (PEDAL_ATTRS,    self.pedal_w),
            (ADVANCED_ATTRS, self.advanced_w),
        ]:
            for attr_id, *_, kind in attrs:
                if attr_id not in values:
                    continue
                w = widgets.get(attr_id)
                if w is None:
                    continue
                if kind == "toggle":
                    w.set_active(bool(values[attr_id]))
                else:
                    w.set_value(values[attr_id])
        slot_w = self.advanced_w.get("SLOT")
        if slot_w and "SLOT" in values:
            slot_w.set_value(values["SLOT"])

    def _preset_path(self, name):
        safe = name.strip().replace("/", "_").replace("\\", "_")
        return CONFIG_DIR / f"{safe}.json"

    def _save_preset(self):
        name = self._preset_name_entry.get_text().strip()
        if not name:
            self._err("No Name", "Enter a preset name before saving.")
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = self._preset_path(name)
        data = {"name": name, "values": self._collect_all_values()}
        try:
            path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            self._err("Save Failed", str(e))
            return
        self._preset_name_entry.set_text("")
        self._refresh_presets_list()

    def _load_preset(self, path):
        try:
            data = json.loads(pathlib.Path(path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            self._err("Load Failed", str(e))
            return
        self._apply_preset_values(data.get("values", {}))

    def _confirm_delete_preset(self, path, name):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f'Delete "{name}"?',
            body="This removes the saved preset file. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, rid):
            if rid == "delete":
                try:
                    pathlib.Path(path).unlink(missing_ok=True)
                except OSError as e:
                    self._err("Delete Failed", str(e))
                self._refresh_presets_list()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.choose(None, None)

    def _refresh_presets_list(self):
        # Remove the old group widget entirely and build a fresh one.
        # Adw.PreferencesGroup does not support programmatic row removal, so
        # attempting get_first_child()/remove() on it causes CRITICAL errors.
        if self._presets_grp is not None:
            self._presets_list_box.remove(self._presets_grp)

        grp = Adw.PreferencesGroup()
        grp.set_title("Saved Presets")
        self._presets_list_box.append(grp)
        self._presets_grp = grp

        files = sorted(CONFIG_DIR.glob("*.json")) if CONFIG_DIR.exists() else []

        if not files:
            empty_row = Adw.ActionRow()
            empty_row.set_title("No presets saved yet")
            empty_row.set_subtitle(
                "Save a preset above and it will appear here.")
            grp.add(empty_row)
            return

        for preset_file in files:
            try:
                data = json.loads(preset_file.read_text())
                display_name = data.get("name", preset_file.stem)
                keys = list(data.get("values", {}).keys())
                subtitle = f"{len(keys)} attributes  •  {preset_file.name}"
            except (OSError, json.JSONDecodeError):
                display_name = preset_file.stem
                subtitle = "⚠ Could not read file"

            row = Adw.ActionRow()
            row.set_title(display_name)
            row.set_subtitle(subtitle)

            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hb.set_valign(Gtk.Align.CENTER)

            load_btn = Gtk.Button(label="Load")
            load_btn.add_css_class("suggested-action")
            load_btn.set_valign(Gtk.Align.CENTER)
            load_btn.connect(
                "clicked",
                lambda b, p=str(preset_file): self._load_preset(p),
            )
            hb.append(load_btn)

            del_btn = Gtk.Button(label="Delete")
            del_btn.add_css_class("destructive-action")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.connect(
                "clicked",
                lambda b, p=str(preset_file), n=display_name:
                    self._confirm_delete_preset(p, n),
            )
            hb.append(del_btn)

            row.add_suffix(hb)
            grp.add(row)

    # -----------------------------------------------------------------------
    # Banners
    # -----------------------------------------------------------------------
    def _update_wheel_banner(self):
        if not self.device_path:
            self.wheel_banner.set_title(
                "No hid-fanatecff sysfs device found. Ensure the base is powered on.")
            self.wheel_banner.set_button_label("Retry")
            self.wheel_banner.connect("button-clicked",
                                      lambda b: self._retry_discovery())
            self.wheel_banner.set_revealed(True)
        elif not can_write(self.device_path):
            self.wheel_banner.set_title(
                "No write permission on sysfs. Writes will request root authorisation.")
            self.wheel_banner.set_revealed(True)
        else:
            self.wheel_banner.set_revealed(False)

    def _update_pedal_banner(self):
        if not self.rumble_path:
            self.pedal_banner.set_title(
                "No CSP rumble node found — rumble unavailable. "
                "Brake tuning attrs are shared with the wheel base sysfs.")
            self.pedal_banner.set_revealed(True)
        else:
            self.pedal_banner.set_title(
                f"CSP rumble node: {self.rumble_path}")
            self.pedal_banner.set_revealed(True)

    def _retry_discovery(self):
        self.device_path = find_device_path()
        self.rumble_path = find_rumble_path()
        self._update_wheel_banner()
        self._update_pedal_banner()
        if self.device_path:
            self._load_current_values()

    # -----------------------------------------------------------------------
    # sysfs read/write
    # -----------------------------------------------------------------------
    def _attr_file(self, attr_id):
        return os.path.join(self.device_path, attr_id) if self.device_path else ""

    def _load_current_values(self):
        if not self.device_path:
            return
        for attrs, widgets in [
            (WHEEL_ATTRS,    self.wheel_w),
            (PEDAL_ATTRS,    self.pedal_w),
            (ADVANCED_ATTRS, self.advanced_w),
        ]:
            for attr_id, *_, kind in attrs:
                path = self._attr_file(attr_id)
                if not os.path.exists(path):
                    continue
                try:
                    with open(path) as f:
                        val = int(f.read().strip())
                    w = widgets.get(attr_id)
                    if w is None:
                        continue
                    if kind == "toggle":
                        w.set_active(bool(val))
                    else:
                        w.set_value(val)
                except (OSError, ValueError):
                    pass

        # Also sync SLOT spinner
        slot_path = self._attr_file("SLOT")
        if os.path.exists(slot_path):
            try:
                with open(slot_path) as f:
                    self.advanced_w["SLOT"].set_value(int(f.read().strip()))
            except (OSError, ValueError):
                pass

    def _write_value(self, path, value):
        try:
            with open(path, "w") as f:
                f.write(str(int(value)))
            return True
        except PermissionError:
            return self._write_privileged(path, value)

    def _write_privileged(self, path, value):
        cmd_str = f'echo {int(value)} > "{path}"'
        cmd = (["pkexec", "/bin/sh", "-c", cmd_str]
               if shutil.which("pkexec")
               else ["sudo", "/bin/sh", "-c", cmd_str])
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            self._err("Privilege Escalation Failed", e.stderr or "Permission denied.")
            return False

    def _write_attr(self, attr_id, widgets):
        if not self.device_path:
            self._err("No Device", "No active wheel interface detected.")
            return
        path = self._attr_file(attr_id)
        if not os.path.exists(path):
            self._err("Attribute Not Found",
                      f"'{attr_id}' not present in sysfs.\n"
                      "This may require specific pedal hardware.")
            return
        w = widgets[attr_id]
        val = w.get_active() if isinstance(w, Gtk.Switch) else w.get_value()
        self._write_value(path, val)

    def _write_wheel_attr(self, aid):
        self._write_attr(aid, self.wheel_w)

    def _write_pedal_attr(self, aid):
        self._write_attr(aid, self.pedal_w)

    def _write_advanced_attr(self, aid):
        self._write_attr(aid, self.advanced_w)

    def _set_sen_auto(self):
        if not self.device_path:
            return
        if self._write_value(self._attr_file("SEN"), 0):
            GLib.timeout_add(150, self._load_current_values)

    def _switch_slot(self, slot):
        if not self.device_path:
            return
        path = self._attr_file("SLOT")
        if self._write_value(path, slot):
            GLib.timeout_add(150, self._load_current_values)

    def _send_rumble(self):
        if not self.rumble_path:
            self._err("No Rumble Hardware",
                      "No CSP/ClubSport rumble sysfs node was found.\n"
                      "Requires ClubSport Pedals V3 or CSL Elite/LC Pedals.")
            return
        a   = int(self.pedal_w["rumble_a"].get_value())
        b   = int(self.pedal_w["rumble_b"].get_value())
        dur = int(self.pedal_w["rumble_dur"].get_value())
        self._write_value(self.rumble_path, (a << 16) | (b << 8) | dur)

    # -----------------------------------------------------------------------
    # Apply-all helpers
    # -----------------------------------------------------------------------
    def _write_group(self, attrs, widgets):
        """Write every attr in the group; return the ids that failed."""
        failures = []
        for attr_id, *_, kind in attrs:
            path = self._attr_file(attr_id)
            if not os.path.exists(path):
                continue
            w = widgets[attr_id]
            val = w.get_active() if isinstance(w, Gtk.Switch) else w.get_value()
            if not self._write_value(path, val):
                failures.append(attr_id)
        return failures

    def _apply_group(self, attrs, widgets):
        if not self.device_path:
            return
        failures = self._write_group(attrs, widgets)
        if failures:
            self._err("Write Errors", f"Failed syncing: {', '.join(failures)}")

    def _apply_all_wheel(self):
        self._apply_group(WHEEL_ATTRS, self.wheel_w)

    def _apply_all_pedal(self):
        self._apply_group(PEDAL_ATTRS, self.pedal_w)

    def _apply_all_advanced(self):
        # Write SLOT first: switching loads that slot's stored values, so
        # doing it last would discard the values written below.
        if self.device_path:
            slot_path = self._attr_file("SLOT")
            if os.path.exists(slot_path):
                self._write_value(slot_path,
                                  int(self.advanced_w["SLOT"].get_value()))
        self._apply_group(ADVANCED_ATTRS, self.advanced_w)

    def _apply_everything(self):
        if not self.device_path:
            self._err("No Device", "No active wheel interface detected.")
            return
        failures = []
        # Switch slot first: switching loads that slot's stored values, so
        # doing it last would discard the values written below.
        slot_path = self._attr_file("SLOT")
        if os.path.exists(slot_path):
            if not self._write_value(slot_path,
                                     int(self.advanced_w["SLOT"].get_value())):
                failures.append("SLOT")
        failures += self._write_group(WHEEL_ATTRS, self.wheel_w)
        failures += self._write_group(PEDAL_ATTRS, self.pedal_w)
        failures += self._write_group(ADVANCED_ATTRS, self.advanced_w)
        if failures:
            self._err("Write Errors", f"Failed syncing: {', '.join(failures)}")

    # -----------------------------------------------------------------------
    # Sim-racing defaults (wheel tab)
    # -----------------------------------------------------------------------
    def _apply_sim_defaults(self):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Load Sim-Racing Defaults?",
            body="Applies balanced sim-racing values to all Wheel & FFB settings immediately.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, rid):
            if rid == "apply":
                defaults = {
                    "SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0,
                    "SHO": 80,  "FFS": 1, "FEI": 30,  "INT": 0,
                }
                for aid, val in defaults.items():
                    w = self.wheel_w.get(aid)
                    if w is None:
                        continue
                    if isinstance(w, Gtk.Switch):
                        w.set_active(bool(val))
                    else:
                        w.set_value(val)
                self._apply_all_wheel()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.choose(None, None)

    # -----------------------------------------------------------------------
    # Reset all tuning slots
    # -----------------------------------------------------------------------
    def _confirm_reset(self):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Reset All Tuning Slots?",
            body="This clears all 5 tuning profiles stored on the device and "
                 "cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset Device Tuning")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, rid):
            if rid == "reset":
                path = self._attr_file("RESET")
                if os.path.exists(path):
                    self._write_value(path, 1)
                    GLib.timeout_add(300, self._load_current_values)
                else:
                    self._err("Not Available",
                              "RESET sysfs node not found on this device.")
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.choose(None, None)

    # -----------------------------------------------------------------------
    def _err(self, title, message):
        d = Adw.MessageDialog(transient_for=self, heading=title, body=message)
        d.add_response("ok", "Dismiss")
        d.choose(None, None)


# ===========================================================================
class TunerApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.benryboi.FanatecLinux",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window()
        if not win:
            win = TunerWindow(application=self)
        win.present()


if __name__ == "__main__":
    app = TunerApplication()
    sys.exit(app.run(sys.argv))
