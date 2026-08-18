"""Two operator orders from the night of 2026-08-14, pinned.

1. THE BACK TRAP. Pressing Back off "Take the radio out" landed on the
   radio_ready gate — which had already PASSED, so the gate branch in
   _render_step re-armed its 1.6 s auto-advance and bounced the operator
   straight forward again. ui.log recorded eleven bounces between "The radio
   has to work first" and "Take the radio out"; the operator hit it twice and
   had to restart the UI to get out. The fix is a direction flag: the guide's
   self-driving — a passed gate's advance, every poll that fires _next — is
   FORWARD work, and none of it may run against someone walking backward.

2. AN EXIT ON EVERY SCREEN. Back walks one screen at a time; the trap above
   is what happens when even that road closes. A small, constant Exit is the
   door that cannot be trapped shut — on every step, every picker, every
   prelude this class renders — and it must survive a build running in the
   background (warn, don't block) and leave nothing armed behind it (no stale
   hand-off return, no polls still firing).

Mostly source-inspection (Kivy is not importable in CI — see srcutil), but the
navigation logic itself is exercised by running the SHIPPED methods on a
stand-in, the way test_guide_resume runs _hand_over_name: substring checks over
this file have passed while the behaviour was wrong before (2026-08-09).
"""

import re
import textwrap
import types

from tests.srcutil import func_source, src

SCREEN = "ui/screens/birth_guide_screen.py"


def _method(scr, name, extra_globals=None):
    """Bind the SHIPPED method *name* onto the stand-in *scr*."""
    ns = dict(extra_globals or {})
    exec(compile(textwrap.dedent(func_source(SCREEN, name)), SCREEN, "exec"), ns)
    setattr(scr, name, types.MethodType(ns[name], scr))
    return scr


# --- the direction flag ----------------------------------------------------

def test_a_walkthrough_starts_facing_forward():
    reset = func_source(SCREEN, "reset")
    assert '_nav_dir = "forward"' in reset, \
        "the default direction is forward — only Back ever says otherwise"


def test_next_and_back_declare_their_direction():
    assert '_nav_dir = "forward"' in func_source(SCREEN, "_next")
    assert '_nav_dir = "back"' in func_source(SCREEN, "_back")


def test_every_other_way_into_a_step_is_a_forward_arrival():
    """Resume from a hand-off, the pair-check's re-entry, the name and the map
    question all move the operator FORWARD — if any of them left a stale "back"
    standing, the next gate would refuse to drive itself for no visible
    reason."""
    for fn in ("resume", "_resume_steps", "_render_step_zero",
               "_name_next", "_share_chosen"):
        assert '_nav_dir = "forward"' in func_source(SCREEN, fn), \
            f"{fn} moves forward but does not say so"


# --- the trap itself: a passed gate must not drive backward walkers --------

def test_a_passed_gate_does_not_auto_advance_a_backward_arrival():
    """THE ELEVEN BOUNCES. The gate branch's self-drive must be conditioned on
    the direction of arrival, not just on the gate having passed."""
    rs = func_source(SCREEN, "_render_step")
    assert "back_arrival" in rs, "no notion of how this render was arrived at"
    assert re.search(r"if ok and not failed and not back_arrival", rs), \
        "the passed-gate auto-advance still fires on a BACK arrival"


def test_a_backward_arrival_is_never_re_skipped_forward():
    """The forward pass advances _i past redundant steps BEFORE rendering.
    Running that against a backward arrival un-does the Back press — _back
    already chose the landing step, and it is not to be second-guessed."""
    rs = func_source(SCREEN, "_render_step")
    m = re.search(r"if not back_arrival:.*?while self\._i < len\(steps\)", rs,
                  re.S)
    assert m, "the redundancy skip loop runs whichever way the operator moved"


def test_a_backward_arrival_always_has_a_button_to_go_forward_again():
    """Suppressing the self-drive must not strand anyone: several branches hide
    Next because the medic normally advances itself. Arriving backward, the
    button is the one remaining road forward, so it has to be on screen."""
    rs = func_source(SCREEN, "_render_step")
    i = rs.index("if back_arrival and self._current is step:")
    assert "step.show_next()" in rs[i:i + 800], \
        "backward arrivals can land on a step with no way forward"


def test_the_self_driving_polls_do_not_drive_backward_walkers():
    """The gate was the trap the operator hit, but every poll that ends in
    _next() is the same bounce waiting on a different step: the card is
    already out of the reader on the way back through 'take the card out',
    the radio already absent on 'take the radio out'."""
    for fn in ("_on_board_present", "_on_board_absent",
               "_on_card_gone", "_advance_after_card"):
        body = func_source(SCREEN, fn)
        assert "_nav_dir" in body and '"back"' in body, \
            f"{fn} still advances against a backward walker"
        # against the CALL, not the docstring's mention of _next
        assert body.index("_nav_dir") < body.index("self._next()"), \
            f"{fn} checks the direction after already deciding to advance"


# --- Back is cheap: no hardware probes on the way backward -----------------

def test_back_probes_no_hardware():
    """Each redundancy probe is an lsusb or a serial enumeration; one per step
    made every Back press pay for hardware answers the forward pass already
    had. Back consults the RECORD of what forward skipped instead."""
    back = func_source(SCREEN, "_back")
    assert "probe=False" in back, "Back still runs the hardware probes"
    assert "_skipped_fwd" in back, "Back has no record of what forward skipped"


def test_the_forward_pass_records_what_it_skipped():
    rs = func_source(SCREEN, "_render_step")
    assert "_skipped_fwd" in rs
    assert ".add(" in rs, "skips are not recorded"
    assert ".discard(" in rs, \
        "a step landed on must leave the record, or Back skips a live step"


def test_the_record_dies_with_the_walkthrough():
    """Stale skips from the last build would make Back jump over steps of the
    next one — same rule as every other cross-walkthrough flag."""
    assert "_skipped_fwd" in func_source(SCREEN, "reset")


def test_the_probe_switch_sits_behind_the_structural_rules():
    """probe=False must still never call a gate or a hand-off redundant — and
    must return before any hardware is touched."""
    red = func_source(SCREEN, "_step_is_redundant")
    sig = red[:red.index(":")]
    assert "probe" in sig, "no probe switch in the signature"
    assert "if not probe" in red
    assert red.index('step.get("gate")') < red.index("if not probe"), \
        "the gate/hand-off guard must hold in BOTH modes"
    # against the probe CALLS, not the docstring's naming of them
    assert red.index("if not probe") < red.index("local_board_ports()")
    # Was `red.index('["lsusb"]')`. main replaced that probe with a different
    # mechanism (2026-08-14, "proof not presence"), so asserting on one named
    # call pinned an implementation rather than the rule. Assert the RULE: no
    # hardware call in the body, whichever it is, precedes the guard.
    import re
    body = red[red.index('"""', red.index('"""') + 3) + 3:]
    guard = body.index("if not probe")
    calls = list(re.finditer(r"(lsusb|local_board_ports|subprocess)", body))
    assert calls, "no hardware call found — has the probe moved out entirely?"
    assert all(m.start() > guard for m in calls), \
        "a hardware call runs before the probe guard"


def test_probe_false_answers_without_touching_hardware(monkeypatch):
    """The shipped _step_is_redundant, run: with a board notionally present,
    probe=True says the connect step is done and probe=False refuses to say —
    without asking the hardware at all."""
    import ui.hw_factories as hw
    calls = []

    def ports():
        calls.append(1)
        return ["/dev/cu.usbmodem-x"]

    monkeypatch.setattr(hw, "local_board_ports", ports)
    scr = _method(types.SimpleNamespace(), "_step_is_redundant")
    step = {"anim": "connect_board"}
    assert scr._step_is_redundant(step) is True
    assert calls, "probe=True must actually probe"
    calls.clear()
    assert scr._step_is_redundant(step, probe=False) is False
    assert not calls, "probe=False went to the hardware anyway"


# --- the shipped _back, run ------------------------------------------------

def _back_standin(n=6, i=4, skipped=(), pair_checked=True, share_asked=True):
    scr = types.SimpleNamespace(
        _i=i, _path="pi", _advance_token=0, _skipped_fwd=set(skipped),
        _pair_checked=pair_checked, _share_asked=share_asked,
        landed=[], probes=[])
    scr._pi_key_for_text = lambda: ""
    scr._render_step = lambda: scr.landed.append(scr._i)
    scr._render_pick_board = lambda: scr.landed.append("pick_board")
    scr._render_location_share = lambda: scr.landed.append("share")
    scr._render_name = lambda: scr.landed.append("name")

    def _sir(step, probe=True):
        if probe:
            scr.probes.append(step)
        return False

    scr._step_is_redundant = _sir
    return _method(scr, "_back",
                   {"guide_steps": lambda *a, **k: [{"title": f"s{j}"}
                                                    for j in range(n)]})


def test_back_declares_back_and_steps_over_the_recorded_skips():
    """From step 4 with steps 1–2 skipped on the way forward, one Back press
    lands on 3; the next lands on 0, straight over the record."""
    scr = _back_standin(i=4, skipped=(1, 2))
    scr._back()
    assert scr._nav_dir == "back"
    assert scr.landed == [3]
    scr._back()
    assert scr.landed == [3, 0]


def test_back_runs_no_probes():
    scr = _back_standin(i=4, skipped=(1, 2))
    scr._back()
    scr._back()
    assert scr.probes == [], "Back paid for hardware probes"


def test_back_off_the_first_step_reaches_the_choosers_then_the_preludes():
    """The road home, in order: the board pick when the pair questions were
    asked, the map question when it was, the name otherwise."""
    scr = _back_standin(i=0, pair_checked=True)
    scr._back()
    assert scr.landed == ["pick_board"]
    scr = _back_standin(i=0, pair_checked=False, share_asked=True)
    scr._back()
    assert scr.landed == ["share"]
    scr = _back_standin(i=0, pair_checked=False, share_asked=False)
    scr._back()
    assert scr.landed == ["name"]


# --- the exit: a door on every screen --------------------------------------

def test_every_screen_this_class_renders_carries_the_exit():
    """Every _render_* starts by clearing the screen's widgets, so the
    clear_widgets override is the one seam that reaches ALL of them — steps,
    pickers and preludes alike — without twenty call sites to forget one of."""
    cw = func_source(SCREEN, "clear_widgets", cls="BirthGuideScreen")
    assert "_exit_row" in cw, "clearing a screen must re-seed the exit"
    assert "super().clear_widgets" in cw


def test_every_render_actually_passes_through_that_seam():
    """The override only covers screens that clear first. Every render must —
    either itself or by delegating to one that does."""
    text = src(SCREEN)
    bad = []
    for name, body in re.findall(
            r"def (_render_\w+)\(self[^)]*\):(.*?)(?=\n    def |\Z)", text, re.S):
        if "self.clear_widgets()" in body or re.search(r"self\._render_\w+\(",
                                                       body):
            continue
        bad.append(name)
    assert not bad, f"screens rendered without the exit seam: {bad}"


def test_exit_warns_before_leaving_a_running_build():
    tapped = func_source(SCREEN, "_exit_tapped")
    assert "_build_running" in tapped
    assert "_confirm_exit_popup" in tapped
    assert "_exit_to_home" in tapped
    running = func_source(SCREEN, "_build_running")
    assert "flash_in_progress" in running, \
        "the app's activity tracking is the one source of truth for this"


def test_the_leave_popup_says_the_build_survives():
    """Title, both buttons, and the one fact that makes leaving safe to offer:
    the build keeps running (the activity strip stays up — app-level, drawn
    over every screen until end_activity)."""
    pop = func_source(SCREEN, "_confirm_exit_popup")
    assert "Leave this build?" in pop
    assert "keeps running" in pop
    assert "Cancel — stay" in pop
    assert "OK — go home" in pop
    # the safe choice reads first, same rule as the power verdict's buttons
    assert pop.index("Cancel — stay") < pop.index("OK — go home")


def test_leaving_disarms_the_walkthrough_before_navigating():
    """cancel_resume, or a screen finishing late drags the operator back into
    a walkthrough they left; _stop_current, or its polls keep firing at a
    screen that is gone. Both BEFORE the navigation hands control away."""
    leave = func_source(SCREEN, "_exit_to_home")
    assert "cancel_resume" in leave
    assert "_stop_current" in leave
    assert '"home"' in leave
    assert leave.index("cancel_resume") < leave.index('"home"')
    assert leave.index("_stop_current") < leave.index('"home"')


def test_stop_current_still_covers_every_poll():
    """The cleanup Exit leans on. Pinned here because Exit now depends on it:
    if a new poll is ever added outside _stop_current, Exit starts leaking."""
    sc = func_source(SCREEN, "_stop_current")
    for stopper in ("_stop_board_poll", "_stop_node_poll",
                    "_stop_detect_pi_poll", "_stop_card_poll"):
        assert stopper in sc, f"{stopper} escaped _stop_current"


def test_exit_to_home_run_cleans_up_then_navigates():
    """The shipped _exit_to_home, run: order matters, and the destination is
    the app's own navigation, not a private one."""
    calls = []
    scr = types.SimpleNamespace()
    scr.cancel_resume = lambda: calls.append("cancel_resume")
    scr._stop_current = lambda: calls.append("stop_current")
    scr._on_navigate = lambda name: calls.append(("navigate", name))
    _method(scr, "_exit_to_home")
    scr._exit_to_home()
    assert calls == ["cancel_resume", "stop_current", ("navigate", "home")]


def test_exit_tapped_run_asks_only_when_a_build_is_running():
    for running, expected in ((True, ["popup"]), (False, ["home"])):
        scr = types.SimpleNamespace(calls=[])
        scr._build_running = lambda r=running: r
        scr._confirm_exit_popup = lambda: scr.calls.append("popup")
        scr._exit_to_home = lambda: scr.calls.append("home")
        _method(scr, "_exit_tapped")
        scr._exit_tapped()
        assert scr.calls == expected, \
            f"flash_in_progress={running} routed to {scr.calls}"


def test_a_broken_exit_rail_cannot_take_the_screen_down():
    """The rail is decoration on top of the render; a render must never die
    for it (same rule as _expect_board_absence)."""
    cw = func_source(SCREEN, "clear_widgets", cls="BirthGuideScreen")
    assert "except Exception" in cw


def test_the_exit_strings_are_wrapped_for_translation():
    text = src(SCREEN)
    for s in ('tr("Exit")', 'tr("Leave this build?")',
              'tr("Cancel — stay")', 'tr("OK — go home")'):
        assert s in text, f"{s} missing — exit strings must be translatable"
