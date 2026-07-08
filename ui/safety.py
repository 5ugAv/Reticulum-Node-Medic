"""Safety panel content — board-specific abort-recovery guidance.

Pure data / text (no Kivy) so it is unit-testable and reusable. When Back is
pressed during an active operation (flashing, config write), the UI slides up
this warning plus the recovery steps for the connected board.
"""

from __future__ import annotations

# Boards that recover the same way as a Heltec (hold PRG, tap RST).
_PRG_RST = "Hold PRG, press RST once, then release PRG."
_HELTEC_LIKE = {
    "Heltec V4", "Heltec V3", "Heltec V2", "T114", "Wireless Tracker",
}

BOARD_RECOVERY = {
    "LilyGO T-Beam v1.1":
        "Unplug the board, hold BOOT, plug it back in, then release BOOT "
        "after 3 seconds.",
    "LilyGO T-Beam Supreme":
        "The tool will reset the board automatically — no button needed.",
    "T3S3":
        "The tool will reset the board automatically — no button needed.",
    "LilyGO T-Echo":
        "Hold the lower button, press the upper button briefly, then release "
        "both. The LED pulses green to confirm.",
    "T-Echo":
        "Hold the lower button, press the upper button briefly, then release "
        "both. The LED pulses green to confirm.",
    "RAK4631":
        "Double-tap RST. A USB drive appears to confirm recovery mode.",
    "ATmega":
        "HIGH RISK: this board cannot self-recover. An external programmer is "
        "required to reflash it if the operation is interrupted.",
}

_HIGH_RISK = {"ATmega"}

_GENERIC = (
    "Please wait for the operation to finish. If you must stop, remove power "
    "only as a last resort — it may leave the board in an unrecoverable state."
)


def recovery_text(board: str) -> str:
    """Return abort-recovery instructions for *board* (generic if unknown)."""
    if board in _HELTEC_LIKE:
        return _PRG_RST
    return BOARD_RECOVERY.get(board, _GENERIC)


def is_high_risk(board: str) -> bool:
    return board in _HIGH_RISK


def warning_message(estimated_seconds: int) -> str:
    return (
        "OPERATION IN PROGRESS — Interrupting now may damage the connected "
        "hardware or leave it in an unrecoverable state. It is strongly "
        "recommended to wait.\n"
        f"Estimated time remaining: {estimated_seconds} seconds."
    )
