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


def test_the_whole_setup_passes_and_writes_the_original_medics_wiring():
    c = _conn()
    s = _setup(c)
    res = s.run_all()
    assert [r.name for r in res] == ["flash_radio", "find_its_port", "wire_services",
                                     "start_mesh", "hear_radio", "hear_gps"]
    assert all(r.success for r in res), [r.message for r in res]
    assert s.by_id.endswith("3C:0F:02:AA:BB:CC-if00")


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
    res = _setup(_conn(rnstatus="Shared Instance[37428]\n    Status    : Up\n")).run_all()
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
