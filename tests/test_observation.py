"""Unit tests for ``monitor.observation`` — the one value type a fact cannot be
stored without. Pure and clock-free (``now`` is always passed in), so these
assert the age/never/impossible policy directly, with no registry in the way.

The behaviours guarded here are the ones the SEEN line's honesty rests on, and
they mirror EXACTLY the registry's long-standing SEEN semantics
(SEEN_CLOCK_SKEW_TOLERANCE_S = 120 s): a benign sub-two-minute backward jitter
stays fresh; a genuine backward step is flagged impossible; never-observed is
the absence of an observation, not an observation of zero.
"""

import pytest

from monitor.observation import (
    Age, Observation, CLOCK_SKEW_TOLERANCE_S, KNOWN_SOURCES,
)

NOW = 1_000_000.0
HOUR = 3600.0


# -- construction ------------------------------------------------------------

def test_at_stamps_a_liveness_sighting_with_none_value():
    obs = Observation.at(NOW, "beacon")
    assert obs.observed_at == NOW
    assert obs.source == "beacon"
    assert obs.value is None            # liveness: only WHEN and HOW matter


def test_full_triple_carries_a_value():
    obs = Observation(value=42, observed_at=NOW, source="http")
    assert (obs.value, obs.observed_at, obs.source) == (42, NOW, "http")


def test_observation_is_frozen():
    obs = Observation.at(NOW, "beacon")
    with pytest.raises(Exception):
        obs.observed_at = NOW + 1       # frozen: no silent mutation


def test_the_documented_sources_are_the_honest_vocabulary():
    for s in ("beacon", "http", "announce", "path-table", "operator",
              "assumed", "legacy"):
        assert s in KNOWN_SOURCES


# -- age_at: the one clock-skew policy ---------------------------------------

def test_positive_age_is_returned_as_measured():
    age = Observation.at(NOW, "beacon").age_at(NOW + 3 * HOUR)
    assert age.seconds == pytest.approx(3 * HOUR)
    assert age.hours == pytest.approx(3.0)
    assert age.impossible is False


def test_zero_age_is_a_real_measurement_not_never():
    age = Observation.at(NOW, "beacon").age_at(NOW)
    assert age.seconds == 0.0
    assert age.impossible is False


def test_tiny_backward_jitter_clamps_to_zero_and_stays_possible():
    # 3 s behind a node heard now: benign jitter, NOT a clock step.
    age = Observation.at(NOW, "beacon").age_at(NOW - 3)
    assert age.seconds == 0.0
    assert age.impossible is False


def test_jitter_exactly_at_the_tolerance_edge_is_still_benign():
    # -120 s is the edge; only MORE than the tolerance is impossible.
    age = Observation.at(NOW, "beacon").age_at(NOW - CLOCK_SKEW_TOLERANCE_S)
    assert age.impossible is False


def test_step_beyond_the_tolerance_is_impossible():
    age = Observation.at(NOW, "beacon").age_at(NOW - CLOCK_SKEW_TOLERANCE_S - 1)
    assert age.seconds == 0.0           # clamped, never negative
    assert age.impossible is True       # ...but flagged: age is unknowable


def test_large_backward_step_is_impossible_not_fresh():
    age = Observation.at(NOW, "beacon").age_at(NOW - 50 * HOUR)
    assert age.seconds == 0.0
    assert age.impossible is True


# -- age_of: the None-safe boundary (never != zero) --------------------------

def test_age_of_none_is_never_not_an_age():
    assert Observation.age_of(None, NOW) is None


def test_age_of_a_real_observation_delegates():
    obs = Observation.at(NOW, "beacon")
    assert Observation.age_of(obs, NOW + HOUR).hours == pytest.approx(1.0)


def test_never_and_zero_are_distinguishable():
    """The whole point: no observation (None) is a different answer from an
    observation whose age happens to be 0.0."""
    zero = Observation.age_of(Observation.at(NOW, "beacon"), NOW)
    never = Observation.age_of(None, NOW)
    assert zero == Age(seconds=0.0, impossible=False)
    assert never is None


# -- is_fresh ----------------------------------------------------------------

def test_is_fresh_within_window():
    obs = Observation.at(NOW, "beacon")
    assert obs.is_fresh(NOW + 60, within_s=120) is True
    assert obs.is_fresh(NOW + 300, within_s=120) is False


def test_impossible_reading_is_never_fresh():
    """A clock-stepped reading is unknowable, and unknowable must not pass for
    recent even though its clamped age is 0.0."""
    obs = Observation.at(NOW, "beacon")
    assert obs.is_fresh(NOW - 50 * HOUR, within_s=120) is False


# -- rebased: GPS clock discipline -------------------------------------------

def test_rebased_shifts_observed_at_and_keeps_value_and_source():
    obs = Observation(value=7, observed_at=NOW, source="http")
    moved = obs.rebased(500.0)
    assert moved.observed_at == NOW + 500.0
    assert moved.value == 7 and moved.source == "http"
    assert obs.observed_at == NOW       # original untouched (frozen)


def test_rebase_keeps_a_five_minute_old_reading_five_minutes_old():
    obs = Observation.at(NOW - 300, "beacon")       # heard 5 min ago, old clock
    delta = 3 * HOUR
    moved = obs.rebased(delta)
    assert moved.age_at(NOW + delta).seconds == pytest.approx(300)


# -- serialization -----------------------------------------------------------

def test_to_dict_from_dict_round_trip():
    obs = Observation(value=None, observed_at=NOW, source="announce")
    assert Observation.from_dict(obs.to_dict()) == obs


def test_none_persists_as_null_and_reloads_as_never():
    assert Observation.from_dict(None) is None


def test_from_dict_defaults_missing_source_to_legacy():
    obs = Observation.from_dict({"value": None, "observed_at": NOW})
    assert obs.source == "legacy"


def test_from_legacy_bare_float_becomes_a_legacy_observation():
    obs = Observation.from_legacy(NOW)
    assert obs.observed_at == NOW
    assert obs.source == "legacy"
    assert obs.value is None


def test_from_legacy_none_stays_never():
    assert Observation.from_legacy(None) is None


# -- from_dict must LOAD corrupt input, never crash --------------------------
# The registry loads a whole fleet from one file; one bad `seen` entry must
# degrade to never-observed, not raise here nor defer the crash to age_at at
# render time. (Regression guard for the hardening review.)

def test_from_dict_non_dict_is_never_not_a_raise():
    assert Observation.from_dict("astring") is None
    assert Observation.from_dict(12345) is None
    assert Observation.from_dict([1, 2, 3]) is None


def test_from_dict_empty_dict_is_never_not_a_keyerror():
    assert Observation.from_dict({}) is None


def test_from_dict_non_numeric_observed_at_is_never():
    assert Observation.from_dict({"observed_at": "notafloat"}) is None
    assert Observation.from_dict({"observed_at": None}) is None


def test_from_dict_nan_and_inf_observed_at_are_never():
    assert Observation.from_dict({"observed_at": float("nan")}) is None
    assert Observation.from_dict({"observed_at": float("inf")}) is None
    assert Observation.from_dict({"observed_at": float("-inf")}) is None


def test_from_dict_numeric_string_observed_at_still_loads():
    # float() accepts "1000.0" — a benign coercion, kept rather than rejected.
    obs = Observation.from_dict({"observed_at": "1000.0", "source": "beacon"})
    assert obs.observed_at == 1000.0 and obs.source == "beacon"


def test_from_dict_non_string_source_degrades_to_unknown():
    obs = Observation.from_dict({"observed_at": NOW, "source": 42})
    assert obs.source == "unknown"       # honest label, never a raise


def test_unknown_is_in_the_vocabulary():
    assert "unknown" in KNOWN_SOURCES


def test_from_legacy_corrupt_bare_value_degrades_to_none():
    # A pre-Observation registry file whose bare timestamp got corrupted must
    # null that one field, never raise (the fleet-load loop relies on it).
    assert Observation.from_legacy("notafloat") is None
    assert Observation.from_legacy(float("nan")) is None
    assert Observation.from_legacy(float("inf")) is None
    assert Observation.from_legacy([1, 2, 3]) is None


def test_from_legacy_non_string_source_is_unknown():
    obs = Observation.from_legacy(NOW, source=1234)
    assert obs.source == "unknown"
