import pytest

from node_profile import NodeProfile, NodeHardware
from transport.connection import EmulatedConnection
from workflows.build import (
    BuildWorkflow, StepResult, build_step,
    PACKAGE_DIR, REMOTE_PACKAGE_DIR, REMOTE_ASSET_DIR,
)

EXPECTED_STEPS = [
    "detect_hardware",
    "confirm_radio_parameters",
    "flash_rnode_firmware",
    "set_firmware_radio_parameters",
    "write_reticulum_config",
    "install_software_stack",
    "install_radio_rule",
    "configure_services",
    "install_health_reporter",
    "apply_system_hardening",
    "set_hostname",
    "final_verification",
    "hand_the_usb_port_back",
    "birth_certificate",
]

PI5_CPUINFO = "processor : 0\nModel : Raspberry Pi 5 Model B Rev 1.0\n"


def build_conn(cpuinfo=PI5_CPUINFO, rnode=False):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("/proc/cpuinfo", 0, cpuinfo, ""))
    if rnode:
        # real rnodeconf --info shape: no literal "RNode", but "Firmware version"
        c.rules.insert(0, ("--info", 0,
                           "Device info:\n\tProduct : Heltec LoRa32 v3 850 - 950 MHz\n"
                           "\tFirmware version   : 1.86", ""))
    else:
        c.rules.insert(0, ("--info", 1, "", ""))
    return c


def test_detect_has_rnode_from_real_info_format():
    # a genuine RNode's --info never contains "RNode" (it says Product/Firmware);
    # a blank board replies "RNode did not respond". has_rnode must not invert.
    w = wf(build_conn(rnode=True))
    w.steps[0][1](w)
    assert w.profile.has_rnode is True

    blank = build_conn()
    blank.rules.insert(0, ("--info", 0,
                           "Serial port opened, but RNode did not respond.", ""))
    w2 = wf(blank)
    w2.steps[0][1](w2)
    assert w2.profile.has_rnode is False        # "RNode did not respond" != present


def wf(conn=None, profile=None):
    return BuildWorkflow(conn or build_conn(), profile or NodeProfile())


def test_steps_registered_in_order():
    names = [name for name, _ in wf().steps]
    assert names == EXPECTED_STEPS


def test_run_all_healthy_completes_all_steps():
    w = wf(build_conn(rnode=True))
    w.run_all()
    assert len(w.results) == len(EXPECTED_STEPS)
    assert all(r.success for r in w.results)
    assert w.current_index == len(EXPECTED_STEPS)


def test_detect_rnode_port_by_id_resolves_ttyacm():
    from workflows.build import detect_rnode_port
    conn = EmulatedConnection()
    conn.rule("ls /dev/serial/by-id/", 0,
              "usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:03:00:03-if00")
    conn.rule("readlink -f", 0, "/dev/ttyACM0")
    assert detect_rnode_port(conn) == "/dev/ttyACM0"


def test_detect_rnode_port_fallback_to_ttyusb():
    from workflows.build import detect_rnode_port
    conn = EmulatedConnection()
    conn.rule("ls /dev/serial/by-id/", 0, "")
    conn.rule("ls /dev/ttyACM*", 1, "")
    conn.rule("ls /dev/ttyUSB*", 0, "/dev/ttyUSB0")
    assert detect_rnode_port(conn) == "/dev/ttyUSB0"


def test_detect_rnode_port_none_when_no_serial():
    from workflows.build import detect_rnode_port
    conn = EmulatedConnection(default_code=1, default_stdout="")
    assert detect_rnode_port(conn) is None


def test_detect_hardware_sets_ttyacm_port():
    conn = build_conn(rnode=True)
    conn.rules.insert(0, ("ls /dev/serial/by-id/", 0,
        "usb-Espressif_USB_JTAG_serial_debug_unit_02:00:00:03:00:03-if00", ""))
    conn.rules.insert(0, ("readlink -f", 0, "/dev/ttyACM0", ""))
    w = wf(conn)
    w.steps[0][1](w)
    assert w.profile.radio.serial_port == "/dev/ttyACM0"   # not the ttyUSB0 default


def test_detect_hardware_parses_pi5():
    w = wf(build_conn(cpuinfo=PI5_CPUINFO, rnode=True))
    result = w.steps[0][1](w)
    assert result.success
    assert w.profile.hardware is NodeHardware.PI_5


def test_detect_hardware_empty_cpuinfo_fails_gracefully():
    conn = build_conn(cpuinfo="")
    conn.rules.insert(0, ("/proc/cpuinfo", 1, "", ""))
    w = wf(conn)
    result = w.steps[0][1](w)
    assert result.success is False


def test_confirm_radio_parameters_keeps_operator_values():
    # An explicitly customised radio is KEPT — this step used to stomp the
    # BIRTH form's values with hardcoded 915.125 (fixed 2026-07-31).
    w = wf()
    w.profile.radio.frequency_mhz = 433.0  # operator-set
    result = w.steps[1][1](w)
    assert result.success
    assert w.profile.radio.frequency_mhz == 433.0
    assert "433" in result.message


def test_confirm_radio_parameters_untouched_gets_defaults():
    # A factory-default radio picks up the tool-wide saved defaults (which on
    # a dev box with no store fall back to canonical).
    w = wf()
    result = w.steps[1][1](w)
    assert result.success
    assert w.profile.radio.bandwidth_khz == 125.0
    assert w.profile.radio.spreading_factor == 9
    assert w.profile.radio.coding_rate == 5


def blank_board_conn():
    """A board is physically attached (a ttyACM port exists) but BLANK — --info
    fails — and the firmware cache is seeded so it can be flashed offline."""
    c = build_conn(rnode=False)                       # pi5 cpuinfo, --info exit 1
    c.rules.insert(0, ("ls /dev/ttyACM", 0, "/dev/ttyACM0", ""))
    c.rules.insert(0, ("curl -fsI", 7, "", ""))       # offline
    c.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip",
                       0, "rnode_firmware_heltec32v4pa.zip", ""))
    c.rules.insert(0, ("--autoinstall", 0,
                       "RNode Firmware autoinstallation complete!", ""))
    return c


def test_flash_skipped_when_no_board_attached():
    w = wf(build_conn(rnode=False))          # no port, no firmware -> nothing there
    w.steps[0][1](w)
    assert w.profile.rnode_present is False
    result = w.steps[2][1](w)                # flash_rnode_firmware
    assert result.skipped is True
    assert "No RNode attached" in result.message


def test_flash_skips_already_provisioned_board():
    w = wf(build_conn(rnode=True))
    w.steps[0][1](w)
    assert w.profile.has_rnode is True and w.profile.rnode_present is True
    result = w.steps[2][1](w)
    assert result.skipped is True
    assert "already provisioned" in result.message


def test_flash_births_blank_attached_board_stock_when_no_rgb(monkeypatch):
    import workflows.rnode_v4_rgb as rgb
    monkeypatch.setattr(rgb, "rgb_firmware_available", lambda *a, **k: False)
    conn = blank_board_conn()
    w = wf(conn)
    w.steps[0][1](w)                         # detect: present but blank
    assert w.profile.rnode_present is True and w.profile.has_rnode is False
    result = w.steps[2][1](w)               # flash_rnode_firmware
    assert result.success is True and result.skipped is False
    assert w.profile.has_rnode is True       # now provisioned
    # it used the proven offline pre-fed autoinstall (V4 = index 9) from the cache
    assert any("--autoinstall" in c and "printf" in c and "--nocheck" in c
               for c in conn.history)
    # a brand-new board is birthed in two autoinstall passes
    assert sum(1 for c in conn.history if "--autoinstall" in c) == 2
    assert "stock" in result.message         # notes RGB was not applied


def test_flash_births_blank_v4_with_rgb_when_available(monkeypatch):
    # the medic has the RGB firmware compiled -> carry it to the target Pi and
    # overlay it so the Pi+RNode radio gets the status LED too
    import workflows.rnode_v4_rgb as rgb
    monkeypatch.setattr(rgb, "rgb_firmware_available", lambda *a, **k: True)
    conn = blank_board_conn()
    w = wf(conn)
    w.steps[0][1](w)
    result = w.steps[2][1](w)
    assert result.success is True and w.profile.has_rnode is True
    assert "NeoPixel" in result.message and w.profile.rnode_rgb_pin == rgb.NEOPIXEL_PIN
    # stock-provisioned, then the compiled bin was carried + overlaid + restamped
    assert any("--autoinstall" in c for c in conn.history)
    assert any(local.endswith("RNode_Firmware.ino.bin")
               for local, _ in conn.pushed)
    assert any("esptool" in c and "0x10000" in c for c in conn.history)
    assert any("--firmware-hash" in c for c in conn.history)


def test_flash_blank_board_offline_without_cache_fails():
    conn = blank_board_conn()
    # remove the cached firmware -> offline blank board cannot be birthed
    conn.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip", 2, "", ""))
    w = wf(conn)
    w.steps[0][1](w)
    result = w.steps[2][1](w)
    assert result.success is False
    assert "cached firmware" in result.message


def test_write_config_substitutes_placeholders():
    w = wf(build_conn(rnode=True))
    w.steps[0][1](w)
    w.steps[1][1](w)
    result = w.steps[4][1](w)  # write_reticulum_config
    assert result.success
    rendered = w.rendered_config
    assert "{{" not in rendered
    assert "}}" not in rendered
    # NOT the detected port. On the board this tool is built around the radio
    # cannot be attached during the build, so serial_port is whatever the
    # default happened to be — and it was baked into every node ever birthed.
    from workflows.build import RNODE_SYMLINK
    assert RNODE_SYMLINK in rendered
    assert "/dev/ttyUSB0" not in rendered
    assert "enable_transport = Yes" in rendered


def test_write_config_selects_pi5_template():
    w = wf(build_conn(rnode=True))
    w.profile.hardware = NodeHardware.PI_5
    w.steps[1][1](w)
    w.steps[4][1](w)
    assert "RTT-PI5" in w.rendered_config


def test_write_config_selects_pi_zero_template():
    w = wf()
    w.profile.hardware = NodeHardware.PI_ZERO_2W
    w.steps[1][1](w)
    w.steps[4][1](w)
    assert "RTT-ZERO" in w.rendered_config


def test_failed_step_stops_run_all_and_does_not_advance():
    # make write_reticulum_config's heredoc write fail
    conn = build_conn(rnode=True)
    conn.rules.insert(0, ("cat > ", 1, "", "disk full"))
    w = wf(conn)
    w.run_all()
    # should stop at write_reticulum_config (index 4)
    assert w.current_index == 4
    assert w.results[-1].success is False
    assert w.results[-1].name == "write_reticulum_config"


def test_resume_from():
    w = wf(build_conn(rnode=True))
    w.resume_from("write_reticulum_config")
    assert w.current_index == 4
    w.run_all()
    # resumed run should run from write_reticulum_config onward
    names = [r.name for r in w.results]
    assert names[0] == "write_reticulum_config"
    assert "final_verification" in names


def test_run_all_fires_progress():
    events = []
    w = wf(build_conn(rnode=True))
    w.run_all(on_progress=events.append)
    assert len(events) == len(EXPECTED_STEPS)


def _install_conn(rns=False, lxmf=False, wheels=True, internet=True):
    """A node with controllable install preconditions."""
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("import RNS", 0 if rns else 1, "", ""))
    c.rules.insert(0, ("import LXMF", 0 if lxmf else 1, "", ""))
    c.rules.insert(0, (f"ls {REMOTE_PACKAGE_DIR}/*.whl", 0 if wheels else 2, "", ""))
    c.rules.insert(0, ("curl -fsI", 0 if internet else 7, "", ""))
    return c


def test_install_uses_remote_wheels_when_missing_and_carried(monkeypatch, tmp_path):
    # assets/packages/*.whl is gitignored, so it is ABSENT in a fresh CI checkout
    # (present only on a dev machine that has seeded the cache). _push_dir globs
    # the real filesystem, so relying on those files made this test pass locally
    # yet fail in CI. Point PACKAGE_DIR at a temp dir holding a stand-in wheel so
    # the carried-wheel push is asserted hermetically, independent of the host.
    pkg = tmp_path / "packages"
    pkg.mkdir()
    (pkg / "rns-1.0.0-py3-none-any.whl").write_bytes(b"stand-in wheel")
    monkeypatch.setattr("workflows.build.PACKAGE_DIR", str(pkg))
    conn = _install_conn(rns=False, lxmf=False, wheels=True)
    from workflows.build import install_software_stack
    install_software_stack(wf(conn))
    assert any(dst.startswith(REMOTE_PACKAGE_DIR) for _, dst in conn.pushed)
    pip_cmd = next(c for c in conn.history if "pip3 install" in c)
    assert "--no-index" in pip_cmd
    assert REMOTE_PACKAGE_DIR in pip_cmd          # installs from the node path
    assert PACKAGE_DIR not in pip_cmd             # NOT the tool-local path
    assert "--user" in pip_cmd


def test_install_skips_when_already_present():
    conn = _install_conn(rns=True, lxmf=True)
    from workflows.build import install_software_stack
    result = install_software_stack(wf(conn))
    assert result.success
    assert not any("pip3 install" in c for c in conn.history)   # nothing to do


def test_install_online_fallback_when_no_wheels():
    conn = _install_conn(rns=False, lxmf=True, wheels=False, internet=True)
    from workflows.build import install_software_stack
    result = install_software_stack(wf(conn))
    assert result.success
    pip_cmd = next(c for c in conn.history if "pip3 install" in c)
    assert "--no-index" not in pip_cmd            # online path
    assert "rns" in pip_cmd and "lxmf" not in pip_cmd  # only the missing one


def test_install_fails_without_wheels_or_internet():
    conn = _install_conn(rns=False, lxmf=False, wheels=False, internet=False)
    from workflows.build import install_software_stack
    result = install_software_stack(wf(conn))
    assert result.success is False


def test_hardening_stages_deb_on_node_and_uses_remote_path():
    conn = build_conn(rnode=True)
    w = wf(conn)
    # BY NAME, not by index. The old form was w.steps[8][1] with a comment
    # admitting "index shifts if steps change" — and it duly broke the moment a
    # step was inserted before it. A test that has to be renumbered by hand is a
    # test that will one day be renumbered wrong.
    _run_step(w, "apply_system_hardening")
    assert any(dst == f"{REMOTE_ASSET_DIR}/log2ram.deb" for _, dst in conn.pushed)
    dpkg_cmd = next(c for c in conn.history if "dpkg -i" in c)
    assert REMOTE_ASSET_DIR in dpkg_cmd
    assert PACKAGE_DIR not in dpkg_cmd


# ---- privilege + service correctness (real-hardware fixes) --------------


def _run_step(w, name):
    idx = next(i for i, (n, _) in enumerate(w.steps) if n == name)
    return w.steps[idx][1](w)


def nonroot_conn(**extra):
    """A node where we are a non-root login user (id -u != 0), rnsd/lxmd live
    in ~/.local/bin, and everything else succeeds."""
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("id -u", 0, "1000", ""))          # not root
    c.rules.insert(0, ("id -un", 0, "nodemedic", ""))
    # services steps now ask the TARGET for its real home (the /home/{user}
    # assumption broke on targets whose home dir differs from the username)
    c.rules.insert(0, ("echo $HOME", 0, "/home/nodemedic", ""))
    c.rules.insert(0, ("command -v rnsd", 0, "/home/nodemedic/.local/bin/rnsd", ""))
    c.rules.insert(0, ("command -v lxmd", 0, "/home/nodemedic/.local/bin/lxmd", ""))
    for pattern, code, out in extra.get("rules", []):
        c.rules.insert(0, (pattern, code, out, ""))
    return c


def test_configure_services_uses_sudo_when_not_root():
    conn = nonroot_conn()
    w = wf(conn)
    _run_step(w, "configure_services")
    assert any("sudo -n tee /etc/systemd/system/rnsd.service" in c
               for c in conn.history)
    assert any("sudo -n systemctl daemon-reload" in c for c in conn.history)
    assert any("sudo -n systemctl start rnsd" in c for c in conn.history)


def test_configure_services_uses_detected_binary_path_and_user():
    conn = nonroot_conn()
    w = wf(conn)
    _run_step(w, "configure_services")
    unit_write = next(c for c in conn.history
                      if "tee /etc/systemd/system/rnsd.service" in c)
    assert "ExecStart=/home/nodemedic/.local/bin/rnsd" in unit_write
    assert "User=nodemedic" in unit_write
    assert "Environment=HOME=/home/nodemedic" in unit_write
    assert "/usr/local/bin/rnsd" not in unit_write   # not the old hardcode


def test_configure_services_runs_lxmd_as_propagation_node_after_rnsd():
    conn = nonroot_conn()
    w = wf(conn)
    _run_step(w, "configure_services")
    lxmd_unit = next(c for c in conn.history
                     if "tee /etc/systemd/system/lxmd.service" in c)
    assert "-p --service" in lxmd_unit           # runs an LXMF propagation node
    assert "After=rnsd.service" in lxmd_unit      # joins rnsd's shared instance


def test_configure_services_skips_lxmd_when_absent():
    conn = nonroot_conn(rules=[("command -v lxmd", 1, "")])  # lxmd not installed
    w = wf(conn)
    result = _run_step(w, "configure_services")
    assert result.success
    assert "rnsd" in result.message and "lxmd" not in result.message
    assert not any("lxmd.service" in c for c in conn.history)


def test_configure_services_fails_when_no_rns_tools():
    conn = nonroot_conn(rules=[("command -v rnsd", 1, ""),
                               ("command -v lxmd", 1, "")])
    w = wf(conn)
    result = _run_step(w, "configure_services")
    assert result.success is False


def test_set_hostname_uses_sudo():
    conn = nonroot_conn()
    w = wf(conn)
    w.profile.hostname = "faith"
    _run_step(w, "set_hostname")
    assert any("sudo -n hostnamectl set-hostname faith" in c
               for c in conn.history)


def test_set_firmware_params_bakes_canonical_params_at_birth():
    conn = nonroot_conn()
    w = wf(conn)
    w.profile.has_rnode = True
    result = _run_step(w, "set_firmware_radio_parameters")
    assert result.success
    assert w.profile.radio.firmware_hash_set is True
    # rnodeconf needs the mode flag WITH the params, or it silently ignores them
    tnc = next(c for c in conn.history if "rnodeconf" in c and "--freq" in c)
    assert "--tnc" in tnc
    assert "--freq 915125000" in tnc and "--sf 9" in tnc and "--txp 17" in tnc
    assert "--set-firmware-hash" not in tnc              # flag does not exist
    # and the board is returned to host-controlled mode for rnsd
    assert any(c.rstrip().endswith("-N") for c in conn.history)


def test_set_firmware_params_skipped_without_rnode():
    conn = nonroot_conn()
    w = wf(conn)
    w.profile.has_rnode = False
    result = _run_step(w, "set_firmware_radio_parameters")
    assert result.skipped is True


def test_root_session_omits_sudo():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("id -u", 0, "0", ""))          # root
    conn.rules.insert(0, ("id -un", 0, "root", ""))
    conn.rules.insert(0, ("command -v rnsd", 0, "/usr/local/bin/rnsd", ""))
    conn.rules.insert(0, ("command -v lxmd", 1, "", ""))
    w = wf(conn)
    _run_step(w, "configure_services")
    assert not any("sudo" in c for c in conn.history)


def test_final_verification_runs():
    w = wf(build_conn(rnode=True))
    result = _run_step(w, "final_verification")
    assert result.name == "final_verification"
    assert result.success is True


def test_birth_certificate_records_reachability_and_build_details():
    conn = build_conn(rnode=True)
    conn.rules.insert(0, ("^hostname", 0, "rtt-prop-01", ""))
    conn.rules.insert(0, ("^hostname -I", 0, "192.168.1.42 10.0.0.9", ""))  # first
    conn.rules.insert(0, ("ip route get", 0, "wlan0", ""))
    conn.rules.insert(0, ("class/net/wlan0/address", 0, "b8:27:eb:aa:bb:cc", ""))
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "a1b2c3d4e5f60718293a4b5c6d7e8f90", ""))
    w = wf(conn)
    w.steps[0][1](w)                             # detect (sets radio port etc.)
    w.profile.rnode_rgb_pin = 47                 # RGB build was flashed
    result = _run_step(w, "birth_certificate")
    assert result.success is True
    cert = w.birth_certificate
    assert cert["hostname"] == "rtt-prop-01"
    assert cert["ssh_address"] == "rtt-prop-01.local"
    assert cert["ip_addresses"] == ["192.168.1.42", "10.0.0.9"]
    assert cert["mac_address"] == "b8:27:eb:aa:bb:cc"
    assert cert["reticulum_address"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert cert["rgb_led_pin"] == 47
    assert cert["frequency_mhz"] == 915.125 and cert["spreading_factor"] == 9
    # the resolved Reticulum address is also stored back on the profile
    assert w.profile.reticulum_identity_hash == "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def test_birth_certificate_handles_missing_reticulum_address():
    conn = build_conn(rnode=False)
    conn.rules.insert(0, ("^hostname", 0, "rtt-node", ""))
    conn.rules.insert(0, ("^hostname -I", 0, "192.168.1.42", ""))  # first
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "", ""))   # no identity yet
    w = wf(conn)
    w.steps[0][1](w)
    result = _run_step(w, "birth_certificate")
    assert result.success is True
    assert w.birth_certificate["reticulum_address"] is None
    assert w.birth_certificate["rgb_led_pin"] is None            # stock, no RGB


def test_health_reporter_skipped_for_non_propagation_node():
    # a default (UNKNOWN role) or transport node needs no Python reporter
    w = wf(build_conn(rnode=True))
    result = _run_step(w, "install_health_reporter")
    assert result.success is True
    assert result.skipped is True
    assert w.profile.health_dst_hash is None


def test_health_reporter_installed_for_propagation_node():
    from node_profile import NodeRole
    HEALTH_DST = "11223344556677889900aabbccddeeff"
    conn = build_conn(rnode=True)
    # the on-node dst read (after the service creates its identity)
    conn.rules.insert(0, ("RNS.Destination.IN", 0, HEALTH_DST, ""))
    profile = NodeProfile(role=NodeRole.PROPAGATION, has_solar_controller=True)

    seen = []
    _orig = conn.run

    def run(cmd, timeout=30):
        seen.append(cmd)
        return _orig(cmd, timeout)

    conn.run = run
    w = wf(conn, profile)
    result = _run_step(w, "install_health_reporter")

    assert result.success is True and result.skipped is False
    # rostered under the health destination the beacon announces from
    assert w.profile.health_dst_hash == HEALTH_DST
    # a real systemd unit was written + started, stamped with the power source
    unit_writes = [c for c in seen if "rnm-health.service" in c]
    assert unit_writes and "--power-source solar" in unit_writes[0]
    assert any("systemctl enable rnm-health" in c for c in seen)
    assert any("systemctl start rnm-health" in c for c in seen)


def test_health_reporter_power_source_from_profile():
    from node_profile import NodeRole
    from workflows.build import _power_source
    assert _power_source(NodeProfile(has_solar_controller=True)) == "solar"
    assert _power_source(NodeProfile(has_battery_bank=True)) == "battery"
    assert _power_source(NodeProfile()) == "mains"


def test_health_dst_flows_onto_birth_certificate():
    from node_profile import NodeRole
    conn = build_conn(rnode=True)
    w = wf(conn, NodeProfile(role=NodeRole.PROPAGATION))
    w.profile.health_dst_hash = "11223344556677889900aabbccddeeff"
    w.steps[0][1](w)                       # detect_hardware (sets up profile)
    result = _run_step(w, "birth_certificate")
    assert result.success is True
    assert w.birth_certificate["health_dst"] == "11223344556677889900aabbccddeeff"


def test_all_configs_enable_transport():
    import glob
    import os
    cfg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "configs")
    files = glob.glob(os.path.join(cfg_dir, "*.conf"))
    assert len(files) == 4
    for f in files:
        assert "enable_transport = Yes" in open(f).read()


def test_detect_installs_rns_when_rnodeconf_missing_on_fresh_pi():
    # a stock Pi has no rnodeconf until rns is installed; detect_hardware must
    # install it up front (carried wheels) so the flash path works, not fail.
    conn = build_conn(rnode=True)
    conn.rules.insert(0, (f"ls {REMOTE_PACKAGE_DIR}/*.whl", 0, "rns-1.0-py3.whl", ""))
    state = {"installed": False}
    _orig = conn.run

    def run(cmd, timeout=30):
        if "command -v rnodeconf" in cmd:
            return (0, "", "") if state["installed"] else (1, "", "")
        if "pip3 install" in cmd and "rns" in cmd:
            state["installed"] = True
            return (0, "installed rns", "")
        return _orig(cmd, timeout)

    conn.run = run
    w = wf(conn)
    result = w.steps[0][1](w)                     # detect_hardware
    assert result.success and state["installed"]
    assert "installed rns" in result.message
    assert w.profile.has_rnode is True            # still detects the flashed board


def test_detect_fails_cleanly_when_rnodeconf_missing_and_no_wheels_or_net():
    conn = build_conn()
    conn.rules.insert(0, ("command -v rnodeconf", 1, "", ""))       # absent
    conn.rules.insert(0, (f"ls {REMOTE_PACKAGE_DIR}/*.whl", 2, "", ""))  # no wheels
    conn.rules.insert(0, ("curl -fsI", 7, "", ""))                  # offline
    w = wf(conn)
    result = w.steps[0][1](w)
    assert result.success is False and "wheelhouse" in result.message


# --- the suite must mean the same thing on the medic as on a Mac -------------
# These two pin conftest's hermetic fixture. They pass trivially on a dev Mac
# either way, which is exactly why they are worth writing down: the bug they
# guard against is INVISIBLE here and only shows on the machine the tool
# actually lives on.
#
# History: patching `roster.ROSTER_PATH` was believed to cover callers that take
# `path=ROSTER_PATH` as a default. It cannot — a default argument is bound once,
# at import. So on the medic `is_onboard("/dev/ttyACM0")` read the REAL roster,
# resolved Jonesey's real serial, and detect_rnode_port correctly refused to
# hand out the medic's own radio. Six tests went red for the product doing the
# right thing (task #64).

def test_the_host_lookups_are_neutralised_for_ordinary_tests():
    """On the medic these resolve real hardware. In the suite they must not."""
    import ui.onboard_roster as roster
    assert roster.serial_for_port("/dev/ttyACM0") == ""
    assert roster.is_onboard("/dev/ttyACM0") is False


def test_the_guard_exemption_marker_is_actually_used():
    """`onboard_guard` was declared in conftest and applied to NOTHING for
    months, so the modules it was meant to protect were silently relying on a
    stand-down that did not reach them. If this hits zero again, the exemption
    has been dropped and the guard's own tests are being neutralised."""
    import pathlib
    tests = pathlib.Path(__file__).parent
    users = [p.name for p in tests.glob("test_*.py")
             if "onboard_guard" in p.read_text()]
    assert users, "nothing carries the onboard_guard marker any more"


# --- the first word the build says to a node ------------------------------

def test_first_contact_names_the_failure_it_actually_had():
    """2026-08-10: the walkthrough's gate proved the node reachable — it had
    opened a TCP session to sshd at 10.55.0.1 to get past — and one second
    later the build's first step said "is the node reachable?". Both were about
    the same cable and one was wrong. They are different questions: does sshd
    ANSWER, and will it let us IN. ssh says which, in stderr, and cmd_output
    was throwing it away."""
    from workflows.build import first_contact_reason
    key = first_contact_reason(255, "pi@10.55.0.1: Permission denied (publickey).")
    assert "key" in key.lower() and "card" in key.lower(), \
        "an unauthorised key sends the operator to the card, not the cable"
    assert "reachable" not in key.lower(), "it plainly was reachable"

    refused = first_contact_reason(255, "ssh: connect to host port 22: Connection refused")
    assert "listening" in refused.lower() or "not running" in refused.lower()

    gone = first_contact_reason(255, "ssh: connect to host 10.55.0.1 port 22: "
                                     "Operation timed out")
    assert "power" in gone.lower(), "a Pi that stops mid-build is usually power"

    # never silently swallow something unrecognised
    odd = first_contact_reason(3, "some novel failure")
    assert "some novel failure" in odd


def test_detect_hardware_reads_the_error_rather_than_guessing():
    from tests.srcutil import func_source
    src = func_source("workflows/build.py", "detect_hardware")
    line = next(l for l in src.splitlines() if "/proc/cpuinfo" in l)
    assert "cmd_output" not in line, "cmd_output discards the reason"
    assert "connection.run" in line
    assert "first_contact_reason" in src


# --- the radio the build never gets to meet --------------------------------

def test_the_config_never_names_a_port_the_build_could_not_have_seen():
    """2026-08-10, SolarLove and 2k13. A Pi 3 A+ has ONE USB-A socket and the
    medic's cable is in it, so the radio is physically absent for the whole
    build. detect_rnode_port() looks for it, finds nothing, and NodeProfile's
    /dev/ttyUSB0 default survives into the node's Reticulum config — while the
    Heltec V4 is native USB and comes up as /dev/ttyACM0. rnsd opened a device
    that would never exist, and two nodes went into the world born, powered,
    antenna on, and mute."""
    from workflows.build import RNODE_SYMLINK
    assert RNODE_SYMLINK.startswith("/dev/")
    assert "tty" not in RNODE_SYMLINK, "a symlink, not a guess at a port name"


def test_the_udev_rule_covers_every_radio_this_tool_flashes():
    from workflows.build import rnode_udev_rules
    r = rnode_udev_rules()
    for vid, why in (("303a", "Espressif ESP32-S3 — Heltec V3/V4"),
                     ("10c4", "CP210x — older Heltec"),
                     ("1a86", "CH340"),
                     ("239a", "RAK4631 nRF52840")):
        assert f'"{vid}"' in r, f"no rule for {why}"
    assert 'SYMLINK+="rnode"' in r


def test_the_rule_hands_the_device_to_systemd_rather_than_calling_systemctl():
    """TAG+="systemd" makes dev-rnode.device exist; SYSTEMD_WANTS pulls rnsd in
    when the radio appears. The first version ran `systemctl try-restart` from
    RUN+= instead, which is discouraged (udev has its own mount namespace) and,
    worse, try-restart only acts on a unit already running — the exact state the
    bug leaves rnsd in is "up, holding a dead interface", where a restart is
    both needed and refused."""
    from workflows.build import rnode_udev_rules
    r = rnode_udev_rules()
    assert 'TAG+="systemd"' in r
    assert 'ENV{SYSTEMD_WANTS}="rnsd.service"' in r
    assert "systemctl" not in r, "do not call systemctl from a udev rule"


def test_rnsd_is_not_bound_to_a_device_unit_we_never_verified():
    """BindsTo=dev-rnode.device was added and reverted on 2026-08-10. If systemd
    does not publish that alias for a SYMLINK+= rule on this image, BindsTo
    means rnsd never starts AT ALL — worse than the bug it was meant to fix. And
    for the case that matters (a node powered up with its radio attached) it is
    unnecessary: udev creates the symlink during USB enumeration, long before
    rnsd starts after network-online.target."""
    from tests.srcutil import func_source
    src = func_source("workflows/build.py", "configure_services")
    # comments stripped: the explanation of WHY it was reverted names the
    # directive, and searching the raw source finds its own tombstone.
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "BindsTo=" not in code, \
        "do not gate the radio daemon on a device unit that was never observed"


def test_the_rule_is_written_before_the_services_start():
    """Otherwise rnsd starts first, fails to find the radio, and only recovers
    on the hot-plug path — which is the fragile one."""
    from workflows.build import _BUILD_STEPS
    names = [n for n, _ in _BUILD_STEPS]
    assert names.index("install_radio_rule") < names.index("configure_services")


# --- the port the node needs back ------------------------------------------

def test_the_node_stops_being_a_gadget_before_it_is_certified():
    """SolarLove, 2026-08-10, found by asking the node what USB devices it could
    see and being told: none. The card bakes dr_mode=peripheral so the medic can
    reach the Pi over USB. A Pi 3 A+ has ONE dwc2 controller driving its ONE
    USB-A socket, so in peripheral mode that port can only ever BE a device —
    and the finished node could never see the radio plugged into it. No ttyACM0,
    no /dev/rnode, nothing for rnsd to open."""
    from workflows.build import _BUILD_STEPS
    names = [n for n, _ in _BUILD_STEPS]
    assert "hand_the_usb_port_back" in names
    # LAST, because everything before it talks over that cable
    assert names.index("hand_the_usb_port_back") > names.index("final_verification")
    assert names.index("hand_the_usb_port_back") < names.index("birth_certificate")


def test_it_swaps_peripheral_for_host_and_not_the_other_way():
    from tests.srcutil import func_source
    from workflows.build import _GADGET_OVERLAY, _HOST_OVERLAY
    assert "peripheral" in _GADGET_OVERLAY and "host" in _HOST_OVERLAY
    src = func_source("workflows/build.py", "hand_the_usb_port_back")
    assert f"s|^{{_GADGET_OVERLAY}}|{{_HOST_OVERLAY}}|" in src or \
        ("_GADGET_OVERLAY" in src and "_HOST_OVERLAY" in src)
    assert "skipped=True" in src, "a node never put in gadget mode is left alone"


def test_a_node_that_was_never_a_gadget_is_left_alone():
    """A Pi 4/5 birthed over the network has no peripheral line to undo, and
    rewriting its boot config for no reason is how a working node breaks."""
    from tests.srcutil import func_source
    src = func_source("workflows/build.py", "hand_the_usb_port_back")
    assert "never put in gadget mode" in src


# --- a timeout has two causes and the medic can tell them apart ------------

def test_a_wedged_link_is_not_reported_as_a_brown_out():
    """SkyFinger, 2026-08-11. The build said "a Pi drawing its power from Node
    Medic can brown out under load" while the medic's own rail sat at 5.08 V
    with the undervoltage flag CLEAR, and the kernel had logged NETDEV WATCHDOG
    — the real reason — three lines away. Sending an operator to check a supply
    that is provably fine is the same failure as the old "is the node
    reachable?": a plausible sentence in place of a reading."""
    from workflows.build import _link_died_reason

    def wedged(argv):
        if argv[0] == "vcgencmd":
            return "throttled=0x0\n"
        return "cdc_ether usb0: NETDEV WATCHDOG: transmit queue 0 timed out\n"

    msg = _link_died_reason(wedged)
    assert "NETDEV WATCHDOG" in msg
    assert "power is FINE" in msg, "say the thing they would otherwise go and check"
    assert "different A-to-A cable" in msg, "and what to try when it repeats"


def test_a_real_brown_out_is_still_called_power():
    from workflows.build import _link_died_reason

    def sagging(argv):
        if argv[0] == "vcgencmd":
            return "throttled=0x50000\n"
        return ""

    msg = _link_died_reason(sagging)
    assert "power" in msg.lower()
    assert "supply" in msg.lower()


def test_no_evidence_either_way_claims_neither():
    """The honest third answer. Guessing between two causes is what this
    replaced, so silence about which must stay available."""
    from workflows.build import _link_died_reason
    msg = _link_died_reason(lambda argv: "throttled=0x0\n" if argv[0] == "vcgencmd" else "")
    assert "cannot see why" in msg
    assert "NETDEV" not in msg and "brown out" not in msg


def test_the_usb_handback_reads_back_what_it_wrote():
    """SkyFinger, 2026-08-11. The first version ran a sed expression through ssh:
    rc=0, no output, file unchanged, step reported success. The node came up
    still a gadget, still blind to its own radio, and the operator had to
    power-cycle it a second time to recover an edit that had never happened.

    A regex full of | and ^ crossing shlex.quote, bash -c and the remote shell
    has three chances to arrive as something else. Transform in Python, write
    the whole file with tee, then READ IT BACK — the operator does not find out
    until the node is assembled and mute."""
    from tests.srcutil import func_source
    src = func_source("workflows/build.py", "hand_the_usb_port_back")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "sed -i" not in code, "no regex crossing three parsers"
    assert "tee" in code and "RTTEOF" in code, "the same idiom as configure_services"
    # the read-back, and it must FAIL rather than warn
    # rindex: it reads the file BEFORE too, to transform it. The verify is the
    # read that comes AFTER the write.
    assert code.index("tee") < code.rindex("cat {boot}"), "write, then verify"
    assert "still says gadget" in src, "and say what it means for the node"
    fails = code[code.index("check ="):]
    assert "False" in fails, "a card that did not take must fail the step"
