"""No function may be defined and then referenced nowhere.

The module-level guard in test_wiring.py catches a whole FILE nobody imports.
It does not catch a function inside a live file that nothing calls — and that is
where this project's real failures have hidden:

  * ``effective_bits`` computed the recovery key's cap on vault strength and had
    zero callers, so the cap was never shown.
  * ``can_cable`` (2026-08-xx) was an impossibility check nothing ever ran.
  * ``show_boards`` listed every flashable board in BIRTH and was unreachable —
    and a test named "guards the wiring, not just the data" was asserting it
    rendered correctly, reporting two working board pickers where there was one.

A dead function is worse than missing code: it reads as a working feature to
the next person, and tests written against it give false confidence.

The allowlist below is for names a static pass cannot see being used —
framework callbacks and overrides. Anything added needs a reason that is TRUE.
"""
import ast
import collections
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", ".claude", "assets", "firmware", "docs",
             "tools", ".github", "node_modules"}

#: Called by something a static pass cannot see. Reason must be true.
CALLED_INVISIBLY = {
    # Framework overrides — Kivy / http.server / pytest call these by name.
    "on_stop": "Kivy App lifecycle hook, called by the framework.",
    "log_message": "http.server BaseHTTPRequestHandler override.",
    "received_announce": "RNS announce handler, called by the Reticulum stack.",
    "isalive": "pexpect-style stub, called by the code under test.",
    "makefile": "socket stub, called by http.server internals.",
    "pytest_configure": "pytest hook.",
    "set_data": "Kivy widget method, called on instances built in a screen.",
    "set_selected": "Kivy widget method, called on instances built in a screen.",
    "is_connected": "Connection protocol method, called on transport instances.",
    "on_start": "preview script entry point, run by hand.",
    # Built and deliberately dormant — each has a note where it lives.
    "cache_debs": "Refreshes the carried .deb cache while online; no screen "
                  "reaches it yet, same as cache_wheels.",
    "build_unlock_screen": "The boot-unlock screen, built ahead of the vault "
                           "being switched on.",
    "connect_uart": "UART fallback for boards that cannot do USB gadget.",
    "activate_account_commands": "Imaging step used by the card bake path.",
    "reseed": "Imaging step used by the card bake path.",
    "prep_commands": "nRF52 RGB prep, used by the flash ladder.",
    "addresses_of": "Host-key helper for the cable-birth path.",
    "read_status_from_banner": "Adoption helper for boards that print a banner.",
    "register_port": "Onboard roster helper.",
    "cable_points": "wake_art drawing helper.",
    "colour_fraction": "wake_art drawing helper.",
    "eye_openness": "wake_art drawing helper.",
    "plug_fraction": "wake_art drawing helper.",
    "symbol_positions": "wake_art drawing helper.",
    "activity": "A node's when-is-it-up profile. Real and tested; nothing "
                "draws it yet (see its docstring) — on the v1.2 list.",
}


#: This file is excluded from the scan. Its allowlist names every function as a
#: STRING KEY, and the scanner counts string constants as references (they are,
#: in registries and getattr calls) — so including it would make every
#: allowlisted name look used, and the rot check would fire on all of them.
_SELF = os.path.abspath(__file__)


def _scan():
    """(defs, production refs, test refs).

    Counted SEPARATELY, because a function called only by its own tests is
    still dead in the product. That is not hypothetical here: ``can_cable``
    shipped as a fully-tested impossibility check that nothing ever ran.
    """
    defs = {}
    prod, test = collections.Counter(), collections.Counter()
    for base, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            if os.path.abspath(path) == _SELF:
                continue
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            rel = os.path.relpath(path, REPO)
            refs = test if rel.startswith("tests" + os.sep) else prod
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Helpers defined inside tests are not product surface.
                    if not rel.startswith("tests" + os.sep):
                        defs.setdefault(n.name, []).append(f"{rel}:{n.lineno}")
                elif isinstance(n, ast.Name):
                    refs[n.id] += 1
                elif isinstance(n, ast.Attribute):
                    refs[n.attr] += 1
                elif isinstance(n, ast.alias):
                    refs[n.name.split(".")[-1]] += 1
                    if n.asname:
                        refs[n.asname] += 1
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    refs[n.value] += 1
    return defs, prod, test


def test_no_public_function_is_defined_and_never_referenced():
    defs, prod, test = _scan()
    dead = sorted(
        name for name, sites in defs.items()
        if prod[name] == 0 and test[name] == 0
        and not name.startswith("_")
        and not name.startswith("test_")
        and name != "main"
        and name not in CALLED_INVISIBLY)
    assert not dead, (
        "These functions are defined and referenced NOWHERE — they read as "
        "working features to the next person:\n  " +
        "\n  ".join(f"{n}()  at {', '.join(defs[n])}" for n in dead) +
        "\n\nEither wire it to something an operator can reach, delete it, or "
        "add it to CALLED_INVISIBLY in this file with a TRUE reason.")


#: Production functions whose only caller is their own test suite. Some are
#: legitimate — argv builders run by the shell scripts, checks dispatched from a
#: string-keyed registry — and some are the ``can_cable`` failure: fully tested,
#: never run. Auditing all of them is not a v1 job, so this is a RATCHET: it may
#: fall, it may not rise. Lower it whenever you wire one up or delete one.
MAX_TEST_ONLY = 93


def test_the_tested_but_never_run_set_does_not_grow():
    """A function exercised only by its own tests is still dead in the product.

    ``can_cable`` shipped exactly like this — a fully-tested impossibility check
    that nothing ever called — so a green suite is not evidence a feature runs.
    """
    defs, prod, test = _scan()
    test_only = sorted(n for n, sites in defs.items()
                       if prod[n] == 0 and test[n] > 0
                       and not n.startswith("_") and not n.startswith("test_")
                       and n != "main")
    assert len(test_only) <= MAX_TEST_ONLY, (
        f"{len(test_only)} production functions are reached only by tests, up "
        f"from {MAX_TEST_ONLY}. Something new was written with tests but never "
        f"wired to anything an operator can reach:\n  " +
        "\n  ".join(f"{n}()  at {defs[n][0]}" for n in test_only))


def test_the_allowlist_does_not_rot():
    """An entry that IS now referenced must be removed, or the list slowly stops
    meaning anything — the same rule test_wiring.py applies to its own."""
    defs, refs, test_refs = _scan()
    now_used = sorted(n for n in CALLED_INVISIBLY
                      if n in defs and (refs[n] + test_refs[n]) > 0)
    assert not now_used, (
        "These are allowlisted as invisibly-called but ARE now referenced — "
        "delete them from CALLED_INVISIBLY:\n  " + "\n  ".join(now_used))


def test_a_screen_that_samples_can_be_stopped_and_started():
    """TRIAGE's stop() existed and NOTHING called it — the exact shape this
    file guards, but one layer up: a method, not a module. Its 2 Hz tick was
    scheduled from __init__ (app startup, whether or not anyone opened the
    screen) and never cancelled, which is what let the calibrator grow to
    ~295,000 samples and take the whole UI down to a crawl.

    A sampling screen needs BOTH halves wired, so this asserts the wiring
    rather than the methods' existence."""
    app = open(os.path.join(REPO, "ui", "app.py"), encoding="utf-8").read()
    assert "self.triage_screen.start()" in app, "TRIAGE never starts sampling"
    assert "self.triage_screen.stop()" in app, "TRIAGE never stops sampling"
    src = open(os.path.join(REPO, "ui", "screens", "triage_screen.py"),
               encoding="utf-8").read()
    assert "Clock.schedule_interval(self._tick" not in src.split("def start")[0], (
        "the tick is scheduled before start() — it runs for a screen nobody opened")
