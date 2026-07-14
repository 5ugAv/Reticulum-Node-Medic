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
    "configure_services",
    "apply_system_hardening",
    "set_hostname",
    "final_verification",
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
              "usb-Espressif_USB_JTAG_serial_debug_unit_F8:5B:1B:A6:85:00-if00")
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
        "usb-Espressif_USB_JTAG_serial_debug_unit_F8:5B:1B:A6:85:00-if00", ""))
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


def test_confirm_radio_parameters_sets_australian_defaults():
    w = wf()
    w.profile.radio.frequency_mhz = 433.0  # perturb
    result = w.steps[1][1](w)
    assert result.success
    assert w.profile.radio.frequency_mhz == 915.125
    assert w.profile.radio.bandwidth_khz == 125.0
    assert w.profile.radio.spreading_factor == 9
    assert w.profile.radio.coding_rate == 5
    assert w.profile.radio.tx_power_dbm == 17


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
    assert "RGB" in result.message
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
    assert w.profile.radio.serial_port in rendered
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
    w.steps[7][1](w)            # apply_system_hardening
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
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "1be7e0923d8c0cc95af8ddb65aad804a", ""))
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
    assert cert["reticulum_address"] == "1be7e0923d8c0cc95af8ddb65aad804a"
    assert cert["rgb_led_pin"] == 47
    assert cert["frequency_mhz"] == 915.125 and cert["spreading_factor"] == 9
    # the resolved Reticulum address is also stored back on the profile
    assert w.profile.reticulum_identity_hash == "1be7e0923d8c0cc95af8ddb65aad804a"


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


def test_all_configs_enable_transport():
    import glob
    import os
    cfg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "configs")
    files = glob.glob(os.path.join(cfg_dir, "*.conf"))
    assert len(files) == 4
    for f in files:
        assert "enable_transport = Yes" in open(f).read()
