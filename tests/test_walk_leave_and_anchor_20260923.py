"""Leaving a boundary walk asks first, and the anchor is locked per node.

Operator, 2026-09-23, mid-walk on the roof node: "a user shouldn't be able
to exit it so easily. If they press the back button, a warning should come
up saying confirm exit of boundary walk … I just accidentally exited the
boundary walk halfway through it. It means I can't restart it because I'm
not next to the node to set the GPS coordinates where I started." And:
"once the GPS coordinates are set for a node, that's locked in and the
user can start the boundary walk away from the node rather than under it."

THE FACT BEFORE THE FIX (checked in ui/app.py _with_back and
ui/screens/scan_screen.py): ScanScreen defined neither handle_back nor
handle_home, so the bottom bar's arrow and Home both switched straight to
home. end_walk was not called — the session's Clock intervals kept pinging
with no HUD on the glass and no road back; the walk was banked only when
the app stopped or the next press on a door found a running session.

Kivy has no window provider here: the pure rules are tested as rules, the
screen's leave methods are compiled out of the shipped source and run
against a stub (the tests/test_nav_bar_wiring.py practice), and the gate's
two roads are pinned by reading the source.
"""
import ast
import json
import os
import sys
import textwrap
import types

import pytest

from monitor import walk_anchor as wa
from monitor.boundary_walk import leave_plan
from tests.srcutil import ROOT, func_source, src

SCAN = "ui/screens/scan_screen.py"
APP = "ui/app.py"
CATALOGS = ("de", "es", "fr", "id", "ja", "pl", "ru", "sv")


# -- the anchor file -----------------------------------------------------------

def test_save_then_load_round_trips_the_anchor(tmp_path):
    rec = wa.save_anchor("ab" * 16, -37.7, 145.0, 1_700_000_000.0,
                         name="Roof", base_dir=str(tmp_path))
    got = wa.load_anchor("ab" * 16, base_dir=str(tmp_path))
    assert got == rec == {"lat": -37.7, "lon": 145.0, "at": 1_700_000_000.0,
                          "name": "Roof"}


def test_an_unknown_node_has_no_anchor(tmp_path):
    assert wa.load_anchor("cd" * 16, base_dir=str(tmp_path)) is None
    assert wa.load_anchor("cd" * 16, name="Nobody", base_dir=str(tmp_path)) is None


def test_the_anchor_is_found_by_name_when_the_other_door_keyed_it_differently(tmp_path):
    """VITALS keys the walk by probe_hash_for (the first hex dest), ANTENNA's
    picker by the freshest destination — a T114 sat under four (2026-09-19).
    The anchor must survive the operator using the other door next time."""
    wa.save_anchor("aa" * 16, -37.7, 145.0, 100.0, name="RTnodet114",
                   base_dir=str(tmp_path))
    got = wa.load_anchor("bb" * 16, name="rtnodeT114", base_dir=str(tmp_path))
    assert got is not None and got["lat"] == -37.7


def test_a_key_match_beats_a_name_match(tmp_path):
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, name="X", base_dir=str(tmp_path))
    wa.save_anchor("bb" * 16, 2.0, 2.0, 200.0, name="Y", base_dir=str(tmp_path))
    assert wa.load_anchor("aa" * 16, name="Y", base_dir=str(tmp_path))["lat"] == 1.0


def test_re_anchoring_the_same_node_under_a_new_key_keeps_one_anchor(tmp_path):
    """Standing at the node again REPLACES its anchor — even when the door
    keyed it by a different destination this time. Two anchors for one node
    would let the loader pick the stale one by key."""
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, name="Roof", base_dir=str(tmp_path))
    wa.save_anchor("bb" * 16, 2.0, 2.0, 200.0, name="roof", base_dir=str(tmp_path))
    assert wa.load_anchor("aa" * 16, base_dir=str(tmp_path)) is None
    assert wa.load_anchor("aa" * 16, name="ROOF", base_dir=str(tmp_path))["lat"] == 2.0
    assert len(wa._read_all(str(tmp_path))) == 1


def test_forget_drops_the_anchor_by_key_and_by_name(tmp_path):
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, name="Roof", base_dir=str(tmp_path))
    wa.save_anchor("bb" * 16, 2.0, 2.0, 200.0, name="Shed", base_dir=str(tmp_path))
    assert wa.forget_anchor(node_key="aa" * 16, base_dir=str(tmp_path)) == 1
    assert wa.load_anchor("aa" * 16, name="Roof", base_dir=str(tmp_path)) is None
    assert wa.forget_anchor(name="shed", base_dir=str(tmp_path)) == 1
    assert wa.load_anchor("bb" * 16, base_dir=str(tmp_path)) is None


def test_forgetting_nothing_is_zero_and_writes_nothing(tmp_path):
    assert wa.forget_anchor(node_key="zz" * 16, base_dir=str(tmp_path)) == 0
    assert not os.path.exists(wa._path(str(tmp_path)))


def test_a_corrupt_file_reads_as_empty_and_can_be_written_over(tmp_path):
    """The operator is standing outside in the weather: a corrupt file is
    'no anchor', never a traceback — and the next save must not be blocked
    by it."""
    with open(wa._path(str(tmp_path)), "w") as fh:
        fh.write("{not json")
    assert wa.load_anchor("aa" * 16, base_dir=str(tmp_path)) is None
    assert wa.forget_anchor(node_key="aa" * 16, base_dir=str(tmp_path)) == 0
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, base_dir=str(tmp_path))
    assert wa.load_anchor("aa" * 16, base_dir=str(tmp_path))["lat"] == 1.0


def test_one_bad_record_does_not_lose_the_others(tmp_path):
    with open(wa._path(str(tmp_path)), "w") as fh:
        json.dump({"aa" * 16: {"lat": "x", "lon": 1, "at": 1},
                   "bb" * 16: {"lat": 2.0, "lon": 2.0, "at": 2.0, "name": "B"},
                   "cc" * 16: "not a record",
                   "dd" * 16: {"lat": 3.0}}, fh)
    assert wa.load_anchor("aa" * 16, base_dir=str(tmp_path)) is None
    assert wa.load_anchor("bb" * 16, base_dir=str(tmp_path))["name"] == "B"
    assert wa.load_anchor("dd" * 16, base_dir=str(tmp_path)) is None


def test_a_file_that_is_not_a_map_reads_as_empty(tmp_path):
    with open(wa._path(str(tmp_path)), "w") as fh:
        json.dump([1, 2, 3], fh)
    assert wa._read_all(str(tmp_path)) == {}


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    """Written beside, then renamed over: a crash mid-write leaves the OLD
    file intact, and every node's anchor with it."""
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, base_dir=str(tmp_path))
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(wa.os, "replace",
                        lambda a, b: (replaced.append((a, b)), real_replace(a, b)))
    wa.save_anchor("bb" * 16, 2.0, 2.0, 200.0, base_dir=str(tmp_path))
    assert replaced and replaced[0][1] == wa._path(str(tmp_path))
    assert replaced[0][0] != replaced[0][1], "written to a sibling first"
    assert sorted(os.listdir(tmp_path)) == [wa._ANCHOR_FILE]


def test_a_crash_before_the_rename_keeps_the_old_anchors(tmp_path, monkeypatch):
    wa.save_anchor("aa" * 16, 1.0, 1.0, 100.0, base_dir=str(tmp_path))

    def boom(_a, _b):
        raise OSError("disk went away")
    monkeypatch.setattr(wa.os, "replace", boom)
    with pytest.raises(OSError):
        wa.save_anchor("aa" * 16, 9.0, 9.0, 900.0, base_dir=str(tmp_path))
    monkeypatch.undo()
    assert wa.load_anchor("aa" * 16, base_dir=str(tmp_path))["lat"] == 1.0


def test_an_anchor_needs_a_key(tmp_path):
    with pytest.raises(ValueError):
        wa.save_anchor("", 1.0, 1.0, 1.0, base_dir=str(tmp_path))


def test_the_stamp_is_a_local_date_to_the_minute_and_never_raises():
    s = wa.anchor_stamp(1_700_000_000.0)
    assert len(s) == 16 and s[4] == "-" and s[10] == " " and s[13] == ":"
    assert wa.anchor_stamp("not a time") == "?"
    assert wa.anchor_stamp(float("nan")) == "?"


# -- the leave decision, pure --------------------------------------------------

def test_with_nothing_running_leaving_is_not_a_question():
    assert leave_plan(False, False)["ask"] is False


def test_a_live_walk_asks_and_is_banked_on_confirm():
    p = leave_plan(True, False)
    assert p == {"ask": True, "persist": True, "then": "home"}


def test_a_gate_asks_but_has_nothing_to_bank():
    p = leave_plan(False, True)
    assert p["ask"] is True and p["persist"] is False and p["then"] == "home"


# -- the screen's leave methods, run against a stub ----------------------------

class _FakeConfirm:
    """Records what confirm_leave was asked to show; the test presses."""
    last = None

    def __init__(self, message, title, on_leave, stay_text, leave_text,
                 leave_color="accent"):
        self.message, self.title, self.on_leave = message, title, on_leave
        self.stay_text, self.leave_text = stay_text, leave_text
        self.leave_color = leave_color
        _FakeConfirm.last = self


def _screen(monkeypatch, walk=None, gate=None):
    """A ScanScreen-shaped stub carrying the shipped leave methods."""
    confirm_mod = types.ModuleType("ui.confirm")
    confirm_mod.confirm_leave = _FakeConfirm
    monkeypatch.setitem(sys.modules, "ui.confirm", confirm_mod)
    switched = []
    app_mod = types.ModuleType("kivy.app")
    app_mod.App = types.SimpleNamespace(
        get_running_app=lambda: types.SimpleNamespace(switch_mode=switched.append))
    monkeypatch.setitem(sys.modules, "kivy.app", app_mod)
    ns = {"tr": lambda s: s}
    for name in ("handle_back", "handle_home", "_confirm_leave_walk",
                 "_leave_walk", "_go_home"):
        exec(compile(textwrap.dedent(func_source(SCAN, name, cls="ScanScreen")),
                     SCAN, "exec"), ns)
    ended = []

    class _S:
        _walk_session = walk
        _walk_gate = gate

        def _walk_node_name(self):
            return "Roof"

        def end_walk(self, persist=True):
            ended.append(persist)
            self._walk_session = None
            self._walk_gate = None
    for name in ("handle_back", "handle_home", "_confirm_leave_walk",
                 "_leave_walk", "_go_home"):
        setattr(_S, name, ns[name])
    _FakeConfirm.last = None
    return _S(), ended, switched


def test_back_with_no_walk_falls_through_to_home_as_before(monkeypatch):
    s, ended, switched = _screen(monkeypatch)
    assert s.handle_back() is False
    assert _FakeConfirm.last is None and ended == [] and switched == []


def test_home_with_no_walk_is_a_plain_switch(monkeypatch):
    s, ended, switched = _screen(monkeypatch)
    s.handle_home()
    assert switched == ["home"] and ended == [] and _FakeConfirm.last is None


def test_back_mid_walk_asks_and_stays_put(monkeypatch):
    s, ended, switched = _screen(monkeypatch, walk=object())
    assert s.handle_back() is True, "handled: the popup owns what happens next"
    pop = _FakeConfirm.last
    assert pop is not None and pop.title == "Leave the walk?"
    assert pop.stay_text == "Keep walking"
    assert pop.leave_text == "End the walk and save"
    assert "Roof" in pop.message and "anchor is kept" in pop.message
    assert ended == [] and switched == [], "nothing happens until a press"


def test_confirming_mid_walk_banks_the_walk_then_goes_home(monkeypatch):
    s, ended, switched = _screen(monkeypatch, walk=object())
    s.handle_back()
    _FakeConfirm.last.on_leave()
    assert ended == [True], "end_walk(persist=True): the evidence is banked"
    assert switched == ["home"]


def test_home_mid_walk_asks_the_same_question(monkeypatch):
    s, ended, switched = _screen(monkeypatch, walk=object())
    s.handle_home()
    assert _FakeConfirm.last.title == "Leave the walk?"
    assert switched == [] and ended == []
    _FakeConfirm.last.on_leave()
    assert ended == [True] and switched == ["home"]


def test_leaving_at_the_gate_asks_and_drops_the_gate_without_saving(monkeypatch):
    s, ended, switched = _screen(monkeypatch, gate=object())
    assert s.handle_back() is True
    pop = _FakeConfirm.last
    assert pop.leave_text == "Leave — nothing to save"
    assert "nothing to save" in pop.message and "Roof" in pop.message
    pop.on_leave()
    assert ended == [False] and switched == ["home"]


def test_the_leave_button_is_not_red(monkeypatch):
    """Red on this tool means Delete / Rebirth; this button SAVES the walk
    (the Stop & save rule, 2026-09-21)."""
    s, _, _ = _screen(monkeypatch, walk=object())
    s.handle_back()
    assert _FakeConfirm.last.leave_color != "red"


# -- source pins: the wiring ---------------------------------------------------

def test_scan_screen_offers_the_bar_both_roads():
    tree = ast.parse(src(SCAN))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "ScanScreen")
    names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert {"handle_back", "handle_home"} <= names


def test_with_back_reaches_the_screens_handlers_by_name():
    wb = func_source(APP, "_with_back")
    assert '"handle_back"' in wb and '"handle_home"' in wb


def test_the_fact_is_written_down_where_the_fix_lives():
    hb = func_source(SCAN, "handle_back", cls="ScanScreen")
    assert "2026-09-23" in hb and "end_walk was never called" in hb
    plan = func_source("monitor/boundary_walk.py", "leave_plan")
    assert "THE FACT" in plan


def test_the_confirm_card_puts_the_safe_choice_first():
    body = func_source("ui/confirm.py", "confirm_leave")
    assert body.index("row.add_widget(stay)") < body.index("row.add_widget(go)")
    assert 'leave_color="accent"' in body, "not red by default"
    assert "auto_dismiss=True" in body, "a stray touch outside = stay"


def test_the_gate_has_two_roads_worded_apart():
    show = func_source(SCAN, "_show_walk_gate", cls="ScanScreen")
    assert "load_anchor" in show
    assert "I'm standing at the node" in show
    assert "I'm away from it" in show
    assert "saved on {date}" in show
    assert "GPS found" in show, "both roads still say why they appeared"
    assert "Stand next to" in show


def test_both_roads_wait_for_the_same_steady_fix():
    tick = func_source(SCAN, "_walk_gate_tick", cls="ScanScreen")
    assert "_walk_show_roads(ready)" in tick
    roads = func_source(SCAN, "_walk_show_roads", cls="ScanScreen")
    assert "_walk_go_saved" in roads and "_walk_go" in roads
    go = func_source(SCAN, "_walk_gate_go", cls="ScanScreen")
    code = go.split('"""')[-1]
    assert code.index("if live is None") < code.index("use_saved"), (
        "the saved road is not a way around the sky")


def test_the_saved_road_starts_from_the_saved_anchor_and_does_not_resave():
    go = func_source(SCAN, "_walk_gate_go", cls="ScanScreen")
    assert 'saved_at=saved["at"]' in go
    start = func_source(SCAN, "_start_walk_now", cls="ScanScreen")
    code = start.split('"""')[-1]
    assert "if saved_at is None:" in code
    block = code[code.index("if saved_at is None:"):code.index("self._walk_anchor_saved_at")]
    assert "save_anchor(rec.dst_hash, lat, lon" in block, (
        "a live start at the node locks the anchor; a saved start never moves it")


def test_the_hud_first_line_names_the_anchor():
    hud = func_source(SCAN, "_show_walk_hud", cls="ScanScreen")
    assert "Anchored at {name}" in hud and "Anchored here" in hud
    code = hud.split('"""')[-1]
    assert code.index("hud.add_widget(self._walk_anchor_lbl)") < code.index("hud.add_widget(top)")


def test_forgetting_a_node_forgets_its_anchor():
    body = func_source(APP, "_forget_node", cls="ReticulumNodeMedicApp")
    assert "forget_anchor" in body
    assert "forget_anchor(name=name)" in body, "by name too — the other door's key"


def test_teardown_drops_the_saved_road_button():
    body = func_source(SCAN, "_tear_down_walk_gate", cls="ScanScreen")
    assert "_walk_go_saved = None" in body


def test_the_diagnosis_scope_is_one_walk_and_placement_reads_all():
    """Stated, not changed (brief, 2026-09-23): the end-of-walk signal story
    diagnoses THIS walk's samples; the placement spine reads every walk's
    banked evidence. A 'continued' walk is a new walk from the locked
    anchor, so its verdict is its own — the range model sees both."""
    story = func_source(SCAN, "_walk_signal_story", cls="ScanScreen")
    assert "diagnose(w.samples" in story
    app = src(APP)
    assert "load_walk_observations()" in app and "load_walk_failures()" in app


# -- i18n -----------------------------------------------------------------------

NEW_STRINGS = (
    "Leave the walk?", "Keep walking", "End the walk and save",
    "Leave — nothing to save",
    "Stand next to {name} — or start from where you are.",
    "GPS found — I'm standing at the node (set its position again)",
    "GPS found — I'm away from it (use the position saved on {date})",
    "Anchored at {name} — position saved {date}.",
    "Anchored here — where you pressed start.",
)


@pytest.mark.parametrize("code", CATALOGS)
def test_every_new_string_is_in_every_full_catalog(code):
    path = os.path.join(ROOT, "assets", "i18n", f"{code}.json")
    with open(path, encoding="utf-8") as fh:
        cat = json.load(fh)
    for s in NEW_STRINGS:
        assert cat.get(s), f"{code}.json lacks {s!r}"
    # the long bodies, by their distinctive fragments
    assert any("anchor is kept" in k for k in cat), code
    assert any("nothing has been measured yet" in k for k in cat), code
    assert any("already has a start position" in k for k in cat), code
