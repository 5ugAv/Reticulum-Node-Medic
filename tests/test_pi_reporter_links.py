"""The Pi reporter MEASURES its LoRa and internet-backbone bits from its own
rnstatus — they were hardcoded True, and skyfinger (no network up at all)
showed NET green on VITALS (operator, 2026-09-30)."""
import json

from monitor.pi_health_reporter import gather_link_state


def _status(*ifaces):
    return json.dumps({"interfaces": [dict(i) for i in ifaces]})


def test_rnode_and_tcp_client_measure_true_true():
    raw = _status({"type": "RNodeInterface", "status": True},
                  {"type": "TCPClientInterface", "status": True})
    assert gather_link_state(run=lambda c: raw) == {"radio_up": True, "backbone_up": True}


def test_the_medics_own_shape_is_radio_only_no_backbone():
    """rnstatus --json on the live medic, 2026-09-30: shared instance, local
    clients, the LAN AutoInterface and the RNode. No internet backbone."""
    raw = _status({"type": "LocalServerInterface", "status": True, "clients": 3},
                  {"type": "LocalClientInterface", "status": True},
                  {"type": "AutoInterface", "status": True},
                  {"type": "RNodeInterface", "status": True})
    assert gather_link_state(run=lambda c: raw) == {"radio_up": True, "backbone_up": False}


def test_a_down_interface_does_not_count():
    raw = _status({"type": "RNodeInterface", "status": False},
                  {"type": "TCPClientInterface", "status": False})
    assert gather_link_state(run=lambda c: raw) == {"radio_up": False, "backbone_up": False}


def test_a_tcp_server_is_a_backbone_only_with_someone_on_it():
    empty = _status({"type": "TCPServerInterface", "status": True, "clients": 0})
    busy = _status({"type": "TCPServerInterface", "status": True, "clients": 2})
    assert gather_link_state(run=lambda c: empty)["backbone_up"] is False
    assert gather_link_state(run=lambda c: busy)["backbone_up"] is True
    i2p = _status({"type": "I2PInterface", "status": True})
    assert gather_link_state(run=lambda c: i2p)["backbone_up"] is True


def test_any_failure_is_false_not_a_declared_up():
    assert gather_link_state(run=lambda c: "not json") == {"radio_up": False, "backbone_up": False}
    assert gather_link_state(run=lambda c: "") == {"radio_up": False, "backbone_up": False}
    assert gather_link_state(run=lambda c: (_ for _ in ()).throw(OSError())) == {"radio_up": False, "backbone_up": False}
    assert gather_link_state(run=lambda c: json.dumps([1, 2])) == {"radio_up": False, "backbone_up": False}


def test_nothing_in_the_reporter_is_hardcoded_up_any_more():
    src = open("monitor/pi_health_reporter.py").read()
    assert "radio_up=True," not in src and "rns_transport_up=True," not in src
    assert 'radio_up=links["radio_up"]' in src
