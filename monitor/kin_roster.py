"""The medic's own fleet — the nodes IT built/owns, by RNS identity, with a name,
type, and DEPLOYED LOCATION.

The registry only calls a node "kin" if it beacons, serves HTTP, or was named/
located — so a plain Pi propagation node the medic built (like EVERYWHERE, the
medic's own LoRa uplink relay) shows up as an anonymous "Neighbour xxxx", or not
at all. That's wrong: it's OUR node. This roster is the medic's self-knowledge of
its fleet — seed a record from it and the node shows in VITALS as NAMED KIN and,
with a location, populates the MAP at its deployed spot.

The location is the point: once every built node is on the map, the medic has the
real data — who reaches whom, at what distance — to work out the most valuable
spot for the NEXT node (not too close, not out of range). See monitor.placement.

The medic registers a node here at BIRTH (identity + name + where it's going);
until then, entries can be seeded/edited by hand. Mirrors ui.onboard_roster (the
medic's own BOARDS) — this is the medic's own NODES.
"""

from __future__ import annotations

import json
import os
from typing import Optional

#: Per-medic fleet roster: {rns_hash: {"name", "type", "lat", "lon"}}.
KIN_ROSTER_PATH = os.path.expanduser("~/.reticulum-node-medic/kin.json")


def load_roster(path: str = KIN_ROSTER_PATH) -> dict:
    """The medic's fleet ({hash: {name, type, lat, lon}}); empty if none yet."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(roster: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # atomic: the roster is the medic's memory of its whole fleet
    from monitor.atomic_json import write_json
    write_json(path, roster, indent=2, sort_keys=True)


#: Interfaces a node class physically HAS (the medic only hears LoRa, so it can't
#: infer these — a Pi 3A+ propagation node has onboard wifi + bluetooth, and its
#: internet rides that wifi; it has no Ethernet port). VITALS shows these unless a
#: live reading contradicts them.
DEFAULT_LINKS = {
    "pi_propagation": {"lora": True, "wifi": True, "bluetooth": True, "internet": True},
    "pi": {"lora": True, "wifi": True, "bluetooth": True, "internet": True},
    # An RTNode-2400 is definitionally a LoRa mesh node; its WiFi config AP may be
    # off, so only LoRa is declared — a live HTTP reading lights WiFi when it's up.
    "rtnode2400": {"lora": True},
}


def register(rns_hash: str, name: str, node_type: str = "pi",
             lat: Optional[float] = None, lon: Optional[float] = None,
             links: Optional[dict] = None, builder: Optional[str] = None,
             path: str = KIN_ROSTER_PATH) -> dict:
    """Record one of the medic's own nodes (idempotent — updates in place).
    Returns the updated roster. Called at BIRTH with the node's identity + name +
    where it's being deployed. *links* declares the interfaces it physically has
    (defaults by node type); the medic can't infer these over the mesh. *builder*
    is the identity hash of the medic UNIT that birthed this node — its trust
    (monitor.trust) decides kin vs neighbour, so revoking that unit demotes the
    node. Stamp it with this medic's own unit hash at BIRTH."""
    roster = load_roster(path)
    entry = roster.get(rns_hash, {})
    entry["name"] = name
    entry["type"] = node_type
    if builder is not None:
        entry["builder"] = builder
    if lat is not None:
        entry["lat"] = lat
    if lon is not None:
        entry["lon"] = lon
    resolved = links if links is not None else DEFAULT_LINKS.get(node_type)
    if resolved is not None:
        entry["links"] = resolved
    roster[rns_hash] = entry
    _save(roster, path)
    return roster


def set_location(rns_hash: str, lat: float, lon: float,
                 path: str = KIN_ROSTER_PATH) -> dict:
    """Set/update where a fleet node is deployed (so it lands on the map at the
    right spot — the operator does this when they physically place it)."""
    roster = load_roster(path)
    if rns_hash in roster:
        roster[rns_hash]["lat"] = lat
        roster[rns_hash]["lon"] = lon
        _save(roster, path)
    return roster


#: A certificate's ``role`` -> the roster type whose DEFAULT_LINKS describe it.
#: The role string is the one thing every birth records honestly: it comes from
#: NodeRole, which the build workflow sets from what it actually built.
_ROLE_TYPES = {
    "LXMF propagation node": "pi_propagation",   # Pi + RNode: rnsd + lxmd
    "Transport node": "rtnode2400",              # RTNode-2400, microReticulum
}


def type_for_cert(cert: dict) -> str:
    """Which roster type this certificate describes.

    THE DEFAULT USED TO BE "rtnode2400" AND IT WAS WRONG FOR EVERY PI. The
    caller read ``cert.get("type", "rtnode2400")``, no birth has ever written a
    ``type`` key, so the fallback answered every time — and the first Pi
    propagation node ever built showed up in VITALS labelled "rtnode2400" with
    LoRa as its only interface (operator, 2026-08-10, minutes after 2k13 came
    up). The node had wifi, bluetooth and an internet path, all invisible,
    because a default had been asked to do a lookup's job.

    ``role`` is the honest signal: NodeRole is set by the build workflow from
    what it actually built, and it is on every certificate. An unrecognised
    role returns "" so the caller can decide rather than being handed a guess.
    """
    explicit = (cert or {}).get("type")
    if explicit:
        return str(explicit)
    return _ROLE_TYPES.get((cert or {}).get("role", ""), "")
