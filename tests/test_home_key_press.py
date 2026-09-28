"""The five front-page keys must answer a press.

The cards are PAINTED as raised, bevelled plates. That is a promise, and until
2026-09-28 nothing answered it: the front page is one flat image and a tap
changed screens with no acknowledgement at all. Both reviewers raised it twice
and the operator a third time ("they still look like they're just part of a
screen graphic").

Two things are guarded here. The geometry — the highlight has to land on the
key the finger is actually on, not the one next door, and it has to survive the
letterboxing that ``Image(keep_ratio=True)`` applies. And the wiring — a Kivy
screen cannot be imported in a test, so the handlers are checked in the source
([[module-level-name-guard]]).
"""

import ast
import os

from ui.home_zones import (CARD_ORDER, CARDS_TOP, card_rect, press_fires,
                           rect_to_widget, zone_at)

HOME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "ui", "screens", "home_screen.py")


def _tree():
    with open(HOME) as fh:
        return ast.parse(fh.read())


def _method(name):
    for node in ast.walk(_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


# --- geometry ---------------------------------------------------------------

def test_the_five_pressed_rects_tile_the_row_exactly():
    """No gap between keys and no overlap: a finger on one key lights one key."""
    rects = [rect_to_widget(card_rect(z), 0.0, 0.0, 720.0, 1280.0) for z in CARD_ORDER]
    for (x, y, w, h) in rects:
        assert abs(w - 144.0) < 0.01, "a key is not one fifth of the 720px row"
        assert abs(h - 1280.0 * (1.0 - CARDS_TOP)) < 0.01
        assert abs(y) < 0.01, "the row sits on the bottom edge of the poster"
    for left, right in zip(rects, rects[1:]):
        assert abs((left[0] + left[2]) - right[0]) < 0.01, "keys must abut"
    assert abs(rects[0][0]) < 0.01 and abs(rects[-1][0] + rects[-1][2] - 720.0) < 0.01


def test_the_highlight_lands_on_the_key_the_finger_is_on():
    """Touch -> zone and zone -> rect must agree, INCLUDING letterboxing.

    The poster is fitted with keep_ratio, so on a wider screen it sits inset
    with dark bars either side. A highlight drawn in widget coordinates instead
    of poster coordinates lands on the wrong key — or in the letterbox.
    """
    img_x, img_y, img_w, img_h = 37.0, 11.0, 540.0, 960.0     # a letterboxed poster
    for zone in CARD_ORDER:
        rx, ry, rw, rh = rect_to_widget(card_rect(zone), img_x, img_y, img_w, img_h)
        # a touch at the middle of that drawn rect, in Kivy's bottom-up space
        tx, ty = rx + rw / 2.0, ry + rh / 2.0
        fx = (tx - img_x) / img_w
        fy = 1.0 - (ty - img_y) / img_h                        # zones count downward
        assert zone_at(fx, fy) == zone, (
            f"the {zone} highlight is drawn over the {zone_at(fx, fy)} key")


def test_a_pressed_rect_never_reaches_above_the_card_row():
    for zone in CARD_ORDER:
        _x, y, _w, h = rect_to_widget(card_rect(zone), 0.0, 0.0, 720.0, 1280.0)
        top_down = 1.0 - (y + h) / 1280.0
        assert top_down >= CARDS_TOP - 1e-9, "the press highlight spills off the keys"


# --- wiring -----------------------------------------------------------------

def test_the_screen_still_has_the_press_handlers():
    for name in ("on_touch_down", "on_touch_move", "on_touch_up",
                 "_press_card", "_release_card", "_release_card_soon"):
        assert _method(name) is not None, (
            f"home_screen lost {name}() — the keys stop answering a press")


def test_touch_up_lets_go_of_the_key():
    """Release without clearing and the highlight sticks on a dead screen."""
    src = ast.dump(_method("on_touch_up"))
    assert "_release_card_soon" in src


def test_a_quick_tap_is_still_visible():
    """MIN_PRESS keeps the key down long enough to be seen. Down and up inside
    one frame would otherwise draw the press and erase it before a refresh."""
    tree = _tree()
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "MIN_PRESS" for t in n.targets)]
    assert found, "MIN_PRESS is gone — a flick shows the operator nothing"
    assert found[0].value.value >= 0.05


def test_touch_up_asks_press_fires_rather_than_deciding_for_itself():
    """The fire/cancel rule lives in home_zones.press_fires so it can be tested
    — a Kivy screen cannot be built without a display. If on_touch_up ever
    re-implements the comparison inline, the tested rule stops being the rule
    that runs."""
    src = ast.dump(_method("on_touch_up"))
    assert "press_fires" in src


# --- when a press fires ------------------------------------------------------

def test_press_and_release_on_the_same_key_opens_it():
    for zone in CARD_ORDER:
        assert press_fires(zone, zone) is True


def test_sliding_off_a_key_cancels_it():
    assert press_fires("chat", "triage") is False
    assert press_fires("vitals", "scan") is False


def test_an_unseen_press_still_opens_the_key():
    """THE important case. If on_touch_down never reaches the screen, or the
    poster has not been measured, down_zone is None — and every key must still
    work. A front page that quietly stops navigating is a dead medic."""
    for zone in CARD_ORDER:
        assert press_fires(zone, None) is True, (
            f"{zone} would not open when the press went unseen — the front page "
            "is the only way into the tool")


def test_the_easter_eggs_have_no_pressed_state_and_always_fire():
    for zone in ("credits", "wifi"):
        assert press_fires(zone, None) is True
        assert press_fires(zone, "birth") is True


def test_nothing_fires_on_empty_space():
    assert press_fires(None, None) is False
    assert press_fires(None, "birth") is False
    assert press_fires("", "birth") is False
