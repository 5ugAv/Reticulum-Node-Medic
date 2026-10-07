"""Every answered ping carries the signal it arrived with (operator,
2026-09-21, after the first walk came back with snr=None on every hit and
no way to tell a slope from a cliff). The medic's own radio records RSSI
and SNR per received packet through the splitter; the walk reads them —
but only a packet heard AFTER the ping went out is the reply. And a path
that came back via a relay is the mesh's reach, not this radio's: it is
told, not banked."""
import re

import monitor.boundary_walk as bw
from monitor.boundary_walk import BoundaryWalkSession
from monitor.mesh import parse_path_probe, path_interface

SCAN = "ui/screens/scan_screen.py"
APP = "ui/app.py"

RNPATH_OK = ("Path found, destination <aabbccddeeff00112233445566778899> is 1 hop "
             "away via <aabbccddeeff00112233445566778899> on "
             "RNodeInterface[RNode LoRa Interface]")


def _body(path, name):
    src = open(path).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def |\Z)",
                  src, re.S)
    assert m, f"{name} missing from {path}"
    return m.group(0)


def _s():
    return BoundaryWalkSession(node_key="ab" * 16, node_name="RTnodet114",
                               node_lat=-37.7, node_lon=145.0, now=1000.0)


def test_signal_is_the_packet_heard_after_the_ping_went_out():
    st = {"packet_heard_at": 1005.0, "last_rssi": -88, "last_snr": 9.75}
    assert bw.signal_for_answer(st, sent_at=1000.0) == (-88, 9.75)
    # a packet from BEFORE the ping is somebody else's — never the reply
    assert bw.signal_for_answer(st, sent_at=1010.0) == (None, None)
    assert bw.signal_for_answer(None, sent_at=1000.0) == (None, None)
    assert bw.signal_for_answer({"last_rssi": -88}, sent_at=1000.0) == (None, None)


def test_rnpath_names_the_interface_the_path_came_over():
    assert parse_path_probe(RNPATH_OK) == (True, 1)
    assert path_interface(RNPATH_OK) == "RNodeInterface[RNode LoRa Interface]"
    assert path_interface("Path not found") is None


def test_a_hit_keeps_its_signal_and_the_evidence_carries_it():
    w = _s()
    w.begin_ping(1000.0)
    assert w.ping_sent_at() == 1000.0
    w.ping_result(1002.0, True, gps=(-37.701, 145.0), rssi_dbm=-88,
                  snr_db=9.75, hops=1, direct=True)
    obs, _f = w.evidence()
    assert obs[0].rssi_dbm == -88 and obs[0].snr_db == 9.75
    assert w.counts()["last_rssi"] == -88


def test_a_relayed_answer_is_told_but_not_banked_as_this_radios_reach():
    w = _s()
    w.begin_ping(1000.0)
    w.ping_result(1002.0, True, gps=(-37.705, 145.0), hops=2, direct=False)
    obs, fails = w.evidence()
    assert obs == [] and fails == []
    assert w.counts()["relayed"] == 1
    assert w.state == "linked"            # the mesh reached it — the story says so


def test_screen_reads_the_signal_and_the_probe_reports_the_path():
    res = _body(SCAN, "_walk_result")
    assert "signal_for_answer(" in res and "ping_sent_at()" in res
    assert "rssi_dbm=" in res
    probe = _body(APP, "_walk_probe")
    assert "direct=" in probe and "hops=" in probe
    banner = _body(SCAN, "_walk_banner_text")
    assert "last_rssi" in banner and "relayed" in banner
