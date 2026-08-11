"""Whether a node tells the world roughly where it is — and how it actually does it.

THE DEFAULT IS SILENCE. A node that has never been asked publishes nothing.
This module exists so that saying "yes" is a deliberate, informed act with a
known wire format behind it, and so that saying it produces a pin that is
roughly right and precisely useless for finding the hardware.

---------------------------------------------------------------------------
THE REAL PUBLISHING CONVENTION (verified, with sources)
---------------------------------------------------------------------------

There is exactly one mechanism by which a Reticulum node's position reaches a
public map, and it is NOT LXMF telemetry and NOT ordinary announce app_data.

**RNS interface discovery.** A node opts an INTERFACE in; RNS then announces on
the destination with aspects ``rnstransport.discovery.interface`` and app_data
``bytes([flags]) || umsgpack(info) || stamp``, where *info* is an integer-keyed
map and the stamp is an LXMF proof-of-work.

Read directly out of the RNS the medic itself runs — RNS 1.3.7,
``RNS/Discovery.py`` (2026-08-11):

    LATITUDE = 0x03 · LONGITUDE = 0x04 · HEIGHT = 0x05
    TRANSPORT_ID = 0xFE · NAME = 0xFF · APP_NAME = "rnstransport"     (:13-31)
    discovery_destination = Destination(..., "rnstransport",
                                        "discovery", "interface")     (:57)
    info = {INTERFACE_TYPE, TRANSPORT, TRANSPORT_ID, NAME,
            LATITUDE, LONGITUDE, HEIGHT}                              (:103-109)
    DISCOVERABLE_INTERFACE_TYPES includes "RNodeInterface"            (:36)
    DEFAULT_STAMP_VALUE = 14                                          (:34)

and the config keys that fill those fields, ``RNS/Reticulum.py`` (:824-856):

    discoverable · discovery_name · announce_interval (MINUTES, x60,
    floor 5 min, default 6 h) · latitude · longitude · height

So on a Pi node (the EVERYWHERE class) this is a handful of lines inside the
interface's own stanza of ``~/.reticulum/config``. That is the whole mechanism.
``build_discovery_block`` writes them; ``apply_to_reticulum_config`` puts them
in the right stanza.

On an RTNode-2400 (ESP32) the SAME announce is emitted by the firmware, fed by
four captive-portal form fields — ``advert_en``, ``advert_lat``, ``advert_lon``,
``advert_jitter`` (docs/RTNODE2400_INTEGRATION.md section E, and
``workflows.rtnode_portal.build_form`` which already posts them). Two node
classes, one wire format.

**Who reads it.** rmap.world — "the Reticulum rmap" — runs two maps side by
side: v3 is hand-maintained from a web form, v4 is automatic and listens for
exactly this aspect. Its own instructions (rmap.world/info.html, read
2026-08-11) say the marker needs latitude/longitude present or the node is
listed in the side panel with no pin at all, and that a node is dropped after
7 days without a fresh announce. NOT VERIFIED BY THIS TOOL: we have never
observed our own announce arrive there, and RMAP v4 is described by its own
authors as beta. Nothing in this module may tell an operator their node IS on
a map. It can only report what was configured, and when the node last announced.

**Reaching the map at all.** A LoRa-only node's announce does not leave the
mesh. rmap.world says it runs a public transport node on port 4242 for exactly
this reason. That is a separate, deliberate decision — it hands the node's
public IP to a third party — so it lives behind :func:`rmap_uplink_block` and
is never implied by choosing to share a position.

---------------------------------------------------------------------------
THE TRAP THAT MAKES THIS MORE THAN A CONFIG WRITE
---------------------------------------------------------------------------

Turning on ``discoverable`` for an interface that is not already in gateway or
access-point mode makes RNS **silently change the interface's mode** — and for
an RNodeInterface it picks ACCESS POINT (Reticulum.py:856-864, "Discovery
enabled on interface ... Auto-configured to AP mode").

An access-point interface **stops rebroadcasting other destinations'
announces**: Transport.py:1224, ``elif interface.mode == MODE_ACCESS_POINT:``
-> "Blocking announce broadcast on ... due to AP mode", ``should_transmit =
False``. It also shortens the lifetime of paths learned there to AP_PATH_TIME
(Transport.py:786, :1927).

EVERYWHERE is the relay this whole mesh routes through. Ticking a box to put it
on a map, and thereby stopping it from forwarding announces over LoRa, would be
a straight downgrade of the network in exchange for a dot on a website — and it
would have happened quietly, with the config file showing nothing but the line
the operator asked for. So the managed block declares ``mode = gateway``
explicitly, which satisfies the same precondition (Reticulum.py:856) without
the announce-blocking branch. Gateway mode's only other effect is that this
node will search for unknown paths on behalf of requests arriving on that
interface (Transport.py:2947) — appropriate for a transport node, and the thing
EVERYWHERE already is.

---------------------------------------------------------------------------
WHAT IS PUBLISHED IS NEVER WHERE THE NODE IS
---------------------------------------------------------------------------

Every coordinate that leaves this device goes through ``monitor.geo.fuzz_location``
first (800 m, deterministic per node — a re-rolling offset could be averaged
back to the truth by an observer patient enough to collect announces). The exact
position stays on the medic, on the birth certificate, for the person who has to
go and repair it. See :func:`public_pin`.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Sequence, Tuple

from monitor.geo import FUZZ_RADIUS_M, format_coord, fuzz_location

#: Sharing policy, stored per node (birth certificate, registry, kin roster).
#: A string rather than a bool because "hidden" and "approximate" are not the
#: only two answers this could ever have — an exact-position policy would be a
#: third, and a bool named ``share_location`` that quietly grew a third meaning
#: is how privacy settings turn into privacy bugs.
HIDDEN = "hidden"
APPROX = "approx"
POLICIES = (HIDDEN, APPROX)

#: What a node gets if nobody ever answers the question.
DEFAULT_POLICY = HIDDEN

#: Announce cadence in MINUTES (RNS multiplies by 60; its own default is 6 h).
#: Matches what the RTNode firmware does, and the airtime budget this project
#: keeps to — see docs/RTNODE2400_INTEGRATION.md section E.
ANNOUNCE_INTERVAL_MIN = 360

#: The marked, self-contained region this module owns inside a node's config.
#: Everything it writes goes between these two lines, and removal deletes the
#: region whole. Nothing outside it is ever edited: an operator's hand-written
#: keys are theirs, and a tool that quietly rewrites a config it did not write
#: is a tool nobody can trust with a node they cannot reach.
BLOCK_START = "# --- map location sharing (managed by Node Medic) ---"
BLOCK_END = "# --- end map location sharing ---"

#: Keys whose presence OUTSIDE the managed block collides with what we write.
#: Reported, never silently overwritten.
_CONFLICT_KEYS = ("discoverable", "latitude", "longitude", "mode",
                  "interface_mode", "discovery_name", "announce_interval")

#: rmap.world's own published transport node, from rmap.world/info.html
#: ("RMAP runs a public Reticulum transport node at rmap.world port 4242.
#: Connecting your transport node here ensures your discovery announces reach
#: RMAP at zero hops"), read 2026-08-11.
#:
#: NOT VERIFIED BY THIS TOOL. No announce of ours has ever been observed
#: arriving there. It is offered as an explicit, separate opt-in and never
#: written as a side effect of choosing to share a position.
RMAP_HOST = "rmap.world"
RMAP_PORT = 4242


def is_shared(policy: Optional[str]) -> bool:
    """True only for a policy that actually publishes something."""
    return policy == APPROX


def normalise(policy) -> str:
    """Any stored/legacy value -> a policy string. Anything unrecognised is
    HIDDEN: an unreadable setting must never be read as consent to publish."""
    if policy is True:
        return APPROX
    if isinstance(policy, str) and policy in POLICIES:
        return policy
    return HIDDEN


# --- what actually goes out --------------------------------------------------

def public_pin(lat: Optional[float], lon: Optional[float], node_key: str,
               radius_m: float = FUZZ_RADIUS_M
               ) -> Optional[Tuple[float, float, float]]:
    """The ONLY position this node may transmit: ``(lat, lon, radius_m)``.

    Every publishing path in this module goes through here, so there is exactly
    one place where a real coordinate could turn into a transmitted one — and it
    cannot, because it always fuzzes. Returns ``None`` when there is nothing to
    publish (no coordinates on file), which callers must treat as "no position",
    never as "0, 0".
    """
    if lat is None or lon is None:
        return None
    return fuzz_location(float(lat), float(lon), node_key or "node", radius_m)


def stranger_view(policy: str, name: str, lat: Optional[float],
                  lon: Optional[float], node_key: str,
                  transport: bool = True) -> dict:
    """Exactly what somebody who is not you can learn, in plain words.

    The screen that asks the question shows this. It is not a summary of the
    setting; it is the contents of the packet, in English, because "share
    location" understates it — the same announce carries the node's NAME, its
    transport identity and its radio settings, and an operator agreeing to a pin
    on a map has not thereby agreed to those unless they were told.
    """
    if not is_shared(policy):
        return {"shared": False,
                "headline": "Hidden — this node publishes no position",
                "items": [],
                "pin": None}
    pin = public_pin(lat, lon, node_key)
    items = [
        f"Its name: {name}" if name else "Its name",
        "Roughly where it is — a point up to {:.0f} m from the truth, never "
        "the real one".format(FUZZ_RADIUS_M),
        "Its radio settings (frequency, bandwidth, spreading factor, coding rate)",
        "Its Reticulum transport identity",
    ]
    if transport:
        items.append("That it relays traffic for other nodes")
    return {
        "shared": True,
        "headline": "Shared — roughly where, not exactly where",
        "items": items,
        "pin": pin,
    }


def cannot_be_recalled() -> str:
    """The sentence the share screen must carry. Kept here so it is written
    once and cannot drift between the birth screen and the node-detail one."""
    return ("An announce cannot be taken back. Turning sharing off later stops "
            "future announces; it does not unsay the ones already heard.")


# --- the Pi / rnsd side: a node's own ~/.reticulum/config ---------------------

def build_discovery_block(name: str, lat: float, lon: float, node_key: str,
                          indent: str = "    ",
                          interval_min: int = ANNOUNCE_INTERVAL_MIN,
                          height_m: Optional[float] = None) -> List[str]:
    """The managed config lines that make one interface discoverable, with a
    FUZZED position. Never called with a policy of hidden — see
    :func:`apply_to_reticulum_config`.

    ``mode = gateway`` is not decoration; without it RNS reassigns this
    interface to access-point mode and stops it forwarding announces. The module
    docstring has the line numbers.
    """
    pin = public_pin(lat, lon, node_key)
    if pin is None:
        raise ValueError("no coordinates to publish")
    flat, flon, radius = pin
    lines = [
        indent + BLOCK_START,
        indent + "# Position is fuzzed by Node Medic before it is written here:",
        indent + f"# a stable point up to {radius:.0f} m from the node's real",
        indent + "# location. The exact coordinates are on its birth certificate.",
        indent + "# mode = gateway is required: 'discoverable' alone makes RNS",
        indent + "# reassign this interface to access-point mode, which stops it",
        indent + "# rebroadcasting other nodes' announces.",
        indent + "mode = gateway",
        indent + "discoverable = Yes",
    ]
    if name:
        lines.append(indent + f"discovery_name = {name}")
    lines.append(indent + f"announce_interval = {int(interval_min)}")
    lines.append(indent + f"latitude = {format_coord(flat)}")
    lines.append(indent + f"longitude = {format_coord(flon)}")
    if height_m is not None:
        lines.append(indent + f"height = {float(height_m):.1f}")
    lines.append(indent + BLOCK_END)
    return lines


def _strip_managed(lines: Sequence[str]) -> List[str]:
    """Every managed block removed, wherever it is. Idempotent by construction:
    apply-then-apply writes one block, not two."""
    out: List[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if not inside and stripped == BLOCK_START:
            inside = True
            continue
        if inside:
            if stripped == BLOCK_END:
                inside = False
            continue
        out.append(line)
    return out


_SECTION = re.compile(r"^(\s*)(\[+)([^\]]+)(\]+)\s*$")


def _sections(lines: Sequence[str]) -> List[dict]:
    """Every ``[section]`` / ``[[subsection]]`` header with its body bounds."""
    heads = []
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if m:
            heads.append({"i": i, "indent": m.group(1), "depth": len(m.group(2)),
                          "name": m.group(3).strip()})
    for n, h in enumerate(heads):
        h["start"] = h["i"] + 1
        h["end"] = heads[n + 1]["i"] if n + 1 < len(heads) else len(lines)
    return heads


def _key_of(line: str) -> str:
    if "=" not in line or line.strip().startswith("#"):
        return ""
    return line.split("=", 1)[0].strip().lower()


def find_target_interface(text: str, prefer: str = "") -> Optional[str]:
    """Which interface stanza should carry the position, or ``None``.

    A node's config can hold several. The one that matters is the one RNS will
    actually announce on, so this only ever picks a type RNS lists as
    discoverable (Discovery.py:36). *prefer* names an exact stanza; a name that
    is not there falls through to the type match rather than failing silently.
    """
    lines = text.splitlines()
    subs = [h for h in _sections(lines) if h["depth"] >= 2]
    if prefer:
        for h in subs:
            if h["name"] == prefer:
                return h["name"]
    for h in subs:
        for line in lines[h["start"]:h["end"]]:
            if _key_of(line) == "type":
                value = line.split("=", 1)[1].strip()
                if value in DISCOVERABLE_INTERFACE_TYPES:
                    return h["name"]
    return None


#: The interface types RNS will announce for. Copied from RNS 1.3.7
#: ``Discovery.py:36`` — a node configured to share on anything else publishes
#: nothing at all, silently, which is the failure mode this whole module is
#: written to avoid.
DISCOVERABLE_INTERFACE_TYPES = ("BackboneInterface", "TCPServerInterface",
                                "TCPClientInterface", "RNodeInterface",
                                "WeaveInterface", "I2PInterface",
                                "KISSInterface")


def apply_to_reticulum_config(text: str, *, policy: str, name: str = "",
                              lat: Optional[float] = None,
                              lon: Optional[float] = None,
                              node_key: str = "",
                              interface: str = "",
                              interval_min: int = ANNOUNCE_INTERVAL_MIN
                              ) -> Tuple[str, List[str]]:
    """Return ``(config_text, notes)`` with sharing applied or removed.

    Pure text in, pure text out — so the thing that will be written to a node
    the operator may never physically reach again can be tested, diffed and
    shown to them before it goes. *notes* are plain-language findings for the
    screen: they are how this function says "I could not do what you asked"
    without pretending it did.
    """
    notes: List[str] = []
    lines = _strip_managed(text.splitlines())
    policy = normalise(policy)

    if not is_shared(policy):
        return ("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                ["Location sharing removed from the config. Announces already "
                 "sent cannot be recalled."])

    if lat is None or lon is None:
        return ("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                ["No coordinates on file for this node, so nothing was written. "
                 "Sharing needs a position first."])

    target = find_target_interface("\n".join(lines), prefer=interface)
    if target is None:
        return ("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                ["This node has no interface that Reticulum can announce on "
                 f"({', '.join(DISCOVERABLE_INTERFACE_TYPES[:4])}...), so "
                 "nothing was written."])

    heads = [h for h in _sections(lines) if h["name"] == target and h["depth"] >= 2]
    head = heads[0]
    body = lines[head["start"]:head["end"]]

    # Indent to match the stanza's own keys, not a guess. A config a human will
    # read months from now should not announce which lines a machine wrote.
    indent = None
    for line in body:
        if _key_of(line):
            indent = line[:len(line) - len(line.lstrip())]
            break
    if indent is None:
        indent = head["indent"] + "  "

    for line in body:
        key = _key_of(line)
        if key in _CONFLICT_KEYS:
            notes.append(
                f"'{line.strip()}' is already set by hand in [[{target}]]. It "
                "was left alone — two settings of the same key is a config "
                "Reticulum may read either way round. Remove it if the node "
                "does not appear.")

    block = build_discovery_block(name, lat, lon, node_key or name or target,
                                  indent=indent, interval_min=interval_min)

    # After the last real line of the stanza, before any trailing blank lines,
    # so the block belongs to this interface and not to whatever follows it.
    last = len(body)
    while last > 0 and not body[last - 1].strip():
        last -= 1
    new_body = body[:last] + block + body[last:]
    out = lines[:head["start"]] + new_body + lines[head["end"]:]
    notes.insert(0, f"Sharing written into [[{target}]] as a fuzzed point.")
    return ("\n".join(out) + ("\n" if text.endswith("\n") else ""), notes)


def shared_position_in_config(text: str) -> Optional[dict]:
    """What a config SAYS it publishes: ``{name, lat, lon, interval_min}``, or
    ``None``. Reads the managed block back, so a caller can check what is on the
    node rather than what it believes it wrote."""
    inside = False
    found = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == BLOCK_START:
            inside = True
            continue
        if inside:
            if stripped == BLOCK_END:
                break
            key = _key_of(line)
            if key in ("latitude", "longitude", "announce_interval",
                       "discovery_name"):
                found[key] = line.split("=", 1)[1].strip()
    if "latitude" not in found or "longitude" not in found:
        return None
    try:
        return {"name": found.get("discovery_name", ""),
                "lat": float(found["latitude"]),
                "lon": float(found["longitude"]),
                "interval_min": int(found.get("announce_interval",
                                              ANNOUNCE_INTERVAL_MIN))}
    except ValueError:
        return None


def rmap_uplink_block(host: str = RMAP_HOST, port: int = RMAP_PORT,
                      indent: str = "  ") -> List[str]:
    """A TCP interface to the public map's own transport node.

    SEPARATE FROM SHARING ON PURPOSE. A LoRa-only node's discovery announce
    never leaves the mesh, so a node can be configured perfectly and still not
    appear anywhere — and the fix for that is an internet connection to a
    stranger's server, which tells that stranger this node's public IP address.
    That is a different question from "may people see roughly where this node
    is", and it gets asked separately.
    """
    return [
        indent + f"[[RMAP uplink]]",
        indent + "  # Public map collector, from rmap.world/info.html. Carries",
        indent + "  # this node's discovery announce off the LoRa mesh — and",
        indent + "  # shows this node's public IP address to that server.",
        indent + "  type = TCPClientInterface",
        indent + "  enabled = Yes",
        indent + f"  target_host = {host}",
        indent + f"  target_port = {int(port)}",
    ]


def node_address_from_certs(certs: Sequence[dict], name: str = "",
                            identity_hash: str = "") -> str:
    """The SSH address this node was born with, from its birth certificates.

    A decision recorded on the medic changes nothing on a roof. To actually stop
    or start a node announcing, the medic has to reach it — and the only place
    it ever wrote down how is the certificate it issued at birth. Newest match
    wins (a rebirth supersedes). Pure, so the lookup is testable without a disk.
    """
    best, best_at = "", -1.0
    for cert in certs or ():
        if not isinstance(cert, dict):
            continue
        ident = (cert.get("identity_hash") or cert.get("reticulum_address")
                 or "")
        matched = (identity_hash and ident == identity_hash) or (
            name and (cert.get("name") or cert.get("hostname")) == name)
        if not matched:
            continue
        addr = (cert.get("ssh_address") or "").strip()
        if not addr:
            ips = cert.get("ip_addresses") or []
            addr = (ips[0] if ips else "").strip()
        if not addr:
            continue
        at = float(cert.get("issued_at") or cert.get("saved_at") or 0.0)
        if at >= best_at:
            best, best_at = addr, at
    return best


def reach_note() -> str:
    """The condition nobody thinks about until the pin never appears.

    A discovery announce travels the mesh like any other. On a node whose only
    link is LoRa it reaches the LoRa neighbours and stops — public maps live on
    the internet. So "configured to share" and "visible on a map" are two
    different states, and a tool that conflates them produces a node the
    operator believes is on a map for months. rmap.world's own instructions say
    the same thing and offer a public transport node for it
    (:func:`rmap_uplink_block`); this tool has never verified that path, so it
    describes the condition and does not promise the cure.
    """
    return ("A node whose only link is LoRa announces to its mesh neighbours "
            "and no further. It reaches an internet map only if something in "
            "your mesh carries it there. Node Medic cannot see whether that "
            "happened.")


def status_line(policy: str, applied_to_node: bool, lat: Optional[float],
                lon: Optional[float]) -> str:
    """One honest sentence about where this node's sharing decision has got to.

    THREE STATES, NOT TWO. "Shared" and "hidden" describe the medic's records;
    what the node is actually doing is a third thing, and the gap between them
    is real — a node on a roof keeps announcing until something reaches it and
    changes its config. Saying "shared" the moment a button is pressed would be
    the tool reporting its own intention as an observed fact.
    """
    if lat is None or lon is None:
        return ("No location on file for this node, so there is nothing to "
                "publish. Set one first.")
    if is_shared(policy):
        if applied_to_node:
            return ("Sharing ON and written to the node. It announces a fuzzed "
                    f"point about every {ANNOUNCE_INTERVAL_MIN // 60} hours. "
                    "Whether a public map has picked it up cannot be checked "
                    "from here.")
        return ("Sharing ON in Node Medic's records only — the node has not "
                "been told yet, so it is still announcing nothing.")
    if applied_to_node:
        return ("Sharing OFF and written to the node. It announces no further "
                "positions; the ones already sent cannot be recalled.")
    return ("Sharing OFF in Node Medic's records. If this node was ever told to "
            "share, it is STILL SHARING until Node Medic can reach it and "
            "change its config.")


# --- pushing it to a node that already exists --------------------------------

#: Where rnsd reads its config on a node this tool built.
NODE_CONFIG_PATH = "~/.reticulum/config"


def push_to_node(run: Callable[[str], Tuple[int, str, str]], *, policy: str,
                 name: str = "", lat: Optional[float] = None,
                 lon: Optional[float] = None, node_key: str = "",
                 config_path: str = NODE_CONFIG_PATH,
                 restart: bool = True) -> Tuple[bool, str]:
    """Apply a sharing decision to a LIVE node over an injected command runner.

    *run* is a ``Connection.run``-shaped callable: ``cmd -> (code, out, err)``.

    READ BACK, ALWAYS. Every step is verified against what the node reports
    afterwards, not against the fact that a command was sent: the config is
    re-read and parsed, and the restart is confirmed with ``is-active``. If any
    of that cannot be checked, this returns the truth about what is unknown
    rather than a success message. Nothing here claims the node has been seen on
    a map — this tool cannot observe that, and says so.
    """
    policy = normalise(policy)
    code, out, err = run(f"cat {config_path}")
    if code != 0:
        return (False, f"Could not read {config_path} on the node: "
                       f"{(err or out or '').strip() or 'no output'}")
    new_text, notes = apply_to_reticulum_config(
        out, policy=policy, name=name, lat=lat, lon=lon, node_key=node_key)
    if new_text == out:
        return (True, "Nothing to change — the node already matches. "
                      + " ".join(notes))

    heredoc = f"cat > {config_path} <<'RTTEOF'\n{new_text}\nRTTEOF"
    code, out2, err2 = run(heredoc)
    if code != 0:
        return (False, f"Could not write the config: "
                       f"{(err2 or out2 or '').strip() or 'no output'}")

    code, back, _ = run(f"cat {config_path}")
    if code != 0:
        return (False, "Wrote the config but could not read it back, so the "
                       "change is unconfirmed.")
    on_node = shared_position_in_config(back)
    if is_shared(policy) and on_node is None:
        return (False, "The config came back without the sharing block — the "
                       "write did not take.")
    if not is_shared(policy) and on_node is not None:
        return (False, "The config came back still carrying a position — the "
                       "write did not take.")

    detail = ("Sharing on: the node's config now carries a fuzzed point "
              f"({format_coord(on_node['lat'])}, {format_coord(on_node['lon'])})."
              if on_node else "Sharing off: no position in the node's config.")
    if not restart:
        return (True, detail + " rnsd was not restarted, so this takes effect "
                               "at its next start.")

    # THE RESTART'S OWN EXIT CODE MATTERS, and `is-active` cannot stand in for
    # it. A node with scoped sudo (provisioning.node_sudoers) refuses the
    # restart, the OLD rnsd carries on running perfectly with the OLD config,
    # and `is-active` answers "active" — so a check that only asked whether
    # rnsd was up would report a successful change to a node that had not
    # changed at all. That is the exact shape of failure this project keeps
    # hitting: a true statement standing in for the one that was asked.
    rcode, rout, rerr = run("sudo -n systemctl restart rnsd")
    if rcode != 0:
        return (False, detail + " But the restart was refused ("
                + ((rerr or rout or "").strip() or f"exit {rcode}")
                + "), so rnsd is still running the OLD config and nothing has "
                  "changed on the mesh yet.")
    code, active, _ = run("systemctl is-active rnsd")
    state = (active or "").strip()
    if code != 0 or state != "active":
        return (False, detail + f" But rnsd is '{state or 'unreadable'}' after "
                                "the restart — the node may be off the mesh. "
                                "Check it before you leave.")
    unheard = (" It announces about every "
               f"{ANNOUNCE_INTERVAL_MIN // 60} hours. Whether any public map "
               "picked it up cannot be checked from here."
               if is_shared(policy) else "")
    return (True, detail + " rnsd restarted and is running." + unheard)
