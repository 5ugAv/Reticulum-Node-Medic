"""Work out WHICH Raspberry Pi is plugged in, so nobody has to be asked.

The operator, mid-walkthrough (2026-08-02): *"Can Node Medic automatically
select which Pi I'm using, or do I need to select this manually?"* It can, and
it matters more than convenience — the host Pi is what the power check reasons
about, so a wrong pick produces a wrong verdict about whether the pairing needs
a powered hub.

Two sources, in order of how much they actually prove:

1. **The Pi's own model string.** Once it has booted and is reachable over the
   cable, ``/proc/device-tree/model`` says exactly what it is
   ("Raspberry Pi Zero 2 W Rev 1.0"). This is proof.

2. **The boot-ROM USB id.** Before it has an operating system, all the medic can
   see is the SoC family — ``0a5c:2764`` is BCM2836/2837, which covers the Pi 2,
   Pi 3, Zero 2 W and CM3 alike. This is a HINT, and the difference matters: a
   Zero 2 W has a 500 mA budget and a 3 A+ has 1000 mA, so guessing between them
   would silently change whether a Heltec V3 is safe to hang off it.

Measured on the medic, a Pi in boot-ROM mode advertises ``iSerial 0`` and
``bcdDevice 0.00`` — there is literally no bit present that separates the three.
So rather than stop the operator with a question the tool will be able to answer
itself in a few minutes, it fills in the SAFEST candidate (lowest power budget)
and labels it as assumed. The failure modes aren't symmetric: assuming a Zero
2 W costs a warning that wasn't needed, assuming a 3 B+ costs a genuinely
under-powered node shipping with no warning at all.

Every answer carries how it was reached, so the screen can show the difference
between "read from the Pi" and "assumed from the chip".
Parsing is pure; only ``detect`` touches hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

#: Model-string fragment -> the power-model key in workflows.power_compat.
#: Ordered: the first match wins, so longer/more specific patterns come first.
MODEL_MAP = [
    ("zero 2", "pi_zero_2w"),
    ("zero2", "pi_zero_2w"),
    ("3 model a", "pi_3a_plus"),
    ("3 model b", "pi_3b_plus"),
    ("4 model b", "pi_4b"),
    ("400", "pi_4b"),
    ("5 model b", "pi_5"),
    ("500", "pi_5"),
]

#: Boot-ROM USB id -> the Pis that id could possibly be. A hint, never an answer.
SOC_CANDIDATES = {
    "0a5c:2763": ["pi_zero_2w"],                      # BCM2835: Pi 1 / Zero
    "0a5c:2764": ["pi_zero_2w", "pi_3a_plus", "pi_3b_plus"],   # BCM2836/2837
    "0a5c:2711": ["pi_4b"],                           # BCM2711
    "0a5c:2712": ["pi_5"],                            # BCM2712
}

EXACT = "exact"        # read from the Pi itself
NARROWED = "narrowed"  # SoC family known, model is not
UNKNOWN = "unknown"

#: When the chip can't say WHICH board it is, assume the one with the smallest
#: power budget among the candidates. The only thing this field feeds is the
#: powered-hub warning, and the failure modes are not symmetric: assume a Zero
#: 2 W and the worst case is a warning the operator didn't need; assume a 3 B+
#: and a genuinely under-powered node ships with no warning at all.
#: Confirmed against the Pi itself the moment it boots.
ASSUME_SMALLEST = True


@dataclass
class HostPi:
    key: str = ""
    model: str = ""
    confidence: str = UNKNOWN
    candidates: Optional[List[str]] = None
    how: str = ""

    @property
    def should_auto_select(self) -> bool:
        """May the screen fill this in without asking?

        Yes even on a narrowed guess — but only because the guess is
        deliberately the LOWEST-power candidate, so being wrong produces a
        warning that wasn't needed rather than silence where one was. An exact
        read replaces it the moment the Pi boots.
        """
        return bool(self.key) and self.confidence in (EXACT, NARROWED)

    @property
    def is_assumed(self) -> bool:
        """True when this was inferred from the chip, not read from the board."""
        return self.confidence == NARROWED


def key_from_model(model: str) -> str:
    """Map a ``/proc/device-tree/model`` string to a power-model key, or ""."""
    text = (model or "").strip().lower().replace("\x00", "")
    if "raspberry pi" not in text:
        return ""
    for fragment, key in MODEL_MAP:
        if fragment in text:
            return key
    return ""


def from_model_string(model: str) -> HostPi:
    """An exact identification from the Pi's own model string."""
    key = key_from_model(model)
    clean = (model or "").replace("\x00", "").strip()
    if not key:
        return HostPi(model=clean, confidence=UNKNOWN,
                      how="the Pi reported a model we don't have a profile for")
    return HostPi(key=key, model=clean, confidence=EXACT,
                  how="read from the Pi itself")


def _smallest_budget(keys: List[str]) -> str:
    """The candidate with the least USB power to give."""
    try:
        from workflows.power_compat import PI_POWER
        known = [k for k in keys if k in PI_POWER]
        if known:
            return min(known, key=lambda k: PI_POWER[k]["budget_ma"])
    except Exception:                                  # noqa: BLE001
        pass
    return keys[0] if keys else ""


def from_usb_id(usb_id: str) -> HostPi:
    """A narrowing from the boot-ROM USB id.

    A Pi in boot-ROM mode advertises its chip and NOTHING else — measured on the
    medic, ``iSerial 0``, ``bcdDevice 0.00``, product string "BCM2710 Boot".
    There is no bit present that separates a Zero 2 W from a 3 A+, so this can
    never be an exact answer; it fills in the safest candidate and says so.
    """
    cands = SOC_CANDIDATES.get((usb_id or "").lower())
    if not cands:
        return HostPi(confidence=UNKNOWN, how="no Pi seen on USB")
    if len(cands) == 1:
        return HostPi(key=cands[0], confidence=NARROWED, candidates=cands,
                      how="identified from the chip it uses")
    return HostPi(key=_smallest_budget(cands) if ASSUME_SMALLEST else "",
                  confidence=NARROWED, candidates=cands,
                  how="assumed from the chip — these boards are identical until "
                      "one starts up. Node Medic will confirm it then")


def detect(run_on_pi: Optional[Callable[[str], tuple]] = None,
           lsusb_fn: Optional[Callable[[], str]] = None) -> HostPi:
    """Identify the attached Pi. Tries the exact route first, then the hint.

    *run_on_pi* runs a command on the Pi over the cable and returns
    ``(code, out, err)``; omit it to have one built from the cable link.
    """
    if run_on_pi is None:
        def run_on_pi(cmd):
            from provisioning.pi_discover import cable_address
            addr = cable_address(timeout=3.0)
            if not addr:
                return (1, "", "no cable link")
            from transport.connection import SSHConnection
            return SSHConnection(addr, user="pi").run(cmd)

    try:
        code, out, _ = run_on_pi("tr -d '\\000' < /proc/device-tree/model")
        if code == 0 and (out or "").strip():
            got = from_model_string(out)
            if got.confidence == EXACT:
                return got
    except Exception:                                  # noqa: BLE001
        pass

    try:
        import subprocess
        from provisioning import pi_usbboot
        text = (lsusb_fn or (lambda: subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=8).stdout))()
        return from_usb_id(pi_usbboot.classify(text).usb_id)
    except Exception:                                  # noqa: BLE001
        return HostPi(confidence=UNKNOWN, how="couldn't look")


def describe(host: HostPi, name_for: Callable[[str], str]) -> str:
    """One line for the screen. *name_for* turns a key into its display name."""
    if host.confidence == EXACT:
        return f"{name_for(host.key)} — {host.how}"
    if host.confidence == NARROWED and host.key:
        return f"{name_for(host.key)} — {host.how}"
    if host.confidence == NARROWED:
        names = ", ".join(name_for(k) for k in (host.candidates or []))
        return f"Could be {names} — {host.how}. Pick one."
    return "Tap to choose which Raspberry Pi this is."
