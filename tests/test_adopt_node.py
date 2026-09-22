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
    assert cert["identity_hash"] == "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"
    assert cert["node_name"] == "FAITH RTnode"
    assert cert["spreading_factor"] == 9 and cert["frequency_mhz"] == 915.125
    assert cert["location"] == {"lat": -37.7, "lon": 145.0, "source": "gps"}
    # kin enrolled under the beacon dest, stamped with this medic as builder
    assert kin["rns_hash"] == "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"
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


def test_heard_candidates_fold_one_machine_the_way_vitals_does():
    """Operator's photo, 2026-09-23: the adopt-over-the-air list showed
    ELSEWHERE three times and skyfinger twice — one row per mesh destination.
    The registry already knows those destinations are ONE machine (the kin
    roster's device record, written at birth), and VITALS folds by it; the
    adopt list folded only by announced identity. Same fold, both screens —
    including the guard that two identity-bearing groups sharing a NAME stay
    two machines (a dead board must not hide behind a live namesake)."""
    import time
    from monitor.registry import NodeRegistry
    from monitor.mesh import MeshNode
    from ui.adopt_live import heard_candidates
    now = time.time()
    reg = NodeRegistry()
    dev = "aa11aa11aa11aa11"
    reg.set_kin_roster({
        "aa11aa11aa11aa11": {"name": "ELSEWHERE", "type": "pi_propagation", "device": dev},
        "bb22bb22bb22bb22": {"name": "ELSEWHERE", "type": "pi_propagation", "device": dev},
        "cc33cc33cc33cc33": {"name": "ELSEWHERE", "type": "pi_propagation", "device": dev},
    })
    for h in ("aa11aa11aa11aa11", "bb22bb22bb22bb22", "cc33cc33cc33cc33"):
        reg.ingest_mesh(MeshNode(dst_hash=h, hops=1, interface="LoRa"), now)
    # two OTHER machines that both announce their own identity under one name
    reg.set_kin_roster(dict(reg.kin_roster, **{
        "d1d1d1d1d1d1d1d1": {"name": "A2", "type": "rtnode2400"},
        "e2e2e2e2e2e2e2e2": {"name": "A2", "type": "rtnode2400"}}))
    reg.ingest_announce(bytes.fromhex("d1d1d1d1d1d1d1d1"), b"", now,
                        identity_hash="1111111111111111")
    reg.ingest_announce(bytes.fromhex("e2e2e2e2e2e2e2e2"), b"", now,
                        identity_hash="2222222222222222")
    cands = heard_candidates(reg, now)
    names = sorted(c["name"] for c in cands)
    assert names.count("ELSEWHERE") == 1, names
    assert names.count("A2") == 2, "two identities, two machines — never folded by name"
    els = next(c for c in cands if c["name"] == "ELSEWHERE")
    assert els["key"] in ("aa11aa11aa11aa11", "bb22bb22bb22bb22", "cc33cc33cc33cc33")
    assert ":" not in els["key"], "the kin key is a mesh destination, never a pseudo-hash"
    assert els["aspects"] == 3
    # and it is the SAME fold VITALS draws
    vitals = [d for d in reg.devices(now) if d["name"] == "ELSEWHERE"]
    assert len(vitals) == 1 and vitals[0]["aspects"] == 3


def test_confirmed_location_overrides_gps():
    """An operator-confirmed location wins over the raw GPS reader (the map gate
    already vetted it)."""
    saved, kin, save_cert, register_kin = _capture_writers()
    wf = AdoptWorkflow(board_port="/dev/ttyACM0",
                       banner_reader=lambda p: FAITH_BANNER,
                       status_reader=lambda: FAITH_STATUS,
                       gps_reader=lambda: (1.0, 2.0),          # raw GPS
                       location=(-37.70, 145.00),             # confirmed on map
                       save_cert=save_cert, register_kin=register_kin)
    wf.run_all()
    assert saved["cert"]["location"] == {"lat": -37.70, "lon": 145.00,
                                         "source": "confirmed"}
