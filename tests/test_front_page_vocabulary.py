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

import pytest
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


# --- the two emblem zones must sit on their painted emblems -----------------
# These drifted silently through five repaints between 2026-09-13 and
# 2026-09-28. By the end the credits circle had wandered off the retired leaf
# onto blank mesh, and then — once the bearers moved onto the centre meridian —
# onto the INTERNET marker, so tapping "INTERNET" opened the credits screen.
# Nothing caught it but a human looking at a picture.
#
# The card labels have been pinned against the artwork since 2026-08-07. These
# do the same for the two emblems: assert that the zone lands on lit ink where
# the emblem is painted, and NOT on the markers either side of it. It is a
# claim about the shipped PNG, so a repaint that moves an emblem fails here
# instead of shipping a tap that opens the wrong screen.

def _poster_gray():
    Image = pytest.importorskip("PIL.Image")
    path = os.path.join(REPO, "assets", "ui", "front_page.png")
    if not os.path.exists(path):                # gitignored on CI
        return None
    im = Image.open(path).convert("L")
    assert im.size == (720, 1280), "the zones are fractions of a 720x1280 cut"
    return im


def test_the_credits_zone_sits_on_the_lora_trunk_node():
    """The Easter egg opens the credits; it must land on the painted disc."""
    from ui.home_zones import CROSS_CX, CROSS_CY, zone_at
    assert zone_at(CROSS_CX, CROSS_CY) == "credits"
    im = _poster_gray()
    if im is None:
        return
    # Sample the DISC, not one pixel: the tower mark is knocked out in dark at
    # the disc's centre, so the middle pixel is legitimately black.
    np = pytest.importorskip("numpy")
    cx, cy = int(CROSS_CX * 720), int(CROSS_CY * 1280)
    disc = np.asarray(im, dtype=float)[cy - 20:cy + 20, cx - 20:cx + 20]
    lit = (disc > 150).mean()
    assert lit > 0.35, (
        f"only {lit:.0%} of the credits zone centre is lit — it has drifted "
        "off the LORA trunk node again")


def test_the_wifi_zone_sits_on_the_wifi_marker():
    from ui.home_zones import (WIFI_BOTTOM, WIFI_LEFT, WIFI_RIGHT, WIFI_TOP,
                               zone_at)
    cx, cy = (WIFI_LEFT + WIFI_RIGHT) / 2, (WIFI_TOP + WIFI_BOTTOM) / 2
    assert zone_at(cx, cy) == "wifi"
    im = _poster_gray()
    if im is None:
        return
    np = pytest.importorskip("numpy")
    box = np.asarray(im, dtype=float)[int(WIFI_TOP * 1280):int(WIFI_BOTTOM * 1280),
                                      int(WIFI_LEFT * 720):int(WIFI_RIGHT * 720)]
    assert (box > 150).mean() > 0.04, (
        "the WI-FI zone is mostly empty globe — it has drifted off the marker")


def test_no_other_bearer_marker_is_tappable():
    """BLUETOOTH and INTERNET are painted, labelled and inert.

    This is the assertion that would have caught the 2026-09-28 bug: with the
    old credits circle at (0.50, 0.46) the INTERNET marker opened the credits.
    A marker that looks exactly like the two that DO something, and silently
    does something unrelated, is the wrong-picture failure at its worst.
    """
    from ui.home_zones import zone_at
    for name, (fx, fy) in {"BLUETOOTH": (0.500, 0.3445),
                           "INTERNET": (0.500, 0.4688)}.items():
        assert zone_at(fx, fy) is None, (
            f"the {name} marker now opens {zone_at(fx, fy)} — a painted label "
            "that silently does something else")


def test_the_card_row_zone_meets_the_drawn_keys():
    """CARDS_TOP is measured off the painting, not chosen.

    The drawn key tops wandered 1006 / 1003 / 1005 across three repaints while
    the zone sat at 1011, leaving the top 0.6 mm of every key drawn but dead.
    The zone was moved to meet the art; this checks it still does — the row
    immediately below CARDS_TOP must carry key ink, and the row well above it
    must not.
    """
    from ui.home_zones import CARDS_TOP
    im = _poster_gray()
    if im is None:
        return
    np = pytest.importorskip("numpy")
    a = np.asarray(im, dtype=float)
    y = int(CARDS_TOP * 1280)
    assert (a[y + 2] > 60).sum() > 150, "the card zone starts above the keys"
    assert (a[y - 25] > 60).sum() < 150, "the card zone has eaten into the title"
