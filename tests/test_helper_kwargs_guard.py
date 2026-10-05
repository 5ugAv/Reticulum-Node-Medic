"""A screen helper called with a keyword it does not take raises TypeError
inside a Kivy callback and kills the app. That is how the clone screen came to
crash on its copy page (`_label(..., markup=True)`, caught by review before it
reached a medic, 2026-10-06). No test can import a Kivy screen, so check every
call to a file's own module-level helper against that helper's signature."""
import ast
import pathlib


def _check(path):
    tree = ast.parse(path.read_text())
    sigs = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            a = node.args
            names = {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs}
            sigs[node.name] = (names, a.kwarg is not None)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            sig = sigs.get(node.func.id)
            if not sig or sig[1]:
                continue
            for kw in node.keywords:
                if kw.arg and kw.arg not in sig[0]:
                    bad.append(f"{path}:{node.lineno} {node.func.id}({kw.arg}=)")
    return bad


def test_no_screen_calls_its_own_helper_with_an_unknown_keyword():
    bad = []
    for p in sorted(pathlib.Path("ui").rglob("*.py")):
        bad += _check(p)
    assert not bad, "\n".join(bad)
