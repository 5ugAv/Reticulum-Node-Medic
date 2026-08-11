"""The readability type scale, and the rule that keeps it from clipping anything.

Background (operator, 2026-08-05): "the font is too small for old eyes" — the
5" panel renders a nominal 15sp body line at roughly 5.5 pt on the glass. The
fix was a compressive scale in ``ui.theme`` plus a hard rule that every row grew
with its text. The constraint was equally explicit: "make sure nothing gets cut
off or squashed or forces a screen to scroll."

Nobody can screenshot the device (it renders through KMS/DRM; /dev/fb0 is a
black stub), so these tests are how the layout is checked at all. They are
source-inspection tests for the usual reason — Kivy is not importable in CI, and
the suite's process-global stubs only cover the submodules already in use.
"""

from __future__ import annotations

import ast
import math

import pytest

from ui import theme
from tests.srcutil import src

#: Every ui/ module this change routed through the type scale. Anything not on
#: this list still carries raw "Nsp" literals ON PURPOSE — hand-positioned
#: animation callouts, map overlays and the fixed-width VITALS pills could not be
#: enlarged without seeing them render. Adding a file here means it must satisfy
#: the two container rules below.
SCALED_MODULES = [
    "ui/screens/about_screen.py",
    "ui/screens/birth_guide_screen.py",
    "ui/screens/birth_screen.py",
    "ui/screens/cert_view_screen.py",
    "ui/screens/comms_screen.py",
    "ui/screens/datetime_screen.py",
    "ui/screens/language_screen.py",
    "ui/screens/mitosis_screen.py",
    "ui/screens/node_detail_screen.py",
    "ui/screens/notifications_screen.py",
    "ui/screens/pi_imager_screen.py",
    "ui/screens/probe_screen.py",
    "ui/screens/radio_defaults_screen.py",
    "ui/screens/recovery_key_screen.py",
    "ui/screens/self_diagnose_screen.py",
    "ui/screens/settings_screen.py",
    "ui/screens/storage_screen.py",
    "ui/screens/tool_identity_screen.py",
    "ui/screens/trusted_operators_screen.py",
    "ui/screens/vault_unlock_screen.py",
    "ui/screens/wifi_screen.py",
    "ui/widgets/guide_content.py",
]

#: The per-module label helper: every body string on that screen goes through it.
HELPERS = ("_line", "_lbl", "_label", "_wrap")

#: Helpers whose height is max(dp(h), texture_height) — h is a floor they grow
#: past, so their text cannot be clipped however big it gets.
AUTOGROW_MODULES = {
    "ui/screens/birth_guide_screen.py",
    "ui/screens/comms_screen.py",
    "ui/screens/birth_screen.py",
    "ui/screens/mitosis_screen.py",
    "ui/screens/probe_screen.py",
    "ui/widgets/guide_content.py",
}

#: Design sizes still written as literals anywhere under ui/.
DESIGN_SIZES = [10, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 15.5, 16, 16.5,
                17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 32, 33, 40]


# -- the scale itself -------------------------------------------------------

def test_scale_never_shrinks_any_size():
    """No text may come out smaller than it is today — that is the whole point."""
    for s in DESIGN_SIZES:
        assert theme.type_scale(s) >= s, s


def test_scale_is_monotonic():
    """A bigger design size must never map to a smaller shipped one, or the
    visual hierarchy inverts (a caption outgrowing its heading)."""
    out = [theme.type_scale(s) for s in DESIGN_SIZES]
    assert out == sorted(out)


def test_nothing_ships_below_the_readable_floor():
    for s in DESIGN_SIZES:
        assert theme.type_scale(s) >= theme.FONT_MIN_SP, s


def test_the_smallest_text_gets_the_biggest_lift():
    """Compressive, not a flat multiplier: small text is what could not be read,
    and it is the cheap text to grow. Display text stays put so no screen that
    fits today is pushed into scrolling."""
    assert theme.type_scale(10) / 10 > theme.type_scale(15) / 15
    assert theme.type_scale(15) / 15 > theme.type_scale(22) / 22
    for s in (25, 26, 27, 30, 32, 40):
        assert theme.type_scale(s) == s, f"{s}sp is display type and must not grow"


def test_body_text_still_fits_the_commonest_row():
    """dp(24) is the most-used row height in the codebase. 15sp is the most-used
    body size (75 call sites). Keeping line_dp(15) <= 24 is what lets the bump
    cost nothing on those rows — if this fails the scale got too aggressive and
    a lot of screens are about to grow."""
    assert theme.line_dp(15) <= 24


def test_font_sp_returns_a_kivy_size_string():
    assert theme.font_sp("13sp") == f"{theme.type_scale(13):g}sp"
    assert theme.font_sp(13) == theme.font_sp("13sp") == theme.font_sp("13")


def test_line_dp_bounds_the_real_font_height():
    """Kivy gives a one-line Label a texture height of the face's
    (ascent - descent). Measured off the bundled Roboto that peaks at 1.308 of
    the size, and off RobotoMono at 1.419 — line_dp must sit above both or a row
    sized by it clips."""
    assert theme.LINE_HEIGHT_RATIO > 1.308
    assert theme.MONO_LINE_HEIGHT_RATIO > 1.419
    for s in DESIGN_SIZES:
        assert theme.line_dp(s) >= theme.type_scale(s) * 1.308
        assert theme.line_dp(s, mono=True) > theme.line_dp(s)


def test_panel_budget_matches_the_hardware():
    """1280x720 panel at KIVY_METRICS_DENSITY=1.5 (scripts/start_ui.sh). A screen
    with no ScrollView has to fit its fixed heights inside PANEL_H_DP."""
    assert theme.PANEL_H_DP == pytest.approx(480.0)
    assert theme.PANEL_W_DP == pytest.approx(853.333, rel=1e-3)


# -- the container rules ----------------------------------------------------

def _helper_signatures(tree):
    """{name: ([param names in order], {param: default})} for the label helpers."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS:
            names = [a.arg for a in node.args.args]
            defaults = {}
            for arg, dflt in zip(node.args.args[len(names) - len(node.args.defaults):],
                                 node.args.defaults):
                defaults[arg.arg] = dflt.value if isinstance(dflt, ast.Constant) else None
            out[node.name] = (names, defaults)
    return out


def _const(node):
    """A literal, or an ``N * theme.line_dp("Xsp")`` height expression."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("line_dp", "type_scale") and node.args:
        arg = _const(node.args[0])
        if arg is None:
            return None
        kw = {k.arg: _const(k.value) for k in node.keywords}
        return getattr(theme, node.func.attr)(arg, **{k: v for k, v in kw.items()
                                                      if v is not None})
    if isinstance(node, ast.BinOp):
        a, b = _const(node.left), _const(node.right)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return None
        return {ast.Mult: a * b, ast.Add: a + b}.get(type(node.op))
    return None


def _bind(call, names, defaults):
    bound = dict(defaults)
    for i, a in enumerate(call.args):
        v = _const(a)
        if i < len(names) and v is not None:
            bound[names[i]] = v
    for kw in call.keywords:
        v = _const(kw.value)
        if v is not None:
            bound[kw.arg] = v
    return bound


@pytest.mark.parametrize("rel", SCALED_MODULES)
def test_helper_routes_its_size_through_the_scale(rel):
    """The chokepoint. Each screen builds its text through one helper, so one
    line there enlarges the whole screen — and is the only place that can."""
    text = src(rel)
    tree = ast.parse(text)
    sigs = _helper_signatures(tree)
    assert sigs, f"{rel} has no label helper — did it get renamed?"
    for name in sigs:
        body = ast.get_source_segment(text, next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == name))
        assert "theme.font_sp(size)" in body, (
            f"{rel}:{name}() sets font_size from the raw design size — it must "
            f"go through theme.font_sp() or this screen stays unreadable")
        assert "font_size=size" not in body, f"{rel}:{name}() still passes size raw"


@pytest.mark.parametrize("rel", SCALED_MODULES)
def test_helper_grows_its_row_with_the_text(rel):
    """THE regression this whole change had to avoid: enlarging text inside a
    fixed-height container without enlarging the container in the same edit.

    Every helper that pins its own height must derive it as
    ``max(<the caller's h>, theme.line_dp(size))`` — the row can then never be
    shorter than one enlarged line, whatever the call site asked for, and never
    shorter than it is today either.
    """
    if rel in AUTOGROW_MODULES:
        pytest.skip("height is max(dp(h), texture) — the row grows past h")
    text = src(rel)
    tree = ast.parse(text)
    for name in _helper_signatures(tree):
        body = ast.get_source_segment(text, next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == name))
        pins = [ln for ln in body.splitlines()
                if "height=dp(" in ln or ".height = dp(" in ln]
        if not pins:
            continue        # parent- or texture-driven: no height of its own to grow
        for ln in pins:
            assert "max(" in ln and "theme.line_dp(size" in ln, (
                f"{rel}:{name}() pins a height that does not follow the font "
                f"size — bigger text in a fixed row is exactly how the last "
                f"attempt clipped: {ln.strip()}")


@pytest.mark.parametrize("rel", SCALED_MODULES)
def test_labels_in_fixed_height_rows_still_fit(rel):
    """The other clipping shape: a label with NO height of its own dropped into
    a ``BoxLayout(height=dp(N))``. It stretches to the row, its text_size takes
    that height, and anything taller is cut."""
    tree = ast.parse(src(rel))
    sigs = _helper_signatures(tree)
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        rows = {}
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Name)
                    and n.value.func.id == "BoxLayout"
                    and n.targets and isinstance(n.targets[0], ast.Name)):
                continue
            h = vertical = None
            for kw in n.value.keywords:
                if kw.arg == "height" and isinstance(kw.value, ast.Call) \
                        and getattr(kw.value.func, "id", "") == "dp":
                    h = _const(kw.value.args[0])
                if kw.arg == "orientation":
                    vertical = _const(kw.value) == "vertical"
            if h and not vertical:          # a vertical box stacks, it doesn't stretch
                rows[n.targets[0].id] = h
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "add_widget"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in rows and n.args):
                continue
            call = n.args[0]
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in sigs):
                continue
            names, defaults = sigs[call.func.id]
            bound = _bind(call, names, defaults)
            if bound.get("h") is not None:
                continue                    # sizes itself; covered by the test above
            size = bound.get("size", "15sp")
            need = theme.line_dp(size, mono=bool(bound.get("mono")))
            assert rows[n.func.value.id] >= need, (
                f"{rel}:{n.lineno} label takes its height from a dp("
                f"{rows[n.func.value.id]}) row but {size} now needs dp({need})")


def test_scaled_modules_all_exist():
    for rel in SCALED_MODULES:
        assert src(rel)
    assert AUTOGROW_MODULES <= set(SCALED_MODULES)


def test_a_label_row_never_shrinks_to_exactly_one_line():
    """Inside a label helper the rule is ``max(h, line_dp(size))``: rows grow,
    never shrink. A bare ``dp(line_dp(size))`` there would SHRINK a deliberately
    roomy row down to the text and re-create the cramped look the operator
    complained about. (Sizing a *container* to one line, as the storage rows do,
    is a different and deliberate thing — this only polices the helpers.)"""
    for rel in SCALED_MODULES:
        if rel in AUTOGROW_MODULES:
            continue
        text = src(rel)
        tree = ast.parse(text)
        for name in _helper_signatures(tree):
            body = ast.get_source_segment(text, next(
                n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name))
            for line in body.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if "theme.line_dp" in line:
                    assert "max(" in line, f"{rel}:{name}(): {line.strip()}"


def test_line_dp_is_a_whole_number_of_dp():
    """Kivy rounds fractional widget heights unpredictably across platforms;
    a whole dp keeps the arithmetic in the budget tests exact."""
    for s in DESIGN_SIZES:
        assert theme.line_dp(s) == math.floor(theme.line_dp(s))
