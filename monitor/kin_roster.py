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


#: What a board of this class CAN have. Reference only — never a claim about a
#: particular node, and deliberately not consulted by anything that draws a
#: screen. A node's interfaces are whatever the node itself reports; its board's
#: datasheet is not evidence about the thing on the roof. Kept because it is
#: genuinely useful when deciding what to ASK an operator at birth, and as the
#: record of why the assumption was tempting.
CAPABLE_OF = {
    "pi_propagation": {"lora": True, "wifi": True, "bluetooth": True, "internet": True},
    "pi": {"lora": True, "wifi": True, "bluetooth": True, "internet": True},
    # An RTNode-2400 is definitionally a LoRa mesh node; its WiFi config AP may be
    # off, so only LoRa is declared — a live HTTP reading lights WiFi when it's up.
    "rtnode2400": {"lora": True},
}


def register(rns_hash: str, name: str, node_type: str = "pi",
             lat: Optional[float] = None, lon: Optional[float] = None,
             links: Optional[dict] = None, builder: Optional[str] = None,
             share_location: Optional[str] = None,
             device: Optional[str] = None,
             hw_serial: Optional[str] = None,
             path: str = KIN_ROSTER_PATH) -> dict:
    """Record one of the medic's own nodes (idempotent — updates in place).
    Returns the updated roster. Called at BIRTH with the node's identity + name +
    where it's being deployed. *links* declares the interfaces it physically has
    (defaults by node type); the medic can't infer these over the mesh. *builder*
    is the identity hash of the medic UNIT that birthed this node — its trust
    (monitor.trust) decides kin vs neighbour, so revoking that unit demotes the
    node. Stamp it with this medic's own unit hash at BIRTH. *device* names the
    physical MACHINE this hash belongs to (see ``register_device``)."""
    roster = load_roster(path)
    entry = roster.get(rns_hash, {})
    entry["name"] = name
    entry["type"] = node_type
    if builder is not None:
        entry["builder"] = builder
    if device is not None:
        entry["device"] = device
    if hw_serial:
        entry["hw_serial"] = hw_serial
    if lat is not None:
        entry["lat"] = lat
    if lon is not None:
        entry["lon"] = lon
    if share_location is not None:
        from monitor import location_share
        entry["share_location"] = location_share.normalise(share_location)
    # ONLY WHAT WAS ACTUALLY DECIDED OR MEASURED. This used to fall back to
    # DEFAULT_LINKS — the board type's datasheet — and write that into the
    # roster as though it were fact, where the display then read it back and
    # showed it to the operator as a working interface. See _capabilities in
    # monitor/registry.py for where that surfaced, and what it cost.
    resolved = links
    if resolved is not None:
        entry["links"] = resolved
    roster[rns_hash] = entry
    _save(roster, path)
    return roster


def register_device(hashes, name: str, node_type: str = "pi",
                    lat: Optional[float] = None, lon: Optional[float] = None,
                    links: Optional[dict] = None, builder: Optional[str] = None,
                    hw_serial: Optional[str] = None,
                    path: str = KIN_ROSTER_PATH) -> dict:
    """Record ONE machine that answers on SEVERAL Reticulum destinations.

    A Pi propagation node has two, and they are not related by anything the mesh
    can see: rnsd announces from the node's Reticulum identity, and the health
    reporter announces from an identity of its own, deliberately kept in a
    separate file so it survives a rebuilt Reticulum store. Two identities, two
    announces, two rows in VITALS — SkyFinger showed up twice, and half of what
    the operator wanted to know was on each row.

    Birth is the one moment anybody knows better: the medic has just built the
    machine and holds both hashes in the same certificate. Writing them down as
    one device here is what lets the registry put them back together
    (``NodeRecord.device_id``). Every hash gets the full entry — name, type,
    location — so whichever destination is heard first, the node is named and on
    the map rather than an anonymous neighbour.

    The FIRST hash given is the device's id; pass the health destination first,
    since that is the one whose beacons carry the readings.

    IDEMPOTENT MERGE (2026-08-22): if these hashes touch EXACTLY ONE existing
    device, the whole set joins THAT device instead of minting a second one. So
    re-adopting or repairing a node — which re-runs birth and may add a
    destination the first birth missed (an lxmd aspect that had not announced
    yet) — ADDS the address to the node's one device rather than splitting it
    into a live row and a ghost. A brand-new machine (no hash known) takes its
    first hash as the device id.

    BUT NEVER UNION TWO EXISTING DEVICES (2026-08-22, Finding 2). If the incoming
    set spans hashes belonging to TWO OR MORE different existing devices — which
    should not happen from an honest birth, but is reachable via re-imaged cards
    or a harvested/reused identity_hash — merging them would silently steal a
    hash off its rightful machine and persist that to disk. So in that case do
    NOT union: mint this set under its own device (its first hash) and leave the
    others alone. The collision is logged, because it signals an imaging /
    identity-reuse mistake worth seeing.
    """
    hs = [str(h) for h in (hashes or []) if h]
    roster = load_roster(path)
    if not hs:
        return roster
    existing_devices = []
    for h in hs:
        entry = roster.get(h)
        if isinstance(entry, dict) and entry.get("device"):
            d = entry["device"]
            if d not in existing_devices:
                existing_devices.append(d)
    # The device NEW hashes join: the one device we already touch, else this
    # set's own first hash. Known hashes NEVER move (see below), so this only
    # ever decides where brand-new destinations land.
    if len(existing_devices) == 1:
        target = existing_devices[0]
    else:
        if len(existing_devices) >= 2:
            print(f"[kin] register_device: hashes {hs} span "
                  f"{len(existing_devices)} existing devices {existing_devices} "
                  f"— NOT unioning them (imaging/identity-reuse collision?)")
        target = hs[0]
    for h in hs:
        entry = roster.get(h)
        # A hash already assigned to a device KEEPS it — never stolen onto
        # another machine (Finding 2). Only a genuinely new hash takes *target*.
        own = entry.get("device") if isinstance(entry, dict) else None
        roster = register(h, name, node_type=node_type, lat=lat, lon=lon,
                          links=links, builder=builder, device=(own or target),
                          hw_serial=hw_serial, path=path)
    return roster


def retire_previous_lives(hw_serial: Optional[str], keep_hashes,
                          path: str = KIN_ROSTER_PATH) -> list:
    """Remove roster entries for EARLIER identities of the same physical board.

    Every wipe-and-provision mints a fresh identity, but the old one's roster
    entry (and its registry rows) stayed behind wearing the board's name — on
    2026-08-21 a single T114 held two "live" rows, and replayed announces
    kept the dead one green. Called at birth with the board's hardware serial
    and the hashes the NEW certificate carries: any entry recorded against the
    same serial under a hash not in *keep_hashes* is a previous life. Returns
    the removed hashes so the caller can also purge the live registry. A birth
    with no serial (Pi builds, old certs) is a no-op — never guess.
    """
    if not hw_serial:
        return []
    keep = {str(h) for h in (keep_hashes or [])}
    roster = load_roster(path)
    doomed = [h for h, e in roster.items()
              if isinstance(e, dict) and e.get("hw_serial") == hw_serial
              and h not in keep]
    if doomed:
        for h in doomed:
            del roster[h]
        _save(roster, path)
    return doomed


def retire_same_name(name: str, keep_hashes, hw_serial: Optional[str] = None,
                     path: str = KIN_ROSTER_PATH) -> list:
    """Retire roster entries for a DIFFERENT machine that currently wears *name*.

    ONE NAME MUST MEAN ONE CURRENT MACHINE (2026-08-22, Finding 1). The operator
    reuses sequential names across rebirths of DIFFERENT boards — a spare birthed
    "A2" after the first "A2" was deployed. VITALS must never GUESS that two
    same-named machines are one (folding by name could hide a board that died in
    the field behind a live namesake's freshness — the 2026-08-13 hazard). So
    uniqueness is enforced HERE, at birth: birthing a node under a name already
    held by a DIFFERENT device retires that old device — a clean REPLACE. If the
    old board is still alive it simply re-announces later as an anonymous
    neighbour (honest: it is a different machine the operator has un-named),
    never masked green under the new one.

    The node being (re)birthed is identified by *keep_hashes* (its destinations)
    and *hw_serial* (its board): a same-named entry that shares a hash, shares
    the device those hashes already occupy, or shares the serial is the SAME node
    and is left for ``register_device`` to update. Everything else same-named is a
    different machine and is removed. Returns the retired hashes so the caller
    can also purge the live registry rows.
    """
    want = (name or "").strip().lower()
    if not want:
        return []
    keep = {str(h) for h in (keep_hashes or [])}
    roster = load_roster(path)
    # Devices/hashes the node being birthed already occupies (a re-birth of a
    # node the roster already knows), so its own older rows are NOT retired here.
    keep_devices = set(keep)
    for h, e in roster.items():
        if h in keep and isinstance(e, dict) and e.get("device"):
            keep_devices.add(e["device"])
    doomed = []
    for h, e in roster.items():
        if not isinstance(e, dict):
            continue
        if (e.get("name") or "").strip().lower() != want:
            continue
        if h in keep or e.get("device") in keep_devices:
            continue                              # the same node — leave it
        if hw_serial and e.get("hw_serial") == hw_serial:
            continue                              # same board (retire_previous_lives)
        doomed.append(h)
    if doomed:
        for h in doomed:
            del roster[h]
        _save(roster, path)
    return doomed


def set_location(rns_hash: str, lat: float, lon: float,
                 path: str = KIN_ROSTER_PATH) -> dict:
    """Set/update where a fleet node is deployed (so it lands on the map at the
    right spot — the operator does this when they physically place it).

    THESE COORDINATES ARE THE MEDIC'S OWN KNOWLEDGE and stay here. Nothing
    publishes them: what a shared node announces is the fuzzed pin derived from
    them (monitor.location_share.public_pin), and only if
    ``share_location`` says so."""
    roster = load_roster(path)
    if rns_hash in roster:
        roster[rns_hash]["lat"] = lat
        roster[rns_hash]["lon"] = lon
        _save(roster, path)
    return roster


def set_share_location(rns_hash: str, policy: str,
                       path: str = KIN_ROSTER_PATH) -> dict:
    """Record whether this fleet node publishes a position to the public map.
    Unknown/absent stays hidden — see monitor.location_share.normalise."""
    from monitor import location_share
    roster = load_roster(path)
    if rns_hash in roster:
        roster[rns_hash]["share_location"] = location_share.normalise(policy)
        _save(roster, path)
    return roster


#: A certificate's ``role`` -> the roster type for this node.
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
