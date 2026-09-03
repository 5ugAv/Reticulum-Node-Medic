"""Catch code that is complete, tested, and never called.

THIS TEST EXISTS BECAUSE THE PROJECT SHIPPED THAT BUG TWICE.

  * ``workflows/phone_apps.py`` grew a full downloader — size verification,
    corrupt-discard, version sidecars, its own test file — and **nothing ever
    called it.** Meanwhile the Comms screen told operators to press a button in
    Settings that did not exist. Found on 2026-08-16 by a person looking at an
    empty directory, weeks late.
  * ``workflows/carry.py`` was then committed to answer "is this medic ready to
    go somewhere with no signal" — complete, tested, and with **zero callers** —
    inside a commit whose own message described fixing the first case.

A green test suite says the code is correct. It says nothing about whether the
code runs. That gap is this file's whole subject.

The rule: every module in an action package must be imported by something
outside ``tests/``, or be listed below WITH A REASON. The list is the point —
it converts silence into a decision somebody had to write down.

Adding a module here is not cheating, it is the intended escape hatch for code
that genuinely runs another way (systemd, a deployed node) or is deliberately
dormant. Adding it *without a true reason* is how the bug comes back.
"""

import ast
import collections
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Packages whose modules are meant to DO something for the operator.
ACTION_PACKAGES = ["workflows", "monitor", "provisioning", "diagnostics"]

#: Modules nothing imports, each with the reason it is allowed to stay that way.
#: Verified 2026-08-16. Two kinds only: RUNS ELSEWHERE, or DORMANT.
UNIMPORTED_WITH_REASON = {
    # --- runs elsewhere: imported by something that is not Python source here ---
    # (monitor.serial_splitter used to live here — systemd-only — until the
    # antenna test began importing its KISS constants, 2026-08-27.)
    "monitor.pi_status_server":
        "RUNS ELSEWHERE. Deployed ONTO a built Pi node by "
        "workflows.build.install_status_server and run there, not on the medic.",

    # --- dormant: built, not yet wired to anything an operator can reach -------
    "workflows.build_warnings":
        "DORMANT. Plain-English build cautions, never shown. Note its GNSS text "
        "is also WRONG (tells you to put an antenna on the unconnected GNSS "
        "socket) — fix that before wiring it, not after.",
    "workflows.rnode_nrf52_rgb":
        "DORMANT. nRF52/RAK RGB firmware path, bench work in progress.",
    "provisioning.uart_link":
        "DORMANT. UART fallback provisioning for boards with one data port.",
    "provisioning.card_forensics":
        "DORMANT. Proves from an SD card alone whether a Pi booted. Bench tool.",
    "provisioning.node_sudoers":
        "DORMANT for nodes. The medic's own sudoers is applied by a script, not "
        "by this module.",
    "provisioning.rootfs_wifi":
        "DORMANT. Superseded on the birth path by direct rootfs writes; kept "
        "because the card's WiFi story is still being settled.",
    "monitor.battery":
        "DORMANT. Battery runtime estimator; no screen reads it yet.",
    "monitor.first_link":
        "DORMANT. Guided first-link range walk. Engine only.",
    "monitor.interference_log":
        "DORMANT. Interference history; nothing surfaces it yet.",
    "monitor.placement":
        "DORMANT. Node placement scoring. Engine only.",
}


def _module_files():
    """{dotted.module: path} for every module in the action packages."""
    out = {}
    for pkg in ACTION_PACKAGES:
        root = os.path.join(_REPO, pkg)
        if not os.path.isdir(root):
            continue
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith(".py") and fn != "__init__.py":
                    path = os.path.join(base, fn)
                    dotted = os.path.relpath(path, _REPO)[:-3].replace(os.sep, ".")
                    out[dotted] = path
    return out


def _importers():
    """{dotted.module: {files that import it}}, ignoring tests/.

    Counts BOTH `import pkg.mod` and `from pkg import mod` — the second form is
    how most of this codebase imports its modules, and missing it makes a wired
    module look orphaned.
    """
    found = collections.defaultdict(set)
    for base, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs
                   if d not in ("__pycache__", ".git", ".claude", "tests",
                                "assets", "firmware", "docs")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    found[node.module].add(path)
                    for alias in node.names:
                        found[f"{node.module}.{alias.name}"].add(path)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        found[alias.name].add(path)
    return found


def test_no_module_is_silently_uncalled():
    """Every action module is either imported, or listed with a reason."""
    mods, imps = _module_files(), _importers()
    orphans = sorted(m for m, path in mods.items()
                     if not (imps.get(m, set()) - {path})
                     and m not in UNIMPORTED_WITH_REASON)
    assert not orphans, (
        "These modules are imported by nothing outside tests/ — they are complete, "
        "possibly tested, and NEVER RUN:\n  " + "\n  ".join(orphans) +
        "\n\nEither wire it to something an operator can reach, or add it to "
        "UNIMPORTED_WITH_REASON in this file with a TRUE reason. Passing tests "
        "prove code is correct, not that it runs."
    )


def test_the_allowlist_does_not_rot():
    """A module that got wired up must leave the list.

    Without this the list silently becomes a graveyard of stale claims, and the
    next person cannot tell which entries still mean anything.
    """
    mods, imps = _module_files(), _importers()
    now_wired = sorted(m for m in UNIMPORTED_WITH_REASON
                       if m in mods and (imps.get(m, set()) - {mods[m]}))
    assert not now_wired, (
        "These are listed as uncalled but ARE now imported — delete them from "
        "UNIMPORTED_WITH_REASON:\n  " + "\n  ".join(now_wired)
    )


def test_the_allowlist_has_no_ghosts():
    """Every listed module still exists. A deleted file leaves a stale excuse."""
    mods = _module_files()
    ghosts = sorted(m for m in UNIMPORTED_WITH_REASON if m not in mods)
    assert not ghosts, (
        "Listed modules that no longer exist — remove them:\n  " + "\n  ".join(ghosts)
    )


def test_every_reason_actually_says_something():
    """A reason must classify itself, so 'why is this here' is always answerable."""
    vague = sorted(m for m, why in UNIMPORTED_WITH_REASON.items()
                   if len(why) < 40
                   or not ("DORMANT" in why or "RUNS ELSEWHERE" in why))
    assert not vague, (
        "These entries need a real reason, starting with DORMANT or RUNS "
        "ELSEWHERE:\n  " + "\n  ".join(vague)
    )
