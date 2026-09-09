"""Names used INSIDE functions must exist too.

Twice in one day (2026-09-09) a name that does not exist shipped to the medic
and took the whole application down, and the suite could not see either one:

  * a module-level ``re.compile()`` whose ``import re`` never landed — caught
    now by test_module_level_names;
  * ``guided=guided_birth_pending())`` inside two methods of birth_screen.
    ``guided_birth_pending`` is a method on the APP, and every other caller in
    that file reaches it through ``getattr(app, ...)``. Called bare it is a
    NameError — raised the instant the operator pressed "OK — start", killing
    the app in the middle of their build.

No test in this project can import a Kivy screen, so nothing executes those
lines until the operator does. pyflakes would find both, but this tool is
offline-first and carries no lint dependency, so the check is written here
against the AST.

Scope handling is deliberately conservative: a name counts as defined if it is
a builtin, bound anywhere at module level, bound anywhere in the function or
any enclosing function (parameters, assignments, imports, for/with/except
targets, comprehension variables, walrus, global/nonlocal declarations), or
the module uses a star-import (in which case the file is skipped entirely). It
reports what it cannot explain rather than trying to be clever — a false
positive here is a comment away from being an explicit exemption, and a false
negative is an application that dies in the operator's hands.
"""
import ast
import builtins
import os

import pytest

_ROOTS = ("ui", "workflows", "monitor", "provisioning", "scripts")

#: Names that legitimately appear undefined to a static reader.
_ALLOWED = {"__file__", "__name__", "__doc__", "__path__", "__spec__",
            "_", "reveal_type"}


def _modules():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in _ROOTS:
        d = os.path.join(here, root)
        if not os.path.isdir(d):
            continue
        for base, _dirs, files in os.walk(d):
            if "__pycache__" in base:
                continue
            for fn in sorted(files):
                if fn.endswith(".py"):
                    yield os.path.join(base, fn)


def _bound_in(node, include_nested=False):
    """Every name *node* binds in its own scope."""
    out = set()

    def walk(n, top=False):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                out.add(child.name)
                if include_nested:
                    walk(child)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for a in child.names:
                    out.add((a.asname or a.name).split(".")[0])
            elif isinstance(child, ast.Name) and isinstance(child.ctx,
                                                            ast.Store):
                out.add(child.id)
            elif isinstance(child, ast.arg):
                out.add(child.arg)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                out.update(child.names)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                out.add(child.name)
            walk(child)

    walk(node, top=True)
    return out


def _scopes(tree):
    """Yield (function node, names visible from inside it)."""
    module_names = _bound_in(tree, include_nested=False)
    module_names |= {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))}

    def visit(node, visible):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                own = visible | _bound_in(child, include_nested=True)
                own |= {a.arg for a in ast.walk(child)
                        if isinstance(a, ast.arg)}
                yield child, own
                yield from visit(child, own)
            else:
                yield from visit(child, visible)

    yield from visit(tree, module_names)


def _loads(node):
    """Names READ inside *node*, excluding nested function bodies (those are
    checked as their own scope, with their own visibility)."""
    out = []

    def walk(n, top=False):
        for child in ast.iter_child_nodes(n):
            if not top and isinstance(child, (ast.FunctionDef,
                                              ast.AsyncFunctionDef,
                                              ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append(child)
            walk(child)

    walk(node, top=True)
    return out


@pytest.mark.parametrize("path", list(_modules()),
                         ids=lambda p: os.path.relpath(p, os.getcwd()))
def test_no_undefined_names(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, path)
    if any(a.name == "*" for n in ast.walk(tree)
           if isinstance(n, ast.ImportFrom) for a in n.names):
        pytest.skip("star import — what is in scope cannot be read statically")
    known_builtins = set(dir(builtins)) | _ALLOWED
    bad = []
    for fn, visible in _scopes(tree):
        for name in _loads(fn):
            if name.id in visible or name.id in known_builtins:
                continue
            bad.append(f"{name.id} (line {name.lineno})")
    assert not bad, (f"{os.path.relpath(path)} reads names that are not "
                     f"defined: {sorted(set(bad))[:6]}")
