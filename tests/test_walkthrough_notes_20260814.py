"""The operator's bench-walk wording notes, 2026-08-14 — each pinned.

Nine notes, dictated live during the ELSEWHERE walkthrough with the screens
in hand. Each test names its note. The rule throughout: instructions appear
ONCE, say only what is true, and the screen does the work where it can see.
"""
from tests.srcutil import func_source, src
from ui.birth_guide_flow import guide_steps


def _step(anim, pi_key="pi_3a_plus"):
    return next(s for s in guide_steps("pi", pi_key) if s.get("anim") == anim)


# note 1 — first screen names the thing being plugged
def test_connect_your_node_names_the_radio():
    detect = func_source("ui/screens/birth_guide_screen.py", "_render_detect",
                         cls="BirthGuideScreen")
    # The note was "say WHAT gets plugged" — it named a model because the
    # screen had a model to hand. It does not: this is the DETECT landing, so
    # the board is unknown by definition, and hard-coding "(LoRa32)" told an
    # operator holding a RAK4631 to plug in a LilyGO (audit, 2026-09-09).
    # The note is kept — a named thing gets plugged — without the invention.
    assert "radio board" in detect, "still has to say what gets plugged"
    assert "{board}" in detect, "and name the board once the medic knows it"
    code = "\n".join(l for l in detect.splitlines()
                     if not l.strip().startswith("#"))
    assert "LoRa32" not in code, "never name a board we have not read"
    assert "read and routed" not in detect


# note 2 — already-flashed screen offers keep-and-continue-to-Pi
def test_already_flashed_offers_the_pi_build_road():
    kin = func_source("ui/screens/birth_guide_screen.py", "_render_already_kin",
                      cls="BirthGuideScreen")
    assert "_keep_and_continue" in kin
    keep = func_source("ui/screens/birth_guide_screen.py", "_keep_and_continue",
                       cls="BirthGuideScreen")
    # the radio's serial must ride along, or /dev/rnode loses its pin
    assert "by_id_serial" in keep
    # 2026-09-13 (breaker audit): the road now enters through the NAME step,
    # not straight at the Pi picker. Skipping the name left _node_name empty
    # for the whole build, and the connect-Pi step proves THE Pi by the
    # card's hostname-derived birth token — with no name the buttonless step
    # could never advance, which was the dead end this note's own text
    # ("no reflash, no dead end") forbids. The Pi question is still asked —
    # by the pair check, once — see test_guided_flow_breaker_20260913.
    assert "_render_name" in keep


# note 3 — pi picker buttons get breathing room
def test_pi_picker_buttons_are_not_packed():
    pick = func_source("ui/screens/birth_guide_screen.py", "_render_pick_pi",
                       cls="BirthGuideScreen")
    assert "spacing=dp(16)" in pick, "big fingers need space between models"


# note 4 — the card step stops claiming the card is blank
def test_card_step_does_not_call_the_card_blank():
    s = _step("insert_sd")
    assert "blank" not in s["body"].lower()
    # The note-4 point is the word "blank", and it still holds. The rest of the
    # sentence was rewritten 2026-09-09 so it stops pointing at a card slot on
    # the medic (there is only one, and the medic is running from it).
    assert "card reader" in s["body"], "the card goes into a reader"


# note 5 — one instruction on the handover step, not two
def test_card_handover_says_it_once():
    s = _step("sd_handover")
    assert s["title"].startswith("Remove the SD card from Node Medic")
    assert not s.get("body"), "the title IS the instruction — no duplicate below"


# note 6 — the dual-supply hazard is a warning box, not prose
def test_connect_pi_carries_the_backfeed_warning_on_a_3aplus():
    """The hazard survives; its EXCEPTION does not.

    Note 6's requirement — the dual-supply hazard belongs in a warning box,
    not buried in the hint's prose — still holds and is still pinned below.
    What changed on 2026-09-07, with the operator's approval, is the trailing
    "(safe only with the power wire removed)". An exception inside a hazard
    box turns an absolute into something negotiable, and "the power wire"
    referred to nothing visible on the screen: the only reading that makes it
    actionable is cutting the 5V conductor inside the operator's own cable.
    A tired reader lands on "so there IS a safe way to have both".
    """
    s = _step("connect_pi", "pi_3a_plus")
    warn = s.get("warning", "")
    assert "damage" in warn, "the consequence must still be named"
    assert "5V" in warn, "and WHY — two supplies fighting"
    assert "Never both at once" in warn, "stated as a rule about combining"
    assert "power wire removed" not in warn, "no exception inside a hazard box"
    # and the hint still does not bury the hazard mid-sentence
    assert "don't add a separate supply" not in s["hint"]


def test_boards_without_the_hazard_get_no_scare_box():
    s = _step("connect_pi", "pi_5")             # USB-C: one cable, no back-feed
    assert not s.get("warning", "")


# note 7 — no green Next appearing on the connect-pi step
def test_connect_pi_never_grows_a_next_button():
    step_src = func_source("ui/screens/birth_guide_screen.py", "_render_step",
                           cls="BirthGuideScreen")
    import re
    pi_branch = step_src.split("ConnectPiAnim)", 1)[1].split("elif", 1)[0]
    assert "show_next" not in pi_branch, (
        "operator, 2026-08-14: remove the Next — the medic moves on by itself "
        "(the watcher now walks both roads, so the old stranded-on-Wi-Fi "
        "escape hatch this button provided is gone with it)")


# note 8 — the success popup's instructions are readable at arm's length
def test_success_popup_text_is_bigger():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    # The bigger type belongs to every NON-warning card, not to "success"
    # alone — the blue "progress" waypoint card (2026-09-07) is read at the
    # same arm's length from the same bench.
    assert "_CALM_TONES" in popup and "21sp" in popup
    # source-level: ui.requirement_popup imports Kivy widgets, which do not
    # import in CI — same reason every other pin in this file reads source.
    assert '_CALM_TONES = ("success", "progress")' in src("ui/requirement_popup.py")


# note 9 — final step: bullets once, at the top, and the LED truth
def test_final_step_is_bullets_not_a_title_that_repeats_them():
    s = _step("radio_to_pi")
    assert not s.get("title"), "the bullets replaced the title, not caption it"
    body = s["body"]
    for needle in ("unplug the Pi from Node Medic",
                   "plug in the radio you flashed",
                   "own power supply",
                   "RGB LED"):
        assert needle in body, f"missing bullet: {needle}"


def test_the_led_line_matches_the_firmware_not_the_guess():
    """rnode_v4_rgb's own state chart: slow WHITE BREATHE = radio alive and
    idle; solid white = boot error. 'Stops pulsing white = ready' would be
    the opposite of the truth — the screen says what the firmware does."""
    s = _step("radio_to_pi")
    body = s["body"].lower()
    assert "breathe" in body or "breathing" in body
    assert "stops pulsing" not in body


# 2026-08-14, later the same bench: the write-these-down heading pulses
def test_the_write_these_down_heading_pulses():
    """Operator: 'animate that to make it pulse and catch the user's
    attention' — the imager's yellow box heading breathes; the body text
    holds still so it can be read."""
    callout = src("ui/widgets/callout.py")
    assert "pulse" in callout and "Animation" in callout
    assert "anim.repeat = True" in callout
    imager = src("ui/screens/pi_imager_screen.py")
    assert '"Write these down now!"' in imager and 'pulse=True' in imager


# -- the map step at the prelude (operator, 2026-08-14, note 10) -----------
# "If they click yes, it takes them to the map — populated by GPS if there
#  is a signal. If there isn't, they can type an address in. And if there is
#  a GPS signal, they can override it by typing an address."
# ConfirmLocationPopup already does all three (pin from seed, tap to move,
# address search, pull-GPS button); the step is WHERE it opens: at the
# prelude when Show-on-map is chosen — not at certificate time, when the
# operator has mentally finished.

def test_choosing_show_on_map_opens_the_map():
    pick = func_source("ui/screens/birth_guide_screen.py",
                       "_pick_node_location", cls="BirthGuideScreen")
    assert "ConfirmLocationPopup" in pick
    assert "splitter_gps_reader" in pick, "GPS seeds the pin when there is a fix"
    chosen = func_source("ui/screens/birth_guide_screen.py",
                         "_share_chosen", cls="BirthGuideScreen")
    assert "_pick_node_location" in chosen
    # Hidden must NOT detour through a map for a position it will never use
    assert "APPROX" in chosen or "approx" in chosen


def test_the_confirmed_pin_rides_the_handoff():
    hand = func_source("ui/screens/birth_guide_screen.py",
                       "_hand_over_name", cls="BirthGuideScreen")
    assert "_node_location" in hand
    guided = func_source("ui/screens/birth_screen.py",
                         "begin_guided", cls="BirthScreen")
    assert "map-confirmed" in guided


def test_a_prelude_confirmed_pin_is_not_asked_twice():
    """The cert-time map confirm exists to catch an UNSEEN location. A pin
    the operator placed on the prelude map has been seen by definition —
    asking again at the end of a twenty-minute build is asking twice."""
    commit = func_source("ui/screens/birth_screen.py",
                         "_confirm_location_then_commit", cls="BirthScreen")
    assert "map-confirmed" in commit


def test_the_pin_belongs_to_one_node():
    """Same hygiene as the share answer beside it: a fresh walkthrough must
    not inherit the previous node's coordinates."""
    reset = src("ui/screens/birth_guide_screen.py")
    at = reset.index("self._share_location = location_share.HIDDEN")
    window = reset[at - 600:at + 200]
    assert "_node_location = None" in window


# -- the prelude map can zoom to a suburb (operator, 2026-08-14, note 11) --
# "that's as close as it seems to zoom in, which is not even a suburb view —
#  we need to zoom much closer for the user to pick their location."
# The tile drawer has always been able to OVERZOOM (scale the nearest cached
# ancestor — "blurry beats black"); it was the zoom STEPPER and the draw-path
# snap that refused to go past the cached edge, so a world basemap capped the
# whole map at state level. Interactive zoom now steps past the edge up to
# OVERZOOM_MAX; the drawer blurs gracefully where detail tiles are absent.

def test_interactive_zoom_steps_past_the_cached_edge():
    import sys
    sys.modules.pop("ui.screens.scan_screen", None)
    from tests.srcutil import func_source
    step = func_source("ui/screens/scan_screen.py", "_step_to_next_zoom",
                       cls="MapPlot")
    assert "OVERZOOM_MAX" in step
    draw = func_source("ui/screens/scan_screen.py", "_draw_tiled", cls="MapPlot")
    assert "OVERZOOM_MAX" in draw, (
        "the draw path snapped the zoom back to the cached edge, undoing the "
        "step past it")


def test_overzoom_is_pure_and_capped():
    from ui.map_tiles import step_zoom
    # the pure helper keeps its documented stay-put behaviour at the edge;
    # the widget layers the overzoom on top — so nothing else changes.
    assert step_zoom([4, 8], 8, +1) == 8


def test_the_address_caption_points_at_the_visible_button():
    """Operator, 2026-08-14: the caption said tap 'Show address' — a button
    below the map, off-view under the keyboard — while the button beside the
    field says Find. A caption must name the control the eye can find."""
    popup = src("ui/widgets/confirm_location.py")
    assert "tap Find to look up the spot" in popup
    assert "Tap 'Show address' to look up this spot online (optional)" not in popup


# -- no connect-radio screen after the prelude (operator, note 12) ---------
# "we've already done this step at the very beginning — the user still has
#  the radio connected. Skip this screen; go directly to the Birth a new
#  node page."
# The 2026-08-09 law survives untouched: the WORK is not skipped — the
# button press is. A board present on USB auto-fires the same hand-off the
# green button carried; an already-verified radio (keep-and-continue) lands
# past the gate that agrees; an absent board still shows the instructions.

def test_the_prelude_starts_the_steps_through_one_door():
    for fn in ("_share_chosen", "_pick_node_location"):
        body = func_source("ui/screens/birth_guide_screen.py", fn,
                           cls="BirthGuideScreen")
        assert "_begin_steps" in body, f"{fn} bypasses the connect-radio skip"


def test_the_connect_skip_runs_downstream_of_the_pair_check():
    """2026-08-14 night, both SKYFINGER runs: the skip fired from
    _begin_steps, BYPASSING the pairing check that lives in _render_step —
    the check then fired at resume time, whose success path rewinds to step
    0, marching the operator into re-flashing a green board (five flashes
    in one night). The skip now lives in _render_step, after that check."""
    begin = func_source("ui/screens/birth_guide_screen.py", "_begin_steps",
                        cls="BirthGuideScreen")
    assert "_next()" not in begin, "the door must not bypass _render_step"
    render = func_source("ui/screens/birth_guide_screen.py", "_render_step",
                         cls="BirthGuideScreen")
    at_check = render.index("_pair_checked")
    at_skip = render.index("local_board_ports")
    assert at_check < at_skip, "the skip must come AFTER the pairing check"
    assert "_next()" in render, (
        "the hand-off is FIRED (the work still runs) — the 2026-08-09 "
        "skipped-flash lesson holds")
    assert "cert_for_usb_serial" in render, (
        "a certified radio is skipped, not re-flashed (briefing Task 1)")


# briefing Task 8 — the OLED preview shows the NODE's name, live
def test_the_oled_preview_is_the_nodes_name_live():
    from tests.srcutil import src
    b = src("ui/screens/birth_screen.py")
    assert "c.set_name(t)" in b, "the name field must live-drive the OLED"
    assert 'name=(self._name_in.text or "").strip().upper()' in b
