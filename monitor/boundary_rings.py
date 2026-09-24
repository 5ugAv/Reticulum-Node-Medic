"""The SCAN boundary-ring layer's pure core — joining the walk's banked
losses to physical devices, one ring per device (2026-09-24 review fixes).

Split out of ``ui/app.py``'s ``_boundary_provider`` closure on 2026-09-24
review so it is directly testable: no Kivy, no ``App.get_running_app()``,
callable with a stub registry (needs only ``.all(now)`` and
``.consolidated_records(now)``) and a plain list of LinkFailure-like
objects — the same "pure core, thin Kivy shim" split every other module in
this package already follows (monitor/boundary_shape.py, monitor/
boundary_walk.py, ...). ``ui/app.py``'s ``_boundary_provider`` is now just
that shim: it supplies the live registry, the banked failures, and the
clock, and hands them here.

Pure; no third-party imports, no Kivy.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

__all__ = ["BOUNDARY_MAX_AGE_DAYS", "boundary_rings_for"]

#: A boundary loss older than this is stale evidence of today's edge — new
#: foliage, a parked vehicle, a season, can all move where a link actually
#: dies. About a season (2026-09-24 review fix #6, wired through for real —
#: this used to be computed and then never passed to node_boundary, so
#: nothing ever aged out). Engineering judgement, not physics.
BOUNDARY_MAX_AGE_DAYS = 120.0


def _device_keys(members) -> List[str]:
    """Every key (device_id, identity_hash, dst_hash) any member of a
    device group has ever answered to — the same three attrs monitor.
    walk_anchor.candidate_keys reads off a device group's members, read
    directly here because the members list is already in hand (2026-09-24
    review fix #4): no need to ask candidate_keys to re-find the group we
    are already iterating."""
    out: List[str] = []
    for m in members or ():
        for attr in ("device_id", "identity_hash", "dst_hash"):
            v = getattr(m, attr, None)
            if isinstance(v, str) and v.strip() and v.strip() not in out:
                out.append(v.strip())
    return out


def boundary_rings_for(registry, fails: Iterable, now: float,
                       max_age_days: float = BOUNDARY_MAX_AGE_DAYS
                       ) -> List[dict]:
    """The SCAN boundary-ring layer's pure core (2026-09-24 review fixes).

    ONE ring per physical DEVICE (fix #4): built from
    ``registry.consolidated_records(now)`` ONCE — never by calling
    ``monitor.walk_anchor.candidate_keys`` per row inside a loop over
    ``registry.all(now)``, which was O(devices x failures) for every
    redraw. Every member's device_id/identity_hash/dst_hash is folded into
    one key -> device-index lookup up front, so matching a failure's
    ``node_key`` to its device is then a single dict lookup. This is also
    the O(N^2) performance fix — one restructure, both findings.

    A ring is centred on the CONSOLIDATED record's own stamped lat/lon —
    the birth-cert/roster position — NEVER a beacon (fix #1):
    monitor.geo.FUZZ_RADIUS_M deliberately fuzzes an announced position by
    up to 800 m, and using that fuzzed value as a measurement centre would
    invert what the fuzz exists for. No exact position on file -> no ring
    for that node; not an error, just nothing to draw (fix #2, guarded by
    monitor.geo.valid_position — the same check failures are already held
    to). It is coloured by the CONSOLIDATED record's pooled ``status()``,
    never a single aspect row's possibly-stale/beacon-less one.

    Own-identity/own-destination exclusion matches ``registry.all(now)``
    (fix #11): a device group is drawn only when at least one of its
    members' ``dst_hash`` appears in ``registry.all(now)`` — the SAME fold
    ``located_nodes`` and every other SCAN-facing view already uses, never
    a second convention invented here. (``registry.consolidated_records``
    on its own only excludes the medic's own IDENTITY, not its own
    destinations — see monitor/registry.py's ``_device_groups``.)

    A failure with no ``node_key`` (every walk banked before 2026-09-24) is
    excluded, never guessed at — see scripts/repair_walk_failure_keys.py
    for the operator's own one-pass repair of those old lines.

    One bad device (an invalid centre, or node_boundary/boundary_segments
    raising) is skipped and logged (fix #3/#14) — it never blanks every
    other device's ring. *max_age_days* is passed straight through to
    node_boundary (fix #6): nothing here silently leaves it at the
    default.

    Each returned dict is ``{"lat", "lon", "status", "segments",
    "coverage_frac", "total_failures_used"}`` — the last two survive here
    (fix #13) even though SCAN's draw call only reads the first four, so a
    screen can show real coverage next to the toggle instead of the shape
    being computed and thrown away.
    """
    from monitor.boundary_shape import boundary_segments, node_boundary
    from monitor.geo import valid_position

    fails = list(fails or ())
    if not fails:
        return []

    try:
        allowed = {r.dst_hash for r in registry.all(now)}
    except Exception:                                          # noqa: BLE001
        allowed = None            # registry.all is unavailable — do not filter

    try:
        groups = list(registry.consolidated_records(now))
    except Exception:                                          # noqa: BLE001
        print("[boundary] registry.consolidated_records failed; "
              "no rings this pass", flush=True)
        return []

    key_to_group: Dict[str, int] = {}
    for gi, (_consolidated, members) in enumerate(groups):
        for key in _device_keys(members):
            key_to_group.setdefault(key, gi)

    fails_by_group: Dict[int, list] = {}
    for f in fails:
        key = getattr(f, "node_key", None)
        if not key:
            continue           # pre-migration failure, node unknown — excluded
        gi = key_to_group.get(key)
        if gi is None:
            continue
        fails_by_group.setdefault(gi, []).append(f)

    out: List[dict] = []
    for gi, node_fails in fails_by_group.items():
        consolidated, members = groups[gi]
        label = (getattr(consolidated, "name", "") or "").strip() \
            or getattr(consolidated, "dst_hash", "?")
        if allowed is not None and not any(
                getattr(m, "dst_hash", None) in allowed for m in members or ()):
            continue           # the medic's own device — registry.all excludes it too
        lat, lon = getattr(consolidated, "lat", None), \
            getattr(consolidated, "lon", None)
        if lat is None or lon is None:
            print(f"[boundary] no exact position on file for {label!r}; "
                  "ring skipped", flush=True)
            continue
        if not valid_position(lat, lon):
            print(f"[boundary] invalid centre for {label!r}: "
                  f"({lat!r}, {lon!r}); ring skipped", flush=True)
            continue
        try:
            shape = node_boundary(lat, lon, node_fails,
                                  max_age_days=max_age_days, now=now)
            segs = boundary_segments(shape, lat, lon)
        except Exception as e:                                  # noqa: BLE001
            print(f"[boundary] ring build failed for {label!r}: {e}",
                  flush=True)
            continue
        if not segs:
            continue
        out.append({"lat": lat, "lon": lon, "status": consolidated.status(now),
                   "segments": segs,
                   "coverage_frac": shape.get("coverage_frac"),
                   "total_failures_used": shape.get("total_failures_used")})
    return out
