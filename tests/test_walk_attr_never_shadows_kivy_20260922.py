"""The walk session's attribute name must never collide with Kivy's Widget.

Found on the medic, 2026-09-22 22:20: a freshly started UI wrote the busy
marker "a boundary walk" within a minute of boot, with nobody at the glass
and no walk line in its log; scripts/restart_ui.sh refused to restart it.
The cause: kivy.uix.widget.Widget defines a private generator method
``_walk`` (the tree walker behind ``Widget.walk()``), so on ANY widget
``getattr(w, "_walk", None)`` is a bound method, never None. Every "is a
walk running?" check in the tool — the busy marker, the screensaver
deferral, auto-Backpack standing down, map taps going inert, placement
controls hiding, "starting another walk banks the running one" — had
answered YES since the boundary walk shipped. The session now lives in
``_walk_session``, a name Kivy does not own, and this test keeps it so.
"""
import os
import re

from tests.srcutil import ROOT

UI_FILES = ["ui/app.py", "ui/screens/scan_screen.py"]


def test_no_widget_is_asked_for_an_attribute_kivy_defines():
    for rel in UI_FILES:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert '"_walk"' not in src, f"{rel}: getattr(widget, '_walk') is Kivy's tree walker"
        assert not re.search(r"self\._walk\b(?!_)", src), f"{rel}: bare self._walk"


def test_the_session_attribute_is_not_a_kivy_widget_name():
    """Asked of the REAL Kivy in a subprocess: other tests stub sys.modules
    ["kivy"] in-process, and a stub Widget has no _walk to collide with."""
    import json
    import subprocess
    import sys
    import pytest
    names = ["_walk_session", "_walk_gate", "_walk_ev", "_walk_hud",
             "_walk_record", "_walk_hint", "_walk_flash_ev"]
    code = ("import json,sys\n"
            "try:\n    from kivy.uix.widget import Widget\n"
            "except Exception:\n    print('NOKIVY'); sys.exit(0)\n"
            f"print(json.dumps({{n: hasattr(Widget, n) for n in ['_walk'] + {names!r}}}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120).stdout.strip().splitlines()
    if not out or out[-1] == "NOKIVY":
        pytest.skip("kivy not importable here")
    has = json.loads(out[-1])
    assert has["_walk"], "the collision this test exists for"
    assert not any(has[n] for n in names), has


def test_the_busy_predicate_reads_the_session_not_the_walker():
    src = open(os.path.join(ROOT, "ui/app.py"), encoding="utf-8").read()
    body = src[src.index("    def _busy_reason"):src.index("    def _busy_heartbeat")]
    assert '"_walk_session"' in body
