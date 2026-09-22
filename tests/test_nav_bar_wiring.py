"""The sliver at the foot of every mode screen: '←' far left, 'Home' centre.

Operator, 2026-09-22, after showing the medic to a stranger from scratch: "we
need a dedicated back and home button — a tiny little sliver at the bottom of
every screen". The left-edge swipe stays, but a gesture nobody is told about
is not a way out (the birth guide paid for that lesson on 2026-08-06/07).

Kivy cannot open a window here, so the wrapper classes are compiled out of
the shipped ui/app.py and run against stub widgets — real composition and
real callbacks, no display. Same practice as test_birth_back_swipe.py.
"""
import ast
import json
import os
import re
import sys
import textwrap
import types

from tests.srcutil import ROOT, func_source, src

APP = "ui/app.py"


# ---- stub Kivy just far enough to run the two classes ---------------------

class _Canvas:
    """`with self.canvas.before:` must work; nothing is drawn."""
    def __init__(self):
        self.before = self
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _Widget:
    """Records what a real Kivy widget would receive: children, bindings, props."""
    def __init__(self, **kw):
        self.children = []
        self.bound = {}
        self.canvas = _Canvas()
        self.x = self.y = 0
        self.width = kw.get("width", 100)
        self.height = kw.get("height", 100)
        self.pos = (0, 0)
        self.size = (self.width, self.height)
        for k, v in kw.items():
            setattr(self, k, v)
    def add_widget(self, w):
        self.children.insert(0, w)          # Kivy order: last added is first
    def bind(self, **kw):
        self.bound.update(kw)
    def fire(self, event):
        self.bound[event](self)
    def on_touch_down(self, touch):
        return "passed-to-super"
    def on_touch_move(self, touch):
        return "passed-to-super"
    def on_touch_up(self, touch):
        return "passed-to-super"


def _class_source(name):
    text = src(APP)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"class {name} not found in {APP}")


def _namespace(monkeypatch):
    """Globals the two classes need, with Kivy replaced by _Widget."""
    from ui import theme
    i18n = types.ModuleType("ui.i18n")
    i18n.tr = lambda s: "TR:" + s
    monkeypatch.setitem(sys.modules, "ui.i18n", i18n)
    gfx = types.ModuleType("kivy.graphics")
    gfx.Color = lambda *a, **k: None
    gfx.Rectangle = lambda **k: types.SimpleNamespace(pos=k.get("pos"), size=k.get("size"))
    monkeypatch.setitem(sys.modules, "kivy.graphics", gfx)
    return {
        "FloatLayout": _Widget, "BoxLayout": _Widget, "Button": _Widget,
        "Label": _Widget, "Widget": _Widget, "dp": lambda v: float(v),
        "theme": theme,
    }


def _load_classes(monkeypatch):
    ns = _namespace(monkeypatch)
    for name in ("_glyph_font_name", "_NavBar", "_BackSwipeWrap"):
        source = func_source(APP, name) if name.startswith("_g") else _class_source(name)
        exec(compile(textwrap.dedent(source), APP, "exec"), ns)
    return ns


def _wrap(monkeypatch):
    ns = _load_classes(monkeypatch)
    calls = []
    w = ns["_BackSwipeWrap"](on_back=lambda: calls.append("back"),
                             on_home=lambda: calls.append("home"))
    content = _Widget()
    w.add_content(content)
    return w, content, calls


# ---- composition ----------------------------------------------------------

def test_the_bar_is_a_layout_row_under_the_content_not_an_overlay(monkeypatch):
    """node_detail's Delete and the walk's Stop & save sit on the bottom edge;
    a strip painted over them covers the very control being reached for."""
    w, content, _ = _wrap(monkeypatch)
    column = w._column
    assert column.orientation == "vertical"
    # Kivy vertical BoxLayout: first added sits at the TOP, so the bar (added
    # last) is the bottom row and the content is above it.
    assert column.children == [w._bar, content]
    assert content.size_hint == (1, 1)
    assert w._bar.size_hint_y is None and 26 <= w._bar.height <= 30, "a sliver"


def test_the_bar_has_a_back_control_far_left_and_a_home_control_centred(monkeypatch):
    w, _, _ = _wrap(monkeypatch)
    bar = w._bar
    in_order = list(reversed(bar.children))          # left to right
    assert in_order[0] is bar.back_button, "the arrow is the far-left control"
    assert "←" in bar.back_button.text
    assert bar.home_button in in_order
    assert "TR:Home" in bar.home_button.text, "Home is a tr() string"
    # centred: a spacer either side of Home, and the right end balances the
    # arrow's width so the centre of the bar IS the centre of Home
    i = in_order.index(bar.home_button)
    assert in_order[i - 1].size_hint == (1, 1) and in_order[i + 1].size_hint == (1, 1)
    assert in_order[-1].width == bar.back_button.width


def test_both_controls_are_finger_wide_although_the_bar_is_short(monkeypatch):
    w, _, _ = _wrap(monkeypatch)
    assert w._bar.back_button.width >= 44
    assert w._bar.home_button.width >= 44


def test_the_bar_uses_only_theme_colours(monkeypatch):
    body = _class_source("_NavBar")
    assert re.search(r"#[0-9a-fA-F]{6}", body) is None, "no new colours"
    assert 'COLORS["sidebar"]' in body and 'COLORS["accent"]' in body


# ---- behaviour ------------------------------------------------------------

def test_back_calls_the_same_on_back_the_swipe_uses(monkeypatch):
    w, _, calls = _wrap(monkeypatch)
    w._bar.back_button.fire("on_release")
    assert calls == ["back"]
    calls.clear()
    touch = types.SimpleNamespace(x=5, y=500)          # edge, above the bar
    w.on_touch_down(touch)
    touch.x = 80
    w.on_touch_move(touch)
    assert calls == ["back"], "swipe and button are one action"


def test_home_calls_on_home(monkeypatch):
    w, _, calls = _wrap(monkeypatch)
    w._bar.home_button.fire("on_release")
    assert calls == ["home"]


def test_a_tap_in_the_bar_strip_is_not_claimed_by_the_edge_swipe(monkeypatch):
    """The arrow sits in the far-left dp(26) the swipe zone claims. A touch
    the wrapper swallows never reaches the button, so the left of the arrow
    would be dead — the bar's strip is exempt from the gesture."""
    w, _, calls = _wrap(monkeypatch)
    touch = types.SimpleNamespace(x=5, y=w._bar.height - 1)
    assert w.on_touch_down(touch) == "passed-to-super"
    assert w._edge is None
    above = types.SimpleNamespace(x=5, y=w._bar.height + 1)
    assert w.on_touch_down(above) is True, "the swipe still owns the edge above it"


# ---- the app's wiring -----------------------------------------------------

def _load_with_back():
    source = textwrap.dedent(func_source(APP, "_with_back"))
    ns = {}
    exec(compile(source, APP, "exec"), ns)
    return ns["_with_back"]


class _StubWrap:
    made = None
    def __init__(self, on_back, on_home):
        self.on_back, self.on_home = on_back, on_home
        _StubWrap.made = self
    def add_content(self, widget):
        self.content = widget


def _app():
    switched = []
    app = types.SimpleNamespace(switch_mode=switched.append)
    return app, switched


def test_with_back_hands_the_wrapper_both_callbacks():
    with_back = _load_with_back()
    with_back.__globals__["_BackSwipeWrap"] = _StubWrap
    app, _ = _app()
    widget = object()
    out = with_back(app, widget)
    assert out is _StubWrap.made and out.content is widget
    assert callable(out.on_back) and callable(out.on_home)


def test_home_switches_to_home_and_nothing_else():
    """A plain screen: no confirmation popup. Leaving during a flash never
    interrupts it (the activity counter and busy marker keep that true)."""
    with_back = _load_with_back()
    with_back.__globals__["_BackSwipeWrap"] = _StubWrap
    app, switched = _app()
    with_back(app, object()).on_home()
    assert switched == ["home"]


def test_home_takes_the_screens_own_exit_road_when_it_has_one():
    """The birth guide's Exit does more than switch screens: it cancels the
    late-resume hook and kills its polls, and warns when a build is mid-flight
    (2026-08-14, the door that cannot be trapped shut). A bare switch from the
    bar would leave those armed — a card write finishing late would drag the
    operator back into a walkthrough they had left. Same protocol as
    handle_back: a screen that defines handle_home() owns its own leaving."""
    with_back = _load_with_back()
    with_back.__globals__["_BackSwipeWrap"] = _StubWrap
    app, switched = _app()
    left = []
    guide = types.SimpleNamespace(handle_home=lambda: left.append("exit"))
    with_back(app, guide).on_home()
    assert left == ["exit"]
    assert switched == [], "the screen decides how it leaves; the bar does not double up"


def test_the_birth_guide_leaves_by_its_own_exit_from_the_bar():
    src = open(os.path.join(ROOT, "ui/screens/birth_guide_screen.py")).read()
    m = re.search(r"    def handle_home\(self\):.*?(?=\n    def |\Z)", src, re.S)
    assert m, "the guide must offer the bar its Exit road"
    assert "_exit_tapped()" in m.group(0)


def test_back_steps_one_page_inside_a_flow_before_falling_through_to_home():
    with_back = _load_with_back()
    with_back.__globals__["_BackSwipeWrap"] = _StubWrap
    app, switched = _app()
    flow = types.SimpleNamespace(handle_back=lambda: True)
    with_back(app, flow).on_back()
    assert switched == [], "the flow stepped back a page; the app stays put"
    root = types.SimpleNamespace(handle_back=lambda: False)
    with_back(app, root).on_back()
    assert switched == ["home"]
    plain = object()
    with_back(app, plain).on_back()
    assert switched == ["home", "home"]


def test_every_registered_screen_is_wrapped_except_home_and_the_wizard():
    """'every screen' (operator). Home IS the destination; the setup wizard
    must not offer home before the operator has seen it (its own test pins
    that). cert_view and node_detail are filled on demand, wrapped there."""
    text = src(APP)
    unwrapped = []
    for m in re.finditer(r'Screen\(name="([a-z_]+)"\)', text):
        name = m.group(1)
        if name in ("home", "setup", "cert_view", "node_detail"):
            continue
        block = text[m.end():]
        block = block[:block.index("self.sm.add_widget(")]
        if "_with_back(" not in block:
            unwrapped.append(name)
    assert not unwrapped, unwrapped
    assert "_with_back(" in func_source(APP, "_open_cert")
    assert "_with_back(" in func_source(APP, "_open_node_detail")


def test_the_home_screen_itself_carries_no_bar():
    text = src(APP)
    block = text[text.index('home = Screen(name="home")'):]
    block = block[:block.index("self.sm.add_widget(home)")]
    assert "_with_back" not in block


# ---- i18n -----------------------------------------------------------------

def test_home_has_a_key_in_every_full_parity_catalog():
    for code in ("de", "es", "fr", "id", "ja", "pl", "ru", "sv"):
        path = os.path.join(ROOT, "assets", "i18n", f"{code}.json")
        with open(path, encoding="utf-8") as fh:
            catalog = json.load(fh)
        assert catalog.get("Home"), f"{code}.json has no 'Home'"
