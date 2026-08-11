"""One machine, one row in VITALS — and one grey chip per thing nobody measured.

SkyFinger, 2026-08-11: a single Pi propagation node held TWO records in the
medic's registry, ``SkyFinger`` and ``skyfinger!``, because it announces from two
unrelated identities (rnsd's, and the health reporter's own file). Half of what
the operator wanted to know was on each row, and the half that was quiet looked
like a node in trouble.
"""

from monitor import kin_roster
from monitor.http_status import NodeStatus, parse_status
from monitor.registry import NodeRegistry, name_key

NOW = 1_000_000.0
HEALTH = "aa" * 16
RNSD = "bb" * 16


# ---- name_key --------------------------------------------------------------

def test_punctuation_does_not_make_two_nodes_out_of_one():
    assert name_key("SkyFinger") == name_key("skyfinger!")


def test_spacing_does_not_make_two_nodes_out_of_one():
    assert name_key("Rooftop North") == name_key("rooftop-north")


def test_a_name_of_nothing_but_punctuation_keys_to_nothing():
    # An empty key must never group, or every unnamed neighbour becomes one node.
    assert name_key("!!!") == ""
    assert name_key(None) == ""


def test_different_nodes_still_key_differently():
    assert name_key("Rooftop") != name_key("Rooftop2")


# ---- the roster records a device, not just a hash --------------------------

def test_register_device_writes_every_hash_under_one_device(tmp_path):
    path = str(tmp_path / "kin.json")
    roster = kin_roster.register_device([HEALTH, RNSD], "SkyFinger",
                                        "pi_propagation", lat=-37.8, lon=145.0,
                                        path=path)
    assert set(roster) == {HEALTH, RNSD}
    assert roster[HEALTH]["device"] == roster[RNSD]["device"] == HEALTH
    # both hashes carry the full entry, so whichever is heard first is named
    assert roster[RNSD]["name"] == "SkyFinger"
    assert roster[RNSD]["lat"] == -37.8


def test_register_device_with_no_hashes_writes_nothing(tmp_path):
    path = str(tmp_path / "kin.json")
    assert kin_roster.register_device([], "Nobody", path=path) == {}
    assert kin_roster.register_device([None, ""], "Nobody", path=path) == {}


def test_a_plain_register_still_records_no_device(tmp_path):
    path = str(tmp_path / "kin.json")
    roster = kin_roster.register(HEALTH, "Alone", path=path)
    assert "device" not in roster[HEALTH]


# ---- the registry puts them back together ----------------------------------

def _roster():
    # No ``builder``: whether a named builder is TRUSTED is read from the
    # medic's own config, and these tests are about grouping, not about trust.
    return {
        HEALTH: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
        RNSD: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
    }


def test_two_identities_of_one_pi_are_one_row():
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    # each destination announces its OWN identity — nothing links them but the
    # roster
    reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW, identity_hash="ident-health")
    reg.ingest_announce(bytes.fromhex(RNSD), b"", NOW, identity_hash="ident-rnsd")
    rows = reg.devices(NOW)
    assert len(rows) == 1
    assert rows[0]["name"] == "SkyFinger"
    assert rows[0]["aspects"] == 2


def test_without_the_roster_the_two_identities_would_stay_apart():
    """The control: this is the bug, reproduced. It is the roster that fixes it,
    not something in the announce."""
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW, identity_hash="ident-health")
    reg.ingest_announce(bytes.fromhex(RNSD), b"", NOW, identity_hash="ident-rnsd")
    assert len(reg.devices(NOW)) == 2


def test_an_unrelated_node_is_not_swept_into_the_device():
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW, identity_hash="ident-health")
    reg.ingest_announce(bytes.fromhex("cc" * 16), b"", NOW, identity_hash="other")
    assert len(reg.devices(NOW)) == 2


def test_the_health_row_and_a_slightly_different_announced_name_still_merge():
    """The live symptom: the rnsd side announced 'skyfinger!' and the beacon
    side was named 'SkyFinger' from its birth certificate."""
    reg = NodeRegistry()
    reg.register(HEALTH, name="SkyFinger")
    reg.ingest_announce(bytes.fromhex(RNSD), b"\x09skyfinger!", NOW,
                        identity_hash="ident-rnsd")
    assert len(reg.devices(NOW)) == 1


def test_an_http_record_keyed_by_hostname_joins_the_same_device():
    """The /status endpoint reports the node's hostname, which is the node name
    lowercased and hyphenated (provisioning.pi_imager.hostnameify)."""
    reg = NodeRegistry()
    reg.register(HEALTH, name="SkyFinger")
    reg.record_http_status("rtnode:skyfinger",
                           parse_status({"node_name": "skyfinger",
                                         "wifi_connected": True}), NOW)
    rows = reg.devices(NOW)
    assert len(rows) == 1
    assert rows[0]["capabilities"]["wifi"] is True   # the HTTP evidence survives


def test_a_pi_discovered_over_http_is_not_labelled_an_rtnode():
    """register() types an unknown key 'rtnode2400'. That default already
    labelled the first Pi propagation node ever built as an RTNode-2400; this is
    the same default coming in through the discovery door."""
    reg = NodeRegistry()
    reg.record_http_status(
        "rtnode:skyfinger",
        parse_status({"fork": "RNM-Pi", "node_name": "skyfinger"}), NOW)
    assert reg.get("rtnode:skyfinger").node_type == "pi_propagation"


def test_a_real_rtnode_keeps_its_type():
    reg = NodeRegistry()
    reg.record_http_status(
        "rtnode:FAITH",
        parse_status({"fork": "RTNode", "node_name": "FAITH"}), NOW)
    assert reg.get("rtnode:FAITH").node_type == "rtnode2400"


def test_the_roster_outranks_a_nodes_self_report_about_its_type():
    reg = NodeRegistry()
    reg.set_kin_roster({HEALTH: {"name": "SkyFinger", "type": "rtnode2400"}})
    reg.record_http_status(
        HEALTH, parse_status({"fork": "RNM-Pi", "node_name": "x"}), NOW)
    assert reg.get(HEALTH).node_type == "rtnode2400"


def test_a_ping_on_the_http_row_probes_the_real_mesh_destination():
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    reg.register("rtnode:skyfinger", name="SkyFinger")
    assert reg.probe_hash_for("rtnode:skyfinger") in (HEALTH, RNSD)


# ---- an unread link stays grey ---------------------------------------------

def test_a_link_the_node_did_not_mention_is_unknown_not_down():
    reg = NodeRegistry()
    reg.record_http_status(
        "k", parse_status({"node_name": "n", "lora_online": True}), NOW)
    caps = reg.devices(NOW)[0]["capabilities"]
    assert caps["lora"] is True          # it said so
    assert caps["wifi"] is None          # it did not mention wifi at all
    assert caps["internet"] is None
    assert caps["bluetooth"] is None


def test_a_link_the_node_reports_down_is_still_reported_down():
    reg = NodeRegistry()
    reg.record_http_status(
        "k", parse_status({"node_name": "n", "wifi_connected": False,
                           "lora_online": True,
                           "tcp_backbone_connected": False}), NOW)
    caps = reg.devices(NOW)[0]["capabilities"]
    assert caps["wifi"] is False
    assert caps["internet"] is False


def test_an_rtnode_hand_built_status_is_unaffected():
    """Every existing construction of NodeStatus keeps the meaning it had —
    the 'known' flags default to True precisely so nothing silently goes grey."""
    reg = NodeRegistry()
    reg.record_http_status("k", NodeStatus(reachable=True, status="ok",
                                           node_name="n", wifi_connected=True,
                                           lora_online=True), NOW)
    caps = reg.devices(NOW)[0]["capabilities"]
    assert caps["wifi"] is True
    assert caps["internet"] is False     # the dataclass default, as before


def test_a_pi_that_answers_http_lights_lora_wifi_and_net_from_evidence():
    """The goal, stated as a test: everything green here was said by the node."""
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    reg.record_http_status(
        HEALTH,
        parse_status({"fork": "RNM-Pi", "node_name": "skyfinger",
                      "wifi_connected": True, "wifi_rssi": -52,
                      "lora_online": True, "tcp_backbone_connected": True,
                      "faults": []}),
        NOW)
    row = reg.devices(NOW)[0]
    assert row["capabilities"] == {"lora": True, "wifi": True,
                                   "bluetooth": None, "internet": True}
    assert row["status"] == "ok"
    assert row["signal_dbm"] == -52
