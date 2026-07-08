import pytest

from ui import safety


def test_known_boards_have_recovery_text():
    for board in ("Heltec V4", "Heltec V3", "Heltec V2", "T114",
                  "Wireless Tracker", "LilyGO T-Beam v1.1",
                  "LilyGO T-Beam Supreme", "T3S3", "T-Echo", "RAK4631",
                  "ATmega"):
        text = safety.recovery_text(board)
        assert isinstance(text, str) and text


def test_heltec_recovery_mentions_prg_rst():
    text = safety.recovery_text("Heltec V4")
    assert "PRG" in text and "RST" in text


def test_tbeam_v11_recovery_mentions_boot():
    text = safety.recovery_text("LilyGO T-Beam v1.1")
    assert "BOOT" in text


def test_supreme_and_t3s3_auto_reset():
    assert "automatically" in safety.recovery_text("LilyGO T-Beam Supreme")
    assert "automatically" in safety.recovery_text("T3S3")


def test_rak4631_double_tap():
    assert "Double-tap" in safety.recovery_text("RAK4631")


def test_atmega_is_high_risk():
    assert safety.is_high_risk("ATmega") is True
    assert safety.is_high_risk("Heltec V4") is False


def test_unknown_board_returns_generic_guidance():
    text = safety.recovery_text("Some Unknown Board")
    assert isinstance(text, str) and text
    # generic guidance should still be non-empty and cautionary
    assert "wait" in text.lower() or "power" in text.lower()


def test_warning_message_mentions_operation_in_progress():
    msg = safety.warning_message(estimated_seconds=42)
    assert "OPERATION IN PROGRESS" in msg
    assert "42" in msg
