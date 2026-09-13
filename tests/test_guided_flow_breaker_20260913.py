"""Breaker audit of the guided birth/build walkthrough, 2026-09-13.

The operator's order, verbatim intent: "check the flows are good for users —
no misinformation shown on screen about what's happening, the user isn't asked
any questions they don't need to be asked, doesn't press anything Node Medic
could do itself, and the build flow takes us all the way to working and
doesn't dead-end."

Each test below pins one hole that walk found. Source-inspection style
(tests/srcutil), because Kivy is not importable in CI — same as the rest of
the suite.
"""

from __future__ import annotations

import ast
import re

from tests.srcutil import func_source, src

GUIDE = "ui/screens/birth_guide_screen.py"
IMAGER = "ui/screens/pi_imager_screen.py"
APP = "ui/app.py"


def _code_only(body):
    """The executable lines of a function — comments stripped, so a dated
    why-comment quoting the old bug cannot satisfy (or fail) an assertion
    about what the code DOES."""
    return "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))


# --- DEAD END 1: the "Write the card again" button called a method that ----
# --- does not exist ---------------------------------------------------------

def _imager_method_names():
    tree = ast.parse(src(IMAGER))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "PiImagerScreen")
    return {n.name for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

def test_every_situation_action_reaches_a_real_method():
    """plugged_in's settled verdicts offer branch buttons; 'rewrite_card'
    called self.reset(), which PiImagerScreen never defined — so the one
    button offered on a settled failure raised AttributeError and did
    nothing. A dead end wearing the clothes of an escape hatch."""
    body = _code_only(func_source(IMAGER, "_situation_action",
                                  cls="PiImagerScreen"))
    defined = _imager_method_names()
    for name in re.findall(r"self\.(\w+)\(", body):
        assert name in defined, (
            f"_situation_action calls self.{name}() but PiImagerScreen "
            f"defines no such method — the button does nothing")


def test_the_imager_card_poll_greets_a_card_on_every_lap():
    """_on_card_found guards on _card_greeted and nothing in the imager ever
    cleared it — so the SECOND time the 'no card' screen appeared in one
    session, inserting a card was never noticed and the screen sat there
    (the auto-advance the operator asked for on 2026-08-06, silently dead)."""
    poll = func_source(IMAGER, "_start_card_poll", cls="PiImagerScreen")
    assert "_card_greeted = False" in poll, (
        "starting a new watch must re-arm the one-shot greet")


# --- MISINFORMATION 1: the RTNode path's map answer was thrown away ---------

def test_the_radio_paths_map_answer_travels_to_the_build():
    """The walkthrough asks the map question on the 'radio' (RTNode) path and
    lets the operator place a pin — then _finish handed only (path, name) to
    the app, and begin_guided normalised the missing answer to hidden. A
    'Show on map' with a placed pin silently became a hidden node: a question
    asked and its answer discarded."""
    fin = func_source(GUIDE, "_finish", cls="BirthGuideScreen")
    assert "share_location" in fin, "_finish must carry the share answer out"
    assert "_node_location" in fin, "...and the pin that goes with it"
    app = func_source(APP, "_guided_birth_complete")
    assert "share_location" in app, (
        "the app hand-off must forward the share answer to begin_guided")
    assert "location" in app


# --- MISINFORMATION 2: 'Step N of M' counted a screen the host path never --
# --- shows -------------------------------------------------------------------

def test_prelude_count_matches_what_each_path_actually_asks():
    """The +2 offset assumed name + map question on every path, but the map
    question is deliberately not asked on 'host' — so a host build read
    'Step 1 of 3' then 'Step 3 of 3', with a step 2 that never existed."""
    from ui.birth_guide_flow import prelude_count
    assert prelude_count("host") == 1       # name only — no map question
    assert prelude_count("radio") == 2      # name + map
    assert prelude_count("pi") == 2         # name + map (and Bluetooth rides it)


def test_the_step_counters_use_the_shared_prelude_count():
    for fn in ("_render_name", "_render_location_share", "_render_step"):
        body = func_source(GUIDE, fn, cls="BirthGuideScreen")
        assert "prelude_count" in body, (
            f"{fn} must count the prelude screens the path actually shows, "
            "not assume two")


# --- DEAD END 2: a failed radio flash re-fired its own hand-off -------------

def test_a_failed_flash_does_not_refire_the_handoff_by_itself():
    """resume() after build_failed lands back on the flash step with the
    [FAIL] line armed as the gate warning — but the step-0 auto-hand-off
    ('radio already on USB — firing the flash hand-off') ran first, cleared
    the warning via _next(), and threw the operator straight back onto the
    BIRTH screen, wiping the failed build's log via begin_guided's reset.
    Same law as the gate: a build that just failed suspends the
    self-driving (2026-08-10)."""
    body = func_source(GUIDE, "_render_step", cls="BirthGuideScreen")
    start = body.index('self._i == 0 and cur.get("screen")')
    end = body.index("the button press, automated")
    assert "_build_failed" in body[start:end], (
        "the automated flash hand-off must stand down after a failed build "
        "so the operator sees the [FAIL] line and chooses the retry")


# --- DEAD END 3: the keep-and-continue road never asked the node's name -----

def test_keep_and_continue_asks_the_name_before_the_hardware():
    """'Keep it — and build its Pi' jumped straight to the Pi picker, so
    _node_name stayed empty for the whole build. The connect-Pi step proves
    the Pi by the card's hostname-derived birth token, and its Next button
    was removed (2026-08-14) on the promise that the proof watcher walks
    both roads — with no name it walks neither, and the walkthrough
    dead-ends at 'Card in the Pi? Now give it power' with nothing to press.
    The map and Bluetooth questions were skipped with it."""
    keep = func_source(GUIDE, "_keep_and_continue", cls="BirthGuideScreen")
    assert "_render_name" in keep, (
        "the road must pass through the name step — the proof watcher and "
        "the imager's hostname both key on it")


def test_the_pair_check_is_recorded_where_the_pairing_is_checked():
    """On the keep-and-continue road the operator reached _check_pairing
    without ever passing _render_step's pair gate, so _pair_checked stayed
    False and the very next _render_step asked 'Which Raspberry Pi is this?'
    (and its confirm) a second time."""
    body = func_source(GUIDE, "_check_pairing", cls="BirthGuideScreen")
    assert "_pair_checked = True" in body


def test_a_board_already_known_this_lap_is_not_asked_for_again():
    """When the board key arrived with the road itself (keep-and-continue
    reads it off the stored certificate), the radio picker has nothing to
    ask — showing it again is the 2026-08-09 complaint ('we have already
    selected the radio board, is this screen necessary?') in a new spot.
    Back must step over exactly what forward stepped over, or the Back
    button bounces (the 2026-08-03 trap class)."""
    step = func_source(GUIDE, "_render_step", cls="BirthGuideScreen")
    assert "_board_preknown" in step
    back = func_source(GUIDE, "_back_from_pick_pi", cls="BirthGuideScreen")
    assert "_board_preknown" in back
    reset = func_source(GUIDE, "reset", cls="BirthGuideScreen")
    assert "_board_preknown" in reset, "one lap's shortcut must die with it"
    keep = func_source(GUIDE, "_keep_and_continue", cls="BirthGuideScreen")
    assert "_board_preknown" in keep


# --- MISINFORMATION 3: the closing screen claimed a flash that never --------
# --- happened ----------------------------------------------------------------

def test_the_closing_screen_does_not_claim_a_flash_that_never_happened():
    """'Radio flashed and verified, card written…' was unconditional — false
    on the 'I already have a working radio' road, where this walkthrough
    flashed nothing. Same class as the 'provisioned over the cable' lie the
    operator caught on this very screen on 2026-08-11."""
    body = func_source(GUIDE, "_render_done", cls="BirthGuideScreen")
    assert "_pi_flash_radio" in body, (
        "the summary must say only what THIS walkthrough did")


# --- DEAD END 4: the proof watcher was keyed on a name the card may not ----
# --- carry -------------------------------------------------------------------

def test_the_guide_watches_the_hostname_the_card_actually_carries():
    """The imager's hostname field is editable, and a name can hostnameify to
    nothing at all (a name typed in kana or cyrillic strips to "") — either
    way the card answers to a hostname the guide never learns, and the
    buttonless connect-Pi step (its escape removed 2026-08-14 on the promise
    the proof watcher walks both roads) can then never prove anything. The
    written hostname must ride the hand-back and win."""
    for fn in ("_pi_proof", "_pi_answering", "_start_node_poll"):
        body = func_source(GUIDE, fn, cls="BirthGuideScreen")
        assert "_imaged_hostname" in body, (
            f"{fn} must prefer the hostname the imager actually wrote")
    imager = func_source(IMAGER, "_back_to_birth", cls="PiImagerScreen")
    assert "imaged_hostname" in imager, (
        "the imager must hand the written hostname back with the resume")
    reset = func_source(GUIDE, "reset", cls="BirthGuideScreen")
    assert "_imaged_hostname" in reset, (
        "one card's hostname must never be probed for the next node")


def test_a_name_the_network_cannot_speak_is_refused_on_the_pi_path():
    """hostnameify strips everything outside a-z 0-9 and dashes, so a name
    with none of those becomes "" — the imager then blocks on an empty
    hostname, and the guide's proof watcher has nothing to resolve: a
    buttonless dead end several screens after the cause. Refuse it AT THE
    NAME, with the reason, and never behind a 'Use it anyway' that would
    walk straight into the trap."""
    nxt = func_source(GUIDE, "_name_next", cls="BirthGuideScreen")
    assert "_name_blocked" in nxt
    render = func_source(GUIDE, "_render_name", cls="BirthGuideScreen")
    assert "_name_blocked" in render, (
        "the button must not read 'Use it anyway' on a refusal that "
        "cannot be used anyway")


def test_a_stale_name_warning_does_not_survive_a_reset():
    """Type a clashing name, walk away, start a fresh walkthrough: the name
    step re-rendered with the LAST lap's warning over an empty box, and its
    button read 'Use it anyway →' about a name nobody had typed. Same class
    as every other cross-walkthrough leak reset() already sweeps."""
    reset = func_source(GUIDE, "reset", cls="BirthGuideScreen")
    for flag in ("_name_warning", "_name_warned"):
        assert flag in reset, f"reset() must clear {flag}"


# --- MISINFORMATION 4: 'waiting with a blank card' was never checked --------

def test_the_bootrom_screen_does_not_assert_a_blank_card():
    """Boot-ROM mode means the Pi found nothing to boot — which is also what
    a Pi with NO card looks like, and what a full card of last night's
    evidence looks like. 'It's waiting with a blank card' asserts presence
    and blankness, neither of which was read."""
    body = _code_only(func_source(IMAGER, "_offer_pi_as_reader",
                                  cls="PiImagerScreen"))
    assert "blank card" not in body, (
        "say what was checked (nothing bootable answered), not the card's "
        "contents")
