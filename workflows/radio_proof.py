"""Prove the RADIO works before the certificate says the node is born (task #77).

Operator, 2026-08-06: *"During the birth process, we should learn the node's
identity through the cable that is connected, but there should also be a test
during that birth where it tests the lora to see if it can find it over the lora
as well."*

THE GAP. Birth today proves a node is CONFIGURED. It does not prove it is
REACHABLE ON THE MESH, and only the second is what the node is for. A node can
pass every step and still be deaf: antenna not seated, antenna not attached at
all, radio params not what we believe we wrote, a marginal board, a modem that
never came up. Today the operator finds out after they have walked away and put
it on a pole.

TWO CHANNELS, TWO DIFFERENT JOBS:
  * the CABLE establishes WHO the node is — deterministic, immediate, no
    airtime, and it works before a radio is even attached;
  * the RADIO is proved SEPARATELY, by the medic's own RNode actually hearing
    the thing.

THE TRAP: CACHED PATHS LIE. A naive "can I reach it?" succeeds through a stale
cached path, or through a completely different route — another node relaying, or
the USB link still being up — and reports a working radio that is not working.
That already cost a whole evening once (first-range-test-faith: the fix was
`rnpath -d` to drop the path BEFORE `request_path`). So this:

  1. drops any cached path first;
  2. records WHICH interface the reply arrived on;
  3. FAILS the proof if the reply did not come in over a LoRa interface, even
     when the node answered perfectly — answering over the cable proves the
     cable, which we already knew.

FAILURE IS INFORMATIVE, NOT FATAL. "Configured, but I could not hear it on the
radio" is a genuinely useful result and must not fail the whole birth: a node
can be fine and simply sitting in an RF null on a bench next to a metal shelf.
It is recorded on the certificate and shown, with the things worth checking
named. What it must never do is quietly claim success.

Transport is injected, so this is testable without a live mesh — the same shape
as monitor.health_poll, which does the actual talking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

#: Interface-name fragments that mean "this arrived over the air". Matched
#: case-insensitively against the interface a reply came in on. Deliberately a
#: fragment list rather than exact names: operators name their interfaces, and
#: an RNodeInterface may be called anything.
LORA_HINTS = ("rnode", "lora", "rnodeinterface")

#: Interfaces that must NEVER count as proof. The USB/local ones are exactly
#: what this test exists to see past.
NOT_PROOF_HINTS = ("auto", "local", "tcp", "i2p", "usb", "shared", "loopback")


@dataclass
class RadioProof:
    """What was actually established about the radio path."""
    heard: bool = False
    #: The interface the reply arrived on, verbatim, or "".
    interface: str = ""
    rssi: Optional[float] = None
    snr: Optional[float] = None
    #: Unix time of the reply, when there was one.
    at: Optional[float] = None
    #: Plain-language result for the operator and the certificate.
    summary: str = ""
    #: What to check, when it did not work.
    checks: List[str] = field(default_factory=list)
    #: True when the node answered but NOT over the air — the flattering case.
    answered_off_air: bool = False

    def cert_fields(self) -> Dict:
        """What the birth certificate records.

        Evidence, not a promise: the certificate states what was verified, on
        which interface, and when. A certificate that claims a working radio it
        never heard is the same lie as a fake birth ([[no-fake-demos-honesty-gate]]).
        """
        return {
            "radio_verified": bool(self.heard),
            "radio_interface": self.interface,
            "radio_rssi": self.rssi,
            "radio_snr": self.snr,
            "radio_checked_at": self.at,
            "radio_result": self.summary,
        }


def is_over_air(interface: str) -> bool:
    """True only for an interface that proves an over-the-air path.

    Fail CLOSED on an unrecognised name. Calling an unknown interface proof
    would let the USB link, or a TCP interface to somebody's hub, stand in for a
    radio that may not work at all — and the whole point of this test is that
    the operator is about to walk away and trust it.
    """
    name = (interface or "").strip().lower()
    if not name:
        return False
    if any(h in name for h in NOT_PROOF_HINTS):
        return False
    return any(h in name for h in LORA_HINTS)


#: What to check when the node could not be heard. Ordered by how often it is
#: the answer, and the first two are the ones that damage hardware if ignored.
FAILURE_CHECKS = [
    "Is the antenna screwed onto the SMA connector, and the tiny U.FL plug "
    "clicked onto the board? Both ends.",
    "Never leave a radio powered with no antenna — transmitting without one "
    "can damage it permanently.",
    "Do its radio settings match this medic's? A node on a different "
    "frequency, bandwidth or spreading factor is invisible, not broken.",
    "Try moving it away from metal and from this medic — a bench next to a "
    "shelf can be an RF null at half a metre.",
]


def prove_radio(dst_hash: bytes,
                drop_cached_path: Callable[[bytes], None],
                poll: Callable[[bytes], object],
                interface_for: Callable[[bytes], str],
                now: Callable[[], float],
                node_name: str = "") -> RadioProof:
    """Try to hear *dst_hash* over the air, and report honestly either way.

    *drop_cached_path* — forget any known route BEFORE asking. Without this the
    answer may come from a path recorded when the node was on the cable.
    *poll* — ask the node to speak (the 0x01 health pull, #26). Returns
    something truthy with ``.reachable``, or None.
    *interface_for* — which interface the reply arrived on.

    Never raises for a node that simply cannot be heard: that is a RESULT, not
    an error. It does not raise for a broken transport either — an exception
    here would fail a birth that has otherwise succeeded, so it degrades to
    "could not check".
    """
    who = node_name or "the node"
    try:
        drop_cached_path(dst_hash)
    except Exception:
        pass                     # best effort; a stale path is a weaker test,
                                 # not a reason to abandon the check

    try:
        result = poll(dst_hash)
    except Exception as exc:                                  # noqa: BLE001
        return RadioProof(
            heard=False,
            summary=f"Could not run the radio check ({exc}).",
            checks=list(FAILURE_CHECKS))

    reachable = bool(getattr(result, "reachable", False)) if result else False
    if not reachable:
        return RadioProof(
            heard=False, at=now(),
            summary=f"{who} did not answer over the radio.",
            checks=list(FAILURE_CHECKS))

    iface = ""
    try:
        iface = interface_for(dst_hash) or ""
    except Exception:
        iface = ""

    if not is_over_air(iface):
        # THE FLATTERING CASE. It answered — but not through the air, so the
        # radio is exactly as unproven as before we asked.
        where = f" (it came back over {iface})" if iface else ""
        return RadioProof(
            heard=False, interface=iface, at=now(), answered_off_air=True,
            summary=(f"{who} answered, but not over the radio{where}. "
                     "That proves the cable, which we already knew."),
            checks=["Unplug the USB cable and run the check again — while it is "
                    "connected, the reply can travel that way instead."]
                   + FAILURE_CHECKS)

    beacon = getattr(result, "beacon", None)
    return RadioProof(
        heard=True, interface=iface, at=now(),
        rssi=getattr(beacon, "rssi", None),
        snr=getattr(beacon, "snr", None),
        summary=f"Heard {who} over the radio on {iface}.")


# --- reading the interface out of the path table -----------------------------

def path_interface(rnpath_json: str, dst_hex: str) -> str:
    """The interface a destination's path is known through, from
    ``rnpath -t --json``.

    This is the attribution the whole test rests on, and it is available:
    each entry carries an explicit ``interface``, e.g.

        {"hash": "d4b1...", "hops": 0, "interface": "LocalInterface[rns/default]"}

    Read AFTER the probe, so the entry reflects the path just re-established
    rather than a stale one — which is why prove_radio drops the path first.

    Pure, so the parsing is testable without a mesh. Unparseable input yields
    "", which is_over_air treats as not-proof: a reading we could not take is
    not a reading that passed.
    """
    import json
    want = (dst_hex or "").strip().lower()
    if not want:
        return ""
    try:
        rows = json.loads(rnpath_json or "[]")
    except (ValueError, TypeError):
        return ""
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("hash", "")).strip().lower() == want:
            return str(row.get("interface", "") or "")
    return ""


def live_probes(run_shell: Callable[[str], str], wait_s: int = 20) -> Dict:
    """The four callables prove_radio needs, wired to the real tools.

    *run_shell* must run through a LOGIN shell: rnpath and rnstatus live in
    ~/.local/bin and are NOT on a non-login PATH. ui/app.py's ``_local_run``
    already exists for exactly this, and this function was written after being
    caught by it once.

    The sequence mirrors ``_ping_node`` (#26), which learned it the hard way:
    dropping the path and reading the table immediately reports "not answering"
    for every node, healthy or not, because no fresh path has resolved yet.
    ``rnpath -w`` does the request AND the wait.
    """
    import time
    from monitor.mesh import parse_path_probe

    def drop(dst: str) -> None:
        run_shell(f"rnpath --drop {dst} 2>/dev/null")

    def poll(dst: str):
        out = run_shell(f"rnpath -w {wait_s} {dst} 2>/dev/null")
        reachable, hops = parse_path_probe(out)
        return _Reached(bool(reachable), hops)

    def interface_for(dst: str) -> str:
        return path_interface(run_shell("rnpath -t --json 2>/dev/null"), dst)

    return {"drop_cached_path": drop, "poll": poll,
            "interface_for": interface_for, "now": time.time}


@dataclass
class _Reached:
    reachable: bool
    hops: Optional[int] = None
    beacon: object = None
