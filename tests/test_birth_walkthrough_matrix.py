"""THE WALKTHROUGH MATRIX — the GUIDE's own step machine, every combination,
end to end, before any of it meets a bench.

Operator, 2026-09-13: "run through emulators all the birth processes for all
boards and all combinations of boards and raspberry pis — check they not only
work, but that they're clear, no repeated steps, the flow takes us all the way
to the end and doesn't dead-end anywhere."

tests/test_birth_matrix.py already sweeps the 17-step BuildWorkflow per
combination. What it never swept is the WALKTHROUGH itself — the navigation
machine in ui/screens/birth_guide_screen.py: renders, skips, gates, screen
hand-offs and resumes. Every bug in that file's why-comments (the silent skip
loop eating the radio gate, the eleven-bounce Back trap, the double-render
smudge) was a NAVIGATION bug the build matrix could never see.

So this file drives the SHIPPED navigation methods — compiled straight out of
the screen module with tests.srcutil.func_source, the same idiom as
test_guide_board_candidates and test_birth_back_swipe, because Kivy cannot be
imported in CI — against a featherweight stand-in screen, through:

    paths (pi / host / radio)
  x Pi models (every pick-list key, plus an unknown "" for defence)
  x every board in workflows.rnode_boards.RNODE_BOARDS
  x radio states (blank-unplugged / blank-on-USB / certified-by-this-medic /
                  "I already have a working radio")

Hand-offs (screen= steps) are resumed immediately with the same payload shapes
birth_screen._hand_back_to_guide and pi_imager_screen actually send.

The laws asserted per run:

  1. TERMINATION   — every combination reaches _finish (the done screen for
                     "pi", on_complete for host/radio), or ends early on an
                     explicitly-refused verdict. The only legitimate early
                     ender is the Pi 3B+ cable impossibility, pinned below.
  2. NO REPEATED STEP — a step index never gets two separate visits on a
                     forward walk (consecutive repaints of one index — a gate
                     warning arriving — are one visit).
  3. MONOTONIC PROGRESS — on success payloads the visit sequence never
                     decreases.
  4. HAND-OFF ROUND TRIP — every hand-off resumes at the step AFTER the one
                     that handed off (or later, when the resumed step is
                     honestly redundant); a failed-build payload lands back ON
                     the step that did the work (the 2026-08-14 law).
  5. GATE HONESTY  — a passed gate advances by itself (never a manual press on
                     the happy path); a blocked gate stays put with its
                     warning however often it is pressed.
"""

import re
import sys
import textwrap
import types

import pytest

from tests.srcutil import func_source, src
from ui.birth_guide_flow import guide_steps
from ui.i18n import tr
from ui.pi_connectors import can_cable
from workflows import power_compat
from workflows.pairing_verdicts import needs_warning
from workflows.rnode_boards import RNODE_BOARDS

SCREEN = "ui/screens/birth_guide_screen.py"
ANIMS_FILE = "ui/widgets/birth_anims.py"

#: The walkthrough's own Pi pick list (birth_screen.PI_HOSTS minus "none" —
#: _render_pick_pi filters that out), plus "" for a Pi key that was somehow
#: never set. "" is unreachable through the flow (the Pi question is always
#: asked) but the machine must not dead-end on it either.
PI_KEYS = ["pi_5_full", "pi_5", "pi_4b", "pi_3b_plus", "pi_3a_plus",
           "pi_zero_2w", ""]
BOARD_KEYS = sorted(RNODE_BOARDS)
#: What the radio's life looks like when the walkthrough starts:
#:   blank_unplugged — nothing on USB yet; the connect instructions must show
#:   blank_present   — a never-flashed board already on USB (the common bench
#:                     case: that is how the chooser knew the board)
#:   certified       — a board this medic flashed AND verified before; its
#:                     certificate is on disk, keyed by the radio's USB serial
#:   have_one        — "I already have a working radio": flash_radio=False,
#:                     the three medic-flashing steps are dropped
RADIO_STATES = ["blank_unplugged", "blank_present", "certified", "have_one"]

CABLE_ADDR = "10.55.0.2"
WIFI_ADDR = "192.168.4.7"


# ---------------------------------------------------------------------------
# featherweight stand-ins for the Kivy-shaped parts
# ---------------------------------------------------------------------------

class _Ev:
    def __init__(self, entry):
        self._entry = entry

    def cancel(self):
        self._entry["dead"] = True


class FakeClock:
    """Records schedules; fires nothing until the driver says so. Delays are
    kept, so the 1.6 s gate beat fires under run_due(5) while the 150 s
    patience button does not — the same distinction the glass shows."""

    def __init__(self):
        self.queue = []

    def schedule_once(self, cb, timeout=0):
        entry = {"cb": cb, "t": timeout, "dead": False}
        self.queue.append(entry)
        return _Ev(entry)

    def schedule_interval(self, cb, dt):
        # Poll loops are stubbed at the _start_* level; an interval that does
        # arrive is simply parked (cancellable, never self-firing).
        entry = {"cb": cb, "t": dt, "dead": True}
        return _Ev(entry)

    def run_due(self, horizon=5.0, limit=50):
        """Fire every pending short-beat callback, in the order scheduled,
        including ones scheduled BY a firing — with a hard cap, because a
        callback chain that never drains is exactly the runaway self-driving
        this suite exists to catch."""
        fired = 0
        while True:
            nxt = next((e for e in self.queue
                        if not e["dead"] and e["t"] <= horizon), None)
            if nxt is None:
                return fired
            nxt["dead"] = True
            fired += 1
            assert fired <= limit, (
                "runaway self-scheduling: the clock never drained — "
                "an auto-advance is re-arming itself")
            nxt["cb"](0)


class FakeAnim:
    def __init__(self, pi_key="", **kw):
        self.pi_key = pi_key
        self.events = []

    def mark_connected(self):
        self.events.append("connected")

    def mark_removed(self):
        self.events.append("removed")

    def mark_moved(self):
        self.events.append("moved")

    def mark_card_found(self):
        self.events.append("card_found")


# The subclass shape matters: _render_step branches on isinstance, and
# DisconnectBoardAnim / RadioToPiAnim / ConnectPiAnim all subclass
# ConnectBoardAnim in the real module — the branch ORDER is what keeps them
# out of the wrong branch. Mirror it exactly (pinned against the source in
# test_the_stand_in_anim_hierarchy_matches_the_real_one).
class FConnectBoard(FakeAnim):
    pass


class FDisconnect(FConnectBoard):
    pass


class FRadioToPi(FConnectBoard):
    pass


class FConnectPi(FConnectBoard):
    pass


class FAntenna(FakeAnim):
    pass


class FInsertSd(FakeAnim):
    pass


class FInsertSdIntoPi(FakeAnim):
    pass


class FSdHandover(FakeAnim):
    pass


class FProvision(FakeAnim):
    pass


class FProvisionCable(FakeAnim):
    pass


FAKE_ANIMS = {"connect_antenna": FAntenna, "connect_board": FConnectBoard,
              "disconnect_board": FDisconnect, "radio_to_pi": FRadioToPi,
              "provision_cable": FProvisionCable, "connect_pi": FConnectPi,
              "insert_sd": FInsertSd, "insert_sd_pi": FInsertSdIntoPi,
              "sd_handover": FSdHandover, "provision": FProvision}

FAKE_PI_ANIMS = (FInsertSdIntoPi, FSdHandover, FConnectPi, FRadioToPi,
                 FProvisionCable)


class FakeWizardStep:
    def __init__(self, **kw):
        self.kw = kw
        self.title = kw.get("title", "")
        self.warning = kw.get("warning", "")
        self.next_hidden = False
        self.status = ""

    def hide_next(self):
        self.next_hidden = True

    def show_next(self):
        self.next_hidden = False

    def set_status(self, text):
        self.status = text

    def start(self):
        pass

    def stop(self):
        pass


# ---------------------------------------------------------------------------
# the shipped navigation methods, compiled out of the screen module
# ---------------------------------------------------------------------------

#: Every method here runs EXACTLY as shipped. The stand-ins below cover only
#: what draws pixels, spawns threads or touches hardware.
_REAL_METHODS = [
    "reset", "_guide_steps", "_begin_steps", "_resume_steps",
    "_render_step", "_next", "_back", "resume", "has_pending_resume",
    "cancel_resume", "_finish", "_step_is_redundant", "_gate_state",
    "_radio_gate", "_node_gate", "_check_pairing", "_pi_picked", "_trace", "_stop_current",
    "_stop_board_poll", "_stop_node_poll", "_stop_card_poll",
    "_stop_detect_nudge", "_stop_detect_pi_poll", "_pi_key_for_art",
    "_pi_key_for_text", "_on_board_present", "_on_board_absent",
    "_on_card_seen", "_on_card_gone", "_advance_after_card",
    "_on_node_online", "_pi_proven",
    # _counter joined the driven set when the breaker merge (2026-09-13) made
    # _render_step's family consult it — the harness missing it broke 489
    # walks, hidden for two days behind a `pytest | tail` pipeline that
    # reported tail's exit code (the SAME trap WORKING_METHOD logged on
    # 2026-08-14; pipefail or read-the-summary-line, never trust the pipe).
    "_counter", "_lead_screens", "_next_text_for",
]

_SHARED_NS = {
    "WizardStep": FakeWizardStep, "_ANIMS": FAKE_ANIMS,
    "_PI_ANIMS": FAKE_PI_ANIMS,
    # mirrors the shipped tuple at birth_guide_screen.py:53 (board-aware
    # anims take board_key=): the fakes accept **kw, so membership is all
    # the exec'd source needs (merge reconciliation, 2026-09-13)
    "_BOARD_ANIMS": (FConnectBoard, FDisconnect, FRadioToPi, FProvision),
    "ConnectAntennaAnim": FAntenna, "ConnectBoardAnim": FConnectBoard,
    "DisconnectBoardAnim": FDisconnect, "RadioToPiAnim": FRadioToPi,
    "ConnectPiAnim": FConnectPi, "InsertSdAnim": FInsertSd,
    "InsertSdIntoPiAnim": FInsertSdIntoPi, "SdHandoverAnim": FSdHandover,
    "ProvisionAnim": FProvision, "ProvisionOverCableAnim": FProvisionCable,
    "tr": tr,
    # The month's fail-closed firmware check (2026-09-09): reads the by-id
    # product string; every harness board presents as "usb-Espressif" (no
    # "rtnode"), for which the shipped function answers True — so the
    # stand-in answers the same, faithfully (reconciliation, 2026-09-15).
    "_is_rnode_firmware": lambda port: True,
}


def _compiled():
    out = {}
    for name in _REAL_METHODS:
        code = textwrap.dedent(func_source(SCREEN, name, cls="BirthGuideScreen"))
        exec(compile(code, SCREEN, "exec"), _SHARED_NS)
        out[name] = _SHARED_NS[name]
    return out


def _wait_patience():
    m = re.search(r"WAIT_PATIENCE_S = ([\d.]+)", src(SCREEN))
    assert m, "WAIT_PATIENCE_S gone from the screen — renamed?"
    return float(m.group(1))


class DrivenGuide:
    """The walkthrough machine on a stand-in body.

    Navigation is the shipped code; rendering is a recorder; every poll
    starter records WHAT it is watching for so the driver can deliver the
    event through the shipped handler (_on_board_present and friends — also
    real), token guards, direction guards and delayed advances included.
    """

    WAIT_PATIENCE_S = _wait_patience()

    def __init__(self, env, clock):
        self.env = env                  # ports / cert / serial / pi_proven
        self.clock = clock
        self.sc_board = ""              # what the operator answers when asked
        self.sc_pi = ""
        self.sc_reach = CABLE_ADDR      # where the provisioning build reached
        self.flash_results = None       # queue of flash hand-back payloads
        self.renders = []               # (step index, title, warning shown)
        self.visit_marks = []
        self.navigations = []           # one dict per hand-off
        self.asked = []                 # which pairing questions were shown
        self.power_verdicts = []
        self.presses = []               # step indices advanced by a "tap"
        self.ended = None
        self.flashed = False
        self.absence_expected = None
        self.widgets = []
        self._path = None
        self._i = 0
        self._current = None
        self._node_name = ""
        # The RTNode hand-off grew keyword answers (share_location, the
        # placed pin — breaker fix 4, 2026-09-13); the stub swallows them,
        # recording only what the walk laws assert on.
        self._on_complete = (lambda path, name, **kw:
                             setattr(self, "ended", ("complete", path)))
        self._on_navigate = self._record_nav
        self.reset()

    # -- widget tree stand-ins ---------------------------------------------
    def clear_widgets(self, *a, **k):
        self.widgets = []

    def add_widget(self, w):
        self.widgets.append(w)
        if isinstance(w, FakeWizardStep):
            self.renders.append((self._i, w.title, w.warning))

    # -- screens that are not guided steps: recorders -----------------------
    def _render_intro(self, *a, **k):
        self.asked.append("intro")

    def _render_name(self, *a, **k):
        self.asked.append("name")

    def _render_location_share(self, *a, **k):
        self.asked.append("share")

    def _render_pick_board(self, force_ask=False):
        # The operator answers the radio question with the scenario's board —
        # the real screen's memory/auto-advance shortcuts are its own tests'
        # business (test_guide_board_candidates); the FLOW is the same either
        # way: board -> Pi -> confirm.
        self.asked.append("pick_board")
        self._board_key = self.sc_board
        self._render_pick_pi()

    def _render_pick_pi(self, *a, **k):
        # Answer the way the SCREEN does — through _pi_picked — and let the
        # real code decide what follows: the pairing confirmation, or (since
        # 2026-09-22, when the question is asked before the steps so the
        # first Pi drawing is the operator's Pi) straight on into the steps.
        # Answering-and-confirming here dragged the pairing gate forward in
        # the simulation only, and failed a test the real flow passes.
        if getattr(self, "_pi_key", "") and not getattr(
                self, "_pi_pick_returns_to_step", False):
            self._render_confirm_pair()     # the real method's short-circuit
            return
        self.asked.append("pick_pi")
        self._pi_picked(self.sc_pi)

    def _render_confirm_pair(self, *a, **k):
        self.asked.append("confirm_pair")
        self._check_pairing()           # "Yes, that's right →"

    def _render_cable_verdict(self, pi_key):
        # Terminal by design: no cable or hub changes the board's USB
        # topology. The only road onward is a different Pi.
        self.ended = ("cable_verdict", pi_key)

    def _render_power_verdict(self, verdict):
        self.power_verdicts.append((verdict or {}).get("verdict"))
        self._resume_steps()            # the walker takes "Continue anyway →"

    def _render_done(self):
        self.ended = ("done",)

    # -- hand-off plumbing ---------------------------------------------------
    def _record_nav(self, screen_name):
        self.navigations.append({"screen": screen_name,
                                 "resume_at": self._resume_at,
                                 "renders_before": len(self.renders),
                                 "job": None, "resumed": False,
                                 "failed": False})

    def _hand_over_name(self, screen_name, job="host"):
        if self.navigations:
            self.navigations[-1]["job"] = job

    def _expect_board_absence(self, expected=True):
        self.absence_expected = expected

    # -- hardware probes the shipped code asks for ---------------------------
    def _pi_proof(self):
        return (bool(self.env.get("pi_proven")), "", "")

    # -- poll starters: record what is being watched for ---------------------
    def _start_node_poll(self):
        if getattr(self, "_node_addr", ""):
            return                       # mirrors the shipped early-return
        self._node_looking = True
        self._node_probe = None
        self.armed = ("node", None, None)

    def _start_pi_poll(self, anim):
        self.armed = ("pi", anim, None)

    def _start_board_poll(self, anim, on_present=None):
        self.armed = ("board", anim, on_present)

    def _start_absence_poll(self, anim):
        self.armed = ("absence", anim, None)

    def _start_card_poll(self, anim):
        self._card_greeted = False
        self.armed = ("card", anim, None)

    def _start_card_gone_poll(self, anim):
        self.armed = ("card_gone", anim, None)

    # -- the driver -----------------------------------------------------------
    armed = None

    def _deliver(self, kind, anim, extra):
        if kind == "board":
            if not self.env["ports"]:
                self.env["ports"] = ["/dev/ttyACM2"]   # the operator plugs in
            handler = extra or self._on_board_present
            handler(anim)
        elif kind == "absence":
            self.env["ports"] = []                     # the operator unplugs
            self._on_board_absent(anim)
        elif kind == "card":
            self._on_card_seen(anim)
        elif kind == "card_gone":
            self._on_card_gone(anim)
        elif kind == "pi":
            self.env["pi_proven"] = True               # THE Pi answers, token
            self._on_board_present(anim)               # and all (_start_pi_poll
                                                       # fires this on proof)
        elif kind == "node":
            self._on_node_online(self.sc_reach)

    def _simulate_handoff(self, nav):
        """Resume with the payload the destination really sends back.

        birth_screen._hand_back_to_guide: {radio_verified, build_failed,
        reached_at [, fail_line][, radio_usb_serial]} — serial only from the
        flash lap. pi_imager_screen hands back with no payload at all.
        """
        nav["resumed"] = True
        if nav["screen"] == "pi_imager":
            self.resume(None)
            return
        if nav["job"] == "host":                      # the radio flash
            if self.flash_results:
                payload = self.flash_results.pop(0)
            else:
                payload = {"radio_verified": True, "build_failed": False,
                           "reached_at": "",
                           "radio_usb_serial": self.env["serial"]}
            nav["failed"] = bool(payload.get("build_failed"))
            self.flashed = bool(payload.get("radio_verified"))
            self.resume(payload)
            return
        # the provisioning build. radio_verified mirrors the shipped screen:
        # birth_screen._radio_verified is only ever set by a "verify" step,
        # which the Pi build does not run — so it still holds whatever the
        # flash lap left there (True after a flash, False on the
        # have-one road where no flash lap ever ran).
        payload = {"radio_verified": self.flashed, "build_failed": False,
                   "reached_at": self.sc_reach}
        self.resume(payload)

    def drive(self, max_iters=60, until=None):
        """Walk forward, doing at each turn the one thing an emulated bench
        would: fire due clock beats, answer a hand-off, deliver the sensed
        event a poll is waiting for, or press the visible button."""
        for _ in range(max_iters):
            if self.ended is not None:
                return
            if until is not None and until(self):
                return
            if self.clock.run_due(5.0):
                continue
            nav = next((n for n in self.navigations if not n["resumed"]), None)
            if nav is not None and self.has_pending_resume():
                self._simulate_handoff(nav)
                continue
            if self.armed is not None:
                kind, anim, extra = self.armed
                self.armed = None
                self._deliver(kind, anim, extra)
                continue
            if self._current is not None and not self._current.next_hidden:
                self.presses.append(self._i)
                self._next()
                continue
            raise AssertionError(
                f"DEAD END: stalled at step {self._i} with nothing armed and "
                f"no button — renders so far: "
                f"{[(i, t) for i, t, _ in self.renders]}")
        raise AssertionError(
            f"DEAD END: {max_iters} turns and no end — renders: "
            f"{[(i, t) for i, t, _ in self.renders]}")

    def visits(self):
        """Render indices with consecutive repaints (a warning arriving on the
        same step) collapsed to one visit."""
        out = []
        for i, _t, _w in self.renders:
            if not out or out[-1] != i:
                out.append(i)
        return out


for _n, _f in _compiled().items():
    setattr(DrivenGuide, _n, _f)


# ---------------------------------------------------------------------------
# the rig: modules the shipped methods import at call time, stubbed the way
# test_guide_board_candidates stubs them (sys.modules — what actually
# intercepts `from x import y`)
# ---------------------------------------------------------------------------

@pytest.fixture
def rig(monkeypatch):
    env = {"ports": [], "cert": None, "pi_proven": False,
           "serial": "02:00:00:01:00:01"}
    clock = FakeClock()
    kivy_pkg = types.ModuleType("kivy")
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = clock
    app_mod = types.ModuleType("kivy.app")
    app_mod.App = types.SimpleNamespace(get_running_app=lambda: (
        types.SimpleNamespace(keyboard=None, switch_mode=lambda *a: None,
                              expect_board_absence=lambda *a: None)))
    kivy_pkg.clock, kivy_pkg.app = clock_mod, app_mod
    monkeypatch.setitem(sys.modules, "kivy", kivy_pkg)
    monkeypatch.setitem(sys.modules, "kivy.clock", clock_mod)
    monkeypatch.setitem(sys.modules, "kivy.app", app_mod)
    monkeypatch.setitem(sys.modules, "ui.hw_factories", types.SimpleNamespace(
        local_board_ports=lambda *a, **k: list(env["ports"]),
        hardware_present=lambda *a, **k: bool(env["ports"]),
        LocalConnection=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "workflows.rnode_flash",
                        types.SimpleNamespace(
                            by_id_serial=lambda usb_id: env["serial"],
                            usb_id_for_port=lambda conn, port: "usb-Espressif"))
    monkeypatch.setitem(sys.modules, "ui.cert_store", types.SimpleNamespace(
        cert_for_usb_serial=lambda serial: env["cert"]))
    return env, clock


def _pi_guide(env, clock, *, pi, board, state, reach=CABLE_ADDR):
    g = DrivenGuide(env, clock)
    g.sc_board, g.sc_pi, g.sc_reach = board, pi, reach
    env["ports"] = (["/dev/ttyACM2"]
                    if state in ("blank_present", "certified") else [])
    env["cert"] = ({"name": "PriorLife", "cert": "5a040004"}
                   if state == "certified" else None)
    g._path = "pi"
    g._node_name = "MatrixNode"
    g._share_asked = True
    g._bt_asked = True
    g._pi_flash_radio = state != "have_one"
    return g


def walk_pi(env, clock, *, pi, board, state, reach=CABLE_ADDR):
    g = _pi_guide(env, clock, pi=pi, board=board, state=state, reach=reach)
    g._begin_steps()
    g.drive()
    return g


def _expected_power_warning(pi, board, state):
    """The verdict screen the walkthrough OWES this pairing, computed the way
    _check_pairing computes it. On the have-one road no board was picked, so
    the check runs against '' exactly as shipped."""
    bk = board if state != "have_one" else ""
    try:
        return bool(needs_warning(pi, bk, power_compat.check(pi, bk)))
    except Exception:                                          # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# harness honesty: the stand-ins must match the module they stand in for
# ---------------------------------------------------------------------------

def test_the_stand_in_anim_map_matches_the_real_one():
    block = src(SCREEN)
    block = block[block.index("_ANIMS = {"):block.index("_PI_ANIMS")]
    assert set(re.findall(r'"(\w+)":', block)) == set(FAKE_ANIMS), \
        "the screen's _ANIMS keys drifted from the harness stand-ins"


def test_the_stand_in_anim_hierarchy_matches_the_real_one():
    """The isinstance branch order in _render_step only works because these
    three subclass ConnectBoardAnim — a stand-in with a different shape would
    walk different branches than the glass does."""
    body = src(ANIMS_FILE)
    for cls in ("DisconnectBoardAnim", "RadioToPiAnim", "ConnectPiAnim"):
        assert f"class {cls}(ConnectBoardAnim)" in body, \
            f"{cls} no longer subclasses ConnectBoardAnim — reshape the fakes"


def test_the_pick_list_this_sweep_uses_is_the_shipped_one():
    # Parsed from source, not imported: birth_screen imports Kivy at module
    # top, which CI does not have.
    body = src("ui/screens/birth_screen.py")
    block = body[body.index("PI_HOSTS = ["):]
    block = block[:block.index("]")]
    shipped = [k for k in re.findall(r'\("([^"]*)",', block) if k != "none"]
    assert [k for k in PI_KEYS if k] == shipped, \
        "PI_HOSTS changed — the matrix must sweep the new list"


# ---------------------------------------------------------------------------
# THE SWEEP: every pi x board x radio state, all five laws per run
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", RADIO_STATES)
@pytest.mark.parametrize("board", BOARD_KEYS)
@pytest.mark.parametrize("pi", PI_KEYS, ids=[k or "unknown" for k in PI_KEYS])
def test_every_pi_walkthrough_flows_to_the_end(rig, pi, board, state):
    env, clock = rig
    g = walk_pi(env, clock, pi=pi, board=board, state=state)
    label = f"[pi={pi or 'unknown'} board={board} radio={state}]"

    # LAW 1 — TERMINATION. The one legitimate early ender: a Pi 3B+ cannot do
    # a cable birth (LAN7515 hub between the SoC and every port — physics,
    # not a setting), and the walkthrough must refuse BEFORE anything is
    # flashed or written.
    if pi == "pi_3b_plus":
        assert g.ended == ("cable_verdict", pi), \
            f"{label} a 3B+ build must stop on the cable verdict, got {g.ended}"
        assert not g.navigations, \
            f"{label} the 3B+ refusal came AFTER work was handed off"
        assert not g.renders, \
            f"{label} the 3B+ refusal came after physical steps were shown"
        return
    assert g.ended == ("done",), f"{label} never reached the end: {g.ended}"

    v = g.visits()
    # LAW 2 — NO REPEATED STEP: no index gets a second, separate visit.
    assert len(set(v)) == len(v), f"{label} step visited twice: {v}"
    # LAW 3 — MONOTONIC PROGRESS on success payloads.
    assert v == sorted(v), f"{label} the walk went backwards: {v}"

    # LAW 4 — HAND-OFF ROUND TRIP: each resume lands at the recorded step
    # (or past it, only ever via the shared redundancy rule).
    for nav in g.navigations:
        later = [i for i, _t, _w in g.renders[nav["renders_before"]:]]
        assert all(i >= nav["resume_at"] for i in later), (
            f"{label} hand-off to {nav['screen']} resumed BEHIND step "
            f"{nav['resume_at']}: {later}")
        # On the cable road nothing after a resume is ever redundant, so the
        # round trip is EXACT: the very next render is the recorded step.
        assert later and later[0] == nav["resume_at"], (
            f"{label} resume from {nav['screen']} landed at "
            f"{later[:1]}, expected step {nav['resume_at']}")

    # LAW 5 — GATE HONESTY: on the happy path every gate advanced itself;
    # no gate index appears among the manual presses.
    steps = guide_steps("pi", "", state != "have_one")
    for gi, s in enumerate(steps):
        if s.get("gate"):
            assert gi not in g.presses, (
                f"{label} the {s['gate']} gate needed a manual press at "
                f"step {gi} — a passed gate must drive itself")

    # And the power verdict appears for EXACTLY the pairings the power model
    # warns about — never silently absent, never invented.
    expected = _expected_power_warning(pi, board, state)
    assert bool(g.power_verdicts) == expected, (
        f"{label} power verdict shown={bool(g.power_verdicts)} but the model "
        f"says warning={expected}")


@pytest.mark.parametrize("state", RADIO_STATES)
def test_the_wifi_road_reaches_the_end_too(rig, state):
    """SkyFinger was provisioned over Wi-Fi (2026-08-11) — the road is real.
    On the have-one road the final hand-off step describes undoing cable work
    that never happened, so it is honestly redundant and must be skipped
    (2026-09-06); with a flash it always renders."""
    env, clock = rig
    g = walk_pi(env, clock, pi="pi_4b", board="heltec32_v3", state=state,
                reach=WIFI_ADDR)
    assert g.ended == ("done",)
    last = len(guide_steps("pi", "", state != "have_one")) - 1
    if state == "have_one":
        assert last not in g.visits(), \
            "the radio-to-Pi step ran for a radio that was never on the medic"
    else:
        assert last in g.visits(), \
            "the flashed radio still has to be moved onto the Pi"


# ---------------------------------------------------------------------------
# pinned canonical traces — the exact shape of the flow, one per road
# ---------------------------------------------------------------------------

def test_the_full_flash_walk_visits_every_step_once_in_order(rig):
    env, clock = rig
    g = walk_pi(env, clock, pi="pi_4b", board="heltec32_v4",
                state="blank_present")
    # Step 0 (connect the radio) is honestly skipped — the board is already on
    # USB, so the shipped auto-fire presses "Flash this radio" itself. Every
    # other step gets exactly one visit, in order.
    assert g.visits() == [1, 2, 3, 4, 5, 6, 7], g.visits()
    assert [n["screen"] for n in g.navigations] == \
        ["birth", "pi_imager", "birth"]
    assert [n["job"] for n in g.navigations] == ["host", "host", "pi"]
    # asked[0] is reset()'s chooser render; the pairing questions follow.
    # Since 2026-09-22 the Pi is asked FIRST, before the steps, so the first
    # Pi drawing is the operator's Pi; the board and the pair follow as before.
    assert g.asked[1:4] == ["pick_pi", "pick_board", "confirm_pair"], \
        "the pairing must be identified before the card is written"
    # the disconnect step told the watcher the absence is intended
    assert g.absence_expected is True


def test_an_unplugged_radio_still_gets_its_connect_instructions(rig):
    env, clock = rig
    g = walk_pi(env, clock, pi="pi_4b", board="heltec32_v4",
                state="blank_unplugged")
    assert g.ended == ("done",)
    assert g.visits()[0] == 0, \
        "with nothing on USB the connect-radio step must actually show"
    assert g.visits() == [0, 1, 2, 3, 4, 5, 6, 7], g.visits()


def test_a_certified_radio_skips_the_flash_entirely(rig):
    """A board this medic already flashed AND verified holds its certificate;
    re-flashing it is pure waste (five in one night, 2026-08-14)."""
    env, clock = rig
    g = walk_pi(env, clock, pi="pi_4b", board="heltec32_v4", state="certified")
    assert g.ended == ("done",)
    assert [n["screen"] for n in g.navigations] == ["pi_imager", "birth"], \
        "a certified radio must not be sent back through the flash"
    assert g.visits()[0] == 1, "the walk must open on the gate agreeing"
    assert g._radio_usb_serial == env["serial"], \
        "the recorded certificate's serial must arm the udev pinning"


def test_the_have_one_road_never_asks_about_a_radio(rig):
    env, clock = rig
    g = walk_pi(env, clock, pi="pi_4b", board="heltec32_v4", state="have_one")
    assert g.ended == ("done",)
    assert "pick_board" not in g.asked, \
        "'I already have a working radio' must not ask which radio"
    assert [n["screen"] for n in g.navigations] == ["pi_imager", "birth"]
    assert g.visits() == [0, 1, 2, 3, 4], g.visits()


# ---------------------------------------------------------------------------
# the failure laws: a failed build never moves the walk forward
# ---------------------------------------------------------------------------

def test_a_failed_provisioning_build_stays_on_the_gate_step(rig):
    """The 2026-08-14 law, driven through the shipped machine: dismissing
    "Build didn't finish" must land the operator back ON the step that did
    the work, warning showing, button reading Try again — and a later success
    must still carry the walk to the end."""
    env, clock = rig
    g = _pi_guide(env, clock, pi="pi_4b", board="heltec32_v4",
                  state="blank_present")
    fail = {"radio_verified": True, "build_failed": True,
            "reached_at": CABLE_ADDR,
            "fail_line": "[FAIL] detect_hardware — the node stopped answering"}
    done = {"radio_verified": True, "build_failed": False,
            "reached_at": CABLE_ADDR}

    real = g._simulate_handoff

    def scripted(nav):
        if nav["job"] == "pi" and not getattr(g, "_pi_failed_once", False):
            g._pi_failed_once = True
            nav["resumed"] = True
            nav["failed"] = True
            g.resume(fail)
            return
        if nav["job"] == "pi":
            nav["resumed"] = True
            g.resume(done)
            return
        real(nav)

    g._simulate_handoff = scripted
    g._begin_steps()
    g.drive()
    assert g.ended == ("done",)
    steps = guide_steps("pi", "", True)
    gate_i = next(i for i, s in enumerate(steps)
                  if s.get("gate") == "node_online")
    fail_nav = next(n for n in g.navigations if n["failed"])
    landed = g.renders[fail_nav["renders_before"]]
    assert landed[0] == gate_i, \
        f"a failed build resumed at step {landed[0]}, not back on the gate"
    assert "didn't finish" in landed[2], \
        "the operator landed back with no explanation on screen"
    assert "[FAIL] detect_hardware" in landed[2], \
        "the [FAIL] line must travel with the failure (briefing Task 2)"
    # the retry that carried on WAS a press — the gate stopped driving itself
    assert gate_i in g.presses, \
        "after a failure the way onward must be the operator's tap"
    # and the stale sighting was forgotten: the retry re-proved the node
    assert g.renders[-1][0] == len(steps) - 1


def test_a_failed_flash_with_the_board_unplugged_shows_the_refusal(rig):
    """The dead board came out. The walk must land back on the connect-radio
    step with the failure ON SCREEN, and a later success must still finish."""
    env, clock = rig
    g = _pi_guide(env, clock, pi="pi_4b", board="heltec32_v4",
                  state="blank_present")
    g.flash_results = [
        {"radio_verified": False, "build_failed": True, "reached_at": "",
         "fail_line": "[FAIL] verify — the board never answered as an RNode"},
        {"radio_verified": True, "build_failed": False, "reached_at": "",
         "radio_usb_serial": env["serial"]},
    ]
    orig = g._simulate_handoff

    def unplug_then(nav):
        if nav["job"] == "host" and g.flash_results and \
                g.flash_results[0]["build_failed"]:
            env["ports"] = []           # the operator removed the dead board
        orig(nav)

    g._simulate_handoff = unplug_then
    g._begin_steps()
    g.drive()
    assert g.ended == ("done",)
    fail_nav = g.navigations[0]
    assert fail_nav["failed"] is True
    landed = g.renders[fail_nav["renders_before"]]
    assert landed[0] == 0, "the failed flash must land back on its own step"
    assert "[FAIL] verify" in landed[2], "the reason must be on the step"


def test_a_failed_flash_with_the_board_still_plugged_rehands_off(rig):
    """PINNED BEHAVIOUR, not blessed (2026-09-13, walkthrough matrix): with
    the dead board still on USB, the failed-flash resume lands on step 0 and
    the step-0 auto-fire immediately re-hands-off to the BIRTH screen — the
    guide's own fail_line warning is never rendered on the guide (the BIRTH
    screen has already shown its failure popup, and its form is where the
    retry happens, so nobody is stranded and the index never moves forward).
    If this assertion starts failing because the guide now SHOWS the failure
    before re-offering the flash, that is an improvement: update this pin."""
    env, clock = rig
    g = _pi_guide(env, clock, pi="pi_4b", board="heltec32_v4",
                  state="blank_present")
    g.flash_results = [
        {"radio_verified": False, "build_failed": True, "reached_at": "",
         "fail_line": "[FAIL] verify — boot-looping every 2.4 s"},
        {"radio_verified": True, "build_failed": False, "reached_at": "",
         "radio_usb_serial": env["serial"]},
    ]
    g._begin_steps()
    g.drive()
    assert g.ended == ("done",)
    # job defaults to "host" on every hand-off, the imager's included, so the
    # flash laps are the BIRTH-screen ones
    flashes = [n for n in g.navigations
               if n["screen"] == "birth" and n["job"] == "host"]
    assert len(flashes) == 2, \
        "the failed flash must re-offer the flash, not march on"
    # the walk never moved PAST the gate on the strength of the failure
    fail_nav = flashes[0]
    between = [i for i, _t, _w in
               g.renders[fail_nav["renders_before"]:flashes[1]["renders_before"]]]
    assert all(i <= 1 for i in between), \
        f"a failed flash let the walk advance past the gate: {between}"
    # THE PIN FLIPPED, exactly as its docstring invited (2026-09-13, same
    # day): the breaker's standdown law landed on main — a failed build
    # suspends the step-0 auto-fire, so the guide now RENDERS step 0 with
    # the [FAIL] line and the button is the retry. The improvement the old
    # pin promised to welcome is here; the second hand-off above is now the
    # operator's press, not the machine's reflex.
    assert between and all(i == 0 for i in between), \
        f"the failure should render on step 0 before any re-hand-off: {between}"


def test_a_flash_that_never_verified_blocks_the_radio_gate(rig):
    """A build can 'finish' with verify red. The gate must stall — presence
    is not a pass (the 2026-08-08 boot-looping V4)."""
    env, clock = rig
    g = _pi_guide(env, clock, pi="pi_4b", board="heltec32_v4",
                  state="blank_present")
    g.flash_results = [{"radio_verified": False, "build_failed": True,
                        "reached_at": "",
                        "fail_line": "[FAIL] verify — no RNode answer"}]
    # the board stays plugged, so the auto-fire will re-hand-off
    g._begin_steps()
    # run only until the second flash hand-off is pending, then stop driving:
    g.drive(until=lambda d: len([n for n in d.navigations
                                 if n["screen"] == "birth"
                                 and n["job"] == "host"]) == 2)
    # the gate has not been passed and the walk has not moved beyond it
    ok, why = g._gate_state("radio_ready")
    assert not ok
    assert "hasn't been flashed and verified" in why
    assert g._i <= 1, f"an unverified radio let the walk reach step {g._i}"


def test_a_blocked_node_gate_stays_put_however_often_it_is_pressed(rig):
    """LAW 5, blocked half: the Pi never answers. The gate must hold, keep its
    warning on screen, and pressing Try again must re-render the SAME step —
    never advance, never dead-end."""
    env, clock = rig
    g = _pi_guide(env, clock, pi="pi_4b", board="heltec32_v4",
                  state="blank_present")
    steps = guide_steps("pi", "", True)
    gate_i = next(i for i, s in enumerate(steps)
                  if s.get("gate") == "node_online")
    g._begin_steps()
    g.drive(until=lambda d: (d._i == gate_i and d.armed is not None
                             and d.armed[0] == "node"))
    assert g._i == gate_i
    assert "Please wait" in g.renders[-1][2], \
        "a working wait must say so from the start (2026-08-09)"
    for _ in range(3):
        g._next()                       # Try again, against silence
        assert g._i == gate_i, "a blocked gate must never advance"
    assert g.renders[-1][0] == gate_i
    assert g.renders[-1][2], "the refusal must stay on screen"
    # and the whole time, nothing was handed off to provisioning
    assert all(n["job"] != "pi" for n in g.navigations), \
        "provisioning started against a Pi that never answered"


# ---------------------------------------------------------------------------
# the short paths: host and radio hand off to BIRTH and terminate there
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plugged", [True, False],
                         ids=["radio_on_usb", "radio_unplugged"])
@pytest.mark.parametrize("board", BOARD_KEYS)
@pytest.mark.parametrize("path", ["host", "radio"])
def test_the_short_paths_flow_to_their_birth_handoff(rig, path, board, plugged):
    env, clock = rig
    g = DrivenGuide(env, clock)
    env["ports"] = ["/dev/ttyACM2"] if plugged else []
    g.sc_board, g.sc_pi = board, ""
    g._path = path
    g._node_name = "MatrixNode"
    g._share_asked = True
    g._begin_steps()
    g.drive()
    label = f"[{path} x {board} {'plugged' if plugged else 'unplugged'}]"
    assert g.ended == ("complete", path), \
        f"{label} never handed off to BIRTH: {g.ended}"
    v = g.visits()
    assert v == sorted(v) and len(set(v)) == len(v), \
        f"{label} unclean walk: {v}"
    if plugged:
        # being told to connect a connected board reads as a bug (2026-07-31)
        assert 0 not in v, f"{label} showed connect-the-board to a seen board"
    else:
        assert 0 in v, f"{label} skipped the connect instructions blind"


# ---------------------------------------------------------------------------
# the early-ender roll call, pinned
# ---------------------------------------------------------------------------

def test_the_only_legitimate_early_ender_is_the_pi_3b_plus():
    """The full enumeration the sweep above relies on: of every Pi the picker
    offers, exactly one refuses the walkthrough outright — the 3B+, whose
    LAN7515 hub makes a cable birth physically impossible. The unknown key
    fails OPEN by design (a wrong exclusion blocks a legitimate build)."""
    refused = [k for k in PI_KEYS if not can_cable(k)]
    assert refused == ["pi_3b_plus"], refused


def test_the_power_verdict_detour_list_is_the_power_models(rig):
    """The sweep asserts verdict-shown == model-warns per combination; this
    pins the OVERALL shape so a silent regression in either direction is a
    one-line diff: every warned pairing is a detour, every other pairing goes
    straight through, and the warned list is not empty (heltec32_v4 on a
    Zero 2 W is the bench-measured brown-out)."""
    warned = [(p, b) for p in PI_KEYS if p != "pi_3b_plus"
              for b in BOARD_KEYS if _expected_power_warning(p, b, "blank")]
    assert ("pi_zero_2w", "heltec32_v4") in warned
    assert all(p for p, _ in warned), "the unknown Pi must never warn blind"
