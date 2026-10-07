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

import json
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
            ("lock_off", self._lock_off),
            ("hand_over", self._hand_over),
        ]

    def _priv(self, cmd: str) -> str:
        return f"sudo -n {cmd}"

    # -- steps ---------------------------------------------------------------

    def _flash(self) -> StepResult:
        # A retry after the board was already locked off as this medic's own:
        # it is flashed and named, so go straight on (the flasher would
        # otherwise refuse it as "the medic's own radio" — adversarial review,
        # 2026-10-06).
        own = self._attached_own_radio()
        if own:
            self.by_id = own
            return StepResult("flash_radio", True,
                              "The Heltec Wireless Tracker is already this medic's radio.",
                              skipped=True)
        other = self._looks_like_another_board()
        if other:
            return StepResult("flash_radio", False,
                              f"The board plugged in looks like a {other}, not a "
                              "Heltec Wireless Tracker. Node Medic will not write "
                              "the Tracker's radio software onto it. Unplug it, plug "
                              "in the Heltec Wireless Tracker, then press Try again.")
        wf = self.flash_factory()
        if getattr(wf, "is_blocked", False):
            return StepResult("flash_radio", False, getattr(wf, "message",
                              "Node Medic can't write to this board right now. Unplug "
                              "it, plug it back in, then press Try again."))
        # every act reaches the screen: the page sat on "Starting…" for the
        # whole flash (keeper, 2026-10-06)
        say = getattr(self, "_say", None)
        if say:
            wf.say = say
        results = wf.run_all(on_progress=(lambda r: say(r.message)) if say else None)
        bad = next((r for r in results if not r.success and not r.skipped), None)
        if bad is not None:
            return StepResult("flash_radio", False, bad.message)
        self._flashed = wf
        return StepResult("flash_radio", True,
                          "The Tracker now carries this medic's radio software.")

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
                return StepResult("find_its_port", True, "Found the radio's plug.")
            self.sleep(1)
        from workflows.own_supply import with_supply_note
        return StepResult("find_its_port", False, with_supply_note(
                          "The Heltec Wireless Tracker did not come back after "
                          "flashing. Unplug it, plug it back in, and press Try again.",
                          self.connection.run))

    def _write(self, path: str, text: str, root: bool) -> bool:
        import shlex
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".nm") as fh:
            fh.write(text)
            local = fh.name
        try:
            # a DIFFERENT name: on the medic itself the temp file is already in
            # /tmp, and copying it onto itself fails (shutil.SameFileError) —
            # found by tracing before the first bench run, 2026-10-06
            tmp = f"/tmp/nm-push-{os.path.basename(local)}"
            if not self.connection.push_file(local, tmp):
                return False
        finally:
            os.unlink(local)
        cmd = f"install -m 644 {tmp} {shlex.quote(path)}"
        code = self.connection.run(self._priv(cmd) if root else cmd)[0]
        return code == 0

    def _wire(self) -> StepResult:
        h, u = self.home, self.user
        self.connection.run(f"mkdir -p {h}/.reticulum {h}/.lxmd {h}/.reticulum-node-medic")
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
                          "Radio set up." if ok else
                          "Setting the radio up did not finish. Press Try again.")

    def _start(self) -> StepResult:
        """Enable all three, start ONLY the splitter now. rnsd must not start
        while this app is still running: an app that came up before any mesh
        service made ITSELF the shared instance, and an rnsd started now would
        join it as a client and never open the radio (traced 2026-10-06)."""
        code, out, err = self.connection.run(self._priv(
            "sh -c 'systemctl daemon-reload && systemctl enable "
            "rnode-splitter.service rnsd.service lxmd.service && "
            "systemctl restart rnode-splitter.service'"), timeout=90)
        return StepResult("start_mesh", code == 0,
                          "Radio switched on." if code == 0 else
                          "Switching the radio on did not finish. Press Try again. ("
                          + " ".join((err or out or "").split())[-120:] + ")")

    def _lock_off(self) -> StepResult:
        """LOCK IT OFF (keeper, 2026-10-06): from here this board is the medic's
        own radio. Its USB serial goes into the onboard roster, so BUILD, PROBE
        and any flash refuse it for good, in whichever socket it sits. LAST
        among the writes, so a failure before this point leaves the board
        flashable for a retry (adversarial review, 2026-10-06)."""
        serial = by_id_to_serial(self.by_id)
        if not serial:
            return StepResult("lock_off", False,
                              "Could not read the Tracker's serial number to "
                              "record it as this medic's radio. Press Try again.")
        h = self.home
        code, out, _e = self.connection.run(f"cat {h}/.reticulum-node-medic/onboard.json")
        try:
            roster = json.loads(out) if code == 0 and (out or "").strip() else {}
        except ValueError:
            roster = {}
        roster["jonesey_lora"] = serial                     # merge, never replace
        ok = self._write(f"{h}/.reticulum-node-medic/onboard.json",
                         json.dumps(roster, indent=2) + "\n", root=False)
        return StepResult("lock_off", ok,
                          "The Tracker is now this medic's own radio: Node Medic "
                          "will never flash it again." if ok else
                          "Could not record the Tracker as this medic's radio. "
                          "Press Try again.")

    def _unlock(self) -> None:
        """Undo _lock_off when the hand-over could not be queued, so a retry can
        run the whole set-up again."""
        h = self.home
        code, out, _e = self.connection.run(f"cat {h}/.reticulum-node-medic/onboard.json")
        try:
            roster = json.loads(out) if code == 0 and (out or "").strip() else {}
        except ValueError:
            roster = {}
        if roster.pop("jonesey_lora", None) is not None:
            self._write(f"{h}/.reticulum-node-medic/onboard.json",
                        json.dumps(roster, indent=2) + "\n", root=False)

    def _looks_like_another_board(self) -> str:
        """The display name of a board the detector is SURE is not a Tracker,
        else "" (unknown is allowed: the keeper checks the label). Any ESP32-S3
        with native USB enumerates like a Tracker; a V4 or XIAO would have been
        written the Tracker's image on say-so (adversarial review, 2026-10-06)."""
        try:
            from ui.board_detect import detect_board
            from workflows.rnode_boards import available_boards, get_board
            res = detect_board(available_boards()) or {}
            key = res.get("board_key") or ""
            if res.get("found") and key and key != "heltec_wireless_tracker":
                b = get_board(key)
                return getattr(b, "display_name", key) if b else key
        except Exception:                                      # noqa: BLE001
            pass
        return ""

    def _attached_own_radio(self) -> str:
        """The by-id path of an attached board the roster already names as this
        medic's radio, or ""."""
        h = self.home
        code, out, _e = self.connection.run(f"cat {h}/.reticulum-node-medic/onboard.json")
        try:
            serial = (json.loads(out) if code == 0 and (out or "").strip() else {}).get(
                "jonesey_lora", "")
        except ValueError:
            serial = ""
        if not serial:
            return ""
        code, out, _e = self.connection.run("ls -1 /dev/serial/by-id/ 2>/dev/null")
        for name in (out or "").splitlines():
            if serial in name:
                return f"/dev/serial/by-id/{name.strip()}"
        return ""

    def _hand_over(self) -> StepResult:
        """Stop this app, start rnsd (now the shared instance, with the radio),
        start the app again — from a transient unit, so it outlives the app.
        The radio and GPS are checked after that (MedicRadioCheck). The
        "check pending" mark is set ONLY once the restart is queued; if it
        cannot be, the lock-off is undone so Try again runs the whole set-up."""
        if self.connection.run(f"systemctl cat {KIOSK_UNIT}")[0] == 0:
            import time as _t
            unit = f"nm-radio-handover-{int(_t.time())}"      # never collides
            cmd = (f"systemd-run --no-block --collect --unit={unit} sh -c "
                   f"'sleep 3; systemctl stop {KIOSK_UNIT}; "
                   "systemctl restart rnsd.service lxmd.service; sleep 5; "
                   f"systemctl start {KIOSK_UNIT}'")
            code, out, err = self.connection.run(self._priv(cmd), timeout=30)
            if code == 0:
                mark_check_pending(self.connection)
                return StepResult("hand_over", True,
                                  "The screen goes dark for about half a minute "
                                  "while Node Medic restarts with its new radio. "
                                  "Unplug nothing — this page comes back by itself.")
            self._unlock()
            return StepResult("hand_over", False,
                              "Node Medic could not restart itself to switch the "
                              "radio on. Press Try again.")
        # no kiosk unit (a medic not started by the clone's unit): start the
        # services and say plainly that a restart is needed — no mark, no
        # promise of a restart that never comes
        code, out, err = self.connection.run(self._priv(
            "systemctl restart rnsd.service lxmd.service"), timeout=60)
        if code != 0:
            self._unlock()
            return StepResult("hand_over", False,
                              "The radio services would not start. Press Try again.")
        self.needs_manual_restart = True
        return StepResult("hand_over", True,
                          "Radio set up. Unplug this medic's power lead, wait five "
                          "seconds, and plug it back in to bring the radio up.")

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        if on_progress:
            self._say = lambda words: on_progress(StepResult("progress", True, words))
        return _run_steps(self, on_progress)


def by_id_to_serial(by_id: str) -> str:
    """``…_A1:B2:C3:D4:E5:F6-if00`` → ``A1:B2:C3:D4:E5:F6``."""
    import re
    m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", by_id or "")
    return m.group(1).upper() if m else ""


KIOSK_UNIT = "reticulum-node-medic.service"
CHECK_PENDING = "~/.config/nodemedic/radio_check_pending"


def mark_check_pending(connection) -> None:
    connection.run(f"mkdir -p ~/.config/nodemedic && touch {CHECK_PENDING}")


def check_pending() -> bool:
    return os.path.exists(os.path.expanduser(CHECK_PENDING))


def unlock_own_radio(home: Optional[str] = None) -> None:
    """Forget the roster's radio (the keeper chose "Set it up again from the
    start"), so the board is flashable once more."""
    path = os.path.join(home or os.path.expanduser("~"), ".reticulum-node-medic",
                        "onboard.json")
    try:
        with open(path) as fh:
            roster = json.load(fh)
    except Exception:                                      # noqa: BLE001
        return
    if roster.pop("jonesey_lora", None) is not None:
        with open(path, "w") as fh:
            json.dump(roster, fh, indent=2)


def clear_check_pending() -> None:
    try:
        os.remove(os.path.expanduser(CHECK_PENDING))
    except OSError:
        pass


def _run_steps(wf, on_progress):
    for name, fn in wf.steps:
        try:
            r = fn()
        except Exception as e:                                     # noqa: BLE001
            r = StepResult(name, False, f"{type(e).__name__}: {e}")
        wf.results.append(r)
        if on_progress:
            on_progress(r)
        if not r.success:
            break
    return wf.results


class MedicRadioCheck:
    """After the hand-over restart: is the LoRa radio up, is GPS reporting?"""

    def __init__(self, connection, home: Optional[str] = None,
                 sleep: Callable[[float], None] = None):
        import time
        self.connection = connection
        self.home = home or os.path.expanduser("~")
        self.sleep = sleep or time.sleep
        self.results: List[StepResult] = []
        self.steps = [("hear_radio", self._hear_radio), ("hear_gps", self._hear_gps)]

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        if on_progress:
            self._say = lambda words: on_progress(StepResult("progress", True, words))
        return _run_steps(self, on_progress)

    def _hear_radio(self) -> StepResult:
        """Is the LoRa radio up? The medic checks ITS OWN side first (keeper:
        never blame the hardware before the software is proven), then asks
        rnstatus. About 90 s at most."""
        say = getattr(self, "_say", None)
        for i in range(9):
            code, out, _e = self.connection.run(
                "systemctl is-active rnode-splitter.service rnsd.service", timeout=10)
            states = (out or "").split()
            if len(states) == 2 and states[0] != "active":
                why = "the radio's port service"
            elif len(states) == 2 and states[1] != "active":
                why = "the mesh service"
            elif self.connection.run(f"test -e {SPLIT_PORT}")[0] != 0:
                why = "the radio's port"
            else:
                why = ""
            code, out, _e = self.connection.run(f"{self.home}/.local/bin/rnstatus",
                                                timeout=10)
            if code == 0 and lora_up(out):
                return StepResult("hear_radio", True, "The radio is on and listening.")
            if say:
                say(f"Checking the radio… {10 * (i + 1)}s")
            self.sleep(10)
        if why:
            _c, j, _e = self.connection.run(
                "journalctl -u rnode-splitter -u rnsd -n 3 --no-pager -o cat", timeout=10)
            tail = " ".join((j or "").split())[-160:]
            return StepResult("hear_radio", False,
                              f"The radio is not on yet: {why} did not start. "
                              "Press Try again; if it fails again, choose 'Set it "
                              f"up again from the start'. ({tail})")
        from workflows.own_supply import with_supply_note
        return StepResult("hear_radio", False, with_supply_note(
                          "The radio services are running but the radio has not "
                          "answered yet. Check the Tracker is still plugged in, "
                          "then press Try again.", self.connection.run))

    def _hear_gps(self) -> StepResult:
        """The board's GPS reports, counted by the splitter — not a satellite
        fix, which needs the sky and can take minutes."""
        import json
        for _ in range(20):
            code, out, _e = self.connection.run(f"cat {GPS_STATE}")
            try:
                frames = int(json.loads(out or "{}").get("gps_frames") or 0)
            except ValueError:
                frames = 0
            if code == 0 and frames > 0:
                return StepResult("hear_gps", True,
                                  "The position finder is reporting. A position "
                                  "needs a view of the sky.")
            self.sleep(3)
        return StepResult("hear_gps", False,
                          "The radio is on, but no position reports have arrived "
                          "yet. Check the board really is a Heltec Wireless "
                          "Tracker, then press Try again.")
