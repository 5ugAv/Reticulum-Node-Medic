"""GNSS diagnostics — reads the splitter's ~/gps_state.json over a mocked
connection. No hardware, no serial port."""

import json

from node_profile import NodeProfile, NodeHardware
from transport.connection import EmulatedConnection
from diagnostics.gnss import GnssCheck


def _tracker():
    return NodeProfile(hardware=NodeHardware.WIRELESS_TRACKER)


def _conn(state, epoch=1000):
    c = EmulatedConnection()
    c.rule("gps_state", code=0, stdout="" if state is None else json.dumps(state))
    c.rule("date", code=0, stdout=str(epoch))
    return c


def _names(issues):
    return {i.check_name for i in issues}


def _sev(issues, name):
    return next(i.severity for i in issues if i.check_name == name)


def test_no_state_file_flags_no_data():
    issues = GnssCheck(_conn(None), _tracker()).run()
    assert "gnss_data_flowing" in _names(issues)
    assert _sev(issues, "gnss_data_flowing") == "warning"


def test_stale_state_flags_no_data():
    st = {"lat": None, "lng": None, "sats": 0, "fix": 0, "has_fix": False, "updated": 500}
    issues = GnssCheck(_conn(st, epoch=1000), _tracker()).run()   # 500 s old
    assert "gnss_data_flowing" in _names(issues)


def test_flowing_but_no_fix_is_info_not_a_data_error():
    st = {"lat": None, "lng": None, "sats": 2, "fix": 0, "has_fix": False, "updated": 995}
    issues = GnssCheck(_conn(st, epoch=1000), _tracker()).run()
    assert "gnss_data_flowing" not in _names(issues)              # data IS flowing
    assert _sev(issues, "gnss_has_fix") == "info"


def test_good_fix_with_enough_satellites_is_all_clear():
    st = {"lat": -37.8, "lng": 144.9, "sats": 9, "fix": 1, "has_fix": True, "updated": 998}
    assert GnssCheck(_conn(st, epoch=1000), _tracker()).run() == []


def test_fix_with_too_few_satellites_flags_accuracy():
    st = {"lat": -37.8, "lng": 144.9, "sats": 3, "fix": 1, "has_fix": True, "updated": 998}
    issues = GnssCheck(_conn(st, epoch=1000), _tracker()).run()
    assert "gnss_enough_satellites" in _names(issues)


def test_no_op_on_non_tracker_hardware():
    st = {"lat": None, "lng": None, "sats": 0, "fix": 0, "has_fix": False, "updated": 0}
    assert GnssCheck(_conn(st), NodeProfile(hardware=NodeHardware.PI_5)).run() == []
