"""Prove the node REPORTS BACK before the certificate says it is born.

Sister to :mod:`workflows.radio_proof`, and the same argument one step further
on. That module asks whether the medic can HEAR the node. This one asks whether
the node will TELL the medic anything — which is what VITALS is made of, and
what the operator actually looks at once they have walked away.

THE GAP. A birth today can pass every step, print a certificate, and leave a
node that shows in VITALS as a grey LoRa-only row for days. SkyFinger did
exactly that: rnsd, lxmd and the health reporter all running, the node in
perfect health, and the medic holding not one reading from it. Nothing in the
build had ever checked, because nothing in the build had ever asked.

TWO CHANNELS, AND THEY FAIL SEPARATELY:

  * ``/status`` over HTTP — instant, no airtime, and the ONLY thing that can
    light the WIFI and NET chips before a beacon arrives. Testable the moment
    the node is built, over whatever address the medic can reach it on.
  * a health BEACON over LoRa — the 0x01 command and the 20-byte reply. This is
    the channel that still works when the LAN is gone, i.e. the one the node
    exists for.

REPORT THEM APART, ALWAYS. "Answered on HTTP, never heard on the radio" is a
real and common state (a bench node with no antenna), and so is its opposite (a
field node with no wifi). Collapsing them into one verdict hides whichever half
went wrong.

AND SAY "NOT TESTED YET" WHEN THAT IS THE TRUTH. On a Pi 3 A+ the radio is
attached AFTER the build — the board has one USB-A socket and the medic's cable
is in it — so at certificate time there is nothing on the air to hear. Reporting
that as a failure is the same offence as reporting it as a pass, and it reads
worse: the operator is told their good node is deaf. ``radio_proof`` already
learned this on Y2K8 and SolarLove (2026-08-10) and the wording here follows it.

Everything is injected, so the decision logic is testable with no node, no LAN
and no mesh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from workflows.radio_proof import NOT_APPLICABLE


@dataclass
class BeaconOutcome:
    """What came back when the medic commanded a health beacon."""
    heard: bool = False
    #: The interface the beacon arrived on, verbatim, or "".
    interface: str = ""
    #: Anything worth saying about how it went (an error, a timeout).
    detail: str = ""


@dataclass
class ReportProof:
    """What the medic can actually SAY it has received from this node."""
    #: HTTP
    http_answered: bool = False
    http_host: str = ""                 # the address that answered, verbatim
    http_tried: List[str] = field(default_factory=list)
    #: LoRa health beacon
    beacon_heard: bool = False
    beacon_interface: str = ""
    beacon_not_applicable: bool = False
    beacon_reason: str = ""             # why there was no test to take
    #: Plain-language result for the operator and the certificate.
    summary: str = ""
    #: What to check, for whichever channel did not answer.
    checks: List[str] = field(default_factory=list)

    @property
    def anything_heard(self) -> bool:
        """Did the node report over ANY channel? The one question a certificate
        must never answer optimistically."""
        return self.http_answered or self.beacon_heard

    def cert_fields(self) -> dict:
        """What the birth certificate records.

        Booleans stay booleans and "no test was possible" is a STRING, for the
        reason ``radio_proof.radio_was_verified`` exists: somebody will write
        ``if cert.get("reports_over_lora"):`` and a non-empty string is truthy,
        so a node that could never have been tested would read as verified. Use
        the readers below, which are strict about ``is True``.
        """
        return {
            "reports_over_http": self.http_answered,
            "reports_http_host": self.http_host,
            "reports_over_lora": (NOT_APPLICABLE if self.beacon_not_applicable
                                  else self.beacon_heard),
            "reports_lora_interface": (NOT_APPLICABLE if self.beacon_not_applicable
                                       else self.beacon_interface),
            "reports_result": self.summary,
        }


def reported_over_http(cert: dict) -> bool:
    """THE ONLY safe way to ask "has the medic read this node's /status?"."""
    return (cert or {}).get("reports_over_http") is True


def reported_over_lora(cert: dict) -> bool:
    """THE ONLY safe way to ask "has the medic decoded this node's beacon?"."""
    return (cert or {}).get("reports_over_lora") is True


#: What to check when ``/status`` did not answer. The first is far and away the
#: most common: the endpoint is a service like any other and can simply be down.
HTTP_CHECKS = [
    "On the node: `systemctl status rnm-status` — the /status service may have "
    "failed to start, and its journal will say why.",
    "Is the node on the same network as Node Medic? A node on a different "
    "subnet is not unreachable, it is just somewhere else.",
    "Port 80 needs CAP_NET_BIND_SERVICE, which the unit grants. If the journal "
    "says 'Permission denied', that capability did not survive onto this image.",
]

#: What to check when the beacon was not heard. Reuses the radio checks — a
#: beacon that never arrives is usually a radio that never transmitted.
BEACON_CHECKS = [
    "Was the node heard on the radio at all? If not, that is the thing to fix "
    "first — the beacon rides on it.",
    "On the node: `systemctl status rnm-health` — the reporter is what answers "
    "the 0x01 command, and it starts after rnsd.",
]


def prove_reporting(hosts: Sequence[str],
                    poll_http: Callable[[str], object],
                    hear_beacon: Optional[Callable[[], BeaconOutcome]] = None,
                    beacon_reason: str = "",
                    node_name: str = "") -> ReportProof:
    """Ask the node to report, over each channel, and say honestly what came back.

    *hosts* — addresses to try ``/status`` on, best first. Each is tried until
    one answers; the address that answered is recorded, because reaching a node
    down the build cable is a different fact from reaching it over its wifi and
    only the second one survives the operator walking away.

    *poll_http* — ``poll_status``-shaped: returns something with ``.reachable``.

    *hear_beacon* — commands a beacon and waits. ``None`` means THERE WAS NO
    TEST TO TAKE (give *beacon_reason*), which is recorded as not-applicable
    rather than as a failure.

    Never raises for a node that simply did not answer: that is a RESULT. It
    does not raise for a broken probe either — a birth that has otherwise
    succeeded must not be thrown away because a check could not run.
    """
    who = node_name or "the node"
    proof = ReportProof(http_tried=[h for h in hosts if h])

    for host in proof.http_tried:
        try:
            status = poll_http(host)
        except Exception:                                      # noqa: BLE001
            continue        # this address did not work; the next one might
        if status is not None and getattr(status, "reachable", False):
            proof.http_answered = True
            proof.http_host = host
            break

    if hear_beacon is None:
        proof.beacon_not_applicable = True
        proof.beacon_reason = beacon_reason or NOT_APPLICABLE
    else:
        try:
            outcome = hear_beacon()
        except Exception as exc:                               # noqa: BLE001
            outcome = BeaconOutcome(detail=str(exc))
        proof.beacon_heard = bool(outcome and outcome.heard)
        proof.beacon_interface = (outcome.interface if outcome else "") or ""
        proof.beacon_reason = (outcome.detail if outcome else "") or ""

    proof.summary = _summarise(proof, who)
    if not proof.http_answered:
        proof.checks.extend(HTTP_CHECKS)
    if not (proof.beacon_heard or proof.beacon_not_applicable):
        proof.checks.extend(BEACON_CHECKS)
    return proof


def _summarise(proof: ReportProof, who: str) -> str:
    """One sentence per channel, in the order the operator cares about.

    Deliberately never blends the two into a single verdict. "Mostly working" is
    the kind of phrase that lets a mute radio out of the door.
    """
    if proof.http_answered:
        http = f"Node Medic read {who}'s status over the network at {proof.http_host}."
    elif proof.http_tried:
        tried = ", ".join(proof.http_tried)
        http = (f"{who} did not answer /status on any address Node Medic could "
                f"try ({tried}).")
    else:
        http = (f"Node Medic had no address to ask {who} for its status, so it "
                f"could not check.")

    if proof.beacon_not_applicable:
        lora = ("Not tested over the radio yet — " + proof.beacon_reason
                if proof.beacon_reason else
                "Not tested over the radio yet.")
    elif proof.beacon_heard:
        where = f" on {proof.beacon_interface}" if proof.beacon_interface else ""
        lora = f"Node Medic heard and decoded {who}'s health beacon{where}."
    else:
        detail = f" ({proof.beacon_reason})" if proof.beacon_reason else ""
        lora = f"{who} sent no health beacon Node Medic could decode{detail}."
    return http + " " + lora


# --- wiring the beacon check to the real mesh --------------------------------


def live_beacon_probe(dst_hex: str, run_shell: Callable[[str], str],
                      wait_s: int = 25) -> Callable[[], BeaconOutcome]:
    """A ``hear_beacon`` that uses the medic's own RNS to command and decode.

    The sequence is the one ``ui/app.py::_ping_node`` arrived at, and each step
    is there because skipping it produced a wrong answer:

      1. drop the cached path — a cached path lies, and answered once from a
         route learned while the node was still on the build cable;
      2. ``rnpath -w`` requests a path AND waits for it (reading the table
         straight after a drop reports every node unreachable, healthy or not);
      3. register an announce handler BEFORE sending, or the reply — which
         arrives in seconds — races past the listener;
      4. send the 0x01 command to the node's ``rtnode.health`` destination;
      5. accept it only when the announce came from OUR destination and the
         payload actually DECODES as a health beacon. An announce alone proves
         the identity is alive, which is precisely the thing SkyFinger had
         plenty of while VITALS stayed grey.

    *run_shell* must run through a LOGIN shell — rnpath lives in ~/.local/bin.
    """
    def probe() -> BeaconOutcome:
        import threading
        import RNS
        from monitor.health_beacon import decode
        from monitor.mesh import parse_path_probe
        from workflows.radio_proof import path_interface

        want = bytes.fromhex(dst_hex)
        run_shell(f"rnpath --drop {dst_hex} 2>/dev/null")
        reachable, _hops = parse_path_probe(
            run_shell(f"rnpath -w {wait_s} {dst_hex} 2>/dev/null"))
        if not reachable:
            return BeaconOutcome(
                detail="no path to its health destination, so the command could "
                       "not be delivered")

        got = threading.Event()
        heard: dict = {}

        class _Listener:
            aspect_filter = "rtnode.health"

            def received_announce(_self, destination_hash, announced_identity,
                                  app_data):
                if destination_hash != want:
                    return
                try:
                    heard["beacon"] = decode(app_data)
                except (ValueError, TypeError):
                    return          # an announce with no readings is not a report
                got.set()

        RNS.Transport.register_announce_handler(_Listener())
        identity = RNS.Identity.recall(want)
        if identity is None:
            return BeaconOutcome(detail="its identity is not known to Node Medic")
        dest = RNS.Destination(identity, RNS.Destination.OUT,
                               RNS.Destination.SINGLE, "rtnode", "health")
        RNS.Packet(dest, bytes([0x01])).send()
        if not got.wait(wait_s):
            return BeaconOutcome(
                detail=f"asked for a beacon and heard nothing back in {wait_s}s")
        iface = path_interface(run_shell("rnpath -t --json 2>/dev/null"), dst_hex)
        return BeaconOutcome(heard=True, interface=iface)

    return probe
