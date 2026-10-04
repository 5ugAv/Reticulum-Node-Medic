"""The 2026-09-09 emulator walkthrough audit — the findings that were about
screens promising things that are not true.

Each test names the finding. The rule throughout is the project's oldest one:
the screen says only what has actually happened, and names only a test the
operator can actually run.
"""
import pytest
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


# --- 5.2. the charge-only cable, taught as a symptom ------------------------

def test_the_cable_demonstration_never_uses_green():
    """Green is this UI's word for 'the medic can see it'. A green ring for a
    hypothetical is the 2026-08-10 fault — a target read as an
    acknowledgement. The difference is drawn in the CABLE."""
    src = open("ui/widgets/birth_anims.py").read()
    pay = func_source("ui/widgets/birth_anims.py", "_payloads",
                      cls="ConnectPiAnim")
    code = "\n".join(l for l in pay.splitlines()
                     if not l.strip().startswith("#"))
    assert "green" not in code.lower(), \
        "green is 'the medic can see it', and nothing has been seen yet"
    assert "_CABLE" not in code and "COLORS[" not in code, \
        "no themed data/connection colour on a hypothetical"
    doubt = func_source("ui/widgets/birth_anims.py", "show_cable_doubt",
                        cls="ConnectPiAnim")
    assert "_doubt" in doubt


def test_the_empty_run_comes_first_and_the_carrying_one_second():
    trav = func_source("ui/widgets/birth_anims.py", "_plug_travel",
                       cls="ConnectPiAnim")
    assert "cycle = int(self.phase * 2) % 2" in trav, \
        "two plug-ins per loop: the two cables look identical"
    src = open("ui/widgets/birth_anims.py").read()
    assert src.count("if doubt_cycle == 1 and doubt_seated > 0.0:") == 2, \
        "both scenes (bottom-entry and side-entry) must demonstrate it"


def test_it_only_starts_once_the_wait_is_overdue():
    """For the first two and a half minutes the honest picture is 'plug it in
    and wait' — a Pi expanding its card looks exactly like a Pi that will
    never come up, and doubting a good cable sends the operator hunting a
    fault they do not have."""
    render = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    code = "\n".join(l for l in render.splitlines()
                     if not l.strip().startswith("#"))
    i = code.index("show_cable_doubt")
    assert "WAIT_PATIENCE_S" in code[i:i + 200], \
        "the demonstration must be on the patience timer, not immediate"
    assert "_nav_token" in code[i - 300:i], \
        "and must not fire onto a step the operator has already left"


# --- boards in a scene, not stickers on the screen --------------------------

def test_no_board_photo_ships_as_an_opaque_white_rectangle():
    """Operator, 2026-09-09, with the walkthrough on the panel: "if there is a
    white background, clear it so the boards look like animations instead of
    stickers". Half the catalogue was already cut; the rest were slabs of
    studio white on a black UI. scripts/cut_board_art.py does it by flooding
    inward from the edges, never by colour-keying white — a board has white
    silkscreen, white shells and white text ON it."""
    import glob
    import os
    Image = pytest.importorskip("PIL.Image")
    bad = []
    files = glob.glob("assets/boards/*.png")
    files.append("assets/ui/anim/pi_zero_2w.png")
    for f in files:
        a = Image.open(f).convert("RGBA").split()[3].histogram()
        opaque = sum(a[201:]) / (sum(a) or 1)
        if opaque > 0.97:
            bad.append(os.path.basename(f))
    assert not bad, f"still on a solid background: {bad}"


def test_the_cutter_leaves_white_that_is_part_of_the_board():
    """The T-Echo is a WHITE-CASED device and the RAK is covered in white
    labels. A colour-key would have eaten both; flooding from the edges must
    not."""
    Image = pytest.importorskip("PIL.Image")
    np = pytest.importorskip("numpy")
    for name, want in (("techo", 0.25), ("rak4631", 0.10)):
        a = np.asarray(Image.open(f"assets/boards/{name}.png").convert("RGBA"))
        opaque = a[..., 3] > 200
        white = (a[..., :3].min(axis=-1) > 225) & opaque
        assert white.mean() > want * 0.5, \
            f"{name}: its own white has been eaten ({white.mean():.2%})"


def test_board_art_is_sized_by_its_longest_side():
    """Every board photo in the catalogue was landscape until the RAK4631 art
    arrived portrait (operator, 2026-09-09). Sizing a sprite by HEIGHT assumes
    the wide shape: at the same height fraction a tall board draws about a
    third of the area and reads as a chip. Each scene fits the longest side to
    one box instead, so the next board's photo lands right whichever way round
    it was taken."""
    src = open("ui/widgets/birth_anims.py").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert code.count("if ba >= 1.0") + code.count("_na >= 1.0") >= 3, \
        "the board-drawing scenes must all fit the longest side"
    Image = pytest.importorskip("PIL.Image")
    im = Image.open("assets/boards/rak4631.png")
    assert im.size[1] > im.size[0], "the RAK art is portrait — that is the case"


# --- the advice must not contradict the page it sits on --------------------

def test_amber_does_not_mean_answering_when_nothing_was_ever_heard():
    """Registry.status promotes an unknown node to warn the moment a probe
    goes unanswered. A node that has NEVER been heard therefore arrives at the
    advice block amber — and was told "is answering, but not happily" and "a
    rebirth would be premature, it is still talking", on a page whose header
    read "Last heard: never" (operator, SolarLove on the screen, 2026-09-09)."""
    from ui.rebirth_advice import advise
    a = advise("warn", name="SolarLove", heard_ever=False)
    assert "answering, but not happily" not in a.headline
    assert "still talking" not in a.rebirth_note
    assert "hasn't answered" in a.headline
    b = advise("warn", name="FAITH", heard_ever=True)
    assert "answering, but not happily" in b.headline


def test_the_node_page_answers_that_question_from_the_same_evidence():
    src = open("ui/screens/node_detail_screen.py").read()
    assert "heard_ever=record.last_seen_hours(now) is not None" in src, \
        "the advice must read the same evidence as the header's Last heard"


# --- the T-Deck firmware that shipped on Heltec V4s (2026-09-11) -----------

def test_each_board_model_builds_into_its_own_directory():
    """arduino-cli keys its build dir by FQBN alone, so every ESP32-S3 target
    in the RNode sketch shares one directory and the last build wins. A T-Deck
    build overwrote the Heltec V4 artifact on 2026-08-27 and every V4 birthed
    afterwards was flashed with T-Deck firmware — radio perfect (both SX1262),
    screen dead, CONF_DSET never written."""
    from workflows.rnode_v4_rgb import bin_for, build_dir_for, compile_command
    assert build_dir_for(0x3F) != build_dir_for(0x3B)
    assert bin_for(0x3F) != bin_for(0x3B)
    cmd = compile_command(board_model=0x3F)
    assert "--build-path" in cmd, \
        "without an explicit build path arduino-cli shares one per FQBN"
    assert "0x3F" in cmd


def test_verify_refuses_firmware_built_for_another_board():
    """The old check asked only whether SOMETHING valid answered, so it
    reported 'Board verified' on a V4 running T-Deck firmware."""
    from tests.srcutil import func_source
    src = func_source("workflows/rnode_v4_rgb.py", "_verify",
                      cls="HeltecV4RGBWorkflow")
    assert "_reported_board_model" in src
    assert "self.board_model" in src


def test_the_board_model_comes_from_the_firmware_not_the_provisioning():
    """rnodeconf's product triple is product:model:BOARD. The first two are
    what we TOLD the board it is; only the third says what the running
    firmware was compiled as."""
    from workflows.rnode_v4_rgb import _reported_board_model
    assert _reported_board_model("Product : Heltec LoRa32 v4 (c3:c8:3f)") == 0x3F
    assert _reported_board_model("Product : Heltec LoRa32 v4 (c3:c8:3b)") == 0x3B
    # unreadable evidence must not fail a build on a guess
    assert _reported_board_model("no triple here") is None
    assert _reported_board_model("") is None
