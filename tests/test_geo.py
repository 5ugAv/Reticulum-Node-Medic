import pytest

from monitor.geo import (
    GpsFix,
    read_gps,
    format_coord,
    maps_url,
    navigation_links,
)


def test_read_gps_with_fix():
    fix = read_gps(reader=lambda: (-37.814, 144.963))
    assert isinstance(fix, GpsFix)
    assert fix.has_fix
    assert fix.lat == -37.814
    assert fix.lon == 144.963
    assert fix.source == "pi_gps"


def test_read_gps_no_fix_returns_none():
    assert read_gps(reader=lambda: None) is None


def test_read_gps_reader_error_returns_none():
    def boom():
        raise OSError("no gpsd")
    assert read_gps(reader=boom) is None


def test_format_coord_six_decimals_signed():
    assert format_coord(-37.814) == "-37.814000"
    assert format_coord(144.963) == "144.963000"


def test_maps_url_google_directions_contains_coords():
    url = maps_url(-37.814, 144.963, provider="google")
    assert "google.com" in url
    assert "-37.814000" in url and "144.963000" in url


def test_maps_url_apple():
    url = maps_url(-37.814, 144.963, provider="apple")
    assert "maps.apple.com" in url
    assert "-37.814000" in url


def test_navigation_links_has_all_forms():
    links = navigation_links(-37.814, 144.963)
    assert "google.com" in links["google"]
    assert "apple.com" in links["apple"]
    assert links["raw"] == "-37.814000, 144.963000"


# ---- location privacy (fuzzed public pin) ---------------------------------

def test_fuzz_is_deterministic_per_node():
    from monitor.geo import fuzz_location
    a = fuzz_location(-37.79, 144.96, "99aabbcc")
    b = fuzz_location(-37.79, 144.96, "99aabbcc")
    assert a == b                       # same node -> same fake pin, forever
    c = fuzz_location(-37.79, 144.96, "deadbeef")
    assert (a[0], a[1]) != (c[0], c[1])  # different node -> different offset


def test_fuzz_offsets_within_radius_but_never_at_centre():
    import math
    from monitor.geo import fuzz_location, FUZZ_RADIUS_M
    for key in ("n1", "n2", "n3", "n4", "n5"):
        flat, flon, r = fuzz_location(-37.79, 144.96, key)
        assert r == FUZZ_RADIUS_M
        dlat_m = (flat - -37.79) * 111_320.0
        dlon_m = (flon - 144.96) * 111_320.0 * math.cos(math.radians(-37.79))
        dist = math.hypot(dlat_m, dlon_m)
        assert 0.25 * r <= dist <= r     # offset real, bounded, off-centre


# ---- GPS fix freshness (confirm-before-commit safety) -----------------------

def test_classify_fix_live_held_none():
    from monitor.geo import GpsFix, classify_fix
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1)) == "live"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=1)) == "held"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=0)) == "none"
    assert classify_fix(GpsFix(lat=-37.7, lon=145.0, sats=5, fix_quality=0)) == "none"
    assert classify_fix(None) == "none"


def test_fix_trust_verdicts_guard_stale_positions():
    from monitor.geo import GpsFix, fix_trust
    live = fix_trust(GpsFix(lat=-37.7, lon=145.0, sats=8, fix_quality=1))
    assert live["level"] == "live" and live["ok"] is True

    held = fix_trust(GpsFix(lat=-37.7, lon=145.0, sats=0, fix_quality=1))
    assert held["level"] == "held" and held["ok"] is False
    assert "where you were" in held["detail"].lower()   # warns it may be stale

    none = fix_trust(None)
    assert none["level"] == "none" and none["ok"] is False


# ---- address geocoding (field operator knows an address, not coords) ---------

def test_geocode_address_parses_nominatim():
    from monitor.geo import geocode_address
    fake = ('[{"lat": "-37.5106", "lon": "145.5107", '
            '"display_name": "12 Wattle St, Sampleton VIC, Australia"}]')
    r = geocode_address("12 Wattle St Sampleton", fetch=lambda url: fake)
    assert r["lat"] == -37.5106 and r["lon"] == 145.5107
    assert "Sampleton" in r["name"]
    # the query is URL-encoded into the request
    seen = {}
    geocode_address("12 Wattle St, Sampleton", fetch=lambda url: seen.setdefault("u", url) or fake)
    assert "12" in seen["u"] and "Wattle" in seen["u"]


def test_geocode_address_none_on_no_match_offline_or_empty():
    from monitor.geo import geocode_address
    assert geocode_address("nowhere at all", fetch=lambda url: "[]") is None   # no match
    def boom(url):
        raise OSError("offline")
    assert geocode_address("anywhere", fetch=boom) is None                     # offline
    assert geocode_address("", fetch=lambda url: "[]") is None                 # empty
    assert geocode_address("x", fetch=lambda url: "not json") is None          # bad body


def test_geocode_address_retries_once_on_transient_failure(monkeypatch):
    """A transient fetch exception (network blip / timeout) must retry ONCE and
    succeed on the second attempt — a valid address shouldn't falsely fail."""
    import monitor.geo as geo
    slept = []
    monkeypatch.setattr(geo.time, "sleep", lambda s: slept.append(s))

    fake = ('[{"lat": "-37.5106", "lon": "145.5107", '
            '"display_name": "12 Wattle St, Sampleton VIC, Australia"}]')
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow response")
        return fake

    r = geo.geocode_address("12 Wattle St Sampleton", fetch=flaky)
    assert r is not None and r["lat"] == -37.5106 and r["lon"] == 145.5107
    assert calls["n"] == 2          # retried exactly once
    assert slept == [geo.GEOCODE_RETRY_DELAY_S]   # short delay before the retry


def test_geocode_address_gives_up_after_second_failure(monkeypatch):
    """Two consecutive fetch exceptions -> None, and only ONE retry (2 calls)."""
    import monitor.geo as geo
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_boom(url):
        calls["n"] += 1
        raise OSError("offline")

    assert geo.geocode_address("anywhere", fetch=always_boom) is None
    assert calls["n"] == 2          # initial + one retry, no more


def test_geocode_address_no_retry_on_empty_result(monkeypatch):
    """A successful-but-empty response is a genuine miss: return None with only
    ONE fetch call — don't hammer Nominatim for a real no-match."""
    import monitor.geo as geo
    slept = []
    monkeypatch.setattr(geo.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def empty(url):
        calls["n"] += 1
        return "[]"

    assert geo.geocode_address("nowhere at all", fetch=empty) is None
    assert calls["n"] == 1          # no retry on a legitimate empty result
    assert slept == []              # and no delay was taken


def test_the_public_pin_radius_counts_BOTH_offsets():
    """The firmware adds its own ~500 m on top of the medic's ~800 m.

    Verified against upstream jrl290/RTNode-HeltecV4 (2026-08-04): the
    advertised coordinates are shifted by "a deterministic per-device offset of
    approximately half a kilometre", applied on top of the stored ones. Anything
    telling the operator how far the public pin sits from the hardware must add
    both, or it understates the displacement by almost a kilometre.
    """
    from monitor.geo import (FIRMWARE_JITTER_M, FUZZ_RADIUS_M,
                             public_pin_radius_m)
    assert public_pin_radius_m(True) == FUZZ_RADIUS_M + FIRMWARE_JITTER_M
    assert public_pin_radius_m(False) == FUZZ_RADIUS_M
    assert public_pin_radius_m(True) > 1000, "a public pin is >1 km out"


def test_the_fuzz_seed_is_stable_so_the_proof_of_work_is_not_rerun():
    """Upstream caches the LXMF stamp (cost 14) and re-runs the proof-of-work
    only when advertised parameters change. A fuzz that moved between births
    would force that work again — and, worse, re-rolling could be averaged back
    to the true position."""
    from monitor.geo import fuzz_location
    a = fuzz_location(-37.79, 144.96, "Rooftop-East")
    b = fuzz_location(-37.79, 144.96, "Rooftop-East")
    assert a == b, "same node must always advertise the same fuzzed pin"
    assert fuzz_location(-37.79, 144.96, "Rooftop-West") != a


# --- a fix is not automatically a PLACE (2026-08-07) ------------------------
# Demonstrated live by turning the Tracker's patch antenna to face the ground:
# satellites went 10 -> 0 inside a minute, while fix stayed 1 and the receiver
# kept serving its LAST position. classify_fix calls that "held". Three
# consumers were taking any fix with lat/lon and never asking.

def _held():
    from monitor.geo import GpsFix
    return GpsFix(lat=-37.7, lon=145.0, source="tracker_gps", sats=0,
                  fix_quality=1, fix_time="2026-08-07T00:00:00+00:00")


def _live():
    from monitor.geo import GpsFix
    return GpsFix(lat=-37.7, lon=145.0, source="tracker_gps", sats=9,
                  fix_quality=1, fix_time="2026-08-07T00:00:00+00:00")


def test_the_coasting_case_is_classified_held_not_live():
    from monitor.geo import classify_fix
    assert classify_fix(_held()) == "held"
    assert classify_fix(_live()) == "live"
    assert classify_fix(None) == "none"


def test_triage_refuses_to_stamp_a_node_from_a_coasting_fix():
    """Writing a frozen position onto a node puts it on the map in the wrong
    place — which is how a repair crew is sent to where the medic USED to be."""
    src = open("ui/screens/triage_screen.py").read()
    save = src[src.index("def _save(self"):]
    save = save[:save.index("\n    def ")]
    assert "classify_fix" in save
    assert 'trust == "held"' in save
    idx_guard = save.index('trust == "held"')
    idx_save = save.index("Location saved")
    assert idx_guard < idx_save, "the held check must come before the save copy"


def test_the_movement_detector_ignores_a_frozen_position():
    """Worse than no reading: a coasting fix looks like 'definitely stationary'
    while the medic could be in a car, and the re-acquire afterwards lands as
    one huge jump that reads as movement nothing observed."""
    src = open("ui/app.py").read()
    blk = src[src.index("load_auto_backpack"):]
    blk = blk[:blk.index("def _auto_backpack")]
    assert 'classify_fix(fix) != "live"' in blk


def test_the_movement_anchor_requires_a_measured_position():
    src = open("ui/app.py").read()
    blk = src[src.index("def _reanchor_movement"):]
    blk = blk[:blk.index("\n    def ", 10)]
    assert 'classify_fix(fix) == "live"' in blk


# --- position lives in the moment, not on the card (2026-08-07) -------------
# Operator: "the NodeMedic can just access the GPS at that moment and centralize
# the map to where the NodeMedic is currently situated. It doesn't need to have
# a permanent home address."
#
# Nothing persists a position by design — but the splitter's handoff file was
# written to the SD card, so the medic's most recent position survived power-off
# on removable media.

def test_the_state_file_lives_in_ram_not_on_the_card():
    from monitor.geo import SPLITTER_STATE
    assert SPLITTER_STATE.startswith("/dev/shm/"), (
        "the medic's position is being written to durable storage again")


def test_the_old_on_disk_path_is_still_READ_during_the_changeover():
    """The systemd unit needs root to update, so the code ships first. Without
    a fallback the GPS would go blind in the gap between the two changes."""
    from monitor.geo import LEGACY_SPLITTER_STATE
    assert LEGACY_SPLITTER_STATE.endswith("gps_state.json")
    src = open("monitor/geo.py").read()
    fn = src[src.index("def read_splitter_state"):src.index("def read_splitter_fix")]
    assert "LEGACY_SPLITTER_STATE" in fn


def test_the_state_file_is_not_world_readable():
    """/dev/shm is world-readable by default, and this file says where the medic
    is right now."""
    src = open("monitor/serial_splitter.py").read()
    fn = src[src.index("def _write_state"):src.index("def run(")]
    assert "0o600" in fn


def test_an_explicit_path_is_never_silently_swapped_for_the_legacy_one(tmp_path):
    """Caught on the medic, where the legacy file exists: a deliberately-missing
    path returned real GPS state through the fallback. An explicit path must be
    honoured exactly, or every injected path in the suite is a lie."""
    from monitor.geo import read_splitter_state
    missing = str(tmp_path / "definitely-not-here.json")
    assert read_splitter_state(path=missing) is None


def test_only_ONE_module_defines_where_the_state_file_lives():
    """Four modules each held their own copy of the path. A migration that
    changed some and not others would have left Triage and Self Diagnose reading
    a file that had stopped being written — silently, with stale positions.
    Caught before deploying, not after."""
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    offenders = []
    for f in list(root.glob("monitor/*.py")) + list(root.glob("diagnostics/*.py")):
        txt = f.read_text()
        if 'expanduser("~/gps_state.json")' in txt and "LEGACY" not in txt:
            offenders.append(f.name)
    assert not offenders, f"these still define the path themselves: {offenders}"


# --- the fuzz has to be a fuzz (audit, 2026-09-09) --------------------------

def test_the_offset_is_not_computable_from_public_information():
    """It used to be. The seed was the node's destination hash — printed in
    every announce — run through a published formula in an open-source repo,
    so anyone seeing the pin could subtract the offset back off. The
    advertised radius was 800 m and the real protection was ZERO, while every
    screen and the README promised "never the real one"."""
    from monitor.geo import fuzz_location
    lat, lon, key = -37.79, 144.96, "99aabbcc"
    a = fuzz_location(lat, lon, key, salt=b"a" * 32)
    b = fuzz_location(lat, lon, key, salt=b"b" * 32)
    assert (a[0], a[1]) != (b[0], b[1]), \
        "two medics must not land on the same offset for the same node"


def test_it_is_still_deterministic_for_one_medic():
    """A pin that wanders can be averaged away — that is why the offset is
    per-node and stable, and this fix must not have traded that away."""
    from monitor.geo import fuzz_location
    s = b"c" * 32
    assert fuzz_location(-37.79, 144.96, "99aabbcc", salt=s) == \
        fuzz_location(-37.79, 144.96, "99aabbcc", salt=s)


def test_the_salt_is_generated_once_and_kept_private(tmp_path):
    from monitor.geo import fuzz_salt
    p = str(tmp_path / "sub" / "location_salt")
    first = fuzz_salt(p)
    assert len(first) >= 16
    assert fuzz_salt(p) == first, "a new salt every read would move the pin"
    import os
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_a_read_only_home_still_publishes(tmp_path, monkeypatch):
    """A crash here must never stop a node publishing — the in-memory
    fallback is worse for pin stability and still unsolvable from outside."""
    from monitor import geo
    monkeypatch.setattr(geo.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    salt = geo.fuzz_salt(str(tmp_path / "nope" / "salt"))
    assert len(salt) >= 16
