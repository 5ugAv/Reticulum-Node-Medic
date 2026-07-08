import pytest

from node_profile import NodeProfile, NodeHardware
from transport.connection import EmulatedConnection
from workflows.build import BuildWorkflow, StepResult, build_step

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
]

PI5_CPUINFO = "processor : 0\nModel : Raspberry Pi 5 Model B Rev 1.0\n"


def build_conn(cpuinfo=PI5_CPUINFO, rnode=False):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("/proc/cpuinfo", 0, cpuinfo, ""))
    if rnode:
        c.rules.insert(0, ("--info", 0, "[Device] RNode\nFirmware version: 1.80", ""))
    else:
        c.rules.insert(0, ("--info", 1, "", ""))
    return c


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


def test_flash_skipped_without_rnode():
    w = wf(build_conn(rnode=False))
    # detect first so has_rnode is set
    w.steps[0][1](w)
    result = w.steps[2][1](w)  # flash_rnode_firmware
    assert result.skipped is True


def test_flash_runs_with_rnode():
    w = wf(build_conn(rnode=True))
    w.steps[0][1](w)
    result = w.steps[2][1](w)
    assert result.skipped is False
    assert result.success is True


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


def test_final_verification_runs():
    w = wf(build_conn(rnode=True))
    result = w.steps[-1][1](w)
    assert result.name == "final_verification"
    assert result.success is True


def test_all_configs_enable_transport():
    import glob
    import os
    cfg_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "configs")
    files = glob.glob(os.path.join(cfg_dir, "*.conf"))
    assert len(files) == 4
    for f in files:
        assert "enable_transport = Yes" in open(f).read()
