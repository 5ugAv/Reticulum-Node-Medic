"""The 2026-09-09 emulator walkthrough audit — the findings that were about
screens promising things that are not true.

Each test names the finding. The rule throughout is the project's oldest one:
the screen says only what has actually happened, and names only a test the
operator can actually run.
"""
from tests.srcutil import func_source
from ui.birth_guide_flow import guide_steps

SCREEN = "ui/screens/birth_guide_screen.py"


# --- 9. "the radio you flashed at the start" on a lap that flashed nothing --

def _radio_body(pi_key, **kw):
    return next(s["body"] for s in guide_steps("pi", pi_key, **kw)
                if s.get("anim") == "radio_to_pi")


def test_the_closing_bullets_credit_a_flash_only_when_one_happened():
    assert "you flashed at the start" in _radio_body("pi_3a_plus")
    assert "you flashed at the start" not in _radio_body("pi_3a_plus",
                                                         flashed_here=False)


def test_dropping_the_flash_steps_and_crediting_the_flash_are_separate():
    """The keep-and-continue road keeps the whole sequence (its radio gate is
    already armed by a live probe) while never writing to the board."""
    full = guide_steps("pi", "pi_3a_plus", True, flashed_here=False)
    assert len(full) == len(guide_steps("pi", "pi_3a_plus", True)), \
        "flashed_here must not remove steps — that is flash_radio's job"
    assert "plug in your radio" in _radio_body("pi_3a_plus", flashed_here=False)


def test_all_four_bullet_combinations_name_the_right_socket():
    """A 4B/5 whose radio this lap did not flash used to fall through to the
    Pi Zero's sockets, because the two overrides could not both win."""
    for pi_key, cable in (("pi_3a_plus", "the radio needs that socket"),
                          ("pi_zero_2w", "the radio needs that socket"),
                          ("pi_4b", "the Pi runs on its own supply"),
                          ("pi_5", "the Pi runs on its own supply")):
        for flashed in (True, False):
            body = _radio_body(pi_key, flashed_here=flashed)
            assert cable in body, (pi_key, flashed, body)
            want = ("plug in the radio you flashed at the start" if flashed
                    else "plug in your radio")
            assert want in body, (pi_key, flashed, body)


def test_the_keep_and_continue_road_declares_it_did_not_flash():
    keep = func_source(SCREEN, "_keep_and_continue", cls="BirthGuideScreen")
    assert "_radio_preflashed = True" in keep
    steps = func_source(SCREEN, "_guide_steps", cls="BirthGuideScreen")
    assert "flashed_here=" in steps and "_radio_preflashed" in steps


def test_a_new_walkthrough_forgets_it():
    choose = func_source(SCREEN, "_choose", cls="BirthGuideScreen")
    assert "_radio_preflashed = False" in choose


# --- 11. a step counter that promises screens that never come --------------

def test_the_counter_counts_only_the_lead_screens_this_path_asks():
    lead = func_source(SCREEN, "_lead_screens", cls="BirthGuideScreen")
    assert '"radio", "pi"' in lead, \
        "the map question is not asked on the host path"


def test_the_counter_leaves_out_steps_the_medic_will_skip():
    counter = func_source(SCREEN, "_counter", cls="BirthGuideScreen")
    assert "_step_is_redundant" in counter
    assert "_skipped_fwd" in counter
    assert "n != i" in counter, \
        "the step being drawn is live by definition — never probe for it"


def test_nothing_still_computes_the_total_by_adding_two():
    src = open(SCREEN).read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "len(self._guide_steps()) + 2" not in code
    assert "total=len(steps) + 2" not in code


# --- 12. "Try again" before anything has been tried ------------------------

def test_the_node_online_step_carries_a_first_time_label():
    step = next(s for s in guide_steps("pi", "pi_3a_plus")
                if s.get("gate") == "node_online")
    assert step["next_first"] == "Start now  →"
    assert step["next"] == "Try again  →"


def test_the_screen_uses_it_until_a_build_has_actually_failed():
    src = func_source(SCREEN, "_next_text_for", cls="BirthGuideScreen")
    assert '_build_failed' in src and 'next_first' in src
    render = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    assert "next_text=self._next_text_for(s)" in render


# --- 14. a completion card that assumes the board has a screen -------------

def test_the_completion_card_only_names_the_screen_on_boards_that_have_one():
    from ui.board_images import has_screen
    assert not has_screen("xiao_esp32s3"), "catalogue says the XIAO has none"
    assert not has_screen("rak4631")
    assert has_screen("eora_s3") and has_screen("heltec32_v3")
    out = func_source("ui/screens/birth_screen.py", "_popup_outcome",
                      cls="BirthScreen")
    code = "\n".join(l for l in out.splitlines()
                     if not l.strip().startswith("#"))
    i = code.index("_setup = (")
    branch = code[i:i + 1400]
    assert "has_screen" in code[:i], "the card must ask whether there IS a screen"
    assert "CONFIG MODE" in branch
    j = branch.index("else:")
    assert "CONFIG MODE" not in branch[j:], \
        "a screenless board must not be told to read its screen"
    assert "VITALS" in branch[j:], "give it the check it CAN run"


# --- 5.4. the Pi picker gets the photos the radio picker has had all along --

def test_the_pi_picker_shows_each_model():
    pick = func_source(SCREEN, "_render_pick_pi", cls="BirthGuideScreen")
    assert "image_for_pi" in pick, "the rows must carry the model's photo"
    code = "\n".join(l for l in pick.splitlines()
                     if not l.strip().startswith("#"))
    i = code.index("if not png:")
    assert "continue" in code[i:i + 120], \
        "a model with no photo stays text-only — never another Pi's picture"


def test_exactly_one_pi_model_still_has_no_art():
    """If art lands for pi_3b_plus this can go; until then the fallback is the
    thing under test, so it must stay reachable."""
    from ui.board_images import pi_art_status
    assert pi_art_status()["pi_3b_plus"] is False


# --- 5.1. the radio-onto-the-Pi hand-off, with the socket and the adapter ---

def test_the_handoff_scene_only_marks_boards_we_measured():
    """ART AND GEOMETRY FROM THE SAME BOARD — ConnectPiAnim's rule, and the
    reason this class refused to point at a socket at all. A board with no
    photo is drawn from another Pi's sprite, so its measured sockets would be
    markers on somebody else's picture."""
    init = func_source("ui/widgets/birth_anims.py", "__init__",
                       cls="RadioToPiAnim")
    assert "sockets_for" in init
    assert "if not self._pi_png:" in init and "self._geo = None" in init
    draw = func_source("ui/widgets/birth_anims.py", "_draw", cls="RadioToPiAnim")
    assert "if self._geo is not None:" in draw, \
        "an unmeasured board keeps the honest two-objects scene"


def test_the_adapter_beat_exists_only_where_an_adapter_is_needed():
    """Not a guess: pi_connectors already says it in words for the Zero."""
    from ui.pi_connectors import standalone_power_hint
    assert "OTG" in standalone_power_hint("pi_zero_2w")
    assert "OTG" not in standalone_power_hint("pi_3a_plus")
    beats = func_source("ui/widgets/birth_anims.py", "_beats",
                        cls="RadioToPiAnim")
    assert "NEEDS_OTG" in beats and '"adapter"' in beats
    src = open("ui/widgets/birth_anims.py").read()
    i = src.index("NEEDS_OTG = (")
    assert "pi_3a_plus" not in src[i:i + 60], \
        "the 3A+ takes the radio in its USB-A — no adapter"


def test_every_beat_of_the_handoff_is_drawn():
    hand = func_source("ui/widgets/birth_anims.py", "_draw_handoff",
                       cls="RadioToPiAnim")
    for beat in ('"unplug"', '"adapter"', '"radio"'):
        assert beat in hand, beat
    assert "_PWR" in hand and "_CABLE" in hand, \
        "green is data and orange is power — the language ConnectPiAnim taught"


def test_a_one_socket_board_is_never_given_a_second_one():
    hand = func_source("ui/widgets/birth_anims.py", "_draw_handoff",
                       cls="RadioToPiAnim")
    code = "\n".join(l for l in hand.splitlines()
                     if not l.strip().startswith("#"))
    assert "if power is not None:" in code
