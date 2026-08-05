"""Recovery advice must match the board in the operator's hand.

The failed-build popup told everyone to "hold BOOT, tap RST". A RAK4631 has no
BOOT button — it double-taps RESET into a UF2 bootloader — so the one
instruction on screen was impossible to follow (operator, 2026-08-05). Being
told to press a button that does not exist is worse than being told nothing:
it sends someone hunting the board for a control that was never there.

The per-board text already existed in ui.safety; the failure paths simply were
not asking for it.
"""
from ui.safety import recovery_for_board, _GENERIC_FLASH
from workflows.rnode_boards import RNODE_BOARDS


def _board(key):
    bs = (RNODE_BOARDS if isinstance(RNODE_BOARDS, dict)
          else {b.key: b for b in RNODE_BOARDS})
    return bs.get(key)


def test_the_rak4631_is_never_told_to_hold_boot():
    """THE bug. It has no BOOT button."""
    txt = recovery_for_board(_board("rak4631"))
    assert "BOOT" not in txt
    assert "double-tap" in txt.lower() and "RST" in txt


def test_the_t_echo_gets_its_own_two_button_dance():
    txt = recovery_for_board(_board("techo"))
    assert "BOOT" not in txt
    assert "lower button" in txt and "upper button" in txt


def test_the_heltec_v4_is_matched_by_KEY_not_display_name():
    """Pre-existing silent bug: the catalogue calls it "Heltec LoRa32 v4" while
    the recovery table says "Heltec V4", so a display-name lookup fell through
    to the generic text — for the board this tool flashes most."""
    b = _board("heltec32_v4")
    assert b.display_name not in ("Heltec V4",), "display name drifted, as expected"
    txt = recovery_for_board(b)
    assert "PRG" in txt and "RST" in txt, f"V4 fell through to generic: {txt!r}"


def test_a_board_that_self_resets_says_no_button_is_needed():
    txt = recovery_for_board(_board("tbeam_supreme"))
    assert "no button" in txt.lower()


def test_an_unknown_board_does_not_guess_a_button_combination():
    """Guessing a bootloader combination for an unknown board can make things
    worse, so the fallback sends the operator to that board's own manual."""
    txt = recovery_for_board(None)
    assert txt == _GENERIC_FLASH
    assert "BOOT" not in txt and "PRG" not in txt
    assert recovery_for_board("no_such_board") == _GENERIC_FLASH


def test_the_fallback_is_not_the_abort_wording():
    """_GENERIC is written for aborting mid-operation ("please wait for the
    operation to finish"), which is a non-sequitur once the flash has already
    failed."""
    assert "please wait" not in _GENERIC_FLASH.lower()


def test_every_catalogue_board_gets_usable_advice():
    bs = (list(RNODE_BOARDS.values()) if isinstance(RNODE_BOARDS, dict)
          else list(RNODE_BOARDS))
    for b in bs:
        txt = recovery_for_board(b)
        assert txt and len(txt) > 20, f"{b.key} has no advice"


#: nRF52 boards whose recovery has been CONFIRMED against the hardware or the
#: vendor's own documentation. heltec_t114 is deliberately absent: it is nRF52,
#: but the table maps it to the Heltec "hold PRG, tap RST" dance rather than the
#: UF2 double-tap its bootloader family normally uses, and nobody here has held
#: one. Asserting a blanket "no nRF52 board mentions PRG" would encode a guess
#: as a fact — see the note in test_t114_recovery_is_unverified below.
_CONFIRMED_NRF52 = {"rak4631", "techo"}


def test_confirmed_nrf52_boards_never_get_the_esp32_button_dance():
    for key in _CONFIRMED_NRF52:
        txt = recovery_for_board(_board(key))
        assert "BOOT" not in txt and "PRG" not in txt, f"{key}: {txt!r}"


def test_t114_recovery_is_unverified_and_says_so_somewhere():
    """A live question, not a passing test dressed up as an answer.

    The Heltec Mesh Node T114 is nRF52840, yet it sits in the Heltec-like group
    and is told to hold PRG. That may be right — Heltec ships its own bootloader
    on some boards — or it may be the same class of mistake the RAK4631 hit. It
    needs someone holding the board. Until then this test simply pins the
    CURRENT behaviour so a change is deliberate rather than accidental.
    """
    txt = recovery_for_board(_board("heltec_t114"))
    assert "PRG" in txt, ("if this changed, someone verified the T114 — update "
                          "_CONFIRMED_NRF52 and this test together")


def test_the_failure_paths_actually_ask_for_it():
    """Guards the wiring: the text existed all along and simply was not used."""
    from tests.srcutil import src
    s = src("ui/screens/birth_screen.py")
    assert s.count("recovery_for_board") >= 2, (
        "both the failure popup and the outcome panel must use per-board advice")
    assert "Hold BOOT, tap RST, " not in s, "hard-coded ESP32 advice is back"
