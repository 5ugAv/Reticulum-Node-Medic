"""The Pi propagation node's own ``/status`` endpoint.

Weighted at what it must NOT say. The whole point of this endpoint is to give
VITALS evidence instead of grey, and the cheapest way to ruin that is to fill a
key it could not read with a comfortable default — at which case the medic draws
amber "the node says this is down" for a thing nobody ever measured.
"""

import json

from monitor import pi_status_server as pss
from monitor.http_status import PI_FORK, parse_status


# ---- what an unreadable reading does (the point of the module) -------------

def test_a_reading_that_could_not_be_taken_is_absent_not_false():
    d = pss.build_status(pss.PiStatusInputs())
    for key in ("wifi_connected", "lora_online", "tcp_backbone_connected",
                "wdt_armed", "uptime_ms", "wifi_rssi", "wifi_ip"):
        assert key not in d, f"{key} was invented for a reading nobody took"


def test_an_absent_key_reaches_the_medic_as_unknown_not_as_down():
    ns = parse_status(pss.build_status(pss.PiStatusInputs()))
    assert ns.wifi_known is False
    assert ns.lora_known is False
    assert ns.backbone_known is False


def test_a_reading_that_says_down_is_reported_as_down():
    # The opposite failure: a link the node CAN read and that is genuinely off
    # must arrive as an explicit false, not vanish into "unknown".
    d = pss.build_status(pss.PiStatusInputs(wifi_connected=False,
                                            lora_online=False))
    assert d["wifi_connected"] is False and d["lora_online"] is False
    ns = parse_status(d)
    assert ns.wifi_known is True and ns.wifi_connected is False


def test_the_fork_is_not_a_claim_to_be_rtnode_firmware():
    d = pss.build_status(pss.PiStatusInputs())
    assert d["fork"] == PI_FORK
    assert d["fork"] != "RTNode"


def test_uptime_is_carried_in_milliseconds_like_the_firmware_does():
    d = pss.build_status(pss.PiStatusInputs(uptime_s=90))
    assert d["uptime_ms"] == 90000
    assert parse_status(d).uptime_s == 90


def test_a_negative_uptime_never_leaves_the_node():
    assert pss.build_status(pss.PiStatusInputs(uptime_s=-5))["uptime_ms"] == 0


# ---- /proc/net/wireless ----------------------------------------------------

WIRELESS = (
    "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
    " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
    " wlan0: 0000   62.  -48.  -256        0      0      0      0      0        0\n"
)


def test_wireless_headers_are_not_mistaken_for_interfaces():
    assert list(pss.parse_proc_net_wireless(WIRELESS)) == ["wlan0"]


def test_wireless_level_is_read_as_dbm():
    assert pss.parse_proc_net_wireless(WIRELESS)["wlan0"] == -48


def test_a_positive_level_is_no_reading_at_all():
    """Old WEXT drivers report an unsigned 0-255 byte here. Passing that off as
    dBm is how VITALS once drew a colossal signal for a node with none."""
    text = WIRELESS.replace("-48.", "154.")
    assert pss.parse_proc_net_wireless(text)["wlan0"] is None


def test_wireless_garbage_does_not_raise():
    assert pss.parse_proc_net_wireless("wlan0: not numbers here")["wlan0"] is None
    assert pss.parse_proc_net_wireless("") == {}


# ---- ip addresses ----------------------------------------------------------

IP_OUT = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
    "3: wlan0    inet 192.168.1.42/24 brd 192.168.1.255 scope global wlan0\n"
)


def test_ipv4_addresses_are_read_without_their_prefix():
    assert pss.parse_ipv4_addresses(IP_OUT) == {"lo": "127.0.0.1",
                                                "wlan0": "192.168.1.42"}


def test_ipv4_addresses_of_nothing_is_nothing():
    assert pss.parse_ipv4_addresses("") == {}
    assert pss.parse_ipv4_addresses("total nonsense") == {}


# ---- rnstatus --------------------------------------------------------------

def _rnstatus(*interfaces):
    return json.dumps({"interfaces": list(interfaces)})


def test_unrunnable_rnstatus_is_unknown_not_down():
    """The scar from check_rns_responding: failing to run the tool is not
    evidence about the mesh, and a node must not report its radio down because
    it could not find rnstatus on PATH."""
    assert pss.rns_interface_state("") == (None, None)
    assert pss.rns_interface_state("not json") == (None, None)
    assert pss.rns_interface_state("[]") == (None, None)


def test_radio_up_is_read_from_the_interfaces_own_boolean():
    out = _rnstatus({"type": "RNodeInterface", "status": True})
    assert pss.rns_interface_state(out)[0] is True


def test_a_tcp_interface_being_up_does_not_make_the_radio_up():
    out = _rnstatus({"type": "TCPClientInterface", "status": True},
                    {"type": "RNodeInterface", "status": False})
    lora, tcp = pss.rns_interface_state(out)
    assert lora is False
    assert tcp is True


def test_no_tcp_interface_at_all_is_unknown_not_a_backbone_that_is_down():
    """A node nobody asked to have a backbone is not a node whose backbone
    failed. Reporting false would show a permanent amber 'Internet: down — the
    node says so' on every field node in the fleet."""
    out = _rnstatus({"type": "RNodeInterface", "status": True})
    assert pss.rns_interface_state(out)[1] is None


def test_a_configured_tcp_interface_that_is_down_is_reported_down():
    out = _rnstatus({"type": "TCPClientInterface", "status": False})
    assert pss.rns_interface_state(out)[1] is False


def test_the_local_shared_instance_server_is_not_a_backbone():
    out = _rnstatus({"type": "TCPServerInterface", "status": True})
    assert pss.rns_interface_state(out)[1] is None


# ---- faults ----------------------------------------------------------------

def test_a_full_disk_is_a_fault_at_the_same_threshold_as_the_beacon():
    from monitor.pi_health_reporter import DISK_FAULT_PCT
    assert pss.collect_faults(DISK_FAULT_PCT - 1) == []
    assert len(pss.collect_faults(DISK_FAULT_PCT)) == 1


def test_an_unknown_disk_is_not_a_fault():
    assert pss.collect_faults(None) == []


def test_a_fault_makes_the_medic_colour_the_node_red():
    from monitor.http_status import status_colour
    d = pss.build_status(pss.PiStatusInputs(faults=["Disk 99% full"]))
    assert status_colour(d) == "alert"


# ---- wifi reading ----------------------------------------------------------

def _files(**mapping):
    def read(path):
        return mapping.get(path)
    return read


def test_no_wireless_stack_means_unknown_wifi_not_wifi_that_is_off():
    connected, rssi, ip = pss.read_wifi(_files(), lambda argv, **kw: "")
    assert connected is None and rssi is None and ip == ""


def test_a_wireless_interface_that_is_down_is_honestly_not_connected():
    read = _files(**{"/proc/net/wireless": WIRELESS,
                     "/sys/class/net/wlan0/operstate": "down\n"})
    connected, _rssi, ip = pss.read_wifi(read, lambda argv, **kw: IP_OUT)
    assert connected is False and ip == ""


def test_an_interface_up_with_no_address_has_joined_nothing():
    read = _files(**{"/proc/net/wireless": WIRELESS,
                     "/sys/class/net/wlan0/operstate": "up\n"})
    connected, _rssi, ip = pss.read_wifi(read, lambda argv, **kw: "")
    assert connected is False and ip == ""


def test_wifi_up_with_an_address_reports_the_address_and_the_signal():
    read = _files(**{"/proc/net/wireless": WIRELESS,
                     "/sys/class/net/wlan0/operstate": "up\n"})
    connected, rssi, ip = pss.read_wifi(read, lambda argv, **kw: IP_OUT)
    assert connected is True and rssi == -48 and ip == "192.168.1.42"


def test_a_registered_but_absent_wireless_list_reports_no_wifi():
    read = _files(**{"/proc/net/wireless": "Inter-| sta-|\n face | tus |\n"})
    assert pss.read_wifi(read, lambda argv, **kw: "")[0] is False


# ---- the watchdog ----------------------------------------------------------

def test_systemd_that_will_not_answer_leaves_the_watchdog_unknown():
    assert pss.read_watchdog(lambda argv, **kw: None) is None


def test_watchdog_state_is_read_rather_than_assumed():
    assert pss.read_watchdog(lambda argv, **kw: "active\n") is True
    assert pss.read_watchdog(lambda argv, **kw: "inactive\n") is False


# ---- the request handler ---------------------------------------------------

class _FakeSocket:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def makefile(self, *a, **kw):
        import io
        return io.BytesIO()


def _serve_one(path, collect):
    """Drive the handler over a fake connection; return (status_line, body)."""
    import io
    handler_cls = pss.make_handler(collect)
    handler = handler_cls.__new__(handler_cls)
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.requestline = f"GET {path} HTTP/1.0"
    handler.request_version = "HTTP/1.0"
    handler.command = "GET"
    handler.path = path
    handler.client_address = ("192.168.1.9", 5000)
    handler.send_response = lambda code, *a: handler._codes.append(code)
    handler.send_header = lambda *a, **kw: None
    handler.end_headers = lambda: None
    handler.send_error = lambda code, *a, **kw: handler._codes.append(code)
    handler._codes = []
    handler.do_GET()
    return handler._codes, handler.wfile.getvalue()


def test_a_request_is_served_from_a_reading_already_taken():
    """The medic finds nodes with `curl -m3` across a whole /24. A node that
    takes four seconds to answer is a node the sweep never sees — indistinguishable
    from a node that is not there."""
    calls = []

    def collect():
        calls.append(1)
        return {"fork": PI_FORK, "n": len(calls)}

    snap = pss.Snapshot(collect, interval=999)
    assert len(calls) == 1                  # taken once, up front
    assert snap.value()["n"] == 1
    assert snap.value()["n"] == 1           # serving costs nothing more


def test_a_refresh_that_throws_keeps_the_last_good_reading():
    """A momentary failure to run a tool is not news about the node, and
    blanking would flicker every chip in VITALS grey for a cycle."""
    state = {"fail": False}

    def collect():
        if state["fail"]:
            raise OSError("rnstatus vanished")
        return {"fork": PI_FORK, "lora_online": True}

    snap = pss.Snapshot(collect, interval=999)
    state["fail"] = True
    snap.refresh()
    assert snap.value()["lora_online"] is True


def test_the_snapshot_hands_out_copies_not_its_own_dict():
    snap = pss.Snapshot(lambda: {"faults": []}, interval=999)
    snap.value()["faults"].append("mutated from outside")
    assert snap.value()["faults"] == []


def test_status_is_served_as_json():
    codes, body = _serve_one("/status", lambda: {"fork": PI_FORK})
    assert codes == [200]
    assert json.loads(body)["fork"] == PI_FORK


def test_a_query_string_still_reaches_status():
    codes, _ = _serve_one("/status?x=1", lambda: {})
    assert codes == [200]


def test_any_other_path_is_a_404_and_reveals_nothing():
    codes, body = _serve_one("/", lambda: {"fork": PI_FORK})
    assert codes == [404]
    assert body == b""


def test_a_reading_that_blows_up_is_a_500_not_a_dead_socket():
    """500 says 'I am here and I could not answer', which is a different thing
    from an unreachable host and has to read that way."""
    def boom():
        raise OSError("proc went away")
    codes, _ = _serve_one("/status", boom)
    assert codes == [500]


# ---- the contract the medic parses ----------------------------------------

def test_a_full_pi_status_round_trips_through_the_medics_own_parser():
    d = pss.build_status(pss.PiStatusInputs(
        node_name="skyfinger", board="Raspberry Pi 3 Model A Plus",
        uptime_s=3600, wifi_connected=True, wifi_rssi_dbm=-52,
        wifi_ip="192.168.1.42", lora_online=True,
        tcp_backbone_connected=False, wdt_armed=True))
    ns = parse_status(d)
    assert ns.reachable and ns.status == "ok"
    assert ns.node_name == "skyfinger"
    assert ns.wifi_connected is True and ns.wifi_rssi_dbm == -52
    assert ns.lora_online is True
    assert ns.tcp_backbone_connected is False
    assert ns.uptime_s == 3600


def test_the_pi_endpoint_uses_no_key_the_firmware_does_not_also_use():
    """One contract, one parser. A key here that an RTNode-2400 never sends is
    the beginning of a second shape."""
    rtnode_keys = {
        "fork", "fw_version", "board", "board_model", "node_name",
        "wifi_connected", "wifi_rssi", "wifi_ip", "lora_online",
        "local_tcp_server_up", "tcp_backbone_connected", "wdt_armed",
        "uptime_ms", "reset_reason", "faults",
    }
    d = pss.build_status(pss.PiStatusInputs(
        node_name="n", board="b", uptime_s=1, wifi_connected=True,
        wifi_rssi_dbm=-1, wifi_ip="1.2.3.4", lora_online=True,
        tcp_backbone_connected=True, wdt_armed=True, faults=["x"]))
    assert set(d) <= rtnode_keys


def test_the_endpoint_never_publishes_anything_identifying():
    """It answers unauthenticated requests from anyone on the LAN. A wild node
    stays untraceable to a person or a place ([[anonymity-ethos]])."""
    d = pss.build_status(pss.PiStatusInputs(
        node_name="n", board="b", uptime_s=1, wifi_connected=True,
        wifi_ip="1.2.3.4", lora_online=True))
    blob = json.dumps(d).lower()
    for leak in ("lat", "lon", "identity", "dst", "hash", "destination",
                 "operator", "owner"):
        assert leak not in blob


def test_tool_path_returns_empty_when_the_tool_is_nowhere(tmp_path):
    assert pss.tool_path("definitely-not-a-tool", dirs=(str(tmp_path),)) == ""


def test_tool_path_finds_a_tool_where_pip_user_puts_it(tmp_path):
    (tmp_path / "rnstatus").write_text("#!/bin/sh\n")
    assert pss.tool_path("rnstatus", dirs=(str(tmp_path),)) == \
        str(tmp_path / "rnstatus")
