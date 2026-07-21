"""Auto-detect the board plugged into the medic, so BIRTH can pre-select it.

The operator shouldn't have to know whether the thing on their desk is a Heltec
V4 or a XIAO S3 — the medic can read the chip over USB (esptool) and narrow it
down. What a chip read gives us reliably is the CHIP FAMILY (ESP32 / ESP32-S3 /
…); that maps to a firmware suggestion and a shortlist of boards (a single chip
family covers several boards, so it isn't always one answer — we pre-select when
it is, and filter the picker when it isn't).

Pure + injectable: ``parse_chip`` and the mappings are unit-tested; the actual
esptool call and the connected-port lookup are injected (defaults wired for the
medic). Onboard boards (Jonesey) are already excluded by local_board_ports.
"""

from __future__ import annotations

from typing import Callable, List, Optional

#: esptool bundled with the rnodeconf firmware cache on the medic (same as the
#: flash workflows use). ``chip_id`` with ``--chip auto`` prints "Chip is <X>".
DEFAULT_ESPTOOL = "python3 ~/.config/rnodeconf/update/1.86/esptool.py"

#: Order matters — the specific S3/C3/S2 needles are tried before plain esp32,
#: since "esp32-s3" also contains "esp32".
_CHIP_PATTERNS = [
    ("esp32s3", ("esp32-s3", "esp32s3")),
    ("esp32c3", ("esp32-c3", "esp32c3")),
    ("esp32s2", ("esp32-s2", "esp32s2")),
    ("esp32c6", ("esp32-c6", "esp32c6")),
    ("esp32", ("esp32",)),
]

_PLATFORM_BY_CHIP = {
    "esp32s3": "ESP32-S3", "esp32": "ESP32", "esp32c3": "ESP32-C3",
    "esp32s2": "ESP32-S2", "esp32c6": "ESP32-C6",
}


def parse_chip(esptool_output: str) -> Optional[str]:
    """The chip family from esptool's stdout ("Chip is ESP32-S3 …"), or None."""
    low = (esptool_output or "").lower()
    for chip, needles in _CHIP_PATTERNS:
        if any(n in low for n in needles):
            return chip
    return None


def firmware_options(chip: Optional[str]) -> List[str]:
    """Firmware the chip can take, best-first. ESP32-S3 boards are the RTNode-2400
    targets (Grey Hat's standalone transport node — health beacon + remote repair),
    so RTNode-2400 leads there; everything can run RNode."""
    if chip == "esp32s3":
        return ["rtnode2400", "rnode"]
    return ["rnode"]


def _platform_key(p: str) -> str:
    return (p or "").replace("-", "").replace(" ", "").lower()


def detect_board(boards, ports_fn: Optional[Callable[[], List[str]]] = None,
                 reader: Optional[Callable[[str], str]] = None) -> dict:
    """Detect the connected work board. Returns a result dict:
    ``{found, port?, chip?, platform?, firmware?, boards?, board_key?, reason?}``.
    ``boards`` is the full board catalogue (to shortlist); ``ports_fn`` returns the
    connected WORK-board ports (onboard excluded); ``reader`` reads a chip on a port.
    Both are injected in tests and default to the medic's real hardware."""
    ports = (ports_fn or _default_ports)()
    if not ports:
        return {"found": False, "reason":
                "No work board on the medic's USB — plug the board in with a "
                "known-good data cable (its own onboard board doesn't count)."}
    port = ports[0]
    try:
        out = (reader or _default_reader)(port)
    except Exception as e:
        return {"found": False, "port": port, "reason": f"Couldn't talk to the "
                f"board on {port}: {e}. Try another data cable, or hold BOOT while "
                "plugging it in."}
    chip = parse_chip(out)
    if not chip:
        return {"found": False, "port": port, "raw": out, "reason":
                "Reached the port but couldn't read the chip — hold BOOT, tap RST, "
                "release BOOT, then Detect again (S3 boards need download mode)."}
    platform = _PLATFORM_BY_CHIP.get(chip)
    shortlist = [b for b in boards
                 if _platform_key(getattr(b, "platform", "")) == _platform_key(platform)]
    return {"found": True, "port": port, "chip": chip, "platform": platform,
            "firmware": firmware_options(chip), "boards": shortlist,
            "board_key": shortlist[0].key if len(shortlist) == 1 else None}


def _default_ports() -> List[str]:
    from ui.hw_factories import local_board_ports
    return list(local_board_ports())


def _default_reader(port: str, esptool: str = DEFAULT_ESPTOOL) -> str:
    import subprocess
    cmd = f"{esptool} --chip auto --port {port} --before default_reset chip_id"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=40)
    return (r.stdout or "") + (r.stderr or "")
