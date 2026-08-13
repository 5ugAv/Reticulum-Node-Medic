"""SYNAPSE phase 2 — the learned range model, honest about how little it knows.

The estimator turns the phase-1 link store into ONE range number for
placement, and it must fail toward the labelled default rather than toward a
confident-sounding fiction. The break-lens corpus cases (C2, L1-L7) are pinned
here as real tests: thin data, pseudo-replication, the exact percentile
convention, zero variance, survivorship, the contraction loop, and the
log10/extrapolation guards.
"""

import calendar
import math

from monitor.placement import REACH_PERCENTILE
from monitor.synapse_links import (
    DEFAULT_HOP_RANGE_KM,
    LinkObservation,
    LinkObservationStore,
    POSITION_EXACT,
    POSITION_FUZZED,
)
from monitor.synapse_range import (
    LinkFailure,
    MIN_DISTINCT_LINKS,
    PLACEMENT_PERCENTILE,
    PROJECTION_CAP,
    estimate_range,
    nearest_rank,
    walk_failures,
)
from tests.srcutil import src

NOW = 1_000_000.0
#: 2026-08-13 12:00 UTC — the day the operator's spec quotes for a recorded loss.
AUG_13 = calendar.timegm((2026, 8, 13, 12, 0, 0, 0, 0, 0))


def _obs(a, b, km, at=NOW, rssi=None, a_pos=POSITION_EXACT,
         b_pos=POSITION_EXACT):
    return LinkObservation(heard_by=a, heard_from=b, observed_at=at,
                           rssi_dbm=rssi, distance_km=km,
                           heard_by_position=a_pos, heard_from_position=b_pos)


def _store(distances, **obs_kw):
    """A store with one exact-exact link per distance, each a distinct pair."""
    store = LinkObservationStore()
    for i, km in enumerate(distances):
        store.add(_obs(f"n{i}", f"m{i}", km, **obs_kw))
    return store


# ---- the operator's percentile decision --------------------------------------

def test_two_questions_two_numbers_both_named():
    # Placement plans on the 70th (operator, 2026-08-13); the SCAN display
    # halo keeps its 90th. Different questions, different constants.
    assert PLACEMENT_PERCENTILE == 70
    assert REACH_PERCENTILE == 90
    text = src("monitor/synapse_range.py")
    assert "operator" in text and "2026-08-13" in text


# ---- L3: the percentile convention, pinned at exactly n=5 --------------------

def test_nearest_rank_at_exactly_five_samples():
    # Nearest-rank: ceil(p/100 * n)-th smallest. For n=5 at the 70th that is
    # the 4th smallest — a distance some link actually achieved, never an
    # interpolated distance nobody ever measured.
    assert nearest_rank([1.0, 2.0, 3.0, 4.0, 5.0], 70) == 4.0
    assert nearest_rank([5.0, 1.0, 3.0, 2.0, 4.0], 70) == 4.0   # order-free
    assert nearest_rank([1.0, 2.0, 3.0], 100) == 3.0
    assert nearest_rank([1.0, 2.0, 3.0], 1) == 1.0
    assert "nearest-rank" in src("monitor/synapse_range.py")


# ---- L1: four links are not five ---------------------------------------------

def test_four_distinct_links_keep_the_default():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0]))
    assert est.source == "default"
    assert est.range_km == DEFAULT_HOP_RANGE_KM
    assert est.distinct_links == 4 and MIN_DISTINCT_LINKS == 5
    assert est.confidence == "none"
    assert any("5" in c for c in est.cautions)      # says what it is waiting for


def test_an_empty_store_is_the_default_and_says_so():
    est = estimate_range(LinkObservationStore())
    assert est.source == "default" and est.range_km == DEFAULT_HOP_RANGE_KM
    assert est.distinct_links == 0 and est.confidence == "none"


def test_the_default_can_be_the_hardware_pair_assumption():
    est = estimate_range(LinkObservationStore(), default_km=3.3)
    assert est.range_km == 3.3 and est.source == "default"


# ---- L2: pseudo-replication --------------------------------------------------

def test_five_observations_of_one_link_are_one_link():
    store = LinkObservationStore()
    for i in range(5):
        store.add(_obs("aaaa", "bbbb", 2.0, at=NOW + i))
    est = estimate_range(store)
    assert est.distinct_links == 1
    assert est.observation_count == 5
    assert est.source == "default" and est.range_km == DEFAULT_HOP_RANGE_KM


def test_a_chatty_link_does_not_outvote_the_quiet_ones():
    store = _store([1.0, 2.0, 3.0, 4.0, 5.0])
    for i in range(50):                       # one pair heard 50 more times
        store.add(_obs("n0", "m0", 1.0, at=NOW + i))
    est = estimate_range(store)
    assert est.range_km == 4.0                # 70th over LINKS, not observations


# ---- the measured estimate ---------------------------------------------------

def test_five_links_earn_a_measured_estimate():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert est.source == "measured"
    assert est.range_km == 4.0                # nearest-rank 70th of five
    assert est.distinct_links == 5


# ---- L4: zero variance -------------------------------------------------------

def test_zero_variance_is_a_number_and_a_warning_not_a_crash():
    est = estimate_range(_store([2.0, 2.0, 2.0, 2.0, 2.0]))
    assert est.source == "measured" and est.range_km == 2.0
    assert est.variance_km2 == 0.0            # reported, never divided by
    assert est.confidence != "high"           # sameness is not certainty
    assert any("spread is zero" in c for c in est.cautions)


# ---- L5: survivorship --------------------------------------------------------

def test_success_only_data_carries_the_survivorship_caveat():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert est.survivorship is True
    assert est.confidence == "low"            # nothing bounds the far side
    assert any("worked" in c for c in est.cautions)


def test_a_loss_beyond_the_estimate_bounds_the_tail():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]),
                         failures=[LinkFailure(distance_km=6.0,
                                               observed_at=AUG_13)])
    assert est.survivorship is False
    assert est.contradictions == []
    assert est.confidence == "medium"


def test_many_consistent_bounded_links_earn_high_confidence():
    distances = [2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9]
    est = estimate_range(_store(distances),
                         failures=[LinkFailure(distance_km=4.0,
                                               observed_at=AUG_13)])
    assert est.confidence == "high"
    assert est.range_km == 2.6                # nearest-rank 70th of ten


# ---- negative evidence inside the estimate -----------------------------------

def test_a_closer_loss_is_surfaced_with_its_date_and_pulls_confidence():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]),
                         failures=[LinkFailure(distance_km=3.1,
                                               observed_at=AUG_13)])
    assert est.confidence == "low"
    joined = " ".join(est.contradictions)
    assert "3.1 km" in joined and "2026-08-13" in joined


def test_a_loss_inside_the_default_is_surfaced_too():
    est = estimate_range(_store([1.0]),       # thin data -> 5 km default
                         failures=[LinkFailure(distance_km=3.1,
                                               observed_at=AUG_13)])
    assert est.source == "default"
    assert any("3.1 km" in c for c in est.contradictions)


# ---- L6: the contraction loop ------------------------------------------------

def test_losses_label_the_conflict_but_never_ratchet_the_number():
    # Contracting the estimate on every closer loss, then re-judging losses
    # against the smaller number, ratchets to zero. The number stands; the
    # conflict is labelled; more data resolves it, not the estimator.
    store = _store([1.0, 2.0, 3.0, 4.0, 5.0])
    bare = estimate_range(store)
    one = estimate_range(store, failures=[
        LinkFailure(distance_km=3.1, observed_at=AUG_13)])
    two = estimate_range(store, failures=[
        LinkFailure(distance_km=3.1, observed_at=AUG_13),
        LinkFailure(distance_km=2.5, observed_at=AUG_13)])
    assert bare.range_km == one.range_km == two.range_km
    assert len(two.contradictions) == 2


# ---- the fuzz rule -----------------------------------------------------------

def test_fuzzed_pairs_too_close_to_trust_are_excluded():
    store = _store([1.5, 2.0, 3.0, 4.0])                  # four exact links
    store.add(_obs("fz", "zz", 1.0, b_pos=POSITION_FUZZED))   # < 2x 0.8 km
    est = estimate_range(store)
    assert est.excluded_fuzzed == 1
    assert est.distinct_links == 4                        # so: still default
    assert est.source == "default"


def test_fuzzed_pairs_far_enough_count_with_inflated_variance():
    exact = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]))
    store = _store([1.0, 2.0, 3.0, 4.0])
    store.add(_obs("fz", "zz", 5.0, b_pos=POSITION_FUZZED))   # > 2x 0.8 km
    fuzzed = estimate_range(store)
    assert fuzzed.source == "measured" and fuzzed.distinct_links == 5
    assert fuzzed.excluded_fuzzed == 0
    assert fuzzed.variance_km2 > exact.variance_km2


# ---- L7: the signal projection guards ----------------------------------------

def test_a_colocated_zero_distance_reading_cannot_crash_the_projection():
    store = _store([1.0, 2.0, 3.0, 4.0, 5.0])
    store.add(_obs("aaaa", "bbbb", 0.0, rssi=-40))        # log10(0) bait
    est = estimate_range(store)                           # must not raise
    assert est.source == "measured"


def test_projection_is_capped_at_1p5x_the_farthest_link_and_labelled():
    # A shallow measured slope projects absurdly far; the cap holds it to
    # 1.5x the farthest measured link and says the number is an extrapolation.
    store = LinkObservationStore()
    store.add(_obs("a1", "b1", 1.0, rssi=-60))
    store.add(_obs("a2", "b2", 2.0, rssi=-61))
    store.add(_obs("a3", "b3", 3.0, rssi=-61))
    store.add(_obs("a4", "b4", 4.0, rssi=-62))
    store.add(_obs("a5", "b5", 5.0, rssi=-63))
    est = estimate_range(store)
    assert PROJECTION_CAP == 1.5
    assert est.projected_reach_km == 1.5 * 5.0
    joined = " ".join(est.cautions).lower()
    assert "extrapolat" in joined
    assert est.range_km == 4.0                # projection never sets the range


def test_projection_refuses_a_signal_that_improves_with_distance():
    store = LinkObservationStore()
    store.add(_obs("a1", "b1", 1.0, rssi=-90))
    store.add(_obs("a2", "b2", 2.0, rssi=-80))
    store.add(_obs("a3", "b3", 3.0, rssi=-70))
    est = estimate_range(store)
    assert est.projected_reach_km is None     # nonsense slope: refuse, don't fit


def test_no_signal_readings_mean_no_projection():
    est = estimate_range(_store([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert est.projected_reach_km is None


# ---- the boundary walk feeds the model ---------------------------------------

def test_walk_attempts_become_dated_failures():
    # first_link / boundary-walk sample shape: {lat, lon, km, connected, snr_db}
    attempts = [
        {"lat": -37.77, "lon": 145.0, "km": 5.2, "connected": False,
         "snr_db": None},
        {"lat": -37.76, "lon": 145.0, "km": 4.1, "connected": True,
         "snr_db": -8.0},
        {"lat": -37.75, "lon": 145.0, "km": 3.0, "connected": True,
         "snr_db": 4.5},
    ]
    fails = walk_failures(attempts, observed_at=AUG_13)
    assert [f.distance_km for f in fails] == [5.2]        # only the losses
    assert fails[0].observed_at == AUG_13
    assert fails[0].lat == -37.77


def test_failure_round_trips_through_json_shape():
    f = LinkFailure(distance_km=3.1, observed_at=AUG_13, snr_db=None)
    again = LinkFailure.from_dict(f.to_dict())
    assert again == f and again.snr_db is None


# ---- the module owns its epistemics ------------------------------------------

def test_the_model_calls_its_judgement_judgement():
    text = src("monitor/synapse_range.py")
    assert "judgement" in text
    assert "survivorship" in text.lower()
