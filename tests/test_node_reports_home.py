"""The two build steps that make a birthed Pi visible in VITALS, and honest.

``install_status_server`` gives the node something the medic can read;
``prove_the_node_reports`` refuses to let the certificate claim a node reports
when nobody has heard it. Both are written around the Pi 3 A+, whose radio is
not attached during the build — so the beacon half of the proof is always
"not tested yet" on the walkthrough these were built for.
"""

from node_profile import NodeProfile, NodeRole
from transport.connection import EmulatedConnection
from workflows.build import BuildWorkflow, node_addresses

PI3A_CPUINFO = ("processor : 0\nHardware : BCM2835\n"
                "Model : Raspberry Pi 3 Model A Plus Rev 1.0\n")


def conn(**rules):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("/proc/cpuinfo", 0, PI3A_CPUINFO, ""))
    c.rules.insert(0, ("--info", 1, "", ""))          # no radio on this node
    for pattern, out in rules.items():
        c.rules.insert(0, (pattern, 0, out, ""))
    return c


def wf(c=None, role=NodeRole.PROPAGATION, **profile_kw):
    return BuildWorkflow(c or conn(), NodeProfile(role=role, **profile_kw))


def step(w, name):
    idx = next(i for i, (n, _) in enumerate(w.steps) if n == name)
    return w.steps[idx][1](w)


def _decoded_payload(cmd):
    """What ``_write_remote_file`` is actually sending: the base64 between the
    echo and the pipe. shlex.quote leaves a plain base64 blob unquoted, so the
    quotes are optional here — assuming they are there reads an empty match and
    the test blames the code."""
    import base64
    import re
    blob = re.search(r"echo '?([A-Za-z0-9+/=]+)'? \| base64 -d", cmd)
    assert blob, f"no base64 payload in: {cmd}"
    return base64.b64decode(blob.group(1)).decode()


def recording(c):
    """Wrap a connection so every command it is asked to run is captured."""
    seen = []
    original = c.run

    def run(cmd, timeout=30):
        seen.append(cmd)
        return original(cmd, timeout)
    c.run = run
    return seen


# ---- the /status service ---------------------------------------------------

def test_a_transport_node_gets_no_status_endpoint():
    result = step(wf(role=NodeRole.TRANSPORT), "install_status_server")
    assert result.skipped is True and result.success is True


def test_the_endpoint_is_proved_by_asking_the_node_itself():
    c = conn(**{"curl -fsS": '{"fork":"RNM-Pi","node_name":"x"}'})
    seen = recording(c)
    w = wf(c)
    result = step(w, "install_status_server")
    assert result.success is True
    assert w.profile.serves_status is True
    assert any("127.0.0.1" in cmd and "/status" in cmd for cmd in seen)


def test_a_written_unit_that_never_answers_is_not_called_a_success_story():
    c = conn()
    c.rules.insert(0, ("curl -fsS", 7, "", "connection refused"))
    c.rules.insert(0, ("systemctl is-active rnm-status", 3, "failed", ""))
    w = wf(c)
    result = step(w, "install_status_server")
    assert w.profile.serves_status is False
    assert "did not answer" in result.message
    assert "failed" in result.message


def test_a_status_endpoint_that_fails_never_stops_the_build():
    """A stopped build never reaches hand_the_usb_port_back, and a Pi 3 A+ that
    never gets its USB socket back cannot see its own radio at all. Losing the
    dashboard is a bad evening; shipping a mute node is a wasted trip."""
    c = conn()
    c.rules.insert(0, ("curl -fsS", 7, "", ""))
    result = step(wf(c), "install_status_server")
    assert result.success is True


def test_the_unit_binds_port_eighty_without_running_as_root():
    c = conn(**{"curl -fsS": '{"fork":"RNM-Pi"}'})
    seen = recording(c)
    step(wf(c), "install_status_server")
    unit = next(cmd for cmd in seen if "rnm-status.service" in cmd)
    # written through the base64 pipe, so decode it back out
    payload = _decoded_payload(unit)
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in payload
    assert "--port 80" in payload
    assert "User=root" not in payload
    assert "NoNewPrivileges=yes" in payload


def test_the_endpoints_code_is_pushed_onto_the_node():
    c = conn(**{"curl -fsS": '{"fork":"RNM-Pi"}'})
    w = wf(c)
    step(w, "install_status_server")
    pushed = [dst for _src, dst in c.pushed]
    assert any(p.endswith("pi_status_server.py") for p in pushed)
    assert any(p.endswith("http_status.py") for p in pushed)


def test_the_package_is_pushed_even_when_the_reporter_step_did_not_run():
    """A resumed build must not install a service with no code under it."""
    c = conn(**{"curl -fsS": '{"fork":"RNM-Pi"}'})
    w = wf(c)
    step(w, "install_status_server")           # WITHOUT install_health_reporter
    assert any(dst.endswith("pi_health_reporter.py") for _s, dst in c.pushed)


# ---- addresses the medic can try -------------------------------------------

def test_the_lan_address_comes_before_the_disappearing_cable_one():
    c = conn(**{"hostname -I": "10.55.0.1 192.168.1.42"})
    assert node_addresses(wf(c)) == ["192.168.1.42", "10.55.0.1"]


def test_loopback_and_ipv6_are_not_addresses_to_try():
    c = conn(**{"hostname -I": "127.0.0.1 fe80::1 192.168.1.42"})
    assert node_addresses(wf(c)) == ["192.168.1.42"]


def test_a_node_with_no_address_offers_none():
    c = conn(**{"hostname -I": ""})
    assert node_addresses(wf(c)) == []


# ---- proving the node reports ----------------------------------------------

class _Answer:
    def __init__(self, reachable=True):
        self.reachable = reachable


def _propagation_wf(answers=True, **rules):
    c = conn(**{"hostname -I": "192.168.1.42", **rules})
    w = wf(c)
    w.http_poll = lambda host: _Answer(answers)
    return w


def test_the_proof_is_skipped_for_a_node_that_reports_nothing():
    w = wf(role=NodeRole.TRANSPORT)
    assert step(w, "prove_the_node_reports").skipped is True


def test_a_pi_with_no_radio_yet_is_not_told_its_radio_failed():
    """The Pi 3 A+ case, and it is the normal one: the radio goes on after the
    build. Every Pi certificate used to read 'did not answer over the radio'."""
    w = _propagation_wf()
    w.profile.health_dst_hash = "11" * 16
    w.profile.has_rnode = False
    result = step(w, "prove_the_node_reports")
    assert w.report_proof.beacon_not_applicable is True
    assert "after the build" in result.message
    assert "Ping node now" in result.message      # what to do once it IS attached
    assert w.report_proof.checks == []


def test_the_status_poll_is_what_actually_proves_it_at_birth():
    w = _propagation_wf()
    w.profile.health_dst_hash = "11" * 16
    step(w, "prove_the_node_reports")
    assert w.report_proof.http_answered is True
    assert w.report_proof.http_host == "192.168.1.42"


def test_a_node_that_answers_nothing_says_so_and_offers_checks():
    w = _propagation_wf(answers=False)
    w.profile.health_dst_hash = "11" * 16
    step(w, "prove_the_node_reports")
    assert w.report_proof.anything_heard is False
    assert w.report_proof.checks


def test_no_health_address_is_could_not_call_not_did_not_answer():
    w = _propagation_wf()
    w.profile.health_dst_hash = None
    step(w, "prove_the_node_reports")
    assert w.report_proof.beacon_not_applicable is True
    assert "nothing to call" in w.report_proof.summary


def test_a_node_with_its_radio_on_gets_the_beacon_asked_for():
    from workflows.report_proof import BeaconOutcome
    w = _propagation_wf()
    w.profile.health_dst_hash = "11" * 16
    w.profile.has_rnode = True
    w.beacon_probe = lambda: BeaconOutcome(heard=True, interface="RNodeInterface[x]")
    step(w, "prove_the_node_reports")
    assert w.report_proof.beacon_heard is True


def test_the_proof_never_stops_a_build_that_otherwise_worked():
    w = _propagation_wf(answers=False)
    w.profile.health_dst_hash = "11" * 16
    result = step(w, "prove_the_node_reports")
    assert result.success is True and result.skipped is False


# ---- what reaches the certificate ------------------------------------------

def test_the_certificate_records_what_was_heard_per_channel():
    from workflows.radio_proof import NOT_APPLICABLE
    from workflows.report_proof import reported_over_http, reported_over_lora
    c = conn(**{"^hostname": "skyfinger", "hostname -I": "192.168.1.42"})
    w = wf(c)
    w.http_poll = lambda host: _Answer(True)
    w.profile.health_dst_hash = "11" * 16
    step(w, "detect_hardware")
    step(w, "prove_the_node_reports")
    step(w, "birth_certificate")
    cert = w.birth_certificate
    assert reported_over_http(cert) is True
    assert cert["reports_http_host"] == "192.168.1.42"
    assert cert["reports_over_lora"] == NOT_APPLICABLE
    assert reported_over_lora(cert) is False       # not applicable is not proof


def test_a_certificate_from_a_build_with_no_proof_gains_no_claims():
    w = wf()
    step(w, "detect_hardware")
    step(w, "birth_certificate")
    assert "reports_over_http" not in w.birth_certificate


# ---- the Pi 3 A+ still gets its USB socket back ----------------------------

def test_the_new_steps_run_before_the_socket_is_handed_back():
    """Everything before hand_the_usb_port_back talks over the cable; after it
    the node is finished. Both new steps need the node reachable."""
    names = [n for n, _ in BuildWorkflow(conn(), NodeProfile()).steps]
    assert names.index("install_status_server") < names.index("hand_the_usb_port_back")
    assert names.index("prove_the_node_reports") < names.index("hand_the_usb_port_back")
    assert names.index("hand_the_usb_port_back") < names.index("birth_certificate")


def test_handing_the_usb_port_back_still_works_after_them():
    from workflows.build import _GADGET_OVERLAY, _HOST_OVERLAY
    c = conn()
    before = f"dtparam=audio=on\n{_GADGET_OVERLAY}\n"
    after = {"value": before}

    def run(cmd, timeout=30):
        if cmd.startswith("cat /boot"):
            return (0, after["value"], "")
        if "base64 -d" in cmd:
            after["value"] = _decoded_payload(cmd)
            return (0, "", "")
        if cmd.startswith("test -f /boot/firmware/config.txt"):
            return (0, "", "")
        if cmd.startswith("test -f /boot"):
            return (1, "", "")
        return (0, "ok", "")
    c.run = run
    result = step(wf(c), "hand_the_usb_port_back")
    assert result.success is True
    assert _HOST_OVERLAY in after["value"]
    assert _GADGET_OVERLAY not in after["value"]


def test_the_radio_rule_falls_back_to_vendors_when_no_radio_is_attached():
    """On a Pi 3 A+ the radio cannot be attached during the build, so its serial
    is unknowable and the vendor fallback is what ships. It must still name a
    radio from every maker this tool flashes."""
    from workflows.build import (attached_radio_serial, rnode_udev_rules,
                                 _RNODE_USB_VENDORS, RNODE_SYMLINK)
    c = conn()
    c.rules.insert(0, ("udevadm info", 1, "", "no such device"))
    w = wf(c)
    assert attached_radio_serial(w) == ""
    rules = rnode_udev_rules("")
    assert "FALLBACK" in rules
    for vid, _why in _RNODE_USB_VENDORS:
        assert f'ATTRS{{idVendor}}=="{vid}"' in rules
    assert f'SYMLINK+="{RNODE_SYMLINK.rsplit("/", 1)[-1]}"' in rules
    assert 'ATTRS{serial}' not in rules            # nothing to pin it to


def test_the_node_still_points_its_config_at_the_symlink_with_no_radio_present():
    from workflows.build import RNODE_SYMLINK
    w = wf()
    step(w, "detect_hardware")
    assert RNODE_SYMLINK in w.render_config()
    assert "/dev/ttyUSB0" not in w.render_config()
