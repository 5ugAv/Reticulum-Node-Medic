import json

import pytest

from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry, CommissionEvent, version_tuple

HASH = "eabdd142596bcae888242ec1b172d566"
HASH2 = "aa11bb22cc33dd44ee55ff6600778899"
NOW = 1_000_000.0


def beacon(fw=(0, 6, 2), **over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=fw)
    kw.update(over)
    return decode(encode(**kw))


# ---- field notes ---------------------------------------------------------


def test_add_note_appends_and_logs_event():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.add_note(HASH, "Antenna re-seated on the mast", NOW, operator="suga")
    rec = r.get(HASH)
    assert "Antenna re-seated on the mast" in rec.notes
    assert any(e.kind == "note" for e in rec.events)


def test_add_note_auto_registers_unknown():
    r = NodeRegistry()
    r.add_note(HASH, "seen in the field", NOW)
    assert r.get(HASH) is not None


# ---- commissioning log ---------------------------------------------------


def test_log_event_records_provisioning_history():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.log_event(HASH, "build", "Flashed heltec_V4_boundary-local", NOW, "suga")
    r.log_event(HASH, "onboard", "WiFi + LoRa configured", NOW + 60, "suga")
    events = r.get(HASH).events
    assert [e.kind for e in events] == ["build", "onboard"]
    assert events[0].operator == "suga"
    assert isinstance(events[0], CommissionEvent)


# ---- firmware version tracking -------------------------------------------


def test_firmware_version_from_beacon():
    r = NodeRegistry()
    r.ingest(HASH, beacon(fw=(0, 6, 2)), NOW)
    assert r.get(HASH).firmware_version == "0.6.2"


def test_needs_update_when_older():
    r = NodeRegistry()
    r.ingest(HASH, beacon(fw=(0, 6, 2)), NOW)
    assert r.get(HASH).needs_firmware_update("0.7.0") is True


def test_no_update_when_equal_or_newer():
    r = NodeRegistry()
    r.ingest(HASH, beacon(fw=(0, 7, 0)), NOW)
    assert r.get(HASH).needs_firmware_update("0.7.0") is False
    assert r.get(HASH).needs_firmware_update("0.6.9") is False


def test_no_update_without_beacon():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    assert r.get(HASH).needs_firmware_update("9.9.9") is False


def test_nodes_needing_update_filters():
    r = NodeRegistry()
    r.register(HASH, name="Old"); r.ingest(HASH, beacon(fw=(0, 6, 2)), NOW)
    r.register(HASH2, name="New"); r.ingest(HASH2, beacon(fw=(0, 7, 0)), NOW)
    stale = r.nodes_needing_update("0.7.0")
    assert [n.name for n in stale] == ["Old"]


def test_version_tuple_helper():
    assert version_tuple("0.6.2") == (0, 6, 2)
    assert version_tuple("1.10.0") > version_tuple("1.9.9")
    assert version_tuple("0.6") == (0, 6)


# ---- persistence (monitoring DB round-trip) ------------------------------


def test_registry_json_round_trip():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", location="Northcote", node_type="rtnode2400")
    r.ingest(HASH, beacon(fw=(0, 6, 2)), NOW)
    r.add_note(HASH, "solar panel cleaned", NOW, operator="suga")
    r.log_event(HASH, "build", "provisioned", NOW, "suga")

    # serialize -> JSON string -> deserialize
    blob = json.dumps(r.to_dict())
    r2 = NodeRegistry.from_dict(json.loads(blob))

    rec = r2.get(HASH)
    assert rec.name == "TRUTH"
    assert rec.location == "Northcote"
    assert rec.last_seen == NOW
    assert rec.notes == ["solar panel cleaned"]
    assert [e.kind for e in rec.events] == ["note", "build"]
    assert rec.latest_beacon is not None
    assert rec.latest_beacon.firmware_version == "0.6.2"
    assert rec.status(NOW) == "ok"


def test_from_dict_tolerates_node_without_beacon():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r2 = NodeRegistry.from_dict(json.loads(json.dumps(r.to_dict())))
    assert r2.get(HASH).latest_beacon is None
    assert r2.get(HASH).status(NOW) == "unknown"


# ---- location / navigation -----------------------------------------------


def test_register_with_location_gives_navigation():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", lat=-37.814, lon=144.963)
    rec = r.get(HASH)
    assert rec.has_location()
    nav = rec.navigation()
    assert "google.com" in nav["google"]
    assert nav["raw"] == "-37.814000, 144.963000"


def test_no_location_navigation_is_none():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    assert r.get(HASH).navigation() is None


def test_location_survives_json_round_trip():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", lat=-37.814, lon=144.963)
    r2 = NodeRegistry.from_dict(json.loads(json.dumps(r.to_dict())))
    assert r2.get(HASH).lat == -37.814
    assert r2.get(HASH).navigation()["raw"] == "-37.814000, 144.963000"


def test_register_from_birth_certificate():
    cert = {
        "identity_hash": HASH,
        "board": "Heltec32 V4",
        "firmware": "0.6.2",
        "location": {"lat": -37.814, "lon": 144.963, "source": "pi_gps"},
    }
    r = NodeRegistry()
    rec = r.register_from_birth_certificate(cert, name="TRUTH", now=NOW,
                                            operator="suga")
    assert rec.name == "TRUTH"
    assert rec.has_location()
    assert any(e.kind == "build" for e in rec.events)


def test_register_from_birth_certificate_without_identity_is_noop():
    r = NodeRegistry()
    assert r.register_from_birth_certificate({"board": "x"}) is None
