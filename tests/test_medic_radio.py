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


def _conn(rnstatus="RNodeInterface[RNode LoRa Interface]\n    Status    : Up\n",
          helper=False):
    """A medic WITHOUT the root radio helper by default (one cloned before
    2026-10-08: full sudo, the old road); helper=True is a clone from now on,
    whose sudo is scoped and whose radio set-up goes through the helper."""
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule(f"test -x {mr.RADIO_HELPER}", 0 if helper else 1, "", "")
    c.rule("ls -1 /dev/serial/by-id/", 0,
           "usb-Espressif_USB_JTAG_serial_debug_unit_3C:0F:02:AA:BB:CC-if00\n", "")
    c.rule("rnstatus", 0, rnstatus, "")
    c.rule("systemctl is-active rnode-splitter.service rnsd.service", 0, "active\nactive\n", "")
    c.rule("cat /home/pi/.reticulum-node-medic/onboard.json", 1, "", "")
    c.push_file = lambda local, remote: True
    return c


def _setup(conn, flash=None, other=""):
    s = mr.MedicRadioSetup(conn, lambda: flash or _Flash(), radio=RADIO,
                           user="pi", home="/home/pi", sleep=lambda s: None)
    s._looks_like_another_board = lambda: other        # no real detector in tests
    return s


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
                                     "start_mesh", "lock_off", "hand_over"]
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
    # the lock-off comes AFTER the services are written and enabled, and the
    # pending mark only after the restart was queued (adversarial review)
    assert joined.rindex("onboard.json") > joined.index("systemctl enable")
    assert joined.index("touch ") > joined.index("systemd-run")
    assert "--collect --unit=nm-radio-handover-" in hand


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
    assert "aerial" not in res[-1].message            # never blame the hardware first
    assert "Try again" in res[-1].message


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
    """A Tracker re-used from an earlier RNode build already has its identity.
    The set-up wipes it first and provisions fresh under THIS medic; if the
    wipe did not take, "already present" means the OLD identity stayed."""
    from workflows.rnode_flash import _identity_ok
    already = ("[16:29:14] eeprom bootstrap was requested, but a valid eeprom "
               "was already present.\n[16:29:14] no changes are being made.")
    # after --eeprom-wipe an identity that is STILL "already present" is the
    # OLD owner's: a failure with its own plain sentence, never success
    assert not _identity_ok(already)
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
    assert "--before default_reset --after no_reset" in joined     # pieces
    # the app is booted by a DTR-low RTS pulse, NOT esptool's --after
    # hard_reset, which a native-USB S3 ignores (left the Tracker in download
    # mode, dark, 2026-10-06)
    assert "--after hard_reset read_mac" not in joined
    assert "_reset_into_app(" in joined and "_wait_for_rnode(" in joined
    assert "--flash_size detect" in joined
    code_only = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "push" not in code_only.lower()                 # fragile ports: never


def test_a_failed_hand_over_unlocks_the_board_so_try_again_can_flash(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CHECK_PENDING", str(tmp_path / "pending"))
    c = _conn()
    c.rule("systemd-run", 1, "", "Failed to start transient service unit")
    seen = []
    real = c.run

    def run(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)
    c.run = run
    res = _setup(c).run_all()
    assert res[-1].name == "hand_over" and not res[-1].success
    assert "touch " not in "\n".join(seen)                 # no pending mark
    assert "Try again" in res[-1].message


def test_a_retry_after_lock_off_skips_the_flash():
    c = _conn()
    c.rules.insert(0, ("cat /home/pi/.reticulum-node-medic/onboard.json", 0,
                       '{"jonesey_lora": "3C:0F:02:AA:BB:CC"}', ""))
    s = _setup(c, flash=_Flash(ok=False))              # a flash WOULD fail
    res = s.run_all()
    assert res[0].name == "flash_radio" and res[0].success and res[0].skipped
    assert s.by_id.endswith("3C:0F:02:AA:BB:CC-if00")


def test_the_radio_check_names_the_medics_own_failing_service_not_the_aerial():
    c = _conn(rnstatus="Shared Instance[37428]\n    Status    : Up\n")
    c.rules.insert(0, ("systemctl is-active rnode-splitter.service rnsd.service", 3,
                       "active\ninactive\n", ""))
    c.rules.insert(0, ("journalctl", 0, "rnsd: splitter port never appeared", ""))
    res = mr.MedicRadioCheck(c, home="/home/pi", sleep=lambda s: None).run_all()
    assert not res[-1].success and "the mesh service did not start" in res[-1].message
    assert "aerial" not in res[-1].message


def test_a_board_the_detector_is_sure_is_not_a_tracker_is_refused():
    res = _setup(_conn(), other="Heltec V4").run_all()
    assert len(res) == 1 and not res[0].success
    assert "looks like a Heltec V4" in res[0].message and "Try again" in res[0].message


def test_the_tracker_app_image_is_written_in_retried_pieces():
    """Node Medic 2's Tracker dropped off USB at the first big flash region on
    three cables, two sockets, with and without the stub; 64 KB pieces with a
    retry each landed the whole image. The flasher now writes that way."""
    from workflows.rnode_flash import RNodeFlashWorkflow
    from workflows.rnode_boards import get_board
    seen = []

    class C(EmulatedConnection):
        def run(self, cmd, timeout=30, **k):
            seen.append(cmd)
            if cmd.startswith("stat -c %s"):
                return 0, str(3 * 65536 + 10), ""
            if "write_flash" in cmd:
                # the second part fails once, then succeeds
                if "0x20000" in cmd and sum("0x20000" in c for c in seen) == 1:
                    return 1, "", "A serial exception error occurred: write failed"
                return 0, "Hash of data verified.", ""
            return 0, "", ""
    wf = RNodeFlashWorkflow(C(), get_board("heltec_wireless_tracker"), port="/dev/ttyACM0")
    code, out, _e = wf._write_app_in_pieces("/x/app.bin", "AA:BB", tries=3)
    assert code == 0
    writes = [c for c in seen if "write_flash" in c]
    assert [c.split()[-2] for c in writes] == ["0x10000", "0x20000", "0x20000", "0x30000", "0x40000"]
    assert all("--before default_reset --after no_reset" in c for c in writes)
    boots = [c for c in seen if "s.dtr=False; s.rts=True" in c]     # boots the app at the end
    assert len(boots) == 1 and "--after hard_reset" not in " ".join(seen)
    assert seen.index(boots[0]) > seen.index(writes[-1])


# ---------------------------------------------------------------------------
# The root radio helper (2026-10-08). A new medic sets up its own radio AFTER
# its clone flow has scoped its sudo, so every privileged step must be one
# exact, whitelisted command — never a unit the app wrote, a `sh -c`, or a
# systemd-run (provisioning/sudoers.d/nodemedic, NM_RADIO_SETUP).
# ---------------------------------------------------------------------------

_BY_ID = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_3C:0F:02:AA:BB:CC-if00"


def _watched(c):
    seen = []
    real = c.run

    def run(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)
    c.run = run
    return seen


def test_with_the_helper_every_privileged_step_is_an_exact_command(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CHECK_PENDING", str(tmp_path / "pending"))
    c = _conn(helper=True)
    seen = _watched(c)
    res = _setup(c).run_all()
    assert all(r.success for r in res), [r.message for r in res]
    assert [x for x in seen if x.startswith("sudo ")] == [
        f"sudo -n {mr.RADIO_HELPER} --port {_BY_ID}",
        "sudo -n systemctl enable rnode-splitter.service rnsd.service lxmd.service",
        "sudo -n systemctl restart rnode-splitter.service",
        f"sudo -n systemctl start --no-block {mr.HANDOVER_UNIT}",
    ]
    joined = "\n".join(seen)
    assert "/etc/systemd/system" not in joined          # the helper writes the units
    assert "sh -c" not in joined and "systemd-run" not in joined
    # still: the pending mark only once the restart is queued
    assert joined.index("touch ") > joined.index(mr.HANDOVER_UNIT)
    # and the lock-off after the services are enabled
    assert joined.rindex("onboard.json") > joined.index("systemctl enable")


def test_with_the_helper_and_no_kiosk_the_mesh_restart_is_exact_too(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CHECK_PENDING", str(tmp_path / "pending"))
    c = _conn(helper=True)
    c.rules.insert(0, (f"systemctl cat {mr.KIOSK_UNIT}", 1, "", "No files found"))
    seen = _watched(c)
    res = _setup(c).run_all()
    assert res[-1].name == "hand_over" and res[-1].success
    assert "sudo -n systemctl restart rnsd.service lxmd.service" in seen


def test_with_the_helper_a_failed_hand_over_still_unlocks_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "CHECK_PENDING", str(tmp_path / "pending"))
    c = _conn(helper=True)
    c.rules.insert(0, (f"start --no-block {mr.HANDOVER_UNIT}", 1, "", "Access denied"))
    seen = _watched(c)
    res = _setup(c).run_all()
    assert res[-1].name == "hand_over" and not res[-1].success
    assert "touch " not in "\n".join(seen)                 # no pending mark
    assert "Try again" in res[-1].message


def test_a_helper_that_refuses_stops_before_anything_is_enabled():
    c = _conn(helper=True)
    c.rules.insert(0, (f"{mr.RADIO_HELPER} --port", 2, "RADIO_FAIL: not plugged in", ""))
    seen = _watched(c)
    res = _setup(c).run_all()
    assert res[-1].name == "wire_services" and not res[-1].success
    assert not any("systemctl enable" in x for x in seen)


def _helper_module():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("radio_units_helper",
                                                  mr.RADIO_HELPER_SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_helper_writes_exactly_the_units_the_set_up_would_have_written():
    """The helper runs as root and imports nothing from the user-writable tree,
    so it carries a COPY of the unit text. The copy must not drift."""
    h = _helper_module()
    for user, home in (("pi", "/home/pi"), ("nodemedic", "/home/nodemedic")):
        assert h.splitter_unit(_BY_ID, user, home) == mr.splitter_unit(_BY_ID, user, home)
        assert h.rnsd_unit(user, home) == mr.rnsd_unit(user, home)
        assert h.lxmd_unit(user, home) == mr.lxmd_unit(user, home)
    assert (h.SPLIT_PORT, h.GPS_STATE, h.KIOSK_UNIT, h.HANDOVER_UNIT) == \
        (mr.SPLIT_PORT, mr.GPS_STATE, mr.KIOSK_UNIT, mr.HANDOVER_UNIT)


def test_the_hand_over_unit_stops_the_app_restarts_the_mesh_then_starts_the_app():
    unit = _helper_module().handover_unit()
    assert unit.index("systemctl stop reticulum-node-medic.service") \
        < unit.index("restart rnsd.service lxmd.service") \
        < unit.index("systemctl start reticulum-node-medic.service")
    assert "Type=oneshot" in unit and "[Install]" not in unit      # started, never enabled
    assert "User=" not in unit


def test_the_helper_refuses_anything_but_one_plugged_in_by_id_port(monkeypatch):
    """sudo matches the rule's trailing `*` across arguments, so the helper —
    not sudo — is what refuses extra arguments and odd ports."""
    import pytest
    h = _helper_module()
    monkeypatch.setattr(h.os.path, "islink", lambda p: True)
    monkeypatch.setattr(h.os.path, "realpath", lambda p: "/dev/ttyACM0")
    assert h.checked_port(["--port", _BY_ID]) == _BY_ID
    for argv in ([], ["--port"], ["--port", _BY_ID, "--port", _BY_ID],
                 ["--port", _BY_ID, "x"], ["--device", _BY_ID],
                 ["--port", "/dev/sda"], ["--port", "/dev/serial/by-id/../../sda"],
                 ["--port", "/dev/serial/by-id/x'y"], ["--port", "/dev/serial/by-id/a\nUser=root"],
                 ["--port", "/dev/serial/by-id/a b"]):
        with pytest.raises(SystemExit):
            h.checked_port(argv)
    monkeypatch.setattr(h.os.path, "realpath", lambda p: "/dev/mmcblk0")
    with pytest.raises(SystemExit):
        h.checked_port(["--port", _BY_ID])                  # must point at a USB tty
    monkeypatch.setattr(h.os.path, "islink", lambda p: False)
    with pytest.raises(SystemExit):
        h.checked_port(["--port", _BY_ID])                  # must be plugged in


def test_the_helper_takes_its_user_from_sudo_only(monkeypatch):
    import pytest
    h = _helper_module()
    for bad in ("", "root", "../etc", "Pi"):
        monkeypatch.setenv("SUDO_USER", bad)
        with pytest.raises(SystemExit):
            h.calling_user()
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(SystemExit):
        h.calling_user()
