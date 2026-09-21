"""Slope or cliff — what a walk's signal readings say (2026-09-21)."""
import math

import monitor.walk_diagnostics as wd


def _hit(t, m, rssi, snr=None, direct=True):
    return {"t": t, "km": m / 1000.0, "connected": True, "rssi_dbm": rssi,
            "snr_db": snr, "direct": direct}


def _miss(t, m):
    return {"t": t, "km": m / 1000.0, "connected": False, "rssi_dbm": None,
            "snr_db": None}


def test_free_space_loss_at_915_mhz_is_the_textbook_number():
    # 30 m at 915 MHz ≈ 61 dB; 1 km ≈ 91.7 dB
    assert abs(wd.fspl_db(30) - 61.2) < 0.5
    assert abs(wd.fspl_db(1000) - 91.7) < 0.5
    assert abs(wd.open_air_rssi(30, tx_dbm=17) - (-44.2)) < 0.5


def test_path_loss_exponent_is_recovered_from_a_synthetic_walk():
    pts = [(d, 10 - 3.0 * 10 * math.log10(d)) for d in (10, 20, 50, 100, 200)]
    n, a = wd.fit_path_loss(pts)
    assert abs(n - 3.0) < 0.01 and abs(a - 10) < 0.1
    assert wd.fit_path_loss(pts[:3]) is None            # too few
    assert wd.fit_path_loss([(10, -50), (12, -51), (14, -52), (15, -53)]) is None  # no span


def test_no_readings_is_said_not_guessed():
    d = wd.diagnose([_miss(1, 100), {"t": 0, "km": 0.03, "connected": True,
                                     "rssi_dbm": None, "snr_db": None}])
    assert d["verdict"] == "no_signal" and d["readings"] == 0


def test_a_cliff_is_silence_with_margin_left():
    samples = [_hit(0, 30, -60, snr=10), _hit(20, 60, -66, snr=9),
               _hit(40, 100, -72, snr=8), _hit(60, 107, -73, snr=8),
               _miss(80, 150), _miss(100, 200)]
    d = wd.diagnose(samples, sf=9, tx_dbm=17, noise_floor_dbm=-104)
    assert d["verdict"] == "cliff"
    assert d["margin_db"] == 20.5                       # 8 - (-12.5)
    assert d["last"]["m"] == 107 and d["predicted_extra_m"] == {}
    assert d["noise_penalty_db"] == 11.0


def test_a_slope_is_the_budget_running_out_and_says_what_would_help():
    samples = [_hit(0, 30, -70, snr=8), _hit(20, 100, -95, snr=-2),
               _hit(40, 300, -110, snr=-8), _hit(60, 500, -118, snr=-11),
               _miss(80, 600), _miss(100, 700)]
    d = wd.diagnose(samples, sf=9, tx_dbm=17)
    assert d["verdict"] == "slope"
    assert d["margin_db"] == 1.5
    assert d["exponent"] is not None and 3.0 < d["exponent"] < 4.5
    ex = d["predicted_extra_m"]
    assert set(ex) == {"sf10", "sf11", "sf12", "txp22_both"}
    assert 0 < ex["sf10"] < ex["sf11"] < ex["sf12"]


def test_the_doorstep_deficit_names_an_antenna_problem():
    # -88 dBm at 30 m where open air would give about -44: 44 dB missing
    d = wd.diagnose([_hit(0, 30, -88, snr=9.75), _miss(20, 150)], tx_dbm=17)
    assert d["doorstep"]["flag"] is True
    assert 40 < d["doorstep"]["deficit_db"] < 48
    ok = wd.diagnose([_hit(0, 30, -50, snr=12), _miss(20, 150)], tx_dbm=17)
    assert ok["doorstep"]["flag"] is False


def test_an_open_walk_has_no_edge_to_judge():
    d = wd.diagnose([_hit(0, 30, -60, snr=10), _hit(20, 200, -80, snr=5)])
    assert d["verdict"] == "open"


def test_relayed_hits_carry_no_reading_for_this_radio():
    d = wd.diagnose([_hit(0, 30, -60, snr=10, direct=False), _miss(20, 100)])
    assert d["verdict"] == "no_signal"


def test_medic_radio_params_come_from_the_rnode_stanza():
    cfg = """[interfaces]
  [[RNode LoRa Interface]]
    type = RNodeInterface
    port = /tmp/x
    frequency = 915125000
    bandwidth = 125000
    txpower = 17
    spreadingfactor = 9
  [[LAN Interface]]
    type = AutoInterface
    txpower = 99
"""
    p = wd.medic_radio_params(cfg)
    assert p == {"freq_mhz": 915.125, "bw_khz": 125.0, "txp": 17, "sf": 9}
    assert wd.medic_radio_params("") == {}


def test_diagnoses_persist_and_load(tmp_path):
    wd.append_diagnosis("ab" * 16, {"verdict": "cliff"}, base_dir=str(tmp_path),
                        now=lambda: 5.0)
    with open(tmp_path / wd._DIAG_FILE, "a") as fh:
        fh.write("{broken\n")
    got = wd.load_diagnoses(str(tmp_path))
    assert len(got) == 1 and got[0]["verdict"] == "cliff" and got[0]["t"] == 5.0


# -- wiring, pinned in code -------------------------------------------------

def _body(path, name):
    import re
    src = open(path).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def |\n    @|\Z)",
                  src, re.S)
    assert m, f"{name} missing from {path}"
    return m.group(0)


def test_the_walk_end_tells_and_keeps_the_diagnosis():
    body = _body("ui/screens/scan_screen.py", "_walk_signal_story")
    assert "diagnose(" in body and "append_diagnosis(" in body
    assert "medic_radio_params(" in body            # real SF/TX, not assumed
    assert "_walk_signal_story(w)" in _body("ui/screens/scan_screen.py", "end_walk")


def test_the_nodes_page_shows_the_last_walk():
    body = _body("ui/screens/node_detail_screen.py", "_last_walk_note")
    assert "latest_diagnosis(" in body


def test_latest_diagnosis_matches_by_key_or_name(tmp_path):
    wd.append_diagnosis("aa" * 16, {"verdict": "cliff"}, base_dir=str(tmp_path),
                        now=lambda: 1.0, name="RAIN")
    wd.append_diagnosis("aa" * 16, {"verdict": "slope"}, base_dir=str(tmp_path),
                        now=lambda: 2.0, name="RAIN")
    assert wd.latest_diagnosis("aa" * 16, base_dir=str(tmp_path))["verdict"] == "slope"
    assert wd.latest_diagnosis("rtnode:RAIN", name="RAIN",
                               base_dir=str(tmp_path))["verdict"] == "slope"
    assert wd.latest_diagnosis("bb" * 16, base_dir=str(tmp_path)) is None
