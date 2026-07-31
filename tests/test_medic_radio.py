"""The medic-retune config rewrite — the pure transform, no rnsd involved."""

from provisioning.medic_radio import render_config, retune_medic

SAMPLE = """\
[reticulum]
  enable_transport = Yes

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes

  [[RNode LoRa Interface]]
    type = RNodeInterface
    enabled = Yes
    port = /tmp/rnode-jonesey
    frequency = 915125000
    bandwidth = 125000
    txpower = 17
    spreadingfactor = 9
    codingrate = 5
    id_callsign = RTT-PI5
"""

EU = {"freq": 869.525, "bw": 125.0, "sf": 9, "cr": 5, "txp": 14}


def test_rewrites_only_the_rnode_lora_keys():
    out = render_config(SAMPLE, EU)
    assert "frequency = 869525000" in out
    assert "txpower = 14" in out
    assert "bandwidth = 125000" in out
    # untouched: port, callsign, the other interface, transport flag
    assert "port = /tmp/rnode-jonesey" in out
    assert "id_callsign = RTT-PI5" in out
    assert "type = AutoInterface" in out
    assert "enable_transport = Yes" in out


def test_standard_params_are_a_noop():
    std = {"freq": 915.125, "bw": 125.0, "sf": 9, "cr": 5, "txp": 17}
    assert render_config(SAMPLE, std) == SAMPLE


def test_non_rnode_sections_never_touched():
    cfg = SAMPLE.replace("type = RNodeInterface", "type = SerialInterface")
    assert render_config(cfg, EU) == cfg          # no RNode section -> no edits


def test_retune_writes_file_and_backs_up(tmp_path):
    p = tmp_path / "config"
    p.write_text(SAMPLE)
    ok, msg = retune_medic(EU, config_path=str(p), restart=False)
    assert ok and "retuned" in msg
    assert "frequency = 869525000" in p.read_text()
    assert (tmp_path / "config.bak-retune").read_text() == SAMPLE
    # already matching -> honest no-op
    ok2, msg2 = retune_medic(EU, config_path=str(p), restart=False)
    assert ok2 and "already matches" in msg2
