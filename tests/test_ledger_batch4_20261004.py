"""Readiness ledger, 2026-10-04 fourth batch:

  #154  the offline Pi build said "carry the wheels" with no way to
  #168/#43  a custom-board build the medic lacks: one honest sentence, and the
            gate held at the workflow, not only in the two pickers
  #135  every nameless located device folded into one "(unnamed)" dot on MAPS
  #26   a packet heard before ANTENNA opened silenced the "isn't answering" watchdog
"""
import json

from monitor.registry import NodeRegistry
from tests.srcutil import src

NOW = 1_000_000.0


# -- #154 ------------------------------------------------------------------

def test_the_offline_pi_build_names_the_button_that_fills_the_wheelhouse():
    s = src("workflows/build.py")
    assert "Prepare for the field, then build again." in s
    assert "Carry the wheels for a field build." not in s


# -- #168 / #43 ------------------------------------------------------------

def test_a_missing_custom_build_is_refused_in_one_voice_and_before_any_port():
    s = src("workflows/rnode_flash.py")
    assert "build it before flashing this board." not in s
    assert s.count("That board's firmware is built from source") == 2   # dfu + fork
    gate = s[s.index("def _detect_port"):s.index("port = self.port or detect_rnode_port")]
    assert "board_blocker(self.board, self.band_mhz)" in gate
    assert "connection_is_local(self.connection)" in gate


# -- #135 ------------------------------------------------------------------

def test_two_nameless_located_devices_are_two_dots():
    reg = NodeRegistry()
    reg.register("aa" * 16, lat=-37.80, lon=144.90)
    reg.register("bb" * 16, lat=-37.90, lon=144.80)
    dots = reg.located_nodes(NOW)
    assert len(dots) == 2, dots
    names = {d["name"] for d in dots}
    assert len(names) == 2 and "(unnamed)" not in names
    for d in dots:
        assert "aaaaaaaa" in d["name"] or "bbbbbbbb" in d["name"]   # the VITALS label


def test_a_named_device_still_folds_to_one_dot():
    reg = NodeRegistry()
    reg.register("cc" * 16, name="FAITH", lat=-37.80, lon=144.90)
    reg.register("dd" * 16, name="faith", lat=-37.80, lon=144.90)     # same name, other case
    assert len(reg.located_nodes(NOW)) == 1


# -- #26 -------------------------------------------------------------------

def test_a_full_sample_says_when_its_packet_was_heard(tmp_path):
    from monitor.triage_feed import live_triage_feed
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_rssi": -88, "last_snr": 6.0, "noise_floor": -105,
                             "packet_heard_at": 990.0, "updated": 999.0, "peers": 1}))
    sample = live_triage_feed(str(p), max_age_s=30, now=lambda: 1001.0)()
    assert sample["rssi"] == -88 and sample["heard_at"] == 990.0


def test_the_watchdog_is_answered_only_by_a_packet_heard_since_it_asked():
    s = src("ui/screens/triage_screen.py")
    enter = s[s.index("def enter_triage"):s.index("def _apply_lighthouse")]
    assert "self._beacon_started = _time.time()" in enter
    tick = s[s.index("def _tick(self, dt)"):s.index("snap = self._session.feed(")]
    assert 'heard_at = sample.get("heard_at")' in tick
    assert "heard_at > getattr(self, \"_beacon_started\", 0.0)" in tick
    assert "self._beacon_answered = True      # a real packet arrived (beacon works)" not in s
