"""The poster's settled vocabulary is the ONLY vocabulary the operator reads.

The 2026-09-13 repaint (docs/FRONT_PAGE_BRIEF.md) renamed three cards:
SCAN -> MAPS, BIRTH -> BUILD, TRIAGE -> ANTENNA. The screens keep their
internal names — ``ui.home_zones.POSTER_WORD_FOR`` is the one place the
painted word and the screen key are tied. What must not survive is PROSE:
a sentence telling the operator to "check it in TRIAGE" now points at a
word that exists nowhere on the glass, which is the wrong-picture failure
in words. These tests grep the user-facing string sources for the dead
words so the old vocabulary cannot creep back one helpful sentence at a
time.

Deliberately NOT policed, to keep the grep quiet enough to trust:

* python identifiers, screen keys ("scan", "birth", "triage"), file names —
  those are code, the operator never reads them;
* docstrings and comments — the file's own voice, not the screen's;
* log lines (``print`` calls) — evidence for the bench, not the operator;
* the verbs "birth" / "scan" and the noun "birth certificate" — project
  language older than the poster, still true ("Birth it from BUILD");
* ``RETICULUM NODE — BIRTH CERTIFICATE`` in ui/qr.py — the document's own
  painted title, embedded in already-issued QR payloads. Renaming it would
  orphan every certificate printed before 2026-09-13.
"""

import ast
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The dead words, as the operator would read them: the all-caps card words,
# plus capitalised "Triage" which — unlike "Scan" and "Birth" — was never a
# verb in this project, only ever the mode's name.
DEAD = re.compile(r"\b(SCAN|BIRTH|TRIAGE|Triage)\b")

#: Exact strings the rule stands back from, each for a stated reason above.
ALLOWED = {
    "RETICULUM NODE — BIRTH CERTIFICATE",
}

#: Every package whose strings can reach the glass. tests/, scripts/ and
#: docs/ are for people reading the repo, not the operator.
USER_FACING_TREES = ("ui", "monitor", "diagnostics", "workflows",
                     "provisioning", "transport", "sandbox")


def _display_strings(path):
    """Non-docstring, non-print string constants in one file, with line nos."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    skip = set()
    for node in ast.walk(tree):
        # docstrings: the first statement-expression string of a scope
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                skip.add(id(body[0].value))
        # log lines: anything inside a print(...) call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "print":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    skip.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in skip:
            yield node.lineno, node.value


def test_no_display_string_speaks_the_dead_words():
    offenders = []
    for tree_name in USER_FACING_TREES:
        for base, _dirs, files in os.walk(os.path.join(REPO, tree_name)):
            if "__pycache__" in base:
                continue
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(base, fn)
                for lineno, s in _display_strings(path):
                    if s in ALLOWED:
                        continue
                    if DEAD.search(s):
                        rel = os.path.relpath(path, REPO)
                        offenders.append(f"{rel}:{lineno}: {s[:80]!r}")
    assert not offenders, (
        "operator-facing strings still speak the pre-repaint words "
        "(SCAN/BIRTH/TRIAGE -> MAPS/BUILD/ANTENNA, docs/FRONT_PAGE_BRIEF.md):\n"
        + "\n".join(offenders))


def test_no_catalog_carries_the_dead_words():
    """The catalogs are keyed by the English source, so a dead word in a KEY
    is a dead word still on some screen; one in a VALUE is a translator being
    told to keep pointing at a card that no longer exists."""
    i18n_dir = os.path.join(REPO, "assets", "i18n")
    offenders = []
    for fn in sorted(os.listdir(i18n_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(i18n_dir, fn), encoding="utf-8") as f:
            catalog = json.load(f)
        if isinstance(catalog, list):      # _critical.json: a bare key list
            catalog = {key: key for key in catalog}
        for key, value in catalog.items():
            for role, s in (("key", key), ("value", value)):
                if s in ALLOWED:
                    continue
                if DEAD.search(s):
                    offenders.append(f"{fn} {role}: {s[:80]!r}")
    assert not offenders, (
        "translation catalogs still carry the pre-repaint words:\n"
        + "\n".join(offenders))


def test_tour_titles_open_with_the_painted_word():
    """The first-use tour shows the ACTUAL painted card (cropped via
    home_zones.card_rect) next to its title — so a title that opens with any
    other word than the one in the crop contradicts the picture beside it."""
    from ui.home_zones import POSTER_WORD_FOR
    from ui import setup_flow as sf
    for step in sf._TOUR_STEPS:
        zone = step.get("poster_card")
        if not zone:
            continue
        word = POSTER_WORD_FOR[zone]
        assert step["title"].startswith(word), (
            f"tour step {step['key']!r} shows the painted {word} card but its "
            f"title opens {step['title']!r}")


def test_sidebar_speaks_the_painted_words():
    """The sidebar is the poster's card row in another dress: same screens,
    so the same words, from the same single source of truth."""
    from ui.home_zones import POSTER_WORD_FOR
    from ui.widgets.sidebar import MODES
    for key, label in MODES:
        if key in POSTER_WORD_FOR:
            assert label == POSTER_WORD_FOR[key], (
                f"sidebar labels {key!r} as {label!r}; the poster paints "
                f"{POSTER_WORD_FOR[key]!r}")
