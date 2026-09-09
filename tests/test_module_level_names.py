"""Names used at MODULE level must actually exist there.

Live, 2026-09-09: a module-level ``re.compile(...)`` was added to
``ui/screens/birth_screen.py`` and the ``import re`` that was meant to
accompany it silently did not land. The file still compiled, every test still
passed — the suite cannot import Kivy modules, so nothing ever executed the
module body — and the UI died at startup on the medic with ``NameError: name
're' is not defined``.

A NameError at module scope takes the whole application down before its first
frame. This walks each module's top level with the AST and checks that every
name it reads is imported, assigned, defined, or a builtin. It needs no
imports of its own, so it covers the Kivy screens the rest of the suite
cannot touch.
"""
import ast
import builtins
import os

import pytest

_ROOTS = ("ui", "workflows", "monitor", "provisioning")


def _modules():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in _ROOTS:
        for base, _dirs, files in os.walk(os.path.join(here, root)):
            if "__pycache__" in base:
                continue
            for fn in sorted(files):
                if fn.endswith(".py"):
                    yield os.path.join(base, fn)


def _bound_and_used(tree):
    """(names bound at module level, names read at module level).

    Function and class BODIES are skipped: those run later, by which time an
    import lower in the file has happened. Decorators, default arguments and
    base classes are NOT skipped — they evaluate at definition time.
    """
    bound, used = set(), set()

    def read(node):
        _scan(node, set(), used)

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            bound.add(node.name)
            for d in node.decorator_list:
                read(d)
            if isinstance(node, ast.ClassDef):
                for b in node.bases:
                    read(b)
            else:
                for d in node.args.defaults + [
                        d for d in node.args.kw_defaults if d]:
                    read(d)
        else:
            _scan(node, bound, used)
    return bound, used


#: Nodes that open a NEW scope. Their bodies run later (or never), so a name
#: read inside one is not a module-level read — walking into them reported
#: every lambda parameter in a module-level table as an unbound name.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _scan(node, bound, used):
    """Walk one module-level statement, pruning at every nested scope."""
    if isinstance(node, _SCOPES):
        return
    if isinstance(node, ast.Name):
        (bound if isinstance(node.ctx, ast.Store) else used).add(node.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            bound.add((a.asname or a.name).split(".")[0])
    for child in ast.iter_child_nodes(node):
        _scan(child, bound, used)


@pytest.mark.parametrize("path", list(_modules()),
                         ids=lambda p: os.path.relpath(p, os.getcwd()))
def test_module_level_names_resolve(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    bound, used = _bound_and_used(tree)
    known = bound | set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    missing = sorted(n for n in used if n not in known)
    assert not missing, (f"{os.path.basename(path)} reads {missing} at module "
                         "level with nothing to bind them — the app would die "
                         "on import")
