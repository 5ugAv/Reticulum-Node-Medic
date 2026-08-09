"""An animation that crashes on render is a black screen.

Live, 2026-08-09: the new cable-provisioning animation used Ellipse, which was
never imported in birth_anims.py. Every source-inspection test passed — they
check that the right words are in the file, and NameError is precisely the case
where the right word is in the file and nothing is behind it. The operator's
screen went black on the step the animation belongs to, and the only trace was
a traceback in ui.log.

The offline PIL preview did not catch it either: PIL has its own ellipse, so
the drawing looked right while the Kivy path was broken. Previewing GEOMETRY is
not the same as running the CODE.
"""
import ast
import pathlib

#: Drawing primitives that live in kivy.graphics. Using one without importing
#: it is a NameError at draw time — i.e. at the worst possible moment.
PRIMITIVES = {
    "Color", "Ellipse", "Line", "Rectangle", "RoundedRectangle", "Quad", "Mesh",
    "PushMatrix", "PopMatrix", "Rotate", "Scale", "Translate", "Point", "Bezier",
    "StencilPush", "StencilPop", "StencilUse", "StencilUnUse", "Callback",
}

WIDGETS = sorted(pathlib.Path("ui/widgets").glob("*.py"))


def _imported_graphics_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("kivy.graphics"):
            names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
    return names


def _called_primitives(tree):
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in PRIMITIVES:
            used.add(node.func.id)
    return used


def test_every_drawing_primitive_used_is_imported():
    problems = []
    for path in WIDGETS:
        tree = ast.parse(path.read_text())
        missing = _called_primitives(tree) - _imported_graphics_names(tree)
        # a name bound locally (from kivy.graphics import X inside a function)
        # counts too — walk() already saw those ImportFroms, so anything left
        # really is unbound.
        if missing:
            problems.append(f"{path.name}: {sorted(missing)}")
    assert not problems, "drawing names used but never imported: " + "; ".join(problems)


def test_the_one_that_blacked_out_the_screen():
    """Ellipse in birth_anims, specifically — the regression this file exists
    for. Kept as its own case so a rename of the sweep above cannot quietly
    stop covering it."""
    tree = ast.parse(pathlib.Path("ui/widgets/birth_anims.py").read_text())
    assert "Ellipse" in _called_primitives(tree), "the bolus animation draws them"
    assert "Ellipse" in _imported_graphics_names(tree)
