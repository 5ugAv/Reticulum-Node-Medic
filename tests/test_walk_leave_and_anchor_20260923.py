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

THE SECOND PASS (two review lenses, 2026-09-23): the anchor is keyed by
the DEVICE, never by a name (the registry's own law — a reused name is a
different board); positions are validated on save, load and at the press;
the leave road never raises and never leaves by accident; one rule for
what is banked; the gate says what the press does to the file.

Kivy has no window provider here: the pure rules are tested as rules, and
the screen's methods are compiled out of the shipped source and run
against a stub (the tests/test_nav_bar_wiring.py practice). No test here
asserts on what its own stub did — the stubs record, the shipped code is
what is judged.
"""
import ast
import json
import os
import sys
import textwrap
import types

import pytest

from monitor import walk_anchor as wa
from monitor.boundary_walk import (BoundaryWalkSession, append_evidence,
                                   gps_gate, leave_plan, walk_position)
from monitor.geo import GpsFix, valid_position
from tests.srcutil import ROOT, func_source, src

SCAN = "ui/screens/scan_screen.py"
APP = "ui/app.py"
GUIDE = "ui/screens/birth_guide_screen.py"
BIRTH = "ui/screens/birth_screen.py"
CATALOGS = ("de", "es", "fr", "id", "ja", "pl", "ru", "sv")

A, B, C = "aa" * 16, "bb" * 16, "cc" * 16
DEV = "device-roof-t114"


@pytest.fixture
def anchors(tmp_path, monkeypatch):
    """Every anchor call in the test and in the compiled screen code lands
    in tmp_path — _WALK_DIR is read at call time, not bound at def time."""
    monkeypatch.setattr(wa, "_WALK_DIR", str(tmp_path))
    return str(tmp_path)


# -- positions on Earth ---------------------------------------------------------

@pytest.mark.parametrize("lat,lon", [
    (float("nan"), 1.0), (1.0, float("nan")), (float("inf"), 1.0),
    (1.0, float("-inf")), (0, 0), (0.0, 0.0), (91.0, 1.0), (-91.0, 1.0),
    (1.0, 181.0), (1.0, -180.5), ("12.3", 1.0), (1.0, "4"), (None, 1.0),
    (True, 1.0), (1.0, False),
])
def test_positions_that_are_not_on_earth_are_refused(lat, lon):
    assert valid_position(lat, lon) is False


@pytest.mark.parametrize("lat,lon", [(-37.7, 145.0), (90, 180), (-90, -180),
                                     (0.0, 1.0), (1, 0)])
def test_positions_on_earth_pass(lat, lon):
    assert valid_position(lat, lon) is True


# -- the anchor file: keys ---------------------------------------------------------

def test_the_key_is_the_device_then_identity_then_destination():
    rec = types.SimpleNamespace(dst_hash=A, identity_hash=B, device_id=DEV)
    assert wa.anchor_key(rec) == DEV
    assert wa.anchor_key(types.SimpleNamespace(dst_hash=A, identity_hash=B,
                                               device_id=None)) == B
    assert wa.anchor_key(types.SimpleNamespace(dst_hash=A)) == A
    assert wa.anchor_key(types.SimpleNamespace(dst_hash="", name="Roof")) == ""


def test_a_name_is_never_a_key(anchors):
    """Two different boards can wear one name (registry law, 2026-08-22):
    an anchor saved for one must not be found by the other's name."""
    wa.save_anchor(A, -37.7, 145.0, 100.0)
    named = types.SimpleNamespace(dst_hash=B, name="Roof")
    assert wa.load_anchor(wa.candidate_keys(named)) is None
    text = src("monitor/walk_anchor.py")
    assert "_fold" not in text and "name=" not in func_source(
        "monitor/walk_anchor.py", "save_anchor")


class _Reg:
    """A registry-shaped stub: consolidated_records is the one device fold."""

    def __init__(self, groups):
        self._groups = groups
        self.nodes = {m.dst_hash: m for _c, ms in groups for m in ms}

    def consolidated_records(self, now):
        return list(self._groups)

    def get(self, h):
        return self.nodes.get(h)


def _t114():
    """One T114 under three destinations, joined by the roster's device id
    on two of them and by identity on the third — the 2026-09-19 case."""
    m1 = types.SimpleNamespace(dst_hash=A, identity_hash="id-1", device_id=DEV,
                               name="RTnodet114", lat=-37.7, lon=145.0)
    m2 = types.SimpleNamespace(dst_hash=B, identity_hash="id-1", device_id=None,
                               name="rtnodeT114", lat=None, lon=None)
    m3 = types.SimpleNamespace(dst_hash=C, identity_hash="id-2", device_id=DEV,
                               name="", lat=None, lon=None)
    consolidated = types.SimpleNamespace(dst_hash=A, identity_hash="id-1",
                                         device_id=DEV, name="RTnodet114",
                                         lat=-37.7, lon=145.0)
    return _Reg([(consolidated, [m1, m2, m3])]), consolidated, m2, m3


def test_both_doors_converge_on_one_key(anchors):
    """VITALS hands over the consolidated record (led by A); ANTENNA's
    picker hands over the freshest member (B, or C which has no identity
    link to B at all). All three must save under and find ONE anchor."""
    reg, vitals, antenna_b, antenna_c = _t114()
    kv = wa.candidate_keys(vitals, registry=reg)
    kb = wa.candidate_keys(antenna_b, registry=reg)
    kc = wa.candidate_keys(antenna_c, registry=reg)
    assert kv[0] == kc[0] == DEV, "the primary is the device"
    assert kb[0] == "id-1", "a member without the device id leads with identity"
    assert set(kv) == set(kb) == set(kc) == {DEV, "id-1", "id-2", A, B, C}
    wa.save_anchor(kv[0], -37.7, 145.0, 100.0)
    assert wa.load_anchor(kb)["lat"] == -37.7
    assert wa.load_anchor(kc)["lat"] == -37.7
    # and the other way round: saved from the ANTENNA door, found by VITALS
    wa.forget_anchor(kv)
    wa.save_anchor(kb[0], 1.0, 2.0, 200.0)
    assert wa.load_anchor(kv)["lat"] == 1.0


def test_candidate_keys_survive_a_registry_that_cannot_answer():
    rec = types.SimpleNamespace(dst_hash=A, identity_hash=None, device_id=None)
    assert wa.candidate_keys(rec) == [A]
    assert wa.candidate_keys(rec, registry=None) == [A]

    class _Broken:
        def consolidated_records(self, now):
            raise RuntimeError("locked")
    assert wa.candidate_keys(rec, registry=_Broken()) == [A]


def test_the_first_key_with_an_anchor_wins_primary_first(anchors):
    wa.save_anchor(A, 1.0, 1.0, 100.0)
    wa.save_anchor(DEV, 2.0, 2.0, 200.0)
    assert wa.load_anchor([DEV, A])["lat"] == 2.0
    assert wa.load_anchor([A, DEV])["lat"] == 1.0
    assert wa.load_anchor(A)["lat"] == 1.0, "a bare string is one key"
    assert wa.load_anchor([]) is None and wa.load_anchor("") is None


# -- the anchor file: contents -----------------------------------------------------

def test_save_then_load_round_trips_the_anchor_with_the_fix_it_was_taken_on(anchors):
    rec = wa.save_anchor(DEV, -37.7, 145.0, 1_700_000_000.0, sats=9,
                         accuracy_m=3.75)
    got = wa.load_anchor([DEV])
    assert got == rec == {"lat": -37.7, "lon": 145.0, "at": 1_700_000_000.0,
                          "sats": 9, "accuracy_m": 3.75}
    with open(wa._path(), encoding="utf-8") as fh:
        raw = json.load(fh)
    assert set(raw[DEV]) == {"lat", "lon", "at", "sats", "accuracy_m"}
    assert "name" not in raw[DEV]


def test_an_unknown_fix_quality_is_stored_as_none_never_invented(anchors):
    rec = wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    assert rec["sats"] is None and rec["accuracy_m"] is None


@pytest.mark.parametrize("lat,lon", [
    (float("nan"), 1.0), (1.0, float("inf")), (0.0, 0.0), (91.0, 1.0),
    (1.0, -181.0), ("12.3", 1.0), (1.0, "4"),
])
def test_a_position_that_is_not_on_earth_is_never_saved(anchors, lat, lon):
    with pytest.raises(ValueError):
        wa.save_anchor(DEV, lat, lon, 100.0)
    assert not os.path.exists(wa._path()), "nothing written"


def test_a_nan_time_is_never_saved(anchors):
    with pytest.raises(ValueError):
        wa.save_anchor(DEV, 1.0, 1.0, float("nan"))


@pytest.mark.parametrize("bad", [
    {"lat": float("nan"), "lon": 1.0, "at": 1.0},
    {"lat": 1.0, "lon": float("inf"), "at": 1.0},
    {"lat": 0, "lon": 0, "at": 1.0},
    {"lat": 91.0, "lon": 1.0, "at": 1.0},
    {"lat": 1.0, "lon": 181.0, "at": 1.0},
    {"lat": "12.3", "lon": 1.0, "at": 1.0},
    {"lat": 1.0, "lon": "4", "at": 1.0},
    {"lat": 1.0, "lon": 1.0, "at": "yesterday"},
    {"lat": True, "lon": 1.0, "at": 1.0},
    {"lat": 1.0},
    "not a record",
])
def test_an_invalid_anchor_on_disk_reads_as_none_and_is_said(anchors, bad,
                                                            capsys):
    """A file another version (or a hand) wrote: the record is ignored, the
    log says which, and the GOOD record beside it survives."""
    with open(wa._path(), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({A: bad, B: {"lat": 2.0, "lon": 2.0, "at": 2.0}},
                            allow_nan=True))
    assert wa.load_anchor([A]) is None
    assert wa.load_anchor([B])["lat"] == 2.0
    assert "not a usable position" in capsys.readouterr().out


def test_the_file_is_written_without_nan(anchors, monkeypatch):
    """allow_nan off: a NaN that somehow reached the writer raises instead
    of producing a file that is not JSON (and would read as corrupt —
    every node's anchor lost at once)."""
    calls = []
    real = json.dumps

    def spy(*a, **kw):
        calls.append(kw.get("allow_nan"))
        return real(*a, **kw)
    monkeypatch.setattr(wa.json, "dumps", spy)
    wa.save_anchor(DEV, 1.0, 1.0, 1.0)
    assert calls and all(v is False for v in calls)
    with pytest.raises(ValueError):
        wa._write_all({DEV: {"lat": float("nan"), "lon": 1.0, "at": 1.0}})


def test_forget_drops_every_key_given_and_only_those(anchors):
    wa.save_anchor(DEV, 1.0, 1.0, 100.0)
    wa.save_anchor(B, 2.0, 2.0, 200.0)
    wa.save_anchor(C, 3.0, 3.0, 300.0)
    assert wa.forget_anchor([DEV, B, "zz" * 16]) == 2
    assert wa.load_anchor([DEV, B]) is None
    assert wa.load_anchor([C])["lat"] == 3.0
    assert wa.forget_anchor(C) == 1, "a bare string is one key"


def test_forgetting_nothing_is_zero_and_writes_nothing(anchors):
    assert wa.forget_anchor(["zz" * 16]) == 0
    assert wa.forget_anchor([]) == 0 and wa.forget_anchor("") == 0
    assert not os.path.exists(wa._path())


def test_a_corrupt_file_reads_as_empty_and_can_be_written_over(anchors):
    with open(wa._path(), "w") as fh:
        fh.write("{not json")
    assert wa.load_anchor([A]) is None
    assert wa.forget_anchor([A]) == 0
    wa.save_anchor(A, 1.0, 1.0, 100.0)
    assert wa.load_anchor([A])["lat"] == 1.0


def test_a_file_that_is_not_a_map_reads_as_empty(anchors):
    with open(wa._path(), "w") as fh:
        json.dump([1, 2, 3], fh)
    assert wa._read_all() == {}


def test_the_write_is_atomic_and_leaves_no_temp_file(anchors, monkeypatch):
    wa.save_anchor(A, 1.0, 1.0, 100.0)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(wa.os, "replace",
                        lambda a, b: (replaced.append((a, b)), real_replace(a, b)))
    wa.save_anchor(B, 2.0, 2.0, 200.0)
    assert replaced and replaced[0][1] == wa._path()
    assert replaced[0][0] != replaced[0][1], "written to a sibling first"
    assert sorted(os.listdir(anchors)) == [wa._ANCHOR_FILE]


def test_a_crash_before_the_rename_keeps_the_old_anchors(anchors, monkeypatch):
    wa.save_anchor(A, 1.0, 1.0, 100.0)

    def boom(_a, _b):
        raise OSError("disk went away")
    monkeypatch.setattr(wa.os, "replace", boom)
    with pytest.raises(OSError):
        wa.save_anchor(A, 9.0, 9.0, 900.0)
    monkeypatch.undo()
    monkeypatch.setattr(wa, "_WALK_DIR", anchors)
    assert wa.load_anchor([A])["lat"] == 1.0


def test_an_anchor_needs_a_key(anchors):
    with pytest.raises(ValueError):
        wa.save_anchor("", 1.0, 1.0, 1.0)


def test_the_gap_helper_measures_or_says_it_cannot():
    assert wa.anchor_gap_m((-37.7, 145.0), (-37.7, 145.0)) == 0.0
    d = wa.anchor_gap_m((-37.7, 145.0), (-37.7, 145.002))
    assert 150 < d < 200
    assert wa.anchor_gap_m((-37.7, 145.0), (None, None)) is None
    assert wa.anchor_gap_m((float("nan"), 1.0), (1.0, 1.0)) is None
    assert wa.anchor_gap_m(None, (1.0, 1.0)) is None


# -- the gate's pure rule --------------------------------------------------------

def test_the_gate_refuses_a_live_fix_that_is_not_on_earth_and_says_why():
    for lat, lon in ((float("nan"), 1.0), (0.0, 0.0), (91.0, 1.0)):
        g = gps_gate(GpsFix(lat=lat, lon=lon, sats=8, fix_quality=1))
        assert g["ready"] is False and g["stage"] == "invalid"
        assert g["anchor"] is None


def test_the_gate_carries_the_fixes_own_accuracy_never_an_invented_one():
    g = gps_gate(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1,
                        accuracy_m=3.75))
    assert g["ready"] and g["accuracy_m"] == 3.75 and g["sats"] == 8
    g = gps_gate(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1))
    assert g["accuracy_m"] is None
    g = gps_gate(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1,
                        accuracy_m=float("nan")))
    assert g["accuracy_m"] is None


def test_a_sample_on_a_live_fix_off_earth_is_not_placed():
    assert walk_position(GpsFix(lat=0.0, lon=0.0, sats=8, fix_quality=1)) is None
    assert walk_position(GpsFix(lat=float("nan"), lon=1.0, sats=8,
                                fix_quality=1)) is None
    assert walk_position(GpsFix(lat=-37.7, lon=145.0, sats=8,
                                fix_quality=1)) == (-37.7, 145.0)


def test_evidence_is_banked_under_the_device_key_when_given():
    w = BoundaryWalkSession(node_key=B, node_name="Roof", node_lat=-37.7,
                            node_lon=145.0, now=0.0, evidence_key=DEV)
    w.ping_result(1.0, True, snr_db=5.0, gps=(-37.7, 145.01), direct=True)
    obs, _ = w.evidence(medic_id="medic")
    assert obs and obs[0].heard_from == DEV
    w2 = BoundaryWalkSession(node_key=B, node_name="Roof", node_lat=-37.7,
                             node_lon=145.0, now=0.0)
    w2.ping_result(1.0, True, snr_db=5.0, gps=(-37.7, 145.01), direct=True)
    assert w2.evidence(medic_id="medic")[0][0].heard_from == B


def test_a_nan_never_reaches_the_evidence_files(tmp_path):
    w = BoundaryWalkSession(node_key=B, node_name="Roof", node_lat=-37.7,
                            node_lon=145.0, now=0.0)
    w.ping_result(1.0, True, snr_db=float("nan"), gps=(-37.7, 145.01),
                  direct=True)
    w.ping_result(2.0, True, snr_db=4.0, gps=(-37.7, 145.02), direct=True)
    obs, fails = w.evidence(medic_id="medic")
    with pytest.raises(ValueError):
        append_evidence(obs, fails, base_dir=str(tmp_path))
    assert not os.path.exists(tmp_path / "walk_observations.jsonl"), (
        "refused BEFORE anything was written — no half-banked walk")


# -- the leave decision, pure --------------------------------------------------

def test_with_nothing_running_leaving_is_not_a_question():
    assert leave_plan(False, False)["ask"] is False
    assert leave_plan(False, False, has_samples=True)["ask"] is False


def test_a_live_walk_with_samples_asks_and_is_banked_on_confirm():
    p = leave_plan(True, False, has_samples=True)
    assert p == {"ask": True, "persist": True, "then": "home", "reason": "save"}


def test_a_live_walk_with_no_samples_asks_but_banks_nothing():
    p = leave_plan(True, False, has_samples=False)
    assert p == {"ask": True, "persist": False, "then": "home",
                 "reason": "empty"}
    assert leave_plan(True, True, has_samples=False)["reason"] == "empty", (
        "a live walk outranks whatever the gate flag says")


def test_a_gate_asks_but_has_nothing_to_bank():
    p = leave_plan(False, True)
    assert p == {"ask": True, "persist": False, "then": "home", "reason": "gate"}


# -- the screen's methods, run against a stub ----------------------------------

class _FakeConfirm:
    """Records what confirm_leave was asked to show; the test presses."""
    last = None
    opened = 0

    def __init__(self, message, title, on_leave, stay_text, leave_text,
                 leave_color="accent", auto_dismiss=True, on_dismiss=None):
        self.message, self.title, self.on_leave = message, title, on_leave
        self.stay_text, self.leave_text = stay_text, leave_text
        self.leave_color, self.auto_dismiss = leave_color, auto_dismiss
        self.on_dismiss = on_dismiss
        _FakeConfirm.last = self
        _FakeConfirm.opened += 1

    def press_leave(self):
        # the real card dismisses first (on_dismiss), then runs on_leave
        if self.on_dismiss:
            self.on_dismiss()
        self.on_leave()

    def press_stay(self):
        if self.on_dismiss:
            self.on_dismiss()


def _gate():
    """A panel-shaped stub: the real teardown asks for its parent."""
    return types.SimpleNamespace(parent=None)


class _Ev:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Clock:
    scheduled = []

    @staticmethod
    def schedule_interval(fn, dt):
        _Clock.scheduled.append((fn, dt))
        return _Ev()

    @staticmethod
    def schedule_once(fn, dt=0):
        fn(0)


def _install_stubs(monkeypatch, app=None):
    confirm_mod = types.ModuleType("ui.confirm")
    confirm_mod.confirm_leave = _FakeConfirm
    monkeypatch.setitem(sys.modules, "ui.confirm", confirm_mod)
    switched = []
    if app is None:
        app = types.SimpleNamespace()
    app.switch_mode = switched.append
    app_mod = types.ModuleType("kivy.app")
    app_mod.App = types.SimpleNamespace(get_running_app=lambda: app)
    monkeypatch.setitem(sys.modules, "kivy.app", app_mod)
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = _Clock
    monkeypatch.setitem(sys.modules, "kivy.clock", clock_mod)
    popups = []
    req_mod = types.ModuleType("ui.requirement_popup")
    req_mod.requirement_popup = lambda msg, title, *a, **kw: popups.append(
        (title, msg, kw.get("tone")))
    monkeypatch.setitem(sys.modules, "ui.requirement_popup", req_mod)
    _FakeConfirm.last = None
    _FakeConfirm.opened = 0
    _Clock.scheduled = []
    return switched, popups


#: Shipped as @staticmethod — the decorator is not part of the def's
#: source segment, so it is put back when the method is installed.
_STATIC = ("_walk_age_text", "_walk_button")

_METHODS = ("handle_back", "handle_home", "_walk_leave_plan",
            "_confirm_leave_walk", "_leave_walk", "_go_home",
            "_walk_busy_heartbeat", "end_walk", "_walk_gate_go",
            "_start_walk_now", "_tear_down_walk_gate", "_walk_age_text",
            "_walk_show_roads")


class _Colours(dict):
    """theme.COLORS stand-in: any name is a colour, spelled as itself."""

    def __missing__(self, key):
        return key


def _compile(names):
    ns = {"tr": lambda s: s, "theme": types.SimpleNamespace(
        hex_to_rgba=lambda h: h, COLORS=_Colours(), font_sp=lambda s: s),
        "dp": lambda v: v, "print": print}
    for name in names:
        exec(compile(textwrap.dedent(func_source(SCAN, name, cls="ScanScreen")),
                     SCAN, "exec"), ns)
    return ns


class _Plot:
    def __init__(self):
        self.trails = []

    def set_walk_trail(self, samples):
        self.trails.append(list(samples))


def _screen(monkeypatch, walk=None, gate=None, gate_ev=None, inflight=False,
            record=None, app=None):
    """A ScanScreen-shaped stub carrying the shipped methods."""
    switched, popups = _install_stubs(monkeypatch, app=app)
    ns = _compile(_METHODS)
    ended = []

    class _S:
        _walk_session = walk
        _walk_gate = gate
        _walk_gate_ev = gate_ev
        _walk_check_inflight = inflight
        _walk_record = record or types.SimpleNamespace(dst_hash=B, name="Roof",
                                                       lat=None, lon=None)
        _walk_anchor_keys = [DEV, B]
        _walk_fix_meta = {"sats": 7, "accuracy_m": 4.2}
        _walk_leave_popup = None
        _walk_done_cb = None
        plot = _Plot()
        hud_shown = []
        placement = []
        gates_shown = 0
        widgets = []

        def _walk_node_name(self):
            return "Roof"

        def _show_walk_hud(self):
            self.hud_shown.append((getattr(self, "_walk_anchor_saved_at", "?"),
                                   getattr(self, "_walk_anchor_save_ok", "?")))

        def _show_walk_gate(self):
            self.gates_shown += 1

        def _walk_gate_tick(self, _dt):
            self.ticked = getattr(self, "ticked", 0) + 1

        def _walk_tick(self, _dt):
            pass

        def _walk_flash(self, _dt):
            pass

        def _show_placement(self, show):
            self.placement.append(show)

        def _walk_signal_story(self, w):
            return "story"

        def remove_widget(self, w):
            self.widgets.append(("removed", w))
    for name in _METHODS:
        setattr(_S, name, staticmethod(ns[name]) if name in _STATIC
                else ns[name])
    s = _S()
    real_end = s.end_walk

    def spy_end(persist=True):
        ended.append(persist)
        return real_end(persist=persist)
    s.end_walk = spy_end
    return s, ended, switched, popups


def _live_walk(samples=1):
    w = BoundaryWalkSession(node_key=B, node_name="Roof", node_lat=-37.7,
                            node_lon=145.0, now=0.0, evidence_key=DEV)
    for i in range(samples):
        w.ping_result(float(i + 1), True, snr_db=5.0,
                      gps=(-37.7, 145.0 + 0.01 * (i + 1)), direct=True)
    return w


# -- back / home ---------------------------------------------------------------

def test_back_with_no_walk_falls_through_to_home_as_before(monkeypatch):
    s, ended, switched, _ = _screen(monkeypatch)
    assert s.handle_back() is False
    assert _FakeConfirm.last is None and ended == [] and switched == []


def test_home_with_no_walk_is_a_plain_switch(monkeypatch):
    s, ended, switched, _ = _screen(monkeypatch)
    s.handle_home()
    assert switched == ["home"] and ended == [] and _FakeConfirm.last is None


def test_back_mid_walk_asks_and_stays_put(monkeypatch):
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())
    assert s.handle_back() is True, "handled: the popup owns what happens next"
    pop = _FakeConfirm.last
    assert pop is not None and pop.title == "Leave the walk?"
    assert pop.stay_text == "Keep walking"
    assert pop.leave_text == "End the walk and save"
    assert "Roof" in pop.message and "start position is kept" in pop.message
    assert pop.auto_dismiss is False, "a stray tap does nothing"
    assert ended == [] and switched == [], "nothing happens until a press"


def test_a_walk_with_no_ping_yet_asks_and_banks_nothing(monkeypatch, anchors,
                                                        capsys):
    s, ended, switched, popups = _screen(monkeypatch, walk=_live_walk(0))
    s.handle_back()
    pop = _FakeConfirm.last
    assert pop.leave_text == "Leave — nothing recorded yet"
    assert "nothing to save" in pop.message
    pop.press_leave()
    assert ended == [False] and switched == ["home"]
    assert popups == [], "no green card for a walk that measured nothing"
    assert s._walk_session is None


def test_confirming_mid_walk_banks_the_walk_then_goes_home(monkeypatch,
                                                          anchors):
    import monitor.boundary_walk as bw
    banked = []
    monkeypatch.setattr(bw, "append_evidence",
                        lambda obs, fails, **kw: banked.append((obs, fails)))
    s, ended, switched, popups = _screen(monkeypatch, walk=_live_walk(2))
    s.handle_back()
    _FakeConfirm.last.press_leave()
    assert ended == [True], "end_walk(persist=True): the evidence is banked"
    assert banked and banked[0][0][0].heard_from == DEV
    assert switched == ["home"]
    assert popups and popups[0][0] == "Range test finished"
    assert s._walk_session is None


def test_the_plan_is_re_read_at_the_press(monkeypatch, anchors):
    """The card opened on a walk with no pings; one answered while it stood.
    What is banked is what is true at the press."""
    import monitor.boundary_walk as bw
    banked = []
    monkeypatch.setattr(bw, "append_evidence",
                        lambda obs, fails, **kw: banked.append(1))
    w = _live_walk(0)
    s, ended, switched, popups = _screen(monkeypatch, walk=w)
    s.handle_back()
    assert _FakeConfirm.last.leave_text == "Leave — nothing recorded yet"
    w.ping_result(5.0, True, snr_db=5.0, gps=(-37.7, 145.01), direct=True)
    _FakeConfirm.last.press_leave()
    assert ended == [True] and banked == [1] and switched == ["home"]


def test_home_mid_walk_asks_the_same_question(monkeypatch, anchors):
    import monitor.boundary_walk as bw
    monkeypatch.setattr(bw, "append_evidence", lambda *a, **kw: None)
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())
    s.handle_home()
    assert _FakeConfirm.last.title == "Leave the walk?"
    assert switched == [] and ended == []
    _FakeConfirm.last.press_leave()
    assert ended == [True] and switched == ["home"]


def test_leaving_at_a_ticking_gate_asks_and_drops_the_gate_without_saving(
        monkeypatch):
    s, ended, switched, popups = _screen(monkeypatch, gate=_gate(),
                                         gate_ev=_Ev())
    assert s.handle_back() is True
    pop = _FakeConfirm.last
    assert pop.leave_text == "Leave — nothing to save"
    assert "nothing to save" in pop.message and "Roof" in pop.message
    pop.press_leave()
    assert ended == [False] and switched == ["home"] and popups == []
    assert s._walk_gate is None and s._walk_gate_ev is None


def test_a_probe_in_flight_is_a_gate_doing_something(monkeypatch):
    s, ended, switched, _ = _screen(monkeypatch, gate=_gate(), inflight=True)
    assert s.handle_back() is True
    assert _FakeConfirm.last.leave_text == "Leave — nothing to save"


def test_a_refusal_card_is_not_a_walk_back_tears_it_down_and_goes_home(
        monkeypatch):
    """The refusal / finished-check panel has its own Close. Back there is
    not a question: the panel goes, nothing is banked, the bar goes home."""
    s, ended, switched, popups = _screen(monkeypatch, gate=_gate())
    assert s.handle_back() is False
    assert _FakeConfirm.last is None
    assert ended == [False] and s._walk_gate is None and popups == []
    s2, ended2, switched2, _ = _screen(monkeypatch, gate=_gate())
    s2.handle_home()
    assert ended2 == [False] and switched2 == ["home"]


def test_the_leave_button_is_not_red(monkeypatch):
    """Red on this tool means Delete / Rebirth; this button SAVES the walk
    (the Stop & save rule, 2026-09-21)."""
    s, _, _, _ = _screen(monkeypatch, walk=_live_walk())
    s.handle_back()
    assert _FakeConfirm.last.leave_color != "red"


# -- the road never raises, never leaves by accident ------------------------------

def test_a_failed_ask_stays_on_the_walk_and_says_why(monkeypatch, capsys):
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())

    def boom(plan):
        raise RuntimeError("no window")
    s._confirm_leave_walk = boom
    assert s.handle_back() is True, "handled = stay"
    assert ended == [] and switched == [] and s._walk_session is not None
    assert "NOTICE" in capsys.readouterr().out
    s.handle_home()
    assert ended == [] and switched == [] and s._walk_session is not None
    assert "NOTICE" in capsys.readouterr().out


def test_a_broken_plan_stays_too(monkeypatch, capsys):
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())
    s._walk_leave_plan = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    assert s.handle_back() is True
    s.handle_home()
    assert ended == [] and switched == [] and s._walk_session is not None


def test_end_walk_raising_inside_leave_still_goes_home_and_is_said(
        monkeypatch, capsys):
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())
    s.handle_back()

    def boom(persist=True):
        raise RuntimeError("plot gone")
    s.end_walk = boom
    _FakeConfirm.last.press_leave()          # must not raise out of the card
    assert switched == ["home"]
    assert "NOTICE" in capsys.readouterr().out
    assert s._walk_session is not None, "not claimed banked"


def test_the_fuse_a_second_press_while_the_card_is_open_is_ignored(monkeypatch):
    s, ended, switched, _ = _screen(monkeypatch, walk=_live_walk())
    assert s.handle_back() is True
    assert _FakeConfirm.opened == 1
    assert s.handle_back() is True
    s.handle_home()
    assert _FakeConfirm.opened == 1, "one card"
    assert ended == [] and switched == []
    _FakeConfirm.last.press_stay()
    assert s._walk_leave_popup is None, "dismiss resets the fuse"
    s.handle_back()
    assert _FakeConfirm.opened == 2


# -- end_walk: the session goes last, and the busy marker clears at once --------

def test_end_walk_banks_inside_the_try_and_says_a_failed_save(monkeypatch,
                                                              anchors, capsys):
    import monitor.boundary_walk as bw

    def boom(obs, fails, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(bw, "append_evidence", boom)
    s, ended, switched, popups = _screen(monkeypatch, walk=_live_walk(2))
    s.end_walk(persist=True)
    assert popups and popups[0][0] == "Range test not saved"
    assert "Could not save the walk" in popups[0][1]
    assert s._walk_session is None, "let go of AFTER the failure was said"
    assert "not saved" in capsys.readouterr().out


def test_end_walk_keeps_the_session_if_it_dies_before_banking(monkeypatch,
                                                              anchors):
    """A raise before the evidence is written leaves the session in place:
    the leave road then still sees a live walk, not one that silently
    ceased to exist."""
    s, ended, switched, popups = _screen(monkeypatch, walk=_live_walk(2))

    class _DeadPlot:
        def set_walk_trail(self, _s):
            raise RuntimeError("plot gone")
    s.plot = _DeadPlot()
    with pytest.raises(RuntimeError):
        s.end_walk(persist=True)
    assert s._walk_session is not None
    assert popups == []


def test_end_walk_pokes_the_busy_heartbeat_at_once(monkeypatch, anchors):
    import monitor.boundary_walk as bw
    monkeypatch.setattr(bw, "append_evidence", lambda *a, **kw: None)
    beats = []
    app = types.SimpleNamespace(_busy_heartbeat=lambda: beats.append(1))
    s, *_ = _screen(monkeypatch, walk=_live_walk(1), app=app)
    s.end_walk(persist=True)
    assert beats == [1]
    s2, *_ = _screen(monkeypatch, gate=_gate(), app=app)
    s2.end_walk(persist=False)
    assert beats == [1, 1], "a cancelled gate clears the marker too"


def test_end_walk_without_a_heartbeat_on_the_app_is_fine(monkeypatch, anchors):
    s, *_ = _screen(monkeypatch, walk=_live_walk(0))
    s.end_walk(persist=False)
    assert s._walk_session is None


def test_teardown_resets_the_steady_fix_counter(monkeypatch):
    s, *_ = _screen(monkeypatch, gate=_gate(), gate_ev=_Ev(), inflight=True)
    s._walk_ready_ticks = 2
    s._walk_go = object()
    s._walk_go_saved = object()
    s._tear_down_walk_gate()
    assert s._walk_ready_ticks == 0 and s._walk_check_inflight is False
    assert s._walk_go is None and s._walk_go_saved is None
    assert s._walk_gate is None and s._walk_gate_ev is None


# -- the gate's press: two roads, the overwrite guard, the read-back ------------

def _at_gate(monkeypatch, live, saved, record=None):
    s, ended, switched, popups = _screen(monkeypatch, gate=_gate(),
                                         gate_ev=_Ev(), record=record)
    s._walk_anchor = live
    s._walk_saved_anchor = saved
    return s, ended, switched, popups


def test_the_here_road_saves_the_live_fix_and_reads_it_back(monkeypatch,
                                                            anchors):
    s, *_ = _at_gate(monkeypatch, live=(-37.7, 145.0), saved=None)
    s._walk_gate_go()
    assert s._walk_gate is None, "the gate is torn down at the press"
    w = s._walk_session
    assert w is not None and (w.node_lat, w.node_lon) == (-37.7, 145.0)
    assert w.node_key == B and w.evidence_key == DEV
    got = wa.load_anchor([DEV])
    assert got["lat"] == -37.7 and got["sats"] == 7 and got["accuracy_m"] == 4.2
    assert s.hud_shown == [(None, True)], "HUD told only after the read-back"
    assert len(_Clock.scheduled) == 2


def test_the_saved_road_starts_from_the_saved_anchor_and_does_not_resave(
        monkeypatch, anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    s, *_ = _at_gate(monkeypatch, live=(-37.71, 145.01),
                     saved=wa.load_anchor([DEV]))
    s._walk_gate_go(use_saved=True)
    w = s._walk_session
    assert (w.node_lat, w.node_lon) == (-37.7, 145.0)
    assert wa.load_anchor([DEV])["at"] == 100.0, "never moved"
    assert s.hud_shown == [(100.0, None)]
    assert _FakeConfirm.last is None


def test_no_live_fix_at_the_press_starts_nothing_on_either_road(monkeypatch,
                                                                anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    s, *_ = _at_gate(monkeypatch, live=None, saved=wa.load_anchor([DEV]))
    s._walk_gate_go(use_saved=True)
    s._walk_gate_go()
    assert s._walk_session is None and s._walk_gate is not None
    assert s.ticked == 2, "the sky is looked at again instead"


def test_the_overwrite_guard_asks_when_far_from_the_saved_position(monkeypatch,
                                                                   anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    s, *_ = _at_gate(monkeypatch, live=(-37.7, 145.003),
                     saved=wa.load_anchor([DEV]))
    s._walk_gate_go()
    pop = _FakeConfirm.last
    assert pop is not None and pop.title == "Replace the start position?"
    assert pop.stay_text == "Keep the saved position"
    assert pop.leave_text == "Replace it"
    assert "Roof" in pop.message and " m from the start position" in pop.message
    assert pop.auto_dismiss is False
    assert s._walk_session is None and s._walk_gate is not None, (
        "nothing moves until the answer")
    pop.press_stay()
    assert s._walk_session is None and wa.load_anchor([DEV])["at"] == 100.0
    s._walk_gate_go()
    _FakeConfirm.last.press_leave()
    assert s._walk_session is not None and s._walk_gate is None
    assert wa.load_anchor([DEV])["lon"] == 145.003, "replaced on yes"


def test_within_the_threshold_the_here_road_resaves_silently(monkeypatch,
                                                             anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    s, *_ = _at_gate(monkeypatch, live=(-37.7, 145.0005),
                     saved=wa.load_anchor([DEV]))
    s._walk_gate_go()
    assert _FakeConfirm.last is None
    assert s._walk_session is not None
    assert wa.load_anchor([DEV])["lon"] == 145.0005


def test_a_failed_save_still_starts_the_walk_and_says_so(monkeypatch, anchors,
                                                         capsys):
    def boom(*a, **kw):
        raise OSError("read-only file system")
    monkeypatch.setattr(wa, "save_anchor", boom)
    s, *_ = _at_gate(monkeypatch, live=(-37.7, 145.0), saved=None)
    s._walk_gate_go()
    assert s._walk_session is not None, "the walk itself needs no file"
    assert s.hud_shown == [(None, False)], "HUD told NOT saved"
    assert "anchor not saved" in capsys.readouterr().out


def test_a_save_that_reads_back_wrong_is_not_claimed(monkeypatch, anchors,
                                                     capsys):
    monkeypatch.setattr(wa, "load_anchor", lambda keys, **kw: {
        "lat": 1.0, "lon": 1.0, "at": 1.0, "sats": None, "accuracy_m": None})
    s, *_ = _at_gate(monkeypatch, live=(-37.7, 145.0), saved=None)
    s._walk_gate_go()
    assert s.hud_shown == [(None, False)]
    assert "read-back" in capsys.readouterr().out


def test_an_anchor_off_earth_never_starts_a_walk(monkeypatch, anchors, capsys):
    s, *_ = _screen(monkeypatch)
    for bad in ((float("nan"), 1.0), (0.0, 0.0), (91.0, 1.0), ("1", "2")):
        s._start_walk_now(bad)
    assert s._walk_session is None and s.gates_shown == 4
    assert s.hud_shown == [] and _Clock.scheduled == []
    assert "unusable anchor" in capsys.readouterr().out
    assert not os.path.exists(wa._path())


def test_the_hud_line_says_which_start_position_and_only_after_read_back():
    hud = func_source(SCAN, "_show_walk_hud", cls="ScanScreen")
    code = hud.split('"""')[-1]
    # (the long literals are split across lines in the source)
    assert "Measuring from the start position saved {age} " in code
    assert "saved as {name}'s start " in code
    assert "could NOT be saved" in code
    assert "_walk_anchor_save_ok" in code
    assert code.index("elif save_ok") < code.index("saved as {name}'s")
    assert code.index("hud.add_widget(self._walk_anchor_lbl)") < code.index(
        "hud.add_widget(top)")


# -- the gate panel: wording and the disagreement line ---------------------------

class _Box:
    def __init__(self, **kw):
        self.kids = []
        self.kw = kw

    def add_widget(self, w, **kw):
        self.kids.append(w)

    def bind(self, **kw):
        pass

    def setter(self, name):
        return lambda *a: None


def _gate_screen(monkeypatch, saved, rec, sats=None):
    """_show_walk_gate compiled, with every widget a recorder."""
    _install_stubs(monkeypatch)
    ns = _compile(("_show_walk_gate", "_walk_show_roads", "_walk_age_text"))
    texts = []
    buttons = []
    ns.update({"BoxLayout": _Box, "Widget": _Box, "_FixBadge": _Box,
               "Button": lambda **kw: _Box(**kw)})

    class _S:
        _walk_record = rec
        _walk_anchor_keys = [DEV]
        children = []

        def _walk_node_name(self):
            return "Roof"

        def _tear_down_walk_gate(self):
            pass

        def _walk_panel(self):
            return _Box()

        @staticmethod
        def _walk_text(text, size, color, bold=False):
            texts.append((text, color))
            return _Box(text=text)

        @staticmethod
        def _walk_button(text, bg, fg):
            b = _Box(text=text, bg=bg)
            buttons.append(b)
            return b

        def add_widget(self, w, index=0):
            pass

        def _walk_gate_tick(self, _dt):
            pass
    for name in ("_show_walk_gate", "_walk_show_roads", "_walk_age_text"):
        setattr(_S, name, staticmethod(ns[name]) if name in _STATIC
                else ns[name])
    return _S(), texts, buttons


def test_the_gate_with_no_anchor_offers_one_road_worded_as_a_save(monkeypatch,
                                                                  anchors):
    rec = types.SimpleNamespace(dst_hash=B, name="Roof", lat=None, lon=None)
    s, texts, buttons = _gate_screen(monkeypatch, None, rec)
    s._show_walk_gate()
    assert [b.kw["text"] for b in buttons] == [
        "Start here — save this as Roof's start position"]
    assert s._walk_go_saved is None
    assert any(t.startswith("Stand next to Roof.") for t, _c in texts)
    assert not any("reports itself" in t for t, _c in texts)


def test_the_gate_with_an_anchor_offers_both_roads_with_age_and_accuracy(
        monkeypatch, anchors):
    import time as _t
    wa.save_anchor(DEV, -37.7, 145.0, _t.time() - 2 * 3600, sats=8,
                   accuracy_m=3.6)
    rec = types.SimpleNamespace(dst_hash=B, name="Roof", lat=-37.7, lon=145.0)
    s, texts, buttons = _gate_screen(monkeypatch, None, rec)
    s._show_walk_gate()
    words = [b.kw["text"] for b in buttons]
    assert words[0] == "Start here — save this as Roof's start position"
    assert words[1].startswith("Start away — use the start position saved ")
    assert words[1].endswith(" ago (~±4m)")
    assert "2.0h" in words[1]
    assert buttons[0].kw["bg"] == "green" and buttons[1].kw["bg"] == "accent"
    assert any("has a start position saved 2.0h ago" in t for t, _c in texts)


def test_an_anchor_with_no_accuracy_estimate_says_none(monkeypatch, anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    rec = types.SimpleNamespace(dst_hash=B, name="Roof", lat=None, lon=None)
    s, texts, buttons = _gate_screen(monkeypatch, None, rec)
    s._show_walk_gate()
    assert "(~±" not in buttons[1].kw["text"], "never an invented number"


def test_the_disagreement_line_appears_only_when_the_node_reports_elsewhere(
        monkeypatch, anchors):
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    far = types.SimpleNamespace(dst_hash=B, name="Roof", lat=-37.7, lon=145.003)
    s, texts, _ = _gate_screen(monkeypatch, None, far)
    s._show_walk_gate()
    line = [t for t, c in texts if "reports itself" in t]
    assert len(line) == 1 and line[0].startswith(
        "The saved start position is ") and "from where Roof reports" in line[0]
    assert [c for t, c in texts if "reports itself" in t] == ["amber"]
    near = types.SimpleNamespace(dst_hash=B, name="Roof", lat=-37.7, lon=145.0005)
    s, texts, _ = _gate_screen(monkeypatch, None, near)
    s._show_walk_gate()
    assert not any("reports itself" in t for t, _c in texts)
    unknown = types.SimpleNamespace(dst_hash=B, name="Roof", lat=None, lon=None)
    s, texts, _ = _gate_screen(monkeypatch, None, unknown)
    s._show_walk_gate()
    assert not any("reports itself" in t for t, _c in texts)


def test_the_gate_finds_the_anchor_under_any_of_the_devices_keys(monkeypatch,
                                                                 anchors):
    wa.save_anchor("id-1", -37.7, 145.0, 100.0)
    rec = types.SimpleNamespace(dst_hash=B, name="Roof", lat=None, lon=None)
    s, texts, buttons = _gate_screen(monkeypatch, None, rec)
    s._walk_anchor_keys = [DEV, "id-1", B]
    s._show_walk_gate()
    assert s._walk_saved_anchor["lat"] == -37.7 and len(buttons) == 2


def test_the_gate_names_an_unusable_live_fix_as_its_own_state():
    tick = func_source(SCAN, "_walk_gate_tick", cls="ScanScreen")
    assert '"invalid"' in tick and "not a place on Earth" in tick
    assert "_walk_fix_meta" in tick, "sats/accuracy ride with the anchor"


def test_both_start_buttons_grow_to_their_words():
    btn = func_source(SCAN, "_walk_button", cls="ScanScreen")
    assert "text_size" in btn and "texture_size" in btn and "halign" in btn
    gate = func_source(SCAN, "_show_walk_gate", cls="ScanScreen")
    assert gate.count("self._walk_button(") == 2
    assert "GPS found" not in gate, "the button says what the press DOES"


# -- the app's side: both doors, one key; home never raises -----------------------

def _app_method(name):
    ns = {"print": print}
    exec(compile(textwrap.dedent(func_source(APP, name,
                                             cls="ReticulumNodeMedicApp")),
                 APP, "exec"), ns)
    return ns[name]


def test_the_antenna_door_hands_over_the_registrys_own_record():
    reg, _v, member_b, _c = _t114()
    started = []
    app = types.SimpleNamespace(
        monitor_service=types.SimpleNamespace(registry=reg),
        _start_boundary_walk=started.append)
    _app_method("_walk_from_pick")(app, {"dst_hash": B, "name": "x"})
    assert started == [member_b], "the real record, with its identity/device"
    _app_method("_walk_from_pick")(app, {"dst_hash": "zz" * 16, "name": "gone",
                                         "lat": 1.0, "lon": 2.0})
    assert started[1].dst_hash == "zz" * 16 and started[1].name == "gone"


def test_both_doors_reach_begin_walk_with_the_same_anchor_keys():
    reg, vitals, member_b, _c = _t114()
    reg.probe_hash_for = lambda key: key if len(key) == 32 else A
    begun = []
    app = types.SimpleNamespace(
        monitor_service=types.SimpleNamespace(registry=reg),
        switch_mode=lambda m: None,
        scan_screen=types.SimpleNamespace(
            begin_walk=lambda rec, ping, reach_probe=None, anchor_keys=None:
            begun.append((rec, anchor_keys))),
        _walk_probe=lambda *a: None, _mesh_reachable=lambda d, wait=0: True)
    start = _app_method("_start_boundary_walk")
    start(app, vitals)
    start(app, member_b)
    (r1, k1), (r2, k2) = begun
    assert k1[0] == DEV and set(k1) == set(k2)
    assert r1.dst_hash == A and r2.dst_hash == B


def test_the_key_rewrite_is_not_conditioned_on_a_dataclass():
    reg, *_ = _t114()
    reg.probe_hash_for = lambda key: A if key.startswith("rtnode:") else key
    begun = []
    app = types.SimpleNamespace(
        monitor_service=types.SimpleNamespace(registry=reg),
        switch_mode=lambda m: None,
        scan_screen=types.SimpleNamespace(
            begin_walk=lambda rec, ping, reach_probe=None, anchor_keys=None:
            begun.append(rec)),
        _walk_probe=lambda *a: None, _mesh_reachable=lambda d, wait=0: True)
    plain = types.SimpleNamespace(dst_hash="rtnode:roof", name="Roof",
                                  identity_hash=None, device_id=DEV)
    _app_method("_start_boundary_walk")(app, plain)
    assert begun[0].dst_hash == A and begun[0] is not plain, "a copy, rewritten"
    assert plain.dst_hash == "rtnode:roof", "the caller's record untouched"
    body = func_source(APP, "_start_boundary_walk", cls="ReticulumNodeMedicApp")
    assert "is_dataclass" not in body


def test_the_bars_home_swallows_a_raising_exit_road_and_stays(capsys):
    ns = {"print": print}
    exec(compile(textwrap.dedent(func_source(APP, "_with_back")), APP, "exec"),
         ns)

    class _Wrap:
        def __init__(self, on_back, on_home):
            self.on_back, self.on_home = on_back, on_home

        def add_content(self, w):
            pass
    ns["_BackSwipeWrap"] = _Wrap
    switched = []
    app = types.SimpleNamespace(switch_mode=switched.append)

    def boom():
        raise RuntimeError("no window")
    wrap = ns["_with_back"](app, types.SimpleNamespace(handle_home=boom,
                                                       handle_back=boom))
    wrap.on_home()
    assert switched == [], "stays put"
    assert "NOTICE" in capsys.readouterr().out
    wrap.on_back()
    assert switched == ["home"], "back falls through as it always did"


# -- delete / rebirth forget the anchor by every device key -------------------------

def test_forgetting_a_node_forgets_its_anchor_by_keys_only():
    body = func_source(APP, "_forget_node", cls="ReticulumNodeMedicApp")
    assert "forget_anchor(anchor_keys)" in body
    assert "forget_anchor(name" not in body
    assert body.index("anchor_keys |= set(candidate_keys(rec") < body.index(
        "reg.forget_node("), "keys read BEFORE the rows go"


def _nested(rel, outer, inner):
    tree = ast.parse(src(rel))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == outer:
            for m in ast.walk(n):
                if isinstance(m, ast.FunctionDef) and m.name == inner:
                    return textwrap.dedent(ast.get_source_segment(src(rel), m))
    raise AssertionError(f"{outer}.{inner} not found")


def test_rebirth_forgets_the_predecessors_anchor(anchors):
    """The retire sweep's key collector, run: a predecessor row known by
    destination B and device DEV loses its anchor whichever key it was
    saved under — and the collector reads the row BEFORE forget_node."""
    text = src(BIRTH)
    outer = next(n.name for n in ast.walk(ast.parse(text))
                 if isinstance(n, ast.FunctionDef)
                 and any(isinstance(m, ast.FunctionDef)
                         and m.name == "_note_retired" for m in ast.walk(n)))
    reg, _v, member_b, _c = _t114()
    ns = {"_reg": reg, "_anchor_keys": set()}
    exec(compile(_nested(BIRTH, outer, "_note_retired"), BIRTH, "exec"), ns)
    wa.save_anchor(DEV, -37.7, 145.0, 100.0)
    ns["_note_retired"]([B])
    assert {B, DEV, "id-1"} <= ns["_anchor_keys"]
    assert wa.forget_anchor(ns["_anchor_keys"]) == 1
    assert wa.load_anchor([DEV, B]) is None
    # wiring: all three sweeps feed the collector, then one forget
    body = func_source(BIRTH, outer)
    for sweep in ("_note_retired(_prev)", "_note_retired(_replaced)",
                  "_note_retired(_dead)"):
        assert sweep in body, sweep
    assert body.index("_note_retired(_dead)") < body.index(
        "forget_anchor(_anchor_keys)")
    assert body.index("_note_retired(_prev)") < body.index(
        "_reg.forget_node(_old)")


# -- the confirm card ------------------------------------------------------------

def test_the_confirm_card_puts_the_safe_choice_first_and_scrolls():
    body = func_source("ui/confirm.py", "confirm_leave")
    assert body.index("row.add_widget(stay)") < body.index("row.add_widget(go)")
    assert 'leave_color="accent"' in body, "not red by default"
    assert "auto_dismiss=True" in body.split(")")[0], "stay on a stray tap, by default"
    assert "ScrollView" in body and "_LEAVE_MAX_H_FRAC" in body
    assert "Window.unbind(height=_on_win_resize)" in body
    assert src("ui/confirm.py").count("_LEAVE_MAX_H_FRAC = 0.9") == 1


def test_the_birth_guides_leave_card_is_the_same_card_in_red(monkeypatch):
    confirm_mod = types.ModuleType("ui.confirm")
    confirm_mod.confirm_leave = _FakeConfirm
    monkeypatch.setitem(sys.modules, "ui.confirm", confirm_mod)
    _FakeConfirm.last = None
    ns = {"tr": lambda s: s}
    exec(compile(textwrap.dedent(func_source(GUIDE, "_confirm_exit_popup")),
                 GUIDE, "exec"), ns)
    left = []
    scr = types.SimpleNamespace(_exit_to_home=lambda: left.append("home"))
    ns["_confirm_exit_popup"](scr)
    pop = _FakeConfirm.last
    assert pop.title == "Leave this build?" and pop.leave_color == "red"
    assert pop.stay_text == "Cancel — stay" and pop.leave_text == "OK — go home"
    assert "keeps running" in pop.message
    pop.press_leave()
    assert left == ["home"]
    text = src(GUIDE)
    assert "ModalView" not in text and "RoundedRectangle" not in text, (
        "no second copy of the card left behind")


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


def test_both_roads_wait_for_the_same_steady_fix():
    tick = func_source(SCAN, "_walk_gate_tick", cls="ScanScreen")
    assert "_walk_show_roads(ready)" in tick
    go = func_source(SCAN, "_walk_gate_go", cls="ScanScreen")
    code = go.split('"""')[-1]
    assert code.index("if live is None") < code.index("use_saved"), (
        "the saved road is not a way around the sky")


def test_the_diagnosis_scope_is_one_walk_and_placement_reads_all():
    story = func_source(SCAN, "_walk_signal_story", cls="ScanScreen")
    assert "diagnose(w.samples" in story
    app = src(APP)
    assert "load_walk_observations()" in app and "load_walk_failures()" in app


# -- i18n -----------------------------------------------------------------------

NEW_STRINGS = {
    "Leave the walk?": {},
    "Keep walking": {},
    "End the walk and save": {},
    "Leave — nothing to save": {},
    "Leave — nothing recorded yet": {},
    "Stand next to {name} — or start from where you are.": {"name": "Roof"},
    "Stand next to {name}.": {"name": "Roof"},
    "{name} has a start position saved {age} ago — where you stood when that "
    "walk began. Standing at the node again saves a fresh one; away from it, "
    "the saved one is used. Either way, wait for a satellite fix first — every "
    "ping is placed by the live fix.": {"name": "Roof", "age": "3.2h"},
    "The saved start position is {m} m from where {name} reports itself — if "
    "it has been moved, start at the node.": {"m": 210, "name": "Roof"},
    "Start here — save this as {name}'s start position": {"name": "Roof"},
    "Start away — use the start position saved {age} ago (~±{acc}m)":
        {"age": "3.2h", "acc": 4},
    "Start away — use the start position saved {age} ago": {"age": "3.2h"},
    "The GPS reports a fix but its position is not usable (not a place on "
    "Earth). Wait for the next fix; if it stays, run Self Diagnose from Settings.":
        {},
    "You are {m} m from the start position saved for {name} {age} ago. "
    "Replace it?": {"m": 210, "name": "Roof", "age": "3.2h"},
    "Replace the start position?": {},
    "Keep the saved position": {},
    "Replace it": {},
    "Measuring from the start position saved {age} ago.": {"age": "3.2h"},
    "Measuring from here — saved as {name}'s start position.": {"name": "Roof"},
    "Measuring from here — the start position could NOT be saved": {},
    "Leaving ends this walk. Everything it has recorded so far is saved and "
    "counts; you can start another walk against {name} from here — its start "
    "position is kept.": {"name": "Roof"},
    "Leaving ends this walk before its first ping, so there is nothing to "
    "save. You can start another walk against {name} from here — its start "
    "position is kept.": {"name": "Roof"},
    "Leaving cancels the wait at the gate — nothing has been measured yet, so "
    "there is nothing to save. You can start another walk against {name} "
    "from here.": {"name": "Roof"},
    "Leave this build?": {},
    "Cancel — stay": {},
    "OK — go home": {},
}

REPLACED_STRINGS = (
    "GPS found — I'm standing at the node (set its position again)",
    "GPS found — I'm away from it (use the position saved on {date})",
    "GPS found — start the walk",
    "Anchored at {name} — position saved {date}.",
    "Anchored here — where you pressed start.",
)


def test_every_new_string_is_wrapped_in_the_source():
    text = src(SCAN) + src(GUIDE)
    flat = " ".join(" ".join(text.split()).split())
    for s in NEW_STRINGS:
        # the source breaks long strings across adjacent literals; compare
        # on a distinctive head
        head = s[:28]
        assert head in flat, f"{head!r} not in the screens"


@pytest.mark.parametrize("code", CATALOGS)
def test_every_new_string_is_in_every_full_catalog_and_formats(code):
    path = os.path.join(ROOT, "assets", "i18n", f"{code}.json")
    with open(path, encoding="utf-8") as fh:
        cat = json.load(fh)
    for s, args in NEW_STRINGS.items():
        assert cat.get(s), f"{code}.json lacks {s!r}"
        # the translation takes the same arguments the source does — a
        # dropped or renamed placeholder is a KeyError on the operator's
        # glass, in a language the traceback reader may not speak
        out = cat[s].format(**args)
        assert out and "{" not in out, f"{code}.json[{s[:30]!r}] left a brace"
        s.format(**args)
    for s in REPLACED_STRINGS:
        assert s not in cat, f"{code}.json still carries the replaced {s!r}"
    assert not any("anchor is kept" in k for k in cat), code


@pytest.mark.parametrize("code", CATALOGS)
def test_the_catalog_is_in_canonical_form(code):
    path = os.path.join(ROOT, "assets", "i18n", f"{code}.json")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    cat = json.loads(raw)
    assert raw == json.dumps(cat, sort_keys=True, indent=2,
                             ensure_ascii=False) + "\n"
