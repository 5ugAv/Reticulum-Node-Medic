"""The walkthrough's page shape: a FIXED button bar on the bottom edge, the
yellow hint just above it, and the body the only part that flexes.

The keeper walked the first-use tour on a freshly cloned medic (2026-10-07)
and photographed all twelve pages. Four (BUILD, MAPS — carried, ANTENNA,
Settings) ran their body past the bottom of the glass, and on two of those the
Back / Open it now / Next row went with it. A button off the glass is a page
with no way on. The cure is the layout, not the words: the bar is a fixed
height and the last child (the bottom edge of a vertical BoxLayout), every
other part above it is a fixed height too, and the body scrolls inside what is
left — so no amount of text can move the bar. ``_keep_bar_on_glass`` is the
second lock: if the fixed parts themselves ever outgrow the page, the picture
shelf gives way before the bar can.

Kivy cannot open a window in CI (or on the Mac), so these read the shipped
source — by AST where the claim is structural, and by compiling the shipped
method and running it against a stub where the claim is behaviour. The same
split ``test_setup_wizard_screen.py`` uses.
"""

import ast
import re
import textwrap
import types

import pytest

from tests.srcutil import func_source, src
from ui import setup_flow as sf

STEP = "ui/widgets/wizard_step.py"
SCREEN = "ui/screens/setup_wizard_screen.py"


def _init_tree():
    """WizardStep.__init__ as an AST, found by name so a rename fails loudly."""
    text = textwrap.dedent(func_source(STEP, "__init__", cls="WizardStep"))
    return ast.parse(text), text


def _calls(tree, pred):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and pred(n)]


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _is_const(node, value):
    if not isinstance(node, ast.Constant):
        return False
    if value is None or isinstance(value, bool):
        return node.value is value
    return type(node.value) is type(value) and node.value == value


# --------------------------------------------------------------------------- #
# The bar
# --------------------------------------------------------------------------- #

def test_the_bar_is_a_fixed_height_never_a_share():
    text = src(STEP)
    assert re.search(r"^    NAV_H = 62$", text, re.M), "the bar's height is one named number"
    _tree, init = _init_tree()
    assert re.search(
        r'nav = BoxLayout\(orientation="horizontal", size_hint_y=None,\s*'
        r'height=dp\(self\.NAV_H\)', init), "the bar takes a fixed height, not a share"


def test_the_bar_is_the_last_child_so_it_is_the_bottom_edge():
    """A vertical BoxLayout lays children out from the bottom, last added
    first — so the last self.add_widget in __init__ IS the bottom edge."""
    tree, _init = _init_tree()
    adds = _calls(tree, lambda n: isinstance(n.func, ast.Attribute)
                  and n.func.attr == "add_widget"
                  and isinstance(n.func.value, ast.Name) and n.func.value.id == "self")
    assert adds, "no self.add_widget in WizardStep.__init__?"
    last = max(adds, key=lambda n: (n.lineno, n.col_offset))
    assert len(last.args) == 1 and isinstance(last.args[0], ast.Name) \
        and last.args[0].id == "nav", ast.unparse(last)
    # and the guided birth's extras go ABOVE it, never below
    for name in ("set_status", "_start_heartbeat"):
        body = func_source(STEP, name, cls="WizardStep")
        assert "self.add_widget(lbl, index=1)" in body or \
            "self.add_widget(beat, index=1)" in body, f"{name} must sit above the bar"


# --------------------------------------------------------------------------- #
# Everything above the bar
# --------------------------------------------------------------------------- #

def test_the_body_is_the_only_flexible_part_of_a_scrolling_step():
    """One size_hint_y on the page, and it is the body's ScrollView. Every
    Label is a fixed height; the counter block and the picture shelf too."""
    tree, init = _init_tree()
    labels = _calls(tree, lambda n: isinstance(n.func, ast.Name) and n.func.id == "Label")
    assert len(labels) >= 4, "counter, title, body, hint — where did they go?"
    loose = [ast.unparse(c)[:60] for c in labels
             if not _is_const(_kw(c, "size_hint_y"), None)]
    assert not loose, f"labels that could take a share of the page: {loose}"
    top = _calls(tree, lambda n: isinstance(n.func, ast.Name) and n.func.id == "BoxLayout"
                 and _kw(n, "orientation") is not None
                 and _is_const(_kw(n, "orientation"), "vertical"))
    assert top and all(_is_const(_kw(c, "size_hint_y"), None) for c in top), \
        "the counter block takes a fixed height"
    assert "self.stage.size_hint_y = None" in init, "the picture shelf is pinned on a scrolling step"
    shares = [n for n in ast.walk(tree) if isinstance(n, ast.keyword)
              and n.arg == "size_hint_y" and _is_const(n.value, 1)]
    assert len(shares) == 1, f"more than one part asks for a share: {len(shares)}"
    sv = _calls(tree, lambda n: isinstance(n.func, ast.Name) and n.func.id == "ScrollView")
    assert len(sv) == 1 and shares[0] in sv[0].keywords, \
        "the one share belongs to the body's ScrollView"


def test_the_hint_sits_between_the_body_and_the_bar():
    _tree, init = _init_tree()
    order = [init.index("sv = ScrollView("), init.index("if hint:"),
             init.index("if warning:"), init.index("nav = BoxLayout(")]
    assert order == sorted(order), "body, hint, warning, bar — in that order"


def test_the_shelf_gives_way_before_the_bar_can():
    """The second lock, exercised: with the fixed parts 40 px over the page the
    shelf shrinks by 40; with room again it grows back; a step with no shelf
    is left alone."""
    _tree, init = _init_tree()
    bound = init[init.index("if scroll_body:\n            # The second lock"):]
    assert "size=self._keep_bar_on_glass" in bound
    assert "minimum_height=self._keep_bar_on_glass" in bound
    ns = {}
    exec(compile(textwrap.dedent(func_source(STEP, "_keep_bar_on_glass",
                                             cls="WizardStep")), STEP, "exec"), ns)
    keep = ns["_keep_bar_on_glass"]
    page = types.SimpleNamespace(height=480.0, minimum_height=0.0, _shelf_px=180.0)
    page.stage = types.SimpleNamespace(parent=page, size_hint_y=None, height=180.0)
    # counter + title + hint + bar + padding = 340; with the 180 shelf, 520 > 480
    page.minimum_height = 340.0 + page.stage.height
    keep(page)
    assert page.stage.height == pytest.approx(140.0), "the shelf gave up the 40 px overrun"
    page.minimum_height = 340.0 + page.stage.height
    keep(page)
    assert page.stage.height == pytest.approx(140.0), "settles — no oscillation"
    page.height = 853.0                                     # the real panel
    keep(page)
    assert page.stage.height == pytest.approx(180.0), "room again: the full shelf is back"
    page.height = 300.0                                     # absurdly short
    page.minimum_height = 340.0 + page.stage.height
    keep(page)
    assert page.stage.height == 0.0, "the shelf can go to nothing; the bar never does"
    bare = types.SimpleNamespace(height=480.0, minimum_height=600.0, _shelf_px=0.0)
    bare.stage = types.SimpleNamespace(parent=None, size_hint_y=None, height=100.0)
    keep(bare)
    assert bare.stage.height == 100.0, "a step with no shelf is not touched"
    flex = types.SimpleNamespace(height=480.0, minimum_height=600.0, _shelf_px=0.0)
    flex.stage = types.SimpleNamespace(parent=flex, size_hint_y=1, height=100.0)
    keep(flex)
    assert flex.stage.height == 100.0, "the guided birth's flexible stage is not touched"


# --------------------------------------------------------------------------- #
# The tour's pages all take this shape
# --------------------------------------------------------------------------- #

def _render_plain_calls():
    tree = ast.parse(textwrap.dedent(
        func_source(SCREEN, "_render_plain", cls="SetupWizardScreen")))
    return _calls(tree, lambda n: isinstance(n.func, ast.Attribute)
                  and n.func.attr == "_wizard")


def test_every_tour_page_scrolls_its_body_under_the_fixed_bar():
    calls = _render_plain_calls()
    assert len(calls) == 3, "set up / already set up / plain — three ways to draw a page"
    for c in calls:
        assert _is_const(_kw(c, "scroll_body"), True), ast.unparse(c)
        assert _kw(c, "stage_height") is not None, ast.unparse(c)


# --------------------------------------------------------------------------- #
# Page 2 on a medic whose radio is already set up
# --------------------------------------------------------------------------- #

class _Box:
    def __init__(self, **_kw):
        self.children = []
        self.size_hint_y = 1
        self.height = 0

    def add_widget(self, w):
        self.children.append(w)


def _load(name, **extra):
    ns = {"BoxLayout": _Box, "dp": lambda v: v, "tr": lambda s: s,
          "poster_card_image": lambda zone: None,
          "_board_image_widget": lambda key: None,
          "WizardStep": types.SimpleNamespace(PICTURE_STAGE_DP=120)}
    ns.update(extra)
    exec(compile(textwrap.dedent(func_source(SCREEN, name, cls="SetupWizardScreen")),
                 SCREEN, "exec"), ns)
    return ns[name]


def _tracker_step():
    steps = sf.setup_steps(translate=lambda s: s)
    return next(s for s in steps if s["key"] == sf.TOUR_FIRSTBORN)


def _screen(owns_radio):
    calls = []
    obj = types.SimpleNamespace(_owns_radio_fn=owns_radio, _notice_widget=lambda: None,
                                _next=lambda: None, _wizard=lambda step, **kw: calls.append((step, kw)),
                                _muted_button=lambda text, fn: ("muted", text),
                                _see_it_button=lambda step: ("see", step.get("opens")),
                                _set_up_then_return=lambda step: calls.append(("SETUP", step)),
                                calls=calls)
    obj._radio_already_set_up = types.MethodType(_load("_radio_already_set_up"), obj)
    obj._render_plain = types.MethodType(_load("_render_plain"), obj)
    return obj


def test_a_medic_whose_radio_is_set_up_is_not_asked_to_set_it_up():
    """The keeper's clone (2026-10-07) owned its radio and page 2 still said
    "Set up its radio →" / "Skip for now →". Now: one true line, Back and a
    green Next, nothing to skip."""
    s = _screen(lambda: True)
    s._render_plain(_tracker_step())
    (step, kw), = s.calls
    assert kw["next_text"] == "Next  →"
    assert "extra_nav" not in kw and "on_next" not in kw, "no third button, no set-up"
    assert step["body"].endswith(
        "\n\nThis medic's radio and position finder are already set up.")
    assert step["hint"] == "", '"No Tracker yet?" is false on a medic that has one'
    assert kw["scroll_body"] is True


def test_a_medic_without_its_radio_keeps_the_set_up_as_the_main_road():
    s = _screen(lambda: False)
    s._render_plain(_tracker_step())
    (step, kw), = s.calls
    assert kw["next_text"] == "Set up its radio  →"
    assert kw["extra_nav"] == ("muted", "Skip for now  →")
    assert callable(kw["on_next"])
    kw["on_next"]()
    assert s.calls[-1][0] == "SETUP", "green Next runs the set-up, not the next page"
    assert step["hint"].startswith("No Tracker yet?")


def test_the_original_step_is_not_edited_in_place():
    step = _tracker_step()
    before = dict(step)
    _screen(lambda: True)._render_plain(step)
    assert step == before


def test_a_check_that_fails_offers_the_set_up():
    """"Could not check" is not "already set up": a wrong offer costs a tap,
    a wrong "done" costs a medic its radio."""
    def boom():
        raise OSError("no roster")
    s = _screen(boom)
    assert s._radio_already_set_up() is False
    s._render_plain(_tracker_step())
    assert s.calls[0][1]["next_text"] == "Set up its radio  →"


def test_the_check_is_the_firstborn_pages_own_not_a_second_copy():
    text = src(SCREEN)
    assert "from ui.screens.firstborn_screen import _medic_owns_radio" in text
    assert "onboard_serials" not in text and "service_bound_serials" not in text, \
        "the roster rule lives on the firstborn page; do not copy it"
    # and it still exists there to be reused
    assert "onboard_serials" in func_source("ui/screens/firstborn_screen.py",
                                            "_medic_owns_radio")
    assert "owns_radio_fn=None" in text and \
        "self._owns_radio_fn = owns_radio_fn or _medic_radio_is_set_up" in text


def test_the_resume_marker_is_only_touched_by_a_set_up_that_leaves():
    plain = func_source(SCREEN, "_render_plain", cls="SetupWizardScreen")
    done = plain[plain.index("if self._radio_already_set_up():"):plain.index("# SET IT UP is the main road")]
    assert "save_resume" not in done and "_set_up_then_return" not in done
    assert "sf.save_resume(nxt)" in func_source(SCREEN, "_set_up_then_return",
                                                cls="SetupWizardScreen")
