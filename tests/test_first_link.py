"""First-link range discovery — the second-node walk-in protocol."""

import pytest

from monitor.first_link import FirstLinkSession, MAX_START_KM, MIN_LINK_SNR_DB

HOME = (-37.790, 144.960)
KM_LAT = 1 / 111.0                      # ~1 km of latitude


def _session():
    return FirstLinkSession(home_lat=HOME[0], home_lon=HOME[1])


def _spot(km_north):
    return (HOME[0] + km_north * KM_LAT, HOME[1])


def test_desired_spot_beyond_ten_km_is_pulled_back():
    s = _session()
    out = s.start(*_spot(14.0))
    assert "too far" in out["guidance"]
    sp = out["suggested_spot"]
    from monitor.first_link import _km
    assert _km(sp["lat"], sp["lon"], *HOME) == pytest.approx(MAX_START_KM, abs=0.05)


def test_no_connection_steps_one_km_closer_each_time():
    s = _session()
    s.start(*_spot(5.0))
    out1 = s.report_test(connected=False)
    assert "1 km closer" in out1["guidance"] and "4.0 km out" in out1["guidance"]
    out2 = s.report_test(connected=False)
    assert "3.0 km out" in out2["guidance"]
    assert len(s.attempts) == 2


def test_weak_connection_also_steps_closer():
    s = _session()
    s.start(*_spot(4.0))
    out = s.report_test(connected=True, snr_db=MIN_LINK_SNR_DB - 5)
    assert "weak" in out["guidance"] and "closer" in out["guidance"]
    assert s.state == "testing"


def test_strong_connection_completes_with_measured_reach():
    s = _session()
    s.start(*_spot(5.0))
    s.report_test(connected=False)                       # 5 km: nothing
    out = s.report_test(connected=True, snr_db=6.5)      # 4 km: strong
    assert s.state == "done"
    assert "first measured reach" in out["guidance"]
    r = s.result()
    assert r["reach_km"] == pytest.approx(4.0, abs=0.1)
    assert r["final_snr_db"] == 6.5 and r["attempts"] == 2


def test_reaching_home_without_link_flags_hardware():
    s = _session()
    s.start(*_spot(1.0))
    out = s.report_test(connected=False)                 # next step = home itself
    assert s.state == "failed"
    assert "antennas" in out["guidance"] and "Probe" in out["guidance"]


def test_result_is_none_until_done():
    s = _session()
    s.start(*_spot(3.0))
    assert s.result() is None
