"""The live TRIAGE feed off the splitter's state file — no hardware."""

import json
import time

from monitor.triage_feed import live_triage_feed


def _write(tmp_path, **state):
    p = tmp_path / "gps_state.json"
    p.write_text(json.dumps(state))
    return str(p)


def test_yields_a_sample_once_a_packet_was_heard(tmp_path):
    p = _write(tmp_path, last_rssi=-80, last_snr=10.5, noise_floor=-107,
               packet_heard_at=995.0, updated=1000.0)
    sample = live_triage_feed(p, max_age_s=30, now=lambda: 1005.0)()
    assert sample == {"snr": 10.5, "rssi": -80, "noise": -107, "peers": 0}


def test_none_before_any_packet_heard(tmp_path):
    # splitter alive (updated fresh) but radio silent since boot: no rssi/snr yet
    p = _write(tmp_path, last_rssi=None, last_snr=None, noise_floor=-107,
               updated=1000.0)
    assert live_triage_feed(p, max_age_s=30, now=lambda: 1002.0)() is None


def test_none_when_state_stale_or_missing(tmp_path):
    p = _write(tmp_path, last_rssi=-80, last_snr=10.0, noise_floor=-107,
               updated=100.0)
    assert live_triage_feed(p, max_age_s=30, now=lambda: 1000.0)() is None
    assert live_triage_feed(str(tmp_path / "nope.json"))() is None


def test_holds_last_packet_values_while_splitter_fresh(tmp_path):
    # channel stats keep 'updated' fresh even between packets — the last-heard
    # rssi/snr are still returned (the Triage score is designed to hold).
    p = _write(tmp_path, last_rssi=-88, last_snr=6.0, noise_floor=-105,
               packet_heard_at=900.0, updated=999.0)
    sample = live_triage_feed(p, max_age_s=30, now=lambda: 1001.0)()
    assert sample is not None and sample["rssi"] == -88
