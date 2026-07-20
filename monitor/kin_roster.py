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
    with open(path, "w") as f:
        json.dump(roster, f, indent=2, sort_keys=True)


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
             links: Optional[dict] = None,
             path: str = KIN_ROSTER_PATH) -> dict:
    """Record one of the medic's own nodes (idempotent — updates in place).
    Returns the updated roster. Called at BIRTH with the node's identity + name +
    where it's being deployed. *links* declares the interfaces it physically has
    (defaults by node type); the medic can't infer these over the mesh."""
    roster = load_roster(path)
    entry = roster.get(rns_hash, {})
    entry["name"] = name
    entry["type"] = node_type
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
