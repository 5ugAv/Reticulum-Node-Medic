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


#: Catalogue board KEY -> the label the tables above are written against.
#:
#: Display names drift from these labels and the join silently fails: the
#: catalogue calls the V4 "Heltec LoRa32 v4" while the recovery table says
#: "Heltec V4", so looking up by display name returned the GENERIC text for the
#: board this tool flashes most. The key is the stable thing — it is what the
#: catalogue, the images and the power profiles are all keyed by.
_BY_KEY = {
    "rak4631": "RAK4631",
    "techo": "T-Echo",
    "heltec_t114": "T114",
    "heltec32_v4": "Heltec V4",
    "heltec32_v3": "Heltec V3",
    "heltec32_v2": "Heltec V2",
    "heltec_wireless_tracker": "Wireless Tracker",
    "tbeam": "LilyGO T-Beam v1.1",
    "tbeam_supreme": "LilyGO T-Beam Supreme",
    "t3s3": "T3S3",
}


#: The unknown-board fallback for a FAILED FLASH. Distinct from _GENERIC, which
#: is written for the abort case ("please wait for the operation to finish") and
#: reads as a non-sequitur once the operation has already failed.
_GENERIC_FLASH = (
    "Check the board's own manual for how to enter its bootloader — the button "
    "combination differs by board, and guessing can make things worse."
)


def recovery_for_board(board) -> str:
    """Recovery instructions for a catalogue board object (or key, or None).

    Prefers the KEY, falls back to the display name, then to a flash-specific
    generic. Never raises — this is called from failure paths, where the last
    thing the operator needs is a second error.
    """
    key = ""
    if board is not None:
        key = getattr(board, "key", None) or (board if isinstance(board, str) else "")
    label = _BY_KEY.get(key)
    if label:
        return recovery_text(label)
    name = getattr(board, "display_name", None) or key or ""
    if name in _HELTEC_LIKE or name in BOARD_RECOVERY:
        return recovery_text(name)
    return _GENERIC_FLASH


def is_high_risk(board: str) -> bool:
    return board in _HIGH_RISK


def warning_message(estimated_seconds: int) -> str:
    return (
        "OPERATION IN PROGRESS — Interrupting now may damage the connected "
        "hardware or leave it in an unrecoverable state. It is strongly "
        "recommended to wait.\n"
        f"Estimated time remaining: {estimated_seconds} seconds."
    )
