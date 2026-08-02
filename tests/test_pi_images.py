"""Whichever Pi and whichever radio were used, show THEIR photos.

Standing design aim (operator, 2026-08-02): the operator is holding the boards;
matching what's on screen to what's in their hands is the point. A photo of the
WRONG board is worse than no photo, because the check silently passes.
"""

import os

from ui.board_images import image_for, image_for_pi, pi_art_status


def test_the_pi_zero_photo_resolves():
    """The art predates the assets/boards convention and lives in the animation
    folder — the resolver must find it there rather than requiring a move."""
    p = image_for_pi("pi_zero_2w")
    assert p and os.path.exists(p)
    assert p.endswith("pi_zero_2w.png")


def test_the_heltec_v3_photo_resolves():
    p = image_for("heltec32_v3")
    assert p and os.path.exists(p)


def test_a_pi_we_have_no_photo_of_returns_nothing():
    """Degrade to the generic drawing, never to a different Pi's photo."""
    assert image_for_pi("pi_4b") is None or os.path.exists(image_for_pi("pi_4b"))
    assert image_for_pi("") is None
    assert image_for_pi("not_a_pi") is None


def test_the_5a_and_3a_pi5_share_one_photo():
    """Same physical board — the split is about the power supply."""
    assert image_for_pi("pi_5_full") == image_for_pi("pi_5")


def test_art_status_covers_every_pi_the_power_model_knows():
    from workflows.power_compat import PI_POWER
    assert set(pi_art_status()) == set(PI_POWER)


def test_drop_in_contract_holds_for_pis_too():
    """Same as boards: putting assets/boards/<pi_key>.png in place starts using
    it with no code change."""
    import ui.board_images as bi
    assert any("boards" in d for d in bi._PI_DIRS)


# --- wiring -----------------------------------------------------------------

def test_the_handoff_shows_both_real_photos():
    src = open("ui/screens/birth_screen.py").read()
    block = src[src.index("def _handoff_photos"):src.index("def _handoff_block")]
    assert "image_for_pi" in block and "image_for" in block


def test_the_pi_animation_can_render_the_detected_model():
    src = open("ui/widgets/birth_anims.py").read()
    cls = src[src.index("class ConnectPiAnim"):]
    assert "pi_key" in cls and "image_for_pi" in cls
    assert "PI_ZERO_PNG" in cls, "must still fall back to the generic drawing"


def test_the_board_animation_can_render_the_detected_board():
    src = open("ui/widgets/birth_anims.py").read()
    cls = src[src.index("class ConnectBoardAnim"):src.index("class ConnectPiAnim")]
    assert "board_key" in cls and "image_for" in cls
    assert "LORA_PNG" in cls, "must still fall back to the generic drawing"


# --- look-alike boards: name the mark, don't rely on resemblance ------------

def test_the_lora32_v21_says_the_board_never_prints_v2_1():
    """Verified against LilyGO's wiki and their own two-sided product photo:
    the white label reads MODEL: T3 V1.6.1 and the back is silkscreened
    T3_V1.6. The name it is SOLD under appears nowhere on it, which is exactly
    what sends an operator looking for a marking that does not exist."""
    from ui.board_images import how_to_tell
    tell = how_to_tell("lora32_v21")
    assert "T3 V1.6.1" in tell
    assert "T3_V1.6" in tell
    assert "NOT say v2.1" in tell


def test_boards_with_distinctive_looks_get_no_hint():
    """The hint exists for families that look alike. A Heltec V3 does not need
    one, and noise would train the operator to skip the line."""
    from ui.board_images import how_to_tell
    for k in ("heltec32_v3", "heltec32_v4", "rak4631", "tbeam_supreme"):
        assert how_to_tell(k) == ""


def test_only_verified_markings_are_claimed():
    """Same rule as the chip-variant table: a marking nobody has read is a
    guess, and a guess printed as instruction is worse than silence."""
    from ui.board_images import HOW_TO_TELL
    assert set(HOW_TO_TELL) == {"lora32_v21"}


def test_the_birth_screen_shows_the_hint_with_the_photo():
    src = open("ui/screens/birth_screen.py").read()
    assert "how_to_tell" in src


# --- imaging screen: see what you type, and write it down -------------------

IMG_SRC = open("ui/screens/pi_imager_screen.py").read()


def test_password_fields_can_be_revealed():
    """A masked field on a touchscreen keypad is easy to get wrong with no way
    to check, and a mistyped LOGIN password is only discovered much later —
    when the Pi refuses to let you in."""
    block = IMG_SRC[IMG_SRC.index("def _field"):IMG_SRC.index("def _build")]
    assert 'text="Show"' in block and '"Hide"' in block
    assert "ti.password = not ti.password" in block


def test_reveal_starts_hidden():
    """Revealing is the operator's choice, not the default."""
    block = IMG_SRC[IMG_SRC.index("def _field"):IMG_SRC.index("def _build")]
    assert "password=password" in block, "field must honour the masked default"


def test_non_password_fields_get_no_toggle():
    block = IMG_SRC[IMG_SRC.index("def _field"):IMG_SRC.index("def _build")]
    assert "if not password:" in block


def test_the_operator_is_told_to_write_the_credentials_down():
    """The medic does not keep the password — it is hashed onto the card and
    cannot be read back, so losing it means re-imaging."""
    assert "Write these down now" in IMG_SRC
    assert "does NOT store the password" in IMG_SRC
    assert "image the card again" in IMG_SRC


def test_hostnameify_turns_a_node_name_into_a_valid_hostname():
    from provisioning.pi_imager import hostnameify   # pure: no Kivy
    assert hostnameify("Zero V3") == "zero-v3"
    assert hostnameify("HOPE") == "hope"
    assert hostnameify("Rooftop East!!") == "rooftop-east"
    assert hostnameify("  -- weird -- ") == "weird"
    assert hostnameify("") == ""
    assert len(hostnameify("x" * 80)) <= 32


def test_the_imager_takes_a_prefilled_name():
    """The operator named the node one screen ago. Asking again invites two
    different names for one node — and the hostname is what the medic resolves
    later to find it, so a mismatch means a node it cannot reach."""
    src = open("ui/screens/pi_imager_screen.py").read()
    assert "def prefill_hostname" in src
    block = src[src.index("def prefill_hostname"):src.index("def _field")]
    assert "hostnameify" in block
    assert "not ti.text.strip()" in block, "must not clobber a typed hostname"


def test_birth_hands_the_name_to_the_imager():
    src = open("ui/screens/birth_screen.py").read()
    block = src[src.index("def _go_image_pi"):]
    assert "prefill_hostname" in block
    assert "_name_in.text" in block
