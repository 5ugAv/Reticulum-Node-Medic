"""Location capture and navigation helpers.

At build time the Pi is physically at the node, so its GPS fix *is* the node's
location. We capture it once: the node advertises a privacy-fuzzed (~800 m,
firmware-side) pin to the public mesh map, while the exact coordinates are kept
on the birth certificate for a future repair visit.

The GPS read is injected so this is testable without hardware; the default
reader queries gpsd. The coordinate/URL helpers are pure.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

#: Nominatim requires a descriptive User-Agent (generic ones are blocked).
GEOCODE_USER_AGENT = "ReticulumNodeMedic/1.0 (offline mesh node placement)"

#: Nominal User-Equivalent Range Error, metres. HDOP is a UNITLESS geometry
#: factor; turning it into "how many metres off might I be" needs a per-receiver
#: ranging error to multiply it by. 2.5 m is the textbook nominal UERE for a
#: consumer GNSS with a decent sky view. So accuracy_m = HDOP * NOMINAL_UERE_M
#: is an ESTIMATE — an order-of-magnitude "how good is this fix", NOT a measured
#: CEP the receiver reported. Anything that shows it to an operator MUST label it
#: as estimated (see accuracy_label): the honesty ethos forbids dressing a
#: derived guess up as a measurement.
NOMINAL_UERE_M = 2.5


@dataclass
class GpsFix:
    lat: float
    lon: float
    source: str = "pi_gps"
    sats: Optional[int] = None          # satellites used (from the Tracker STATE frame)
    fix_quality: Optional[int] = None   # 0 = no fix, >=1 = fix
    #: ESTIMATED horizontal accuracy, metres = HDOP * NOMINAL_UERE_M. None until
    #: the firmware reports HDOP; never fabricated. This is a derived estimate,
    #: not a measured CEP — see NOMINAL_UERE_M and accuracy_label().
    accuracy_m: Optional[float] = None
    altitude_m: Optional[float] = None  # metres MSL (signed), or None if unreported
    fix_time: Optional[str] = None      # ISO-8601 UTC of the observation

    @property
    def has_fix(self) -> bool:
        return self.lat is not None and self.lon is not None


def _default_gps_reader() -> Optional[Tuple[float, float]]:
    """Query gpsd for a current fix; return (lat, lon) or None."""
    try:
        out = subprocess.run(
            ["gpspipe", "-w", "-n", "10"],
            capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("class") == "TPV" and "lat" in obj and "lon" in obj:
                return (obj["lat"], obj["lon"])
    except Exception:
        return None
    return None


def read_gps(reader: Callable[[], Optional[Tuple[float, float]]]
             = _default_gps_reader) -> Optional[GpsFix]:
    """Return a ``GpsFix`` or ``None`` if there's no fix / no GPS."""
    try:
        coords = reader()
    except Exception:
        return None
    if not coords:
        return None
    lat, lon = coords
    return GpsFix(lat=lat, lon=lon, source="pi_gps")


# --- Tracker GPS via the serial splitter -----------------------------------
# Jonesey (the medic's RNode) skims its own GPS fix into a small JSON state file
# (monitor.serial_splitter), so LoRa (rnsd) and GPS never fight over the one serial
# port. We read that file here rather than owning a port ourselves.

#: WHERE THE MEDIC'S POSITION LIVES — RAM, not the SD card.
#:
#: Operator's design decision, 2026-08-07: *"the NodeMedic can just access the
#: GPS at that moment and centralize the map to where the NodeMedic is currently
#: situated. It doesn't need to have a permanent home address."* Position is a
#: thing the tool has in the MOMENT, never a thing it keeps.
#:
#: This file is the handoff between the splitter service and the app, so it must
#: exist — but it does not have to be durable. On disk it left the medic's most
#: recent position sitting on removable media, surviving power-off, in a tool
#: whose whole ethos is that nodes stay untraceable to a person or a place
#: ([[anonymity-ethos]]). /dev/shm is tmpfs: it is gone when the power is.
#:
#: Second benefit: one fewer continuous writer to the SD card, which the
#: solar-node corruption work says is the failure that actually bites
#: ([[sd-reliability-overlayfs]]).
SPLITTER_STATE = "/dev/shm/nodemedic-gps.json"

#: Where it used to live. Read-only fallback so the code can be deployed BEFORE
#: the systemd unit is updated (that needs root) — otherwise GPS would go blind
#: in the gap between the two changes.
LEGACY_SPLITTER_STATE = os.path.expanduser("~/gps_state.json")


def read_splitter_state(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                        now: Callable[[], float] = time.time) -> Optional[dict]:
    """The splitter's latest GPS state, or ``None`` if the file is missing,
    unreadable, or older than *max_age_s* (the GPS/splitter isn't feeding now)."""
    # The legacy fallback applies ONLY when the caller took the default. An
    # explicit path must be honoured exactly — tests inject their own, and a
    # caller asking about a specific file does not want a different one.
    # (Caught by the suite ON THE MEDIC, where the legacy file exists and a
    # deliberately-missing path quietly returned real GPS state.)
    candidates = ((path, LEGACY_SPLITTER_STATE) if path == SPLITTER_STATE
                  else (path,))
    st = None
    for candidate in candidates:
        try:
            with open(candidate) as f:
                st = json.load(f)
            break
        except (OSError, ValueError):
            continue
    if st is None:
        return None
    upd = st.get("updated")
    if not isinstance(upd, (int, float)) or (now() - upd) > max_age_s:
        return None
    return st


def read_splitter_fix(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                      now: Callable[[], float] = time.time) -> Optional[GpsFix]:
    """A full :class:`GpsFix` from the splitter state (position + sats + fix time),
    or ``None`` if there's no current fix. Used for the birth cert / Triage."""
    st = read_splitter_state(path, max_age_s, now)
    if not st or not st.get("has_fix"):
        return None
    fix_time = datetime.fromtimestamp(st["updated"], timezone.utc).isoformat()
    # HDOP -> an ESTIMATED horizontal accuracy in metres. Carried through only
    # when the firmware actually reported HDOP; absent -> None, never invented.
    hdop = st.get("hdop")
    accuracy_m = (hdop * NOMINAL_UERE_M
                  if isinstance(hdop, (int, float)) else None)
    alt = st.get("alt_m")
    altitude_m = float(alt) if isinstance(alt, (int, float)) else None
    return GpsFix(lat=st["lat"], lon=st["lng"], source="tracker_gps",
                  sats=st.get("sats"), fix_quality=st.get("fix"),
                  accuracy_m=accuracy_m, altitude_m=altitude_m, fix_time=fix_time)


def accuracy_label(fix: Optional[GpsFix]) -> Optional[str]:
    """A short, HONEST accuracy string for display, or ``None`` when there's no
    estimate (so callers show nothing rather than a fabricated number).

    Always spells out that the figure is derived from HDOP, not measured —
    ``"~±3m (est. from HDOP)"`` — so an operator judging a fix is never misled
    into reading it as a receiver-reported CEP."""
    if fix is None or fix.accuracy_m is None:
        return None
    return f"~±{fix.accuracy_m:.0f}m (est. from HDOP)"


def classify_fix(fix: Optional[GpsFix]) -> str:
    """How much to TRUST a fix before stamping a node's location:
      ``live`` — actively tracking satellites; this is where the medic is NOW.
      ``held`` — a fix is flagged but 0 satellites are tracked: the receiver is
                 COASTING on its last lock. The position may be where you WERE,
                 not where you are — confirm on the map or recalibrate first.
      ``none`` — no usable position.
    (The Tracker reports satellites-USED-in-fix, so 0 sats + a fix flag = coasting;
    ``fix_time`` can't distinguish this because the splitter rewrites it every cycle.)"""
    if fix is None or not fix.has_fix or (fix.fix_quality or 0) < 1:
        return "none"
    return "live" if (fix.sats or 0) > 0 else "held"


def fix_trust(fix: Optional[GpsFix]) -> dict:
    """A plain-language verdict for the GPS-confirm screen:
    ``{level, ok, title, detail}`` — ``ok`` True only for a live fix."""
    level = classify_fix(fix)
    if level == "live":
        n = fix.sats or 0
        return {"level": level, "ok": True,
                "title": f"Live GPS — {n} satellite{'' if n == 1 else 's'}",
                "detail": "Actively tracking. This is where the medic is right now."}
    if level == "held":
        return {"level": level, "ok": False,
                "title": "Last location — not currently tracking",
                "detail": "The receiver is coasting on its last lock. This may be "
                          "where you WERE, not where you are. Check the map: if "
                          "you've moved, Recalibrate outside or enter coordinates."}
    return {"level": level, "ok": False,
            "title": "No GPS fix",
            "detail": "No satellite position. Recalibrate outside (needs sky view) "
                      "or enter the coordinates manually."}


#: Delay (seconds) before the single retry after a transient fetch failure.
#: Long enough to ride out a network blip / not trip Nominatim rate-limiting.
GEOCODE_RETRY_DELAY_S = 1.2


def geocode_address(address: str,
                    fetch: Optional[Callable[[str], str]] = None,
                    timeout: float = 8.0) -> Optional[dict]:
    """Forward-geocode a free-text address to ``{lat, lon, name}`` via OSM
    Nominatim — for the field operator who knows an ADDRESS, not coordinates
    (populated areas). NEEDS INTERNET; returns None when offline, on a bad
    response, or no match (the operator then falls back to entering lat/lon, which
    is what regional/unpopulated sites need anyway). *fetch* is injected for tests.
    Nominatim's fair-use is fine for occasional one-off placement lookups.

    A single *transient* fetch failure (network blip, timeout, HTTP error) is
    retried ONCE after a short delay so a valid address isn't falsely reported as
    "not found". A successful-but-empty response is a genuine miss and returns
    None immediately (no retry — don't hammer Nominatim for a real no-match)."""
    address = (address or "").strip()
    if not address:
        return None
    if fetch is None:
        def fetch(url: str) -> str:
            req = urllib.request.Request(
                url, headers={"User-Agent": GEOCODE_USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q="
           + urllib.parse.quote(address))

    # The fetch (network/HTTP) is the only step we retry: a transient exception
    # here is a blip, not a verdict. Parsing an empty/garbage body is separate —
    # that's a real answer from Nominatim, so we don't retry it.
    body = None
    for attempt in range(2):
        try:
            body = fetch(url)
            break
        except Exception:
            if attempt == 0:
                time.sleep(GEOCODE_RETRY_DELAY_S)   # ride out the blip, retry once
                continue
            return None   # second failure: give up, report not found
    try:
        data = json.loads(body)
        top = data[0]        # empty list -> IndexError -> genuine no-match (no retry)
        return {"lat": float(top["lat"]), "lon": float(top["lon"]),
                "name": top.get("display_name", address)}
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float,
                    fetch: Optional[Callable[[str], str]] = None,
                    timeout: float = 8.0) -> Optional[str]:
    """Reverse-geocode coordinates to a human ADDRESS string via OSM Nominatim —
    the sanity check on a node's location ("is this the right street?") before it's
    written to the birth certificate. NEEDS INTERNET; returns None offline / on a
    bad response (the operator then judges by the map pin + coordinates alone).
    *fetch* is injected for tests."""
    try:
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return None
    if fetch is None:
        def fetch(url: str) -> str:
            req = urllib.request.Request(
                url, headers={"User-Agent": GEOCODE_USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
    url = ("https://nominatim.openstreetmap.org/reverse?format=json&zoom=18"
           f"&lat={lat:.6f}&lon={lon:.6f}")
    try:
        data = json.loads(fetch(url))
        name = data.get("display_name")
        return name or None
    except Exception:
        return None


def splitter_gps_reader(path: str = SPLITTER_STATE, max_age_s: float = 30.0
                        ) -> Callable[[], Optional[Tuple[float, float]]]:
    """A ``read_gps``-compatible reader (``() -> (lat, lon) | None``) sourced from
    the Tracker's GPS via the splitter. Drop into ``read_gps``, map centring, or the
    birth cert wherever a ``gps_reader`` is accepted."""
    def reader() -> Optional[Tuple[float, float]]:
        st = read_splitter_state(path, max_age_s)
        if st and st.get("has_fix"):
            return (st["lat"], st["lng"])
        return None
    return reader


# --- location privacy --------------------------------------------------------
# A node's EXACT coordinates belong to its builder only (birth cert, servicing,
# street-detail maps). What's ever shared publicly is a FUZZED pin: enough for
# "there's a working node in this area", useless for finding the hardware.

FUZZ_RADIUS_M = 800.0

#: The RTNode firmware applies its OWN offset on top of whatever coordinates it
#: is given, when its "Randomize Offset" setting is on (we set advert_jitter=1
#: by default in workflows/rtnode_portal.py).
#:
#: VERIFIED against the upstream source this firmware is forked from,
#: jrl290/RTNode-HeltecV4 (read 2026-08-04): "the advertised coordinates are
#: shifted by a deterministic per-device offset of approximately half a
#: kilometre ... The exact stored coordinates are not changed, and the offset is
#: stable across announces so the pin doesn't move around."
#:
#: DETERMINISTIC is the word that matters. Our own fuzz is deterministic per
#: node for a specific reason — an offset that re-rolled each announce could be
#: averaged away by an observer watching long enough. Had the firmware's been
#: random per announce, stacking it on ours would have handed that attack back.
#: It isn't, so the two layers compose safely: both stable, both bounded.
FIRMWARE_JITTER_M = 500.0


def public_pin_radius_m(firmware_jitter: bool = True) -> float:
    """How far the PUBLIC pin can be from the truth, in metres.

    Two independent offsets stack when the firmware's own randomisation is on,
    so anything that tells the operator "accurate to within X" must say the
    total, not just ours. Getting this wrong understates the displacement by
    almost a kilometre — which matters in the other direction too: someone
    reading the public map should not expect to walk to the pin and find
    hardware.
    """
    return FUZZ_RADIUS_M + (FIRMWARE_JITTER_M if firmware_jitter else 0.0)


def fuzz_location(lat: float, lon: float, node_key: str,
                  radius_m: float = FUZZ_RADIUS_M) -> Tuple[float, float, float]:
    """A privacy-fuzzed public position: (lat, lon, radius_m).

    The offset is DETERMINISTIC per node (seeded by *node_key*, e.g. the
    destination hash): the same fake position every time. This matters — a
    random offset per announce could be averaged away by an observer to
    recover the true location. It is also never centred on the real point
    (30-100% of the radius out), so the true position isn't at the middle of
    the advertised circle."""
    import hashlib
    import math
    digest = hashlib.sha256(f"{node_key}:location-fuzz".encode()).digest()
    angle = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF * 2 * math.pi
    frac = 0.3 + int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF * 0.7
    dist = radius_m * frac
    dlat = (dist * math.cos(angle)) / 111_320.0
    dlon = (dist * math.sin(angle)) / (111_320.0 *
                                       max(0.05, math.cos(math.radians(lat))))
    return (lat + dlat, lon + dlon, radius_m)


def format_coord(deg: float) -> str:
    """Signed decimal degrees, 6 dp (~0.1 m resolution)."""
    return f"{deg:.6f}"


def maps_url(lat: float, lon: float, provider: str = "google") -> str:
    """A turn-by-turn directions deep link to the coordinates."""
    la, lo = format_coord(lat), format_coord(lon)
    if provider == "apple":
        return f"https://maps.apple.com/?daddr={la},{lo}"
    return f"https://www.google.com/maps/dir/?api=1&destination={la},{lo}"


def navigation_links(lat: float, lon: float) -> dict:
    return {
        "google": maps_url(lat, lon, "google"),
        "apple": maps_url(lat, lon, "apple"),
        "raw": f"{format_coord(lat)}, {format_coord(lon)}",
    }
