"""Every pressable thing has rounded corners — one rule, and it stays on.

Operator, 2026-09-28: "Anywhere there's something to be pressed, let's make
sure the corners are rounded so it looks like a button."

The rule is applied to the Button CLASS in ui/rounded.py, switched on once in
ui/app.py before any screen is built. That is the whole point — 196 call sites
keep working untouched and the 197th is rounded the day it is written. These
tests guard the two halves: the pure decisions (which buttons, what radius,
what colour), and the fact that the switch is still thrown. No Kivy screen can
be built in this suite ([[module-level-name-guard]]), so the wiring is checked
in the source.
"""

import ast
import os

from ui.rounded import (DISABLED_ALPHA, PRESS_DARKEN, RADIUS_DP, corner_radius,
                        is_flat, press_tint)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- which buttons get rounded ---------------------------------------------

def test_flat_colour_buttons_are_rounded():
    """185 of the 196 call sites pass background_normal="" — the main case."""
    assert is_flat("") is True
    assert is_flat(None) is True
    assert is_flat("   ") is True


def test_unstyled_default_buttons_are_rounded_too():
    """The ten nobody styled. A default-grey rectangle sitting among rounded
    cards is exactly the odd one out the operator was pointing at."""
    assert is_flat("atlas://data/images/defaulttheme/button") is True
    assert is_flat("atlas://data/images/defaulttheme/button_pressed") is True


def test_image_backed_buttons_are_left_alone():
    """The front page's gear is a transparent PNG that carries its own shape.
    Painting a rounded plate behind it would put a box around an icon drawn
    without one."""
    assert is_flat("assets/ui/gear.png") is False
    assert is_flat("/home/nodemedic/reticulum-tool/assets/ui/power.png") is False


# --- the radius -------------------------------------------------------------

def test_a_normal_button_gets_the_full_radius():
    assert corner_radius(300, 104, 10) == 10


def test_a_small_button_never_becomes_a_lozenge():
    """A 14 px-tall control with a 10 px radius is a pill, not a button."""
    assert corner_radius(40, 14, 10) == 7.0
    assert corner_radius(12, 300, 10) == 6.0


def test_an_unlaid_out_button_asks_for_no_radius():
    """Kivy widgets start at 100x100 but can be 0 mid-layout; a negative or
    zero radius raises inside the graphics instruction."""
    assert corner_radius(0, 0, 10) == 0.0
    assert corner_radius(-5, 50, 10) == 0.0


# --- the press --------------------------------------------------------------

def test_pressing_darkens_the_fill():
    """Removing Kivy's atlas removes its pressed look, so the fill carries it.
    Without this, every button in the tool would stop acknowledging a press —
    the exact fault just fixed on the five front-page keys."""
    green = (0.0, 0.784, 0.33, 1.0)
    down = press_tint(green, "down")
    assert down[3] == 1.0, "a press must not change opacity, only value"
    assert down[1] < green[1], "the fill must drop when the finger is down"
    assert abs(down[1] - green[1] * PRESS_DARKEN) < 1e-9


def test_a_disabled_button_fades_rather_than_darkens():
    green = (0.0, 0.784, 0.33, 1.0)
    off = press_tint(green, "normal", disabled=True)
    assert off[:3] == green[:3], "disabled fades, it does not change hue"
    assert off[3] == DISABLED_ALPHA


def test_a_disabled_button_stays_faded_even_while_pressed():
    green = (0.0, 0.784, 0.33, 1.0)
    assert press_tint(green, "down", disabled=True)[3] == DISABLED_ALPHA


def test_a_transparent_button_stays_invisible():
    """The nav bar's Back and Home are flat with alpha 0 — they are text on the
    bar, not plates. Rounding must not give them a visible body."""
    assert press_tint((0, 0, 0, 0))[3] == 0
    assert press_tint((0, 0, 0, 0), "down")[3] == 0


# --- the switch is still thrown --------------------------------------------

def test_the_app_turns_the_rule_on():
    """ui/app.py must call rounded.enable() at import, before any screen is
    built. Lose this line and all 196 buttons quietly go square again — nothing
    else in the suite would notice."""
    with open(os.path.join(REPO, "ui", "app.py")) as fh:
        tree = ast.parse(fh.read())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "enable"]
    assert calls, "ui/app.py no longer calls rounded.enable() — buttons are square"
    assert any(isinstance(n, ast.Module) for n in [tree])
    # and at module level, not inside a function that may never run
    top = [n for n in tree.body if isinstance(n, ast.Expr)
           and isinstance(n.value, ast.Call)
           and isinstance(n.value.func, ast.Attribute)
           and n.value.func.attr == "enable"]
    assert top, "rounded.enable() moved inside a function — screens may be built first"


def test_one_radius_for_the_whole_tool():
    """Buttons that disagree about their corners read as different apps."""
    assert RADIUS_DP == 10


# --- the phosphor rim (2026-09-29) ------------------------------------------

def test_dark_plates_wear_the_rim():
    """The operator's map mockup draws every button as a green outline around a
    dark plate. That is one rule, applied here, not 196 edits."""
    from ui import theme
    from ui.rounded import wants_rim
    for name in ("surface", "background", "sidebar", "black"):
        assert wants_rim(theme.hex_to_rgba(theme.COLORS[name])) is True, name


def test_a_warning_button_is_never_ringed_in_green():
    """Red is the warning. A phosphor rim around the delete button argues with
    the one thing it exists to say — and amber and the status green are bright
    enough that a rim would only muddy their edge."""
    from ui import theme
    from ui.rounded import wants_rim
    for name in ("red", "amber", "green", "warning_yellow", "accent"):
        assert wants_rim(theme.hex_to_rgba(theme.COLORS[name])) is False, name


def test_a_transparent_button_has_no_rim_to_draw():
    """The nav bar's Back and Home are text on the bar, not plates."""
    from ui.rounded import wants_rim
    assert wants_rim((0, 0, 0, 0)) is False
