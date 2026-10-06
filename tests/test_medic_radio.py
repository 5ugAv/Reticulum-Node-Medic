"""The new medic's OWN LoRa radio and GPS: one Heltec Wireless Tracker, wired
the way the original medic's Jonesey is (keeper, 2026-10-06: set up "firstly
its own GPS and LoRa radio, and then walk through the functions")."""
from node_profile import RadioConfig
from transport.connection import EmulatedConnection
from workflows import medic_radio as mr
from workflows.build import StepResult

RADIO = RadioConfig(frequency_mhz=915.125, bandwidth_khz=125, spreading_factor=9,
                    coding_rate=5, tx_power_dbm=17)


class _Flash:
    def __init__(self, ok=True):
        self.ok = ok
        self._usb_serial = "3C:0F:02:AA:BB:CC"

    def run_all(self, on_progress=None):
        return [StepResult("flash", self.ok, "" if self.ok else "flash failed: no board")]


def _conn(rnstatus="RNodeInterface[RNode LoRa Interface]\n    Status    : Up\n"):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("ls -1 /dev/serial/by-id/", 0,
           "usb-Espressif_USB_JTAG_serial_debug_unit_3C:0F:02:AA:BB:CC-if00\n", "")
    c.rule("rnstatus", 0, rnstatus, "")
    c.push_file = lambda local, remote: True
    return c


def _setup(conn, flash=None):
    return mr.MedicRadioSetup(conn, lambda: flash or _Flash(), radio=RADIO,
                              user="pi", home="/home/pi", sleep=lambda s: None)


def test_the_setup_installs_then_hands_over_to_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CHECK_PENDING", str(tmp_path / "pending"))
    c = _conn()
    seen = []
    real = c.run

    def run(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)
    c.run = run
    s = _setup(c)
    res = s.run_all()
    assert [r.name for r in res] == ["flash_radio", "find_its_port", "wire_services",
                                     "start_mesh", "hand_over"]
    assert all(r.success for r in res), [r.message for r in res]
    assert s.by_id.endswith("3C:0F:02:AA:BB:CC-if00")
    joined = "\n".join(seen)
    # rnsd is ENABLED but not started while this app holds the shared instance
    assert "systemctl enable rnode-splitter.service rnsd.service lxmd.service" in joined
    assert "enable --now" not in joined
    # the hand-over stops the app, starts rnsd, starts the app — from its own unit
    hand = [x for x in seen if "systemd-run" in x][0]
    assert hand.index("systemctl stop") < hand.index("restart rnsd") < hand.index("systemctl start")
    assert f"touch {tmp_path / 'pending'}" in joined     # the check runs after the restart


def test_after_the_restart_the_check_hears_radio_and_gps():
    c = _conn()
    c.rule("cat /dev/shm/nodemedic-gps.json", 0, '{"gps_frames": 12, "has_fix": false}', "")
    res = mr.MedicRadioCheck(c, home="/home/pi", sleep=lambda s: None).run_all()
    assert [r.name for r in res] == ["hear_radio", "hear_gps"]
    assert all(r.success for r in res)


def test_no_gps_reports_is_said_plainly():
    c = _conn()
    c.rule("cat /dev/shm/nodemedic-gps.json", 0, '{"gps_frames": 0}', "")
    res = mr.MedicRadioCheck(c, home="/home/pi", sleep=lambda s: None).run_all()
    assert res[-1].name == "hear_gps" and not res[-1].success


def test_the_units_name_this_user_and_board_and_the_config_the_standard_radio():
    unit = mr.splitter_unit("/dev/serial/by-id/X", "pi", "/home/pi")
    assert "User=pi" in unit and "real_port='/dev/serial/by-id/X'" in unit
    assert "symlink='/tmp/rnode-jonesey'" in unit
    assert "ExecStart=/home/pi/.local/bin/rnsd" in mr.rnsd_unit("pi", "/home/pi")
    assert "/tmp/rnode-jonesey" in mr.rnsd_unit("pi", "/home/pi")   # waits for the splitter
    assert "Requires=rnsd.service" in mr.lxmd_unit("pi", "/home/pi")
    cfg = mr.reticulum_config(RADIO)
    for line in ("port = /tmp/rnode-jonesey", "frequency = 915125000",
                 "bandwidth = 125000", "spreadingfactor = 9", "codingrate = 5",
                 "txpower = 17", "share_instance = Yes"):
        assert line in cfg, line
    assert "id_callsign" not in cfg                 # nobody's callsign rides along


def test_a_failed_flash_stops_before_anything_is_wired():
    res = _setup(_conn(), _Flash(ok=False)).run_all()
    assert len(res) == 1 and not res[0].success and "no board" in res[0].message


def test_a_radio_that_never_comes_up_is_said_plainly():
    c = _conn(rnstatus="Shared Instance[37428]\n    Status    : Up\n")
    res = mr.MedicRadioCheck(c, home="/home/pi", sleep=lambda s: None).run_all()
    assert res[-1].name == "hear_radio" and not res[-1].success
    assert "aerial" in res[-1].message


def test_lora_up_reads_rnstatus():
    assert mr.lora_up("RNodeInterface[x]\n    Status    : Up\n")
    assert not mr.lora_up("RNodeInterface[x]\n    Status    : Down\n")
    assert not mr.lora_up("")


def test_the_tracker_screen_runs_this_not_the_gps_only_sketch():
    src = open("ui/screens/firstborn_screen.py", encoding="utf-8").read()
    body = src[src.index("def _default_setup_factory"):]
    assert "MedicRadioSetup" in body and "GpsTrackerSetup" not in body
    assert 'get_board("heltec_wireless_tracker")' in body


def test_writing_a_file_works_with_the_real_local_copy(tmp_path, monkeypatch):
    """On the medic the temp file is ALREADY in /tmp; pushing it to the same
    path raised SameFileError and the wiring step failed. Run the real copy."""
    import tempfile
    from transport.connection import LocalConnection
    monkeypatch.setattr(tempfile, "tempdir", "/tmp")
    s = mr.MedicRadioSetup(LocalConnection(), lambda: _Flash(), radio=RADIO,
                           user="pi", home=str(tmp_path), sleep=lambda x: None)
    target = tmp_path / "written.conf"
    assert s._write(str(target), "hello\n", root=False)
    assert target.read_text() == "hello\n"


def test_an_already_valid_identity_is_success_not_a_failure():
    """A Tracker re-used from an earlier RNode build already has its identity:
    rnodeconf says so and changes nothing. Node Medic 2's set-up called that a
    failure and told the keeper to press RST (2026-10-06)."""
    from workflows.rnode_flash import _identity_ok
    already = ("[16:29:14] eeprom bootstrap was requested, but a valid eeprom "
               "was already present.\n[16:29:14] no changes are being made.")
    assert _identity_ok(already)
    assert _identity_ok("... bootstrapping successful ...")
    assert not _identity_ok("serial port opened, but rnode did not respond")
    src = open("workflows/rnode_flash.py", encoding="utf-8").read()
    assert "Press RST on the board once" not in src


def test_a_timed_out_local_command_leaves_no_orphan_holding_the_port():
    """subprocess.run's timeout killed only the bash wrapper: a hung rnodeconf
    kept the Tracker's port for eight minutes while the retry queued behind it
    (Node Medic 2, 2026-10-06). The runner now kills the whole process group."""
    import subprocess
    import time
    from transport.connection import _default_local_runner
    marker = f"nm-orphan-test-{time.time_ns()}"
    code, _o, err = _default_local_runner(
        ["bash", "-c", f"sleep 300 & echo {marker} > /dev/null; wait"], timeout=1)
    assert code == 255 and "timed out" in err
    time.sleep(0.5)
    left = subprocess.run(["pgrep", "-f", "sleep 300"], capture_output=True, text=True)
    # any 'sleep 300' still alive is not ours unless it is in a dead group;
    # ours was killed with its group, so the bash's children are gone too
    assert marker not in subprocess.run(["ps", "-eo", "args"], capture_output=True,
                                        text=True).stdout


def test_a_mid_write_usb_drop_is_named_and_the_identity_is_wiped_first():
    import re
    src = open("workflows/rnode_flash.py", encoding="utf-8").read()
    body = src[src.index("def _flash_custom_fork"):src.index("def _flash_serial_dfu")]
    joined = re.sub(r'"\s*\n\s*f?"', "", body)        # strings split across lines
    assert "dropped off USB part-way through the write" in joined
    assert "--eeprom-wipe" in joined
    assert 'f"erase_flash"' not in joined and "erase_flash, timeout" not in joined
    assert "--before default_reset --after hard_reset" in joined
    assert "--flash_size detect" in joined
    code_only = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "push" not in code_only.lower()                 # fragile ports: never
