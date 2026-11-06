"""The wheel base settings shown in the app, without depending on GTK."""
from .i18n import _, N_

# ---------------------------------------------------------------------------
# Settings
#
# Each entry describes one sysfs attribute. "kind" is "slider" or "choice".
# Sliders may have a "unit" and a "fmt" function for special values;
# choices list (value, label) pairs.
# ---------------------------------------------------------------------------
PAGES = [
    ("steering", N_("Steering"),       "understeer-steering-symbolic"),
    ("feel",     N_("Wheel Feel"),     "emblem-system-symbolic"),
    ("pedals",   N_("Pedals"),         "understeer-pedals-symbolic"),
    ("paddles",  N_("Paddles"),        "view-dual-symbolic"),
    ("presets",  N_("Presets"),        "document-save-symbolic"),
    ("tools",    N_("Tools"),          "applications-utilities-symbolic"),
]


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
    dict(id="SEN", page="steering", group=N_("Rotation"),
         label=N_("Steering Angle"), lo=90, hi=1080, unit="°",
         desc=N_("Total wheel rotation in degrees. Auto uses the maximum "
                 "range of the wheel base."),
         quick=[540, 720, 900, 1080]),
    dict(id="FF", page="steering", group=N_("Force Feedback"),
         label=N_("Force Feedback Strength"), lo=0, hi=100, unit="%",
         desc=N_("Master force feedback output of the wheel base.")),
    dict(id="FOR", page="steering", group=N_("Force Feedback"),
         label=N_("Force Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of constant-force effects, the core of the game's "
                 "force feedback.")),
    dict(id="SPR", page="steering", group=N_("Force Feedback"),
         label=N_("Spring Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of centering spring effects sent by the game.")),
    dict(id="DPR", page="steering", group=N_("Force Feedback"),
         label=N_("Damper Effects"), lo=0, hi=120, unit="%",
         desc=N_("Strength of damper effects sent by the game.")),
    dict(id="FFS", page="steering", group=N_("Signal"),
         label=N_("Force Feedback Scaling"), kind="choice",
         choices=[(0, N_("Linear")), (1, N_("Peak"))],
         desc=N_("Linear keeps a constant taper. Peak scales to the strongest "
                 "force (default).")),
    dict(id="FEI", page="steering", group=N_("Signal"),
         label=N_("Force Effect Intensity"), lo=0, hi=100, unit="%",
         desc=N_("Filters the force feedback signal. Higher values give "
                 "sharper details.")),
    dict(id="INT", page="steering", group=N_("Signal"),
         label=N_("Interpolation Filter"), lo=0, hi=20,
         desc=N_("Smooths between force feedback updates. 0 turns it off.")),
    # --- Wheel feel ------------------------------------------------------
    dict(id="NDP", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Damper"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical damping added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NFR", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Friction"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical friction added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="NIN", page="feel", group=N_("Natural Feel"),
         label=N_("Natural Inertia"), lo=0, hi=100, unit="%",
         desc=N_("Mechanical inertia added by the wheel base itself, "
                 "independent of the game.")),
    dict(id="SHO", page="feel", group=N_("Effects"),
         label=N_("Wheel Vibration"), lo=0, hi=100, unit="%",
         desc=N_("Strength of the rumble on curbs and impacts.")),
    dict(id="DRI", page="feel", group=N_("Effects"),
         label=N_("Drift Mode"), lo=-5, hi=3, fmt=_fmt_drift,
         desc=N_("Negative values help with counter-steering, positive values "
                 "add centering force. 0 turns it off.")),
    # --- Pedals ----------------------------------------------------------
    dict(id="brF", page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Force"), lo=0, hi=100, unit="%",
         desc=N_("Pedal stiffness. Higher values need more force for full "
                 "braking. Requires a load cell brake.")),
    dict(id="BLI", page="pedals", group=N_("Load Cell Brake"),
         label=N_("Brake Level Indicator"), lo=0, hi=101, fmt=_fmt_brake_led,
         desc=N_("Brake force at which the LED lights up.")),
    dict(id="FUL", page="pedals", group=N_("Vibration"),
         label=N_("FullForce"), lo=0, hi=100, unit="%",
         desc=N_("Pedal vibration routed through the wheel motor. Requires a "
                 "game with FullForce support.")),
    # --- Paddles ---------------------------------------------------------
    dict(id="ACP", page="paddles", group=N_("Analogue Paddles"),
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

SLOTS = 5


def format_value(setting, v):
    """Human-readable value, e.g. "900°", "70 %", "Peak"."""
    if setting.get("kind") == "choice":
        labels = dict(setting["choices"])
        return _(labels[v]) if v in labels else str(v)
    fmt = setting.get("fmt")
    if fmt:
        return fmt(v)
    if setting["id"] == "SEN" and v == 0:
        return _("Auto")
    unit = setting.get("unit", "")
    return f"{v}{unit}" if unit == "°" else f"{v} {unit}".strip()
