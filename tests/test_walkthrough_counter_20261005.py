"""Readiness ledger #71 — the birth walkthrough's step counter runs on from the
antenna/detect pair instead of restarting at 'Step 1 of 7' on the name screen;
and #49's '(untested)' tag reaches the walkthrough's own board picker."""
from tests.srcutil import func_source, src


def test_the_prelude_pair_is_counted_into_the_lap():
    g = src("ui/screens/birth_guide_screen.py")
    assert g.count("self._prelude_shown = 0") >= 3          # init/reset, chooser, over-air
    antenna = func_source("ui/screens/birth_guide_screen.py", "_render_antenna", cls="BirthGuideScreen")
    assert "self._prelude_shown = 2" in antenna
    lead = func_source("ui/screens/birth_guide_screen.py", "_lead_screens", cls="BirthGuideScreen")
    assert 'getattr(self, "_prelude_shown", 0) + 1' in lead
    name = func_source("ui/screens/birth_guide_screen.py", "_render_name", cls="BirthGuideScreen")
    assert 'index=getattr(self, "_prelude_shown", 0), total=total' in name
    assert 'index=getattr(self, "_prelude_shown", 0) + 1, total=total' in g


def test_the_walkthrough_picker_tags_untested_boards():
    g = src("ui/screens/birth_guide_screen.py")
    assert 'elif _bd is not None and not _bd.proven:' in g
    assert g.count('tr("(untested)")') == 1
