"""A class must never define a method AND assign an instance attribute of
the same name: the attribute wins and the method is unreachable.

2026-09-22, live on the medic: SlideToPowerOff gained a geometry method
``_track()`` while ``self._track`` was already its canvas RoundedRectangle.
The suite is green (no Kivy window here, so the widget is never built),
the PIL preview drew the geometry without running it, and the first real
layout raised "RoundedRectangle is not callable" — the front page died on
deploy. This reads every class under ui/ and refuses the pattern.
"""
import ast
import os

from tests.srcutil import ROOT


def _collisions(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        methods = {n.name for n in cls.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assigned = set()
        for node in ast.walk(cls):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            # Only a DIRECT rebinding counts: `self.x = …` or a tuple of
            # them. `self.x[k] = …` and `self.x.y = …` mutate x, not bind it.
            flat = []
            for t in targets:
                flat += list(t.elts) if isinstance(t, (ast.Tuple, ast.List)) else [t]
            for t in flat:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    assigned.add(t.attr)
        for name in sorted(methods & assigned):
            out.append(f"{os.path.relpath(path, ROOT)}: {cls.name}.{name}")
    return out


def test_no_method_is_shadowed_by_an_attribute():
    bad = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "ui")):
        for f in files:
            if f.endswith(".py"):
                bad += _collisions(os.path.join(base, f))
    assert not bad, "method shadowed by an instance attribute:\n" + "\n".join(bad)
