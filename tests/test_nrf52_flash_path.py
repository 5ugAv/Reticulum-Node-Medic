"""Flashing an nRF52 board (RAK4631 / T-Echo / Heltec T114).

A RAK4631 birth failed on 2026-08-05 with "Build didn't finish" and NOTHING
attempted — the watcher on the medic saw no flashing process start and no USB
change at all. The cause was not the missing flasher, as it first appeared:
``autoinstall_answers`` raised "band 915 MHz not yet verified for rak4631"
because the board carried no band_map, and birth_flash returned that as a
failure before any tool ran.

The band menu is not guessable, so it was transcribed from the rnodeconf 2.5.0
source installed on the medic (RNS/Utilities/rnodeconf.py):

    What band is this RAK4631 for?
    [1] 433 MHz   [2] 868 MHz   [3] 915 MHz   [4] 923 MHz
"""
import pytest

from workflows.rnode_boards import RNODE_BOARDS
from workflows.rnode_flash import (NRF_FLASHER, _missing_nrf_flasher,
                                   autoinstall_interactions, birth_flash)


def _board(key):
    bs = (RNODE_BOARDS if isinstance(RNODE_BOARDS, dict)
          else {b.key: b for b in RNODE_BOARDS})
    return bs.get(key)


NRF_KEYS = ("rak4631", "techo", "heltec_t114")


@pytest.mark.parametrize("key", NRF_KEYS)
def test_every_nrf52_board_can_answer_the_915_band_prompt(key):
    """THE bug: no band_map meant the birth died before it started."""
    answers = _board(key).autoinstall_answers(915)
    assert answers[2] == "3", f"{key}: 915 MHz is menu choice 3, got {answers[2]}"


@pytest.mark.parametrize("key", NRF_KEYS)
def test_the_whole_band_menu_matches_rnodeconf(key):
    b = _board(key)
    assert b.autoinstall_bands == {433: 1, 868: 2, 915: 3, 923: 4}, (
        f"{key} band menu drifted from rnodeconf 2.5.0")


def test_the_rak_answers_drive_the_prompts_in_order():
    """[device index, enter past the blurb, band, confirm]."""
    pairs = autoinstall_interactions(_board("rak4631"), 915)
    assert [a for _p, a in pairs] == ["11", "", "3", "y"]
    assert "band" in pairs[2][0].lower()


def test_an_unverified_band_still_refuses_rather_than_guessing():
    """The guard that caught this must stay: a band nobody has transcribed is
    refused, not approximated. Flashing the wrong band byte is not free."""
    with pytest.raises(ValueError, match="not yet verified"):
        _board("rak4631").autoinstall_answers(2400)


# -- the flasher preflight --------------------------------------------------

class _Conn:
    """Minimal connection double: says whether a command is found."""

    def __init__(self, have):
        self.have = have
        self.seen = []

    def run(self, cmd, timeout=None):
        self.seen.append(cmd)
        return (0, "/usr/bin/adafruit-nrfutil\n", "") if self.have else (1, "", "")


def test_a_missing_flasher_is_named_before_anything_is_attempted():
    msg = _missing_nrf_flasher(_Conn(False))
    assert NRF_FLASHER in msg
    assert "pip3 install" in msg, "say how to fix it, not just what is wrong"


def test_a_present_flasher_says_nothing():
    assert _missing_nrf_flasher(_Conn(True)) == ""


def test_the_preflight_asks_the_CONNECTION_not_the_tool_host():
    """On the cable-birth path the flash runs on the medic, so what matters is
    the PATH of whatever will run rnodeconf — not this machine."""
    c = _Conn(True)
    _missing_nrf_flasher(c)
    assert any(NRF_FLASHER in cmd for cmd in c.seen)


def test_an_unreachable_connection_does_not_block_the_flash():
    """If the probe itself fails we cannot tell, and refusing on a guess would
    be worse than letting rnodeconf try and report."""
    class Boom:
        def run(self, *a, **k):
            raise OSError("nope")
    assert _missing_nrf_flasher(Boom()) == ""


def test_esp32_boards_skip_the_nrf_preflight():
    """The check must not cost an ESP32 birth a round trip."""
    c = _Conn(False)
    ok, msg, _ = birth_flash(c, _board("heltec32_v4"), "/dev/ttyUSB0", band_mhz=915)
    assert not any(NRF_FLASHER in cmd for cmd in c.seen), (
        "an ESP32 board asked about the nRF flasher")


def test_an_nrf_board_refuses_early_when_the_flasher_is_absent():
    c = _Conn(False)
    ok, msg, already = birth_flash(c, _board("rak4631"), "/dev/ttyACM1",
                                   band_mhz=915)
    assert ok is False and already is False
    assert NRF_FLASHER in msg
