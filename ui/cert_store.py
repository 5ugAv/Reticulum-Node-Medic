"""On-medic store of birth certificates.

Every node the medic births is saved here as a JSON file, so the operator can:
  * search for a node they birthed earlier (to mount it in the wild -> Triage),
  * re-open a node's certificate and its notes from the map/VITALS later,
  * still export it off-device via the on-screen QR (that path is unchanged).

Pure filesystem + JSON, no Kivy — unit-tested against a temp dir. Runtime code
passes the default CERT_DIR; tests inject their own. A certificate is a plain
dict (the birth-certificate the build produced, plus operator name/notes/location);
each stored file also carries an ``_id`` (stable, so notes update in place) and a
``_saved_at`` epoch for ordering.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

CERT_DIR = os.path.expanduser("~/.reticulum-node-medic/certificates")


def _slug(text: str) -> str:
    """A filesystem-safe, lowercase slug — letters/digits/dashes only."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return s or "node"


def cert_id(cert: Dict) -> str:
    """A STABLE id for a certificate, so saving the same node twice (e.g. after
    adding notes) overwrites rather than duplicates. Prefers the build session id
    / Reticulum address (unique per build); falls back to the node name."""
    base = _slug(cert.get("node_name") or cert.get("hostname") or "node")
    uniq = cert.get("session_id") or cert.get("reticulum_address") or ""
    return f"{base}-{_slug(uniq)}" if uniq else base


def save_cert(cert: Dict, cert_dir: str = CERT_DIR, now: Optional[float] = None) -> str:
    """Persist *cert* and return its id. Idempotent on the id (re-save overwrites).
    ``now`` is injectable for tests; defaults to wall-clock (runtime only).

    ONE BOARD, ONE RECORD (operator decision, 2026-08-07). Saving a certificate
    RETIRES any other certificate describing the same physical board, matched on
    the USB serial rather than the name.

    Why this lives here and not on the rebirth path: it was on the rebirth path,
    and that was not enough. ``delete_by_usb_serial`` was called from exactly one
    place — the wipe flow — so any ordinary birth or adopt of a board that
    already had a certificate simply added another. Measured on the live medic
    2026-08-07: serial ...4631000000000001 had THREE certificates (rak4,
    zerorak, zerorak1) and one Tracker had two (newt, track). save_cert is the
    single choke point every path goes through, so the rule holds wherever a
    certificate comes from.

    A certificate with NO usb_serial retires nothing. Otherwise every unserialled
    record — the Pi nodes, the older ones — would delete each other, which would
    turn a tidy-up into data loss.

    This DELETES rather than archiving, and the previous born date, stamped
    location and notes go with it. That is the operator's explicit choice: the
    fleet list must show one live node per board.
    """
    os.makedirs(cert_dir, exist_ok=True)
    cid = cert.get("_id") or cert_id(cert)
    stored = dict(cert)
    stored["_id"] = cid
    stored.setdefault("_saved_at", now if now is not None else time.time())
    # atomic: a power cut mid-write must not leave an empty certificate
    from monitor.atomic_json import write_json
    write_json(os.path.join(cert_dir, f"{cid}.json"), stored, indent=2)
    # Retire older records of the SAME BOARD — after the write, so a failure
    # here leaves a duplicate rather than nothing at all.
    serial = stored.get("usb_serial") or ""
    if serial:
        delete_by_usb_serial(serial, cert_dir, keep_id=cid)
    return cid


def load_certs(cert_dir: str = CERT_DIR) -> List[Dict]:
    """Every stored certificate, newest first. Empty list if the dir is absent."""
    if not os.path.isdir(cert_dir):
        return []
    out: List[Dict] = []
    for name in os.listdir(cert_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(cert_dir, name)) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda c: c.get("_saved_at", 0), reverse=True)
    return out


def usb_serial_key(usb_serial: str) -> str:
    """The comparable identity of a board, out of a /dev/serial/by-id name.

    THE ONE WAY to ask "is this the same board?". Never compare the whole by-id
    basename: the vendor part of it is not stable across a reset. A RAK4631
    announces itself as ``RAKWireless`` from its bootloader and ``RAKwireless``
    once running firmware — one capital letter apart — so an exact compare
    silently fails on every nRF52 board.

    That single letter broke four separate things on 2026-08-05: the post-flash
    port re-acquire, the DFU pre-touch, retiring an old certificate on rebirth,
    and recognising a board as kin. Only the hardware serial survives, so key on
    that and fold case for the rest.

    A rebirth is usually a REPAIR, not a replacement (operator, 2026-08-05): the
    node keeps its name and must still be kin afterwards. That only works if the
    board's identity is stable across the reflash — which is this function.

    But a serial only IDENTIFIES when it is unique. A USB-UART bridge board ships
    the factory default "0001" (the Heltec V3's CP2102, and any board off that
    line), so keying two different-model boards on "0001" would call them ONE and
    hand back the wrong board's certificate. When the serial is a placeholder we
    fall back to the full by-id name — non-unique, but at least it tells two
    different models apart. (Same-model boards that BOTH carry "0001" still
    collide here, as they did before this reader could read a bridge serial at
    all — identical full names; this only closes the cross-model case the shared
    splitter newly exposed by reading "0001" off boards it used to read blank.)
    """
    if not usb_serial:
        return ""
    try:
        from provisioning.by_id import by_id_serial, is_uniquely_identified
        if is_uniquely_identified(usb_serial):
            return by_id_serial(usb_serial).lower()
        # No unique serial: key on the whole name, never a shared default.
        return usb_serial.lower()
    except Exception:                                             # noqa: BLE001
        return usb_serial.lower()


def same_board(a: str, b: str) -> bool:
    """True when two by-id names denote the same physical board."""
    ka = usb_serial_key(a)
    return bool(ka) and ka == usb_serial_key(b)


def cert_for_usb_serial(usb_serial: str, cert_dir: str = CERT_DIR):
    """The stored certificate for this board, or None — matched by serial, so a
    board recognises whether it is in its bootloader or running firmware."""
    if not usb_serial:
        return None
    for cert in load_certs(cert_dir):
        if same_board(cert.get("usb_serial"), usb_serial):
            return cert
    return None


def radio_serial_for_board(board_name: str, certs=None,
                           cert_dir: str = CERT_DIR):
    """``(serial, how)`` — the ONE USB serial this medic's ledger holds for a
    radio board it flashed earlier, named *board_name* (the catalogue display
    name a flash certificate carries as ``board``).

    For the guide's "I already have a working radio" road (2026-09-22): the
    radio is not on the bench, but if this medic flashed exactly one board of
    that model, its flash certificate holds the serial, and the Pi's udev
    rule can be pinned to that radio instead of the five-vendor net. Two or
    more distinct serials is NOT a guess — ``("", why)`` — and so is none.
    Only flash records count: a Pi certificate that names the board is the
    thing being corrected, not evidence. Placeholder serials (``0001``) are
    not identities (provisioning.by_id).
    """
    from provisioning.by_id import by_id_serial, is_uniquely_identified
    want = (board_name or "").strip().lower()
    if not want:
        return "", "no board named"
    if certs is None:
        certs = load_certs(cert_dir)
    found: Dict[str, Dict] = {}
    for c in certs:
        if (c.get("board") or "").strip().lower() != want:
            continue
        role = (c.get("role") or "").lower()
        if "propagation" in role or "lxmf" in role:
            continue                                  # a Pi's record, not a flash
        usb = c.get("usb_serial") or ""
        if not usb or not is_uniquely_identified(usb):
            continue
        found.setdefault(by_id_serial(usb), c)
    if not found:
        return "", f"no {board_name} flashed by this medic"
    if len(found) > 1:
        return "", (f"{len(found)} {board_name} boards flashed by this medic "
                    "— which one is not known")
    serial, c = next(iter(found.items()))
    when = (c.get("born") or "")[:10]
    if not when and c.get("_saved_at"):
        when = time.strftime("%Y-%m-%d", time.localtime(c["_saved_at"]))
    name = c.get("node_name") or "a board"
    return serial, (f"read by this medic when it flashed '{name}'"
                    + (f" on {when}" if when else ""))


def update_fields(cid: str, fields: Dict, cert_dir: str = CERT_DIR) -> bool:
    """Merge *fields* into a stored certificate (by id) and re-save. False if
    it isn't found. For a repair that corrected a node's radio naming over
    SSH (workflows.radio_repair, 2026-09-22): the medic's own record of the
    node must change with it, or VITALS goes on printing the wrong board."""
    path = os.path.join(cert_dir, f"{cid}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            cert = json.load(f)
    except (OSError, ValueError):
        return False
    cert.update(fields)
    from monitor.atomic_json import write_json
    write_json(path, cert, indent=2)
    return True


def delete_by_usb_serial(usb_serial: str, cert_dir: str = CERT_DIR,
                         keep_id: str = "") -> int:
    """Remove stored certificates whose board fingerprint is *usb_serial*.

    Called when a board is WIPED (rebirth): leaving the old certificate behind
    made the now-blank board recognise as 'already flashed' on the next plug-in
    (2026-08-01 bug hunt). Also called by ``save_cert`` to keep one record per
    board. Returns how many were removed.

    *keep_id* spares one certificate — the one just written. Without it,
    save_cert would delete the record it had this moment created.
    """
    if not usb_serial:
        return 0
    want = usb_serial_key(usb_serial)
    removed = 0
    for cert in load_certs(cert_dir):
        if not want or usb_serial_key(cert.get("usb_serial")) != want:
            continue
        cid = cert.get("_id")
        if not cid or cid == keep_id:
            continue
        try:
            os.remove(os.path.join(cert_dir, f"{cid}.json"))
            removed += 1
        except OSError:
            pass
    return removed


def next_free_name(previous: str, cert_dir: str = CERT_DIR) -> str:
    """A safe default name for a board being rebirthed: ``rak3`` -> ``rak4``.

    Operator, walking a RAK4631 rebirth (2026-08-05): *"It says wiping rak3 ...
    but I'm not prompted to change the name from rak3."* The old name was seeded
    into the field with nothing drawing attention to it, so it was simply
    carried forward — and carrying it forward OVERWRITES the previous
    certificate, born date, location and notes included.

    So the default becomes the next unused number instead of the old name
    (operator's choice, 2026-08-07). The operator can still type anything; this
    only decides what is already in the box, and what is in the box should be
    safe when nobody reads it.

    Rules, in the order they matter:
      * a trailing number is incremented until the name is free — rak3 -> rak4,
        and rak4 -> rak5 if rak4 also exists;
      * a name with NO trailing number gains "2" — hope -> hope2;
      * an empty previous name yields "" — there is nothing to suggest, and
        inventing one would be worse than leaving the field blank.

    Pure apart from reading the cert dir, so the numbering is testable.
    """
    base = (previous or "").strip()
    if not base:
        return ""
    import re
    m = re.match(r"^(.*?)(\d+)$", base)
    stem, n = (m.group(1), int(m.group(2))) if m else (base, 1)
    taken = {str(c.get("node_name", "")).strip().lower()
             for c in load_certs(cert_dir)}
    for i in range(n + 1, n + 1000):
        cand = f"{stem}{i}"
        if cand.lower() not in taken:
            return cand
    return f"{stem}{n + 1}"          # pathological; still not the old name


def search_certs(query: str, cert_dir: str = CERT_DIR) -> List[Dict]:
    """Certificates whose name/hostname/address contains *query* (case-insensitive).
    A blank query returns them all (newest first) — the natural 'browse' state."""
    q = (query or "").strip().lower()
    certs = load_certs(cert_dir)
    if not q:
        return certs
    fields = ("node_name", "hostname", "ssh_address", "reticulum_address")
    return [c for c in certs
            if any(q in str(c.get(f, "")).lower() for f in fields)]


def update_notes(cid: str, notes: str, cert_dir: str = CERT_DIR) -> bool:
    """Set the notes on a stored certificate (by id) and re-save. Returns False if
    it isn't found."""
    path = os.path.join(cert_dir, f"{cid}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            cert = json.load(f)
    except (OSError, ValueError):
        return False
    cert["notes"] = notes
    from monitor.atomic_json import write_json
    write_json(path, cert, indent=2)
    return True


def delete_by_name(name: str, cert_dir: str = CERT_DIR) -> int:
    """Remove every stored certificate carrying *name* (case-insensitive).

    The delete-node action (operator, 2026-08-13): a name is only truly free
    for a new birth when nothing on the medic still answers to it. Returns
    how many were removed; 0 for an unknown name is an answer, not an error.
    """
    low = (name or "").strip().lower()
    if not low:
        return 0
    removed = 0
    for c in load_certs(cert_dir):
        if (c.get("node_name") or "").strip().lower() == low:
            path = os.path.join(cert_dir, f"{c['_id']}.json")
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def predecessor_hashes(node_name: str, keep_hashes,
                       cert_dir: str = None) -> set:
    """Destination hashes that PRIOR certificates for *node_name* recorded and
    the current birth does not carry — the previous machine's identities.

    A rebirth re-images the machine, so its old transport/health/propagation
    identities can never speak again; only the mesh's replayed caches keep
    them moving. The old certs are the one durable record tying those hashes
    to this name (2026-08-25: three of old-ELSEWHERE's identities sat on
    VITALS as anonymous neighbours because nothing consulted them). Matching
    is by node_name OR hostname, case-insensitive; *keep_hashes* (the new
    cert's own destinations) are excluded, so re-issuing a cert for the SAME
    machine retires nothing. Read-only and raise-proof: a corrupt cert file is
    simply skipped.
    """
    import json as _json
    import os as _os
    want = (node_name or "").strip().lower()
    if not want:
        return set()
    keep = {str(h) for h in (keep_hashes or []) if h}
    base = cert_dir or CERT_DIR
    found = set()
    try:
        names = _os.listdir(base)
    except OSError:
        return set()
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(_os.path.join(base, fn)) as f:
                cert = _json.load(f)
        except Exception:                                      # noqa: BLE001
            continue
        if not isinstance(cert, dict):
            continue
        cn = (cert.get("node_name") or "").strip().lower()
        ch = (cert.get("hostname") or "").strip().lower()
        if want not in (cn, ch):
            continue
        for k in ("reticulum_address", "health_dst", "lxmd_dst", "lxmf_dst"):
            h = cert.get(k)
            if h and str(h) not in keep:
                found.add(str(h))
    return found


def cert_for_node(certs, dst_hash: str = "", name: str = ""):
    """The certificate this medic wrote for a node, or None. A registry row
    can be keyed by the node's mesh address, its identity hash, or (an
    RTNode found by name) "rtnode:<name>" — the certificate may carry any
    of those, so match address first, identity second, name last; the
    newest wins (a name is reused after a rebirth). Operator, 2026-09-21:
    the node's page should say what the medic KNOWS it built, not only
    what the node reports about itself."""
    raw = (dst_hash or "").strip()
    nm = (name or "").strip()
    if raw.lower().startswith("rtnode:"):
        nm = nm or raw[len("rtnode:"):]     # the name keeps its case
        dst = ""
    else:
        dst = raw.lower()
    ranked = []
    for c in certs or ():
        addr = (c.get("reticulum_address") or "").lower()
        ident = (c.get("identity_hash") or "").lower()
        if dst and dst in (addr, ident):
            rank = 0
        elif nm and (c.get("node_name") or "") == nm:
            rank = 1
        else:
            continue
        ranked.append((rank, -(c.get("_saved_at") or 0), c))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (t[0], t[1]))
    return ranked[0][2]
