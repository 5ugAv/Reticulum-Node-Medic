"""Adoption workflow — pure, injected readers/writers (no board, no Kivy)."""

from workflows.adopt_node import AdoptWorkflow
from tests.test_node_classifier import FAITH_BANNER, FAITH_STATUS


def _capture_writers():
    saved, kin = {}, {}

    def save_cert(cert):
        saved["cert"] = dict(cert)
        return "cert-123"

    def register_kin(rns_hash, name, node_type="pi", lat=None, lon=None,
                     builder=None):
        kin.update(dict(rns_hash=rns_hash, name=name, node_type=node_type,
                        lat=lat, lon=lon, builder=builder))
    return saved, kin, save_cert, register_kin


def test_adopts_faith_end_to_end():
    saved, kin, save_cert, register_kin = _capture_writers()
    wf = AdoptWorkflow(
        board_port="/dev/ttyACM0",
        banner_reader=lambda p: FAITH_BANNER,
        status_reader=lambda: FAITH_STATUS,
        gps_reader=lambda: (-37.7, 145.0),
        save_cert=save_cert, register_kin=register_kin,
        builder_hash="medic-unit-hash")
    results = wf.run_all()
    assert wf.succeeded
    assert [r.step for r in results] == ["identify", "certificate", "enroll"]
    # certificate carries FAITH's real identity + on-spec params + adopted flag
    cert = saved["cert"]
    assert cert["adopted"] is True
    assert cert["identity_hash"] == "5a0b000b000000000000000000000006"
    assert cert["node_name"] == "FAITH RTnode"
    assert cert["spreading_factor"] == 9 and cert["frequency_mhz"] == 915.125
    assert cert["location"] == {"lat": -37.7, "lon": 145.0, "source": "gps"}
    # kin enrolled under the beacon dest, stamped with this medic as builder
    assert kin["rns_hash"] == "5a0b000b000000000000000000000006"
    assert kin["name"] == "FAITH RTnode"
    assert kin["node_type"] == "rtnode2400"
    assert kin["builder"] == "medic-unit-hash"


def test_name_override_wins():
    saved, kin, save_cert, register_kin = _capture_writers()
    wf = AdoptWorkflow(board_port="/dev/ttyACM0",
                       banner_reader=lambda p: FAITH_BANNER,
                       status_reader=lambda: FAITH_STATUS,
                       node_name_override="Rooftop-Relay",
                       save_cert=save_cert, register_kin=register_kin)
    wf.run_all()
    assert saved["cert"]["node_name"] == "Rooftop-Relay"
    assert kin["name"] == "Rooftop-Relay"


def test_refuses_non_adopt_board():
    # off-canonical params -> classifier says birth -> adoption stops at identify
    saved, kin, save_cert, register_kin = _capture_writers()
    off = FAITH_BANNER.replace("freq=915125000", "freq=868000000")
    wf = AdoptWorkflow(board_port="/dev/ttyACM0", banner_reader=lambda p: off,
                       status_reader=lambda: FAITH_STATUS,
                       save_cert=save_cert, register_kin=register_kin)
    results = wf.run_all()
    assert not wf.succeeded
    assert results[-1].step == "identify" and not results[-1].success
    assert "off canonical" in results[-1].message.lower()
    assert saved == {} and kin == {}      # nothing written for a non-adopt board


def test_blank_board_is_refused():
    saved, kin, save_cert, register_kin = _capture_writers()
    wf = AdoptWorkflow(board_port="/dev/ttyACM0", banner_reader=lambda p: "",
                       save_cert=save_cert, register_kin=register_kin)
    results = wf.run_all()
    assert not wf.succeeded
    assert results[0].step == "identify" and not results[0].success
    assert saved == {} and kin == {}


def test_banner_read_error_is_reported_not_raised():
    def boom(_p):
        raise OSError("port busy")
    wf = AdoptWorkflow(board_port="/dev/ttyACM0", banner_reader=boom)
    results = wf.run_all()
    assert not wf.succeeded and not results[0].success
    assert "couldn't read" in results[0].message.lower()


def test_heard_candidates_lists_devices_neighbours_first():
    """Over-the-air adopt sources candidates from the live registry, one per
    device, neighbours (adopt targets) before already-kin."""
    import time
    from monitor.registry import NodeRegistry
    from monitor.http_status import NodeStatus
    from ui.adopt_live import heard_candidates
    now = time.time()
    reg = NodeRegistry()
    reg.set_kin_roster({"aa11": {"name": "EVERYWHERE", "type": "pi"}})
    reg.record_http_status("aa11", NodeStatus(reachable=True, status="ok",
        node_name="EVERYWHERE", firmware_version="x", lora_online=True,
        local_tcp_server_up=True, faults=[]), now)
    # a bare heard neighbour (not kin)
    from monitor.mesh import MeshNode
    reg.ingest_mesh(MeshNode(dst_hash="bb22", hops=1, interface="LoRa"), now)
    cands = heard_candidates(reg, now)
    names = [c["name"] for c in cands]
    assert any("EVERYWHERE" == c["name"] and c["provenance"] == "kin" for c in cands)
    assert any(c["provenance"] == "neighbour" for c in cands)
    # neighbour (adopt target) sorts before the already-kin node
    provs = [c["provenance"] for c in cands]
    assert provs.index("neighbour") < provs.index("kin")
    # every candidate carries a kin key for enrolment
    assert all(c["key"] for c in cands)
