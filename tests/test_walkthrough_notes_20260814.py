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
    assert "radio node (LoRa32)" in detect
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
    assert "_render_pick_pi" in keep


# note 3 — pi picker buttons get breathing room
def test_pi_picker_buttons_are_not_packed():
    pick = func_source("ui/screens/birth_guide_screen.py", "_render_pick_pi",
                       cls="BirthGuideScreen")
    assert "spacing=dp(16)" in pick, "big fingers need space between models"


# note 4 — the card step stops claiming the card is blank
def test_card_step_does_not_call_the_card_blank():
    s = _step("insert_sd")
    assert "blank" not in s["body"].lower()
    assert "Insert the SD card" in s["body"]


# note 5 — one instruction on the handover step, not two
def test_card_handover_says_it_once():
    s = _step("sd_handover")
    assert s["title"].startswith("Remove the SD card from Node Medic")
    assert not s.get("body"), "the title IS the instruction — no duplicate below"


# note 6 — the dual-supply hazard is a warning box, not prose
def test_connect_pi_carries_the_backfeed_warning_on_a_3aplus():
    s = _step("connect_pi", "pi_3a_plus")
    warn = s.get("warning", "")
    assert "while this cable is in" in warn and "damage" in warn
    assert "power wire removed" in warn          # the one safe exception
    # and the hint no longer buries the hazard mid-sentence
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
    assert 'if tone == "success"' in popup and "21sp" in popup


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
