"""Give a medic its OWN radio and GPS: one Heltec Wireless Tracker.

The original medic's radio and GPS are one board, Jonesey: a Heltec Wireless
Tracker running the medic's RNode fork, which carries LoRa AND streams GPS
frames on the same USB port. A splitter service owns the real port, hands
rnsd a virtual one (/tmp/rnode-jonesey) and skims the GPS frames into a small
state file the tool reads. rnsd and lxmd run as system services.

A clone arrived with none of that — the old first-run step flashed a
GPS-only sketch, and "its own mesh radio is a separate job, still to come"
(keeper, 2026-10-06: the new medic should set up "firstly its own GPS and LoRa
radio, and then walk through the functions"). This is that job, run on the
medic itself:

1. flash the Tracker with the carried fork image (the proven RNode flash path,
   radio settings included),
2. wire the splitter, rnsd and lxmd units and the Reticulum config around it,
3. start them, and prove both halves: rnsd reports the LoRa interface up, and
   the splitter writes GPS state (a satellite fix is not required here).

Everything that touches the system is built as text here and handed to the
connection, so the logic is unit-tested with no hardware. The units name THIS
user and home; the by-id path is THIS board's (a fingerprint of one device,
so it is never stored in the repository).
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional

from workflows.build import StepResult

SPLIT_PORT = "/tmp/rnode-jonesey"          # the name the tool and Self Diagnose read
GPS_STATE = "/dev/shm/nodemedic-gps.json"


def splitter_unit(by_id: str, user: str, home: str) -> str:
    return (
        "[Unit]\n"
        "Description=RNode serial splitter (LoRa + GPS on one port)\n\n"
        "[Service]\nType=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={home}/reticulum-tool\n"
        "ExecStart=/usr/bin/python3 -c \"from monitor.serial_splitter import run; "
        f"run(real_port='{by_id}', symlink='{SPLIT_PORT}', state_file='{GPS_STATE}')\"\n"
        "Restart=always\nRestartSec=5\n\n"
        "[Install]\nWantedBy=multi-user.target\n")


def rnsd_unit(user: str, home: str) -> str:
    return (
        "[Unit]\nDescription=rnsd (Reticulum Node Medic)\n"
        "After=network-online.target rnode-splitter.service\n"
        "Wants=rnode-splitter.service\n\n"
        "[Service]\nType=simple\n"
        f"User={user}\n"
        # wait for the splitter's virtual port, as on the original medic
        "ExecStartPre=/bin/bash -c 'for i in $(seq 1 20); do [ -e "
        f"{SPLIT_PORT} ] && exit 0; sleep 0.5; done; echo \"splitter port never "
        "appeared\" >&2; exit 1'\n"
        f"ExecStart={home}/.local/bin/rnsd\n"
        "Restart=always\nRestartSec=5\n\n"
        "[Install]\nWantedBy=multi-user.target\n")


def lxmd_unit(user: str, home: str) -> str:
    return (
        "[Unit]\nDescription=LXMF Propagation Node (Reticulum Node Medic)\n"
        "After=rnsd.service network-online.target\nRequires=rnsd.service\n\n"
        "[Service]\nType=simple\n"
        f"User={user}\n"
        f"ExecStart={home}/.local/bin/lxmd -s\n"
        "Restart=always\nRestartSec=5\n\n"
        "[Install]\nWantedBy=multi-user.target\n")


def reticulum_config(radio) -> str:
    """The medic's Reticulum config: shared instance, transport on (Home mode,
    the default — workflows.node_mode changes it later), the LoRa radio on the
    splitter's port with the standard settings, and the local network."""
    return (
        "[reticulum]\n  enable_transport = Yes\n  share_instance = Yes\n"
        "  shared_instance_port = 37428\n  instance_control_port = 37429\n"
        "  panic_on_interface_error = No\n\n"
        "[logging]\n  loglevel = 4\n\n"
        "[interfaces]\n"
        "  [[RNode LoRa Interface]]\n    type = RNodeInterface\n    enabled = Yes\n"
        f"    port = {SPLIT_PORT}\n"
        f"    frequency = {int(round(radio.frequency_mhz * 1_000_000))}\n"
        f"    bandwidth = {int(round(radio.bandwidth_khz * 1000))}\n"
        f"    txpower = {int(radio.tx_power_dbm)}\n"
        f"    spreadingfactor = {int(radio.spreading_factor)}\n"
        f"    codingrate = {int(radio.coding_rate)}\n\n"
        "  [[LAN Interface]]\n    type = AutoInterface\n    enabled = Yes\n")


LXMD_CONFIG = (
    "[propagation]\n  enable_node = yes\n  announce_interval = 360\n"
    "  announce_at_start = yes\n  autopeer = yes\n  autopeer_maxdepth = 6\n\n"
    "[lxmf]\n  display_name = Anonymous Peer\n  announce_at_start = no\n\n"
    "[logging]\n  loglevel = 4\n")


def lora_up(rnstatus_out: str) -> bool:
    """rnstatus names the interface and, a few lines on, its status."""
    lines = (rnstatus_out or "").splitlines()
    for i, line in enumerate(lines):
        if "RNodeInterface" in line:
            for nxt in lines[i + 1:i + 4]:
                if "Status" in nxt and "Up" in nxt:
                    return True
    return False


class MedicRadioSetup:
    """Flash, wire, start and prove the medic's own Tracker radio + GPS."""

    def __init__(self, connection, flash_factory: Callable, radio=None,
                 user: Optional[str] = None, home: Optional[str] = None,
                 sleep: Callable[[float], None] = None):
        self.connection = connection
        self.flash_factory = flash_factory
        self.user = user or os.environ.get("USER") or "pi"
        self.home = home or os.path.expanduser("~")
        if radio is None:
            from provisioning.radio_defaults import load_radio_config
            radio = load_radio_config()
        self.radio = radio
        import time
        self.sleep = sleep or time.sleep
        self.by_id = ""
        self.results: List[StepResult] = []
        self.steps = [
            ("flash_radio", self._flash),
            ("find_its_port", self._find_by_id),
            ("wire_services", self._wire),
            ("start_mesh", self._start),
            ("hear_radio", self._hear_radio),
            ("hear_gps", self._hear_gps),
        ]

    def _priv(self, cmd: str) -> str:
        return f"sudo -n {cmd}"

    # -- steps ---------------------------------------------------------------

    def _flash(self) -> StepResult:
        wf = self.flash_factory()
        if getattr(wf, "is_blocked", False):
            return StepResult("flash_radio", False, getattr(wf, "message", "Cannot flash."))
        results = wf.run_all()
        bad = next((r for r in results if not r.success and not r.skipped), None)
        if bad is not None:
            return StepResult("flash_radio", False, bad.message)
        self._flashed = wf
        return StepResult("flash_radio", True,
                          "The Heltec Wireless Tracker is flashed as this medic's radio and GPS.")

    def _find_by_id(self) -> StepResult:
        """The splitter must own the board by its permanent by-id name: the
        ttyACM number moves on every replug."""
        serial = getattr(getattr(self, "_flashed", None), "_usb_serial", "") or ""
        for _ in range(20):
            code, out, _e = self.connection.run("ls -1 /dev/serial/by-id/ 2>/dev/null")
            names = [n.strip() for n in (out or "").splitlines() if n.strip()]
            pick = [n for n in names if serial and serial in n] or (
                names if len(names) == 1 else [])
            if len(pick) == 1:
                self.by_id = f"/dev/serial/by-id/{pick[0]}"
                return StepResult("find_its_port", True, f"Radio found at {self.by_id}.")
            self.sleep(1)
        return StepResult("find_its_port", False,
                          "The Heltec Wireless Tracker did not come back after "
                          "flashing. Unplug it, plug it back in, and press Try again.")

    def _write(self, path: str, text: str, root: bool) -> bool:
        import shlex
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".nm") as fh:
            fh.write(text)
            local = fh.name
        try:
            tmp = f"/tmp/{os.path.basename(local)}"
            if not self.connection.push_file(local, tmp):
                return False
        finally:
            os.unlink(local)
        cmd = f"install -m 644 {tmp} {shlex.quote(path)}"
        code = self.connection.run(self._priv(cmd) if root else cmd)[0]
        return code == 0

    def _wire(self) -> StepResult:
        h, u = self.home, self.user
        self.connection.run(f"mkdir -p {h}/.reticulum {h}/.lxmd")
        # keep whatever config was there, once
        self.connection.run(f"[ -f {h}/.reticulum/config ] && [ ! -f "
                            f"{h}/.reticulum/config.pre-radio.bak ] && cp "
                            f"{h}/.reticulum/config {h}/.reticulum/config.pre-radio.bak")
        ok = all([
            self._write("/etc/systemd/system/rnode-splitter.service",
                        splitter_unit(self.by_id, u, h), root=True),
            self._write("/etc/systemd/system/rnsd.service", rnsd_unit(u, h), root=True),
            self._write("/etc/systemd/system/lxmd.service", lxmd_unit(u, h), root=True),
            self._write(f"{h}/.reticulum/config", reticulum_config(self.radio), root=False),
        ])
        if self.connection.run(f"test -s {h}/.lxmd/config")[0] != 0:
            ok = ok and self._write(f"{h}/.lxmd/config", LXMD_CONFIG, root=False)
        return StepResult("wire_services", ok,
                          "Radio, mesh and message services written." if ok else
                          "Could not write the radio's service files.")

    def _start(self) -> StepResult:
        code, out, err = self.connection.run(self._priv(
            "sh -c 'systemctl daemon-reload && systemctl enable --now "
            "rnode-splitter.service rnsd.service lxmd.service'"), timeout=90)
        return StepResult("start_mesh", code == 0,
                          "Radio and mesh services started." if code == 0 else
                          "The mesh services would not start: "
                          + " ".join((err or out or "").split())[-200:])

    def _hear_radio(self) -> StepResult:
        for _ in range(30):
            code, out, _e = self.connection.run(f"{self.home}/.local/bin/rnstatus",
                                                timeout=20)
            if code == 0 and lora_up(out):
                return StepResult("hear_radio", True, "The LoRa radio is up on the mesh.")
            self.sleep(3)
        return StepResult("hear_radio", False,
                          "The mesh service started but the LoRa radio did not come "
                          "up. Check the aerial is attached, then press Try again.")

    def _hear_gps(self) -> StepResult:
        for _ in range(20):
            if self.connection.run(f"test -s {GPS_STATE}")[0] == 0:
                return StepResult("hear_gps", True,
                                  "The GPS is reporting. A position needs a view of the sky.")
            self.sleep(3)
        return StepResult("hear_gps", False,
                          "The radio is up but no GPS report has arrived yet. Check "
                          "the GPS aerial, then press Try again.")

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        for name, fn in self.steps:
            try:
                r = fn()
            except Exception as e:                                 # noqa: BLE001
                r = StepResult(name, False, f"{type(e).__name__}: {e}")
            self.results.append(r)
            if on_progress:
                on_progress(r)
            if not r.success:
                break
        return self.results
