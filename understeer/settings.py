"""The wheel base settings shown in the app, without depending on GTK."""
from .i18n import _, N_

# ---------------------------------------------------------------------------
# Settings
#
# Each entry describes one sysfs attribute. "kind" is "slider" or "choice";
# "icon" is a symbolic icon name shown on its tile.
# Sliders may have a "unit" and a "fmt" function for special values;
# choices list (value, label) pairs.
# ---------------------------------------------------------------------------
# Views in the header bar.
VIEWS = [
    ("tuning",   N_("Tuning"),  "understeer-steering-symbolic"),
    ("presets",  N_("Presets"), "document-save-symbolic"),
    ("games",    N_("Games"),   "applications-games-symbolic"),
]

# Tabs of the Tuning view; each setting's "page" is one of them.
TABS = [
    ("steering", N_("Steering & Force Feedback")),
    ("feel",     N_("Wheel Feel")),
    ("pedals",   N_("Pedals & Paddles")),
]

# Groups that are rarely changed: folded away, with their values in one line.
ADVANCED_GROUPS = {"Signal"}


def _fmt_drift(v):
    if v == 0:
        return _("Off")
    if v < 0:
        return _("Counter-steer {n}").format(n=-v)
    return _("Centering +{n}").format(n=v)


def _fmt_brake_led(v):
    if v == 0:
        return _("Off")
    if v >= 101:
        return _("Always on")
    return f"{v} %"


SETTINGS = [
    # --- Steering --------------------------------------------------------
    # hi: the largest angle of any wheelbase (direct drive); the slider
    # goes as far as the connected one allows (device.steering_range).
    # auto: 0 stands for Auto, the wheelbase's whole range.
    dict(id="SEN", icon="understeer-steering-symbolic",
         page="steering", group=N_("Rotation"),
         label=N_("Steering Angle"), lo=90, hi=2520, unit="°", auto=True,
         desc=N_("Total wheel rotation in degrees. Auto uses the maximum "
                 "range of the wheel base."),
         quick=[540, 720, 900, 1080]),
    dict(id="FF", icon="power-profile-performance-symbolic",
         page="steering", group=N_("Force Feedback"),
         label=N_("Force Feedback Strength"), lo=0, hi=100, unit="%",
         desc=N_("Master force feedback output of the wheel base.")),
    dict(id="FOR", icon="weather-windy-symbolic",
         page="steering", group=N_("Force Feedback"),
         label=N_("Force Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of constant-force effects, the core of the game's "
                 "force feedback.")),
    dict(id="SPR", icon="object-rotate-left-symbolic",
         page="steering", group=N_("Force Feedback"),
         label=N_("Spring Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of centering spring effects sent by the game.")),
    dict(id="DPR", icon="weather-fog-symbolic",
         page="steering", group=N_("Force Feedback"),
         label=N_("Damper Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of damper effects sent by the game.")),
    dict(id="FFS", icon="view-sort-ascending-symbolic",
         page="steering", group=N_("Signal"),
         label=N_("Force Feedback Scaling"), kind="choice",
         choices=[(0, N_("Linear")), (1, N_("Peak"))],
         desc=N_("Linear keeps a constant taper. Peak scales to the strongest "
                 "force (default).")),
    dict(id="FEI", icon="display-brightness-symbolic",
         page="steering", group=N_("Signal"),
         label=N_("Force Effect Intensity"), lo=0, hi=100, unit="%",
         desc=N_("Filters the force feedback signal. Higher values give "
                 "sharper details.")),
    dict(id="INT", icon="view-continuous-symbolic",
         page="steering", group=N_("Signal"),
         label=N_("Interpolation Filter"), lo=0, hi=20,
         desc=N_("Smooths between force feedback updates. 0 turns it off.")),
    # --- Wheel feel ------------------------------------------------------
    dict(id="NDP", icon="weather-overcast-symbolic",
         page="feel", group=N_("Natural Feel"),
         label=N_("Natural Damper"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical damping added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NFR", icon="list-drag-handle-symbolic",
         page="feel", group=N_("Natural Feel"),
         label=N_("Natural Friction"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical friction added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NIN", icon="media-playlist-repeat-symbolic",
         page="feel", group=N_("Natural Feel"),
         label=N_("Natural Inertia"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical inertia added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="SHO", icon="audio-volume-high-symbolic",
         page="feel", group=N_("Effects"),
         label=N_("Wheel Vibration"), lo=0, hi=100, unit="%",
         desc=N_("Strength of the rumble on curbs and impacts.")),
    dict(id="DRI", icon="object-flip-horizontal-symbolic",
         page="feel", group=N_("Effects"),
         label=N_("Drift Mode"), lo=-5, hi=3, fmt=_fmt_drift,
         desc=N_("Negative values help with counter-steering, positive values "
                 "add centering force. 0 turns it off.")),
    # --- Pedals ----------------------------------------------------------
    dict(id="brF", icon="understeer-pedals-symbolic",
         page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Force"), lo=0, hi=100, unit="%",
         desc=N_("Pedal stiffness. Higher values need more force for full "
                 "braking. Requires a load cell brake.")),
    dict(id="BLI", icon="weather-clear-symbolic",
         page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Level Indicator"), lo=0, hi=101, fmt=_fmt_brake_led,
         desc=N_("Brake force at which the LED lights up.")),
    dict(id="FUL", icon="audio-volume-overamplified-symbolic",
         page="pedals", group=N_("Vibration"),
         label=N_("FullForce"), lo=0, hi=100, unit="%",
         desc=N_("Pedal vibration routed through the wheel motor. Requires a "
                 "game with FullForce support.")),
    # --- Paddles ---------------------------------------------------------
    dict(id="ACP", icon="view-dual-symbolic",
         page="pedals", group=N_("Analogue Paddles"),
         label=N_("Paddle Mode"), kind="choice",
         choices=[(1, N_("Independent (CbP)")),
                  (2, N_("Clutch / Handbrake (CH)")),
                  (3, N_("Brake / Throttle (Bt)")),
                  (4, N_("Analogue Axes (AnA)"))],
         desc=N_("How the analogue clutch paddles behave. Read-only on wheels "
                 "with a physical mode switch.")),
]
SETTINGS_BY_ID = {s["id"]: s for s in SETTINGS}

SIM_DEFAULTS = {"SEN": 900, "FF": 70, "FOR": 100, "SPR": 0, "DPR": 0,
                "SHO": 80, "FFS": 1, "FEI": 30, "INT": 0}

SLOTS = 5       # slots the driver can switch to
MAX_SLOT = 15   # the device reports the slot in 4 bits; a CSL DD has 6 slots


def format_value(setting, v):
    """Human-readable value, e.g. "900°", "70 %", "Peak"."""
    if setting.get("kind") == "choice":
        labels = dict(setting["choices"])
        return _(labels[v]) if v in labels else str(v)
    fmt = setting.get("fmt")
    if fmt:
        return fmt(v)
    if setting.get("auto") and v == 0:
        return _("Auto")
    unit = setting.get("unit", "")
    return f"{v}{unit}" if unit == "°" else f"{v} {unit}".strip()


def changes(before, after):
    """[(attr id, value before or None, value after)] for the settings in
    after that differ from before, in the order of SETTINGS."""
    order = {s["id"]: i for i, s in enumerate(SETTINGS)}
    return sorted(((a, before.get(a), v) for a, v in after.items()
                   if before.get(a) != v),
                  key=lambda c: order.get(c[0], len(order)))
