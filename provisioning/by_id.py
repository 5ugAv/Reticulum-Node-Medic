"""The one true reader of a ``/dev/serial/by-id/`` serial.

This lived in THREE places once — ``workflows.rnode_flash``,
``workflows.rtnode_build`` and ``provisioning.cable_birth`` — each with its own
regex, and they did NOT agree. The rnode_flash copy anchored its pattern with
``$``::

    re.compile(r"_([^_]+)-if\\d+$")     # <- the bug

A native-CDC board (RAK4631, ESP32-S3) enumerates as ``…_SERIAL-if00`` and that
pattern matches it. But a USB-UART BRIDGE board — the Heltec V3's CP2102, a
T-Beam's, an FTDI adapter — enumerates with a trailing ``-port0``::

    usb-Silicon_Labs_CP2102N_…_0001-if00-port0

The ``$`` sits right after ``-if00`` and the ``-port0`` shoves the anchor off
the end, so the match FAILS and the serial reads back as "nothing". That is the
exact board family whose re-enumeration the reader was written to survive, so
the failure was silent AND aimed at the boards that needed it most:

  * rnode_flash set ``_usb_serial=None`` -> ``_reacquire_port`` early-returned
    -> after a flash moved a V3/T-Beam to a new tty, nothing followed it.
  * cert_store, given no serial, keyed a board's identity on its FULL by-id
    name instead — and that name is NOT stable across a reflash (the RAK's
    vendor string alone changes case, ``RAKWireless`` -> ``RAKwireless``). So a
    reborn bridge board failed ``same_board()`` and LOST its name and kin
    status: a repair looked like a stranger.

So: one reader, and it parses by SPLITTING, not by anchoring — immune to any
``-ifNN`` / ``-portN`` tail a bridge or hub may add. It accepts either a full
``/dev/serial/by-id/…`` path or a bare basename, because callers hand it both.
"""

from __future__ import annotations


#: Serial numbers that are NOT unique. Seen live on a Heltec V3 (2026-08-01):
#: its CP2102 reports "0001", the factory default — every V3 off that line has
#: it. A by-id path is only as unique as the serial baked into it, so treating
#: one of these as an identity would be a false promise.
PLACEHOLDER_SERIALS = {"0001", "0000", "0", "1", "00000000", "12345678"}


def by_id_serial(by_id_path: str) -> str:
    """The hardware serial embedded in a ``/dev/serial/by-id/`` name, or "".

    Format: ``usb-<vendor>_<product>_<SERIAL>-if<NN>[-port<N>]``. The tail after
    the serial is the ONE thing that survives a re-enumeration; the vendor and
    product strings are not (the RAK4631 says ``RAKWireless`` from its
    bootloader and ``RAKwireless`` once running RNode firmware — a capital W
    goes lowercase — so matching the whole name would fail across a flash).

    Splits on ``-if`` rather than anchoring on it, so a bridge board's trailing
    ``-port0`` (CP2102/FTDI) does not defeat the read — the bug this module was
    born to kill. Returns "" (never a guess) for anything without a serial: a
    raw ``/dev/ttyUSB0``, an empty string, or ``None``.
    """
    name = (by_id_path or "").rsplit("/", 1)[-1]     # basename OR already one
    stem = name.split("-if")[0]                        # drop -ifNN[-portN]
    return stem.rsplit("_", 1)[-1] if "_" in stem else ""


def is_uniquely_identified(by_id_path: str) -> bool:
    """Does this by-id path actually pin ONE physical board?"""
    serial = by_id_serial(by_id_path)
    return bool(serial) and serial not in PLACEHOLDER_SERIALS
