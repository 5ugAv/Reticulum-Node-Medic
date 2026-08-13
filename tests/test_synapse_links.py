"""SYNAPSE phase 1 — link observations, honestly recorded, honestly persisted.

The link store remembers what every heard link actually looked like: who heard
whom, at what signal, over what distance, on what hardware. Absence is a first-
class value — a field the mesh never reported stays None, never a defaulted
number. Everything here is pure data + arithmetic; no hardware, no third-party
imports.
"""

import json
import math

from monitor.health_beacon import encode, decode
from monitor.movement import haversine_m
from monitor.registry import NodeRegistry
from monitor.synapse_links import (
    DEFAULT_HOP_RANGE_KM,
    FADE_MARGIN_DB,
    HARDWARE_PROFILES,
    LinkObservation,
    LinkObservationStore,
    MAX_HOPS_TO_PROP,
    MIN_RELIABILITY,
    POSITION_EXACT,
    POSITION_FUZZED,
    POSITION_UNKNOWN,
    PROP_SPACING_KM,
    assumed_link_range_km,
    link_distance_km,
    observe_between,
    observe_from_beacon,
    observe_medic_heard,
    profile_for,
)
from monitor.topology import MEDIC_ID
from tests.srcutil import src

NOW = 1_000_000.0

# Two Sampleton rooftops about 1.6 km apart, and the straight-line truth
# computed by the ONE haversine this module is required to reuse.
WREN = (-37.770, 145.000)
IRON = (-37.756, 145.005)
WREN_IRON_KM = haversine_m(*WREN, *IRON) / 1000.0


def _beacon(lora_rssi=None, lora_snr=None):
    return decode(encode(uptime_s=100, heap_kb=140, wifi_rssi_dbm=-70,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2),
                         lora_rssi_dbm=lora_rssi, lora_snr_db=lora_snr))


def _registry():
    r = NodeRegistry()
    r.register("aaaa", name="Wrenhill", lat=WREN[0], lon=WREN[1])
    r.register("bbbb", name="Ironbark", lat=IRON[0], lon=IRON[1])
    r.register("cccc", name="Saltbush")            # known, never located
    r.ingest("aaaa", _beacon(), now=NOW)
    return r


def _splitter_state(**over):
    """A KissGpsSplitter.state() dict, defaulting to a live fix + fresh packet."""
    state = {"lat": WREN[0], "lng": WREN[1], "sats": 8, "fix": 1,
             "has_fix": True, "gps_frames": 40, "gps_seen_at": NOW,
             "last_rssi": -88, "last_snr": 5.5, "packet_heard_at": NOW - 1.0,
             "noise_floor": -110, "airtime": 0.01, "channel_load": 0.02,
             "interference": None, "updated": NOW}
    state.update(over)
    return state


# ---- the record: absence is representable, never defaulted -------------------

def test_unreported_fields_stay_none():
    obs = LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa", observed_at=NOW)
    assert obs.rssi_dbm is None and obs.snr_db is None
    assert obs.distance_km is None
    assert obs.heard_by_board is None and obs.heard_from_board is None
    assert obs.heard_by_antenna_m is None and obs.heard_from_antenna_m is None


def test_position_provenance_defaults_to_unknown():
    obs = LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa", observed_at=NOW)
    assert obs.heard_by_position == POSITION_UNKNOWN
    assert obs.heard_from_position == POSITION_UNKNOWN
    assert {POSITION_EXACT, POSITION_FUZZED, POSITION_UNKNOWN} == \
        {"exact", "fuzzed", "unknown"}


def test_pair_is_order_free_and_absent_when_the_peer_is_unnamed():
    a = LinkObservation(heard_by="aaaa", heard_from="bbbb", observed_at=NOW)
    b = LinkObservation(heard_by="bbbb", heard_from="aaaa", observed_at=NOW)
    assert a.pair() == b.pair() == ("aaaa", "bbbb")
    unnamed = LinkObservation(heard_by="aaaa", heard_from=None, observed_at=NOW)
    assert unnamed.pair() is None


def test_round_trip_preserves_absence():
    obs = LinkObservation(heard_by=MEDIC_ID, heard_from=None, observed_at=NOW,
                          snr_db=3.25, heard_from_position=POSITION_FUZZED)
    again = LinkObservation.from_dict(obs.to_dict())
    assert again == obs
    assert again.rssi_dbm is None and again.heard_from is None
    assert again.heard_from_position == POSITION_FUZZED


def test_from_dict_of_an_older_file_defaults_honestly():
    # A store written before a field existed carries no answer for it.
    again = LinkObservation.from_dict(
        {"heard_by": "aaaa", "heard_from": "bbbb", "observed_at": NOW})
    assert again.rssi_dbm is None
    assert again.heard_by_position == POSITION_UNKNOWN


# ---- distance: one haversine, shared, never a sixth copy ---------------------

def test_no_new_haversine_is_defined_here():
    text = src("monitor/synapse_links.py")
    assert "from monitor.movement import haversine_m" in text
    assert "def haversine" not in text
    assert "6371" not in text          # no fresh copy of the Earth radius


def test_link_distance_matches_the_shared_haversine():
    d = link_distance_km(*WREN, *IRON)
    assert d is not None and math.isclose(d, WREN_IRON_KM)


def test_link_distance_is_none_when_any_coordinate_is_absent():
    assert link_distance_km(None, 145.0, *IRON) is None
    assert link_distance_km(*WREN, IRON[0], None) is None


# ---- the store ---------------------------------------------------------------

def test_store_add_and_query_by_pair():
    store = LinkObservationStore()
    store.add(LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa",
                              observed_at=NOW))
    store.add(LinkObservation(heard_by="aaaa", heard_from=MEDIC_ID,
                              observed_at=NOW + 10))
    store.add(LinkObservation(heard_by=MEDIC_ID, heard_from="bbbb",
                              observed_at=NOW))
    both = store.for_pair("aaaa", MEDIC_ID)
    assert len(both) == 2
    assert store.latest(MEDIC_ID, "aaaa").observed_at == NOW + 10


def test_store_add_ignores_nothing_observations():
    store = LinkObservationStore()
    assert store.add(None) is None
    assert store.observations == []


def test_store_prunes_to_retention_on_add():
    store = LinkObservationStore(retention_s=100)
    store.add(LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa",
                              observed_at=NOW))
    store.add(LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa",
                              observed_at=NOW + 1000))
    assert [o.observed_at for o in store.observations] == [NOW + 1000]


def test_store_save_load_round_trip(tmp_path):
    p = str(tmp_path / "links.json")
    store = LinkObservationStore()
    store.add(LinkObservation(heard_by=MEDIC_ID, heard_from="aaaa",
                              observed_at=NOW, rssi_dbm=-88, snr_db=5.5,
                              heard_from_position=POSITION_EXACT))
    assert store.save(p) is True
    again = LinkObservationStore.load(p)
    assert again.observations == store.observations


def test_store_load_missing_or_corrupt_starts_clean(tmp_path):
    assert LinkObservationStore.load(str(tmp_path / "absent.json")) \
        .observations == []
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert LinkObservationStore.load(str(bad)).observations == []


def test_store_writes_through_the_crash_safe_helper():
    # Persistence must go through monitor.atomic_json — the medic loses power
    # mid-write; a truncated store is silent data loss.
    text = src("monitor/synapse_links.py")
    assert "from monitor.atomic_json import write_json" in text
    assert json is not None            # (json only read here, never written raw)


# ---- ingest: the medic heard a node (splitter state + registry) --------------

def test_medic_heard_records_signal_distance_and_provenance():
    obs = observe_medic_heard("aaaa", _registry(), _splitter_state(), now=NOW)
    assert obs.heard_by == MEDIC_ID and obs.heard_from == "aaaa"
    assert obs.rssi_dbm == -88 and obs.snr_db == 5.5
    assert obs.heard_by_position == POSITION_EXACT       # own GPS fix
    assert obs.heard_from_position == POSITION_EXACT     # birth-certificate coords
    assert math.isclose(obs.distance_km, 0.0, abs_tol=0.01)
    assert obs.heard_from_board == "Heltec32 V4"         # from its beacon
    assert obs.source == "splitter"


def test_medic_heard_stale_stat_frames_are_not_this_packets_signal():
    state = _splitter_state(packet_heard_at=NOW - 120.0)
    obs = observe_medic_heard("aaaa", _registry(), state, now=NOW)
    assert obs.rssi_dbm is None and obs.snr_db is None   # old evidence, not cited


def test_medic_heard_without_a_fix_admits_it():
    state = _splitter_state(lat=None, lng=None, has_fix=False)
    obs = observe_medic_heard("cccc", _registry(), state, now=NOW)
    assert obs.heard_by_position == POSITION_UNKNOWN
    assert obs.heard_from_position == POSITION_UNKNOWN   # never located
    assert obs.distance_km is None


def test_medic_heard_survives_an_absent_splitter_state():
    obs = observe_medic_heard("aaaa", _registry(), None, now=NOW)
    assert obs.rssi_dbm is None and obs.heard_by_position == POSITION_UNKNOWN


# ---- ingest: a node's own beacon (it does not name its peer) -----------------

def test_beacon_link_report_names_no_peer():
    obs = observe_from_beacon("aaaa", _beacon(lora_rssi=-97, lora_snr=-2),
                              now=NOW, registry=_registry())
    assert obs.heard_by == "aaaa" and obs.heard_from is None
    assert obs.rssi_dbm == -97 and obs.snr_db == -2
    assert obs.heard_by_board == "Heltec32 V4"
    assert obs.heard_by_position == POSITION_EXACT
    assert obs.source == "beacon"


def test_beacon_without_link_telemetry_yields_nothing():
    assert observe_from_beacon("aaaa", _beacon(), now=NOW) is None


# ---- ingest: any named pair, enriched from the registry ----------------------

def test_observe_between_fills_positions_boards_and_distance():
    obs = observe_between("aaaa", "bbbb", _registry(), now=NOW)
    assert obs.pair() == ("aaaa", "bbbb")
    assert obs.heard_by_position == POSITION_EXACT
    assert obs.heard_from_position == POSITION_EXACT
    assert math.isclose(obs.distance_km, WREN_IRON_KM)
    assert obs.heard_by_board == "Heltec32 V4"           # aaaa's beacon said so
    assert obs.heard_from_board is None                  # bbbb never reported one
    assert obs.rssi_dbm is None                          # nobody measured this link


def test_observe_between_strangers_still_records_the_pair():
    obs = observe_between("eeee", "ffff", _registry(), now=NOW)
    assert obs.pair() == ("eeee", "ffff")
    assert obs.heard_by_position == POSITION_UNKNOWN
    assert obs.distance_km is None


# ---- hardware profiles: named assumptions, waiting to be overruled -----------

def test_the_rooftop_assumption_is_five_km():
    assert DEFAULT_HOP_RANGE_KM == 5.0


def test_the_planning_constants_hold_their_briefed_values():
    assert MAX_HOPS_TO_PROP == 10
    assert MIN_RELIABILITY == 0.80
    assert FADE_MARGIN_DB == 10
    assert PROP_SPACING_KM == 100


def test_assumptions_declare_themselves_judgement_not_physics():
    text = src("monitor/synapse_links.py")
    assert "engineering judgement" in text
    assert "not physics" in text
    # and they must point at the learned model that overrules them
    assert "learned" in text.lower()


def test_profile_for_a_known_board():
    assert "Heltec32 V4" in HARDWARE_PROFILES
    p = profile_for("Heltec32 V4")
    assert p.assumed_range_km == DEFAULT_HOP_RANGE_KM


def test_profile_for_an_unknown_board_falls_back_and_says_so():
    p = profile_for("Mystery Radio 9000")
    assert p.assumed_range_km == DEFAULT_HOP_RANGE_KM
    assert "default" in p.note.lower()


def test_a_link_is_assumed_no_longer_than_its_weaker_end():
    a = HARDWARE_PROFILES["Heltec32 V4"]
    assert assumed_link_range_km(a.board, a.board) == a.assumed_range_km
    assert assumed_link_range_km(a.board, None) == \
        min(a.assumed_range_km, DEFAULT_HOP_RANGE_KM)
