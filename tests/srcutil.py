"""Shared source-inspection helpers for the test suite.

Two problems this fixes, both raised by the 2026-08-03 audit:

1. **Fixed character windows.** Source-inspection tests sliced ``SRC[i:i+1400]``
   to isolate a function. Those windows went stale twice as the code around
   them grew — and they fail OPEN: the assertion quietly stops covering the
   function instead of breaking. ``func_source`` parses the file and returns
   exactly the function asked for, at any length, and raises loudly if it is
   renamed.

2. **cwd-relative reads.** ~35 tests did ``open("ui/screens/foo.py")``, so the
   suite only ran from the repo root. ``src`` anchors on this file.

Named ``srcutil`` rather than ``conftest`` because the repo root already has a
conftest.py (it puts the project root on sys.path), and a second one would be
imported under the same module name.

Source inspection is legitimate here: Kivy is not importable in CI, and the
suite installs process-global stubs covering only the submodules already in
use, so importing a screen to reach one function breaks collection for
everything after it.
"""

from __future__ import annotations

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(rel_path: str) -> str:
    """The text of a repo file, wherever pytest was invoked from."""
    with open(os.path.join(ROOT, rel_path), encoding="utf-8") as fh:
        return fh.read()


def func_source(rel_path: str, name: str, cls: str = "") -> str:
    """The source of one function/method, located by NAME rather than offset.

    Exact regardless of length or reordering. Raises if *name* is absent, so a
    rename fails loudly instead of silently covering nothing.

    *cls* scopes the search to one class. Without it the FIRST match wins,
    which is silently wrong in a module of siblings: birth_anims.py holds a
    dozen ``_draw`` methods, so a test meaning ConnectPiAnim's asserted against
    whichever animation happened to be defined first.
    """
    text = src(rel_path)
    tree = ast.parse(text)
    if cls:
        holder = next((n for n in ast.walk(tree)
                       if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if holder is None:
            raise AssertionError(
                f"class {cls} not found in {rel_path} — renamed or removed?")
        for node in holder.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == name:
                seg = ast.get_source_segment(text, node)
                if seg:
                    return seg
        raise AssertionError(
            f"{cls}.{name}() not found in {rel_path} — renamed or removed?")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            seg = ast.get_source_segment(text, node)
            if seg:
                return seg
    raise AssertionError(f"{name}() not found in {rel_path} — renamed or removed?")
