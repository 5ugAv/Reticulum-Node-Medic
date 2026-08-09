"""What the operator has already told the medic about a specific chip.

Six ESP32-S3 boards in the catalogue share a chip family and a native-USB
connection. esptool can separate a family; it cannot separate a Heltec V4 from
a T-Deck, because nothing in the silicon says which PCB it was soldered to. So
the medic asks — and, until now, asked again every single time, about the same
physical board, on the same bench.

The operator, live 2026-08-09, looking at that six-way grid for the third time
in a night with one board plugged in: "I'm just hoping that we can whittle down
this section of boards in this stage."

An MCU's MAC is burned into its eFuses and is unique to that chip, so the
answer given once is the answer forever: this exact silicon is a Heltec LoRa32
v4. Remember it, and the second encounter needs no question at all — it goes
straight to the one confirmation, which can still be overruled.

PRIVACY. This file never leaves the medic. A chip MAC identifies a board, and
a board tends to identify a place and a person, so it belongs to the bench and
nothing else: it is not put in a certificate, not announced, not carried into a
clone. See [[anonymity-ethos]] — the wild nodes stay untraceable, and the
workshop notes stay in the workshop.
"""

from __future__ import annotations

import json
import os
from typing import Optional

MEMORY_PATH = os.path.expanduser("~/.reticulum-node-medic/board_memory.json")


def _normalise(mac: str) -> str:
    """Lowercase, colon-free — esptool prints one form, a device path another."""
    return "".join(c for c in (mac or "").lower() if c.isalnum())


def _load(path: Optional[str] = None) -> dict:
    # Resolved at CALL time, never bound as a default: a default freezes the
    # module-level value at import, so a test (or a future relocation) that
    # points MEMORY_PATH somewhere else is silently ignored.
    path = path or MEMORY_PATH
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:                                # noqa: BLE001
        return {}                                    # never block a birth


def recall(mac: str, path: Optional[str] = None) -> Optional[str]:
    """The board key the operator gave for this chip, or None."""
    path = path or MEMORY_PATH
    key = _normalise(mac)
    if not key:
        return None
    val = _load(path).get(key)
    return val if isinstance(val, str) and val else None


def remember(mac: str, board_key: str, path: Optional[str] = None) -> bool:
    """Record that this chip is *board_key*. Returns whether it was written.

    Failure is never raised: not remembering costs one extra question, while an
    exception here would take down a birth that was otherwise going fine.
    """
    path = path or MEMORY_PATH
    key = _normalise(mac)
    if not key or not board_key:
        return False
    data = _load(path)
    if data.get(key) == board_key:
        return True
    data[key] = str(board_key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)                         # bench notes, not world-readable
        os.replace(tmp, path)
        return True
    except Exception:                                # noqa: BLE001
        return False


def forget(mac: str, path: Optional[str] = None) -> bool:
    """Drop what we remember about this chip (the operator corrected us)."""
    path = path or MEMORY_PATH
    key = _normalise(mac)
    data = _load(path)
    if key not in data:
        return False
    data.pop(key, None)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except Exception:                                # noqa: BLE001
        return False
