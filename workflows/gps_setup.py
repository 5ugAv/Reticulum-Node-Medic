"""Give the medic a GPS fix via a Heltec Wireless Tracker.

The Pi 5 has no GNSS, so a Tracker flashed with the GPS->USB NMEA passthrough
(assets/sketches/tracker_gps_passthrough) becomes the medic's dedicated location
source: it streams NMEA over USB, gpsd on the Pi reads it, and the tool's
monitor.geo.read_gps() (gpspipe) returns the fix — feeding map-download centring,
node placement and build-location stamping.

This module does the whole "build it in": compile + flash the passthrough onto a
Tracker, find which serial port is now emitting NMEA (the medic's RNode is on a
look-alike ESP32-S3 port, so we key off the NMEA stream, not the USB id), point
gpsd at it, and confirm a fix. Every seam (connection, port probe) is injected so
the logic is unit-tested without hardware.
"""

from __future__ import annotations

import os
import re
from typing import Callable, List, Optional, Tuple

from transport.connection import Connection
from workflows.build import StepResult, detect_rnode_port

# -- firmware build (reuses the arduino-cli ESP32 toolchain the RGB build sets up)
FQBN = "esp32:esp32:esp32s3:CDCOnBoot=cdc"
ESP32_CORE = "esp32:esp32@2.0.17"
LOCAL_SKETCH_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "sketches",
    "tracker_gps_passthrough")
SKETCH_NAME = "tracker_gps_passthrough.ino"
REMOTE_SKETCH_DIR = "~/tracker_gps_passthrough"


def compile_command(sketch_dir: str = REMOTE_SKETCH_DIR) -> str:
    """arduino-cli line to build the passthrough as a plain ESP32-S3 sketch
    (no Heltec board package needed — the pins are set in the .ino)."""
    return f"arduino-cli compile --fqbn {FQBN} -e {sketch_dir}"


def upload_command(port: str, sketch_dir: str = REMOTE_SKETCH_DIR) -> str:
    """Full flash of the compiled sketch to the Tracker on *port*."""
    return f"arduino-cli upload -p {port} --fqbn {FQBN} {sketch_dir}"


# -- NMEA / port detection -------------------------------------------------

#: An NMEA sentence: '$' + 2-char talker + 3-char type + comma-delimited fields.
_NMEA_RE = re.compile(r"^\$[A-Z]{2}[A-Z]{3},")


def is_nmea(line: str) -> bool:
    """True if *line* is a well-formed NMEA sentence (validating the ``*HH``
    checksum when present). This is what tells the GPS port apart from the
    medic's RNode port — the RNode speaks KISS, never NMEA."""
    line = line.strip()
    if not _NMEA_RE.match(line):
        return False
    if "*" in line:
        body, _, tail = line[1:].partition("*")
        cs = tail[:2]
        if len(cs) == 2:
            calc = 0
            for ch in body:
                calc ^= ord(ch)
            try:
                return calc == int(cs, 16)
            except ValueError:
                return False
    return True


def _serial_ports(connection: Connection) -> List[str]:
    out = connection.run("ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")[1]
    return [p for p in out.split() if p.startswith("/dev/")]


def detect_gps_port(connection: Connection, exclude: Tuple[str, ...] = (),
                    read_seconds: int = 3, min_sentences: int = 2
                    ) -> Optional[str]:
    """Return the serial port streaming NMEA, or None. Probes each candidate
    port (minus *exclude*, e.g. the known RNode port) by reading briefly and
    counting valid NMEA sentences."""
    for port in _serial_ports(connection):
        if port in exclude:
            continue
        out = connection.run(
            f"timeout {read_seconds} cat {port} 2>/dev/null | head -c 4000")[1]
        good = sum(1 for line in out.splitlines() if is_nmea(line))
        if good >= min_sentences:
            return port
    return None


# -- gpsd configuration ----------------------------------------------------

def gpsd_defaults(port: str) -> str:
    """/etc/default/gpsd contents pinning gpsd to *port*. ``-n`` polls the GPS
    even with no client connected, so a fix is ready when the tool asks."""
    return (f'DEVICES="{port}"\n'
            'GPSD_OPTIONS="-n"\n'
            'START_DAEMON="true"\n'
            'USBAUTO="false"\n')


def parse_gpspipe_fix(text: str) -> Optional[Tuple[float, float]]:
    """Pull the first (lat, lon) TPV fix out of gpspipe -w JSON, or None."""
    import json
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("class") == "TPV" and "lat" in obj and "lon" in obj:
            return (obj["lat"], obj["lon"])
    return None


class GpsTrackerSetup:
    """Build + flash the passthrough onto a Tracker, then set up gpsd around it.

    Flashing targets whichever single board is connected, so the operator should
    have ONLY the Tracker plugged in for the flash; the medic's RNode can be
    reconnected afterwards (detection keys off NMEA, not the USB id).
    """

    def __init__(self, connection: Connection, port: Optional[str] = None,
                 rnode_port: Optional[str] = None, build_timeout: int = 600,
                 flash_timeout: int = 240):
        self.connection = connection
        self.port = port
        #: the medic's existing RNode port, excluded from GPS detection.
        self.rnode_port = rnode_port
        self.build_timeout = build_timeout
        self.flash_timeout = flash_timeout
        self.gps_port: Optional[str] = None
        self.fix: Optional[Tuple[float, float]] = None
        self.results: List[StepResult] = []

    def _priv(self, cmd: str) -> str:
        if self.connection.run("id -u")[1].strip() == "0":
            return cmd
        return f"sudo -n {cmd}"

    # -- steps ------------------------------------------------------------

    def _ensure_toolchain(self) -> StepResult:
        if self.connection.run("command -v arduino-cli")[0] != 0:
            return StepResult("ensure_toolchain", False,
                              "arduino-cli not installed — run the V4 RGB build "
                              "once (it installs the ESP32 toolchain).")
        code, out, err = self.connection.run(
            f"arduino-cli core install {ESP32_CORE}", timeout=self.build_timeout)
        return StepResult("ensure_toolchain", code == 0,
                          "ESP32-S3 toolchain ready." if code == 0
                          else f"core install failed: {(err or out)[-160:]}")

    def _build_firmware(self) -> StepResult:
        self.connection.run(f"mkdir -p {REMOTE_SKETCH_DIR}")
        local_ino = os.path.join(LOCAL_SKETCH_DIR, SKETCH_NAME)
        if not self.connection.push_file(
                local_ino, f"{REMOTE_SKETCH_DIR}/{SKETCH_NAME}"):
            return StepResult("build_firmware", False,
                              "could not carry the passthrough sketch to the node.")
        code, out, err = self.connection.run(compile_command(),
                                             timeout=self.build_timeout)
        return StepResult("build_firmware", code == 0,
                          "Compiled the GPS passthrough firmware." if code == 0
                          else f"compile failed: {(err or out)[-200:]}")

    def _ensure_single_board(self) -> StepResult:
        if self.port:
            return StepResult("ensure_single_board", True,
                              f"Flashing the board on {self.port}.")
        ports = _serial_ports(self.connection)
        if len(ports) != 1:
            return StepResult(
                "ensure_single_board", False,
                f"{len(ports)} boards connected — plug in ONLY the Tracker to "
                f"flash it (reconnect the RNode afterwards).")
        self.port = ports[0]
        return StepResult("ensure_single_board", True, f"Tracker on {self.port}.")

    def _flash(self) -> StepResult:
        code, out, err = self.connection.run(
            upload_command(self.port), timeout=self.flash_timeout)
        return StepResult("flash", code == 0,
                          "Flashed the Tracker with the GPS passthrough." if code == 0
                          else f"upload failed: {(err or out)[-200:]}")

    def _detect_gps(self) -> StepResult:
        exclude = tuple(p for p in (self.rnode_port,) if p)
        port = detect_gps_port(self.connection, exclude=exclude)
        if not port:
            return StepResult("detect_gps", False,
                              "No NMEA stream found — check the GNSS pinout in "
                              "the sketch and that the antenna has sky view.")
        self.gps_port = port
        return StepResult("detect_gps", True, f"GPS streaming NMEA on {port}.")

    def _configure_gpsd(self) -> StepResult:
        if self.connection.run("command -v gpsd")[0] != 0:
            code, out, err = self.connection.run(
                self._priv("apt-get install -y gpsd gpsd-clients"),
                timeout=self.build_timeout)
            if code != 0:
                return StepResult("configure_gpsd", False,
                                  "gpsd not installed and install failed "
                                  "(connect to WiFi once to install it).")
        cfg = gpsd_defaults(self.gps_port)
        heredoc = (f"{self._priv('tee /etc/default/gpsd')} >/dev/null "
                   f"<<'GPSDEOF'\n{cfg}GPSDEOF")
        if self.connection.run(heredoc)[0] != 0:
            return StepResult("configure_gpsd", False,
                              "could not write /etc/default/gpsd.")
        self.connection.run(self._priv("systemctl enable gpsd.socket gpsd"))
        self.connection.run(self._priv("systemctl restart gpsd.socket gpsd"))
        return StepResult("configure_gpsd", True,
                          f"gpsd pinned to {self.gps_port} and started.")

    def _verify_fix(self) -> StepResult:
        out = self.connection.run("gpspipe -w -n 20 2>/dev/null",
                                  timeout=30)[1]
        self.fix = parse_gpspipe_fix(out)
        if self.fix:
            lat, lon = self.fix
            return StepResult("verify_fix", True,
                              f"GPS fix acquired: {lat:.5f}, {lon:.5f}.")
        # NMEA is flowing (device works) but no 3D fix yet — a cold start can
        # take a minute with clear sky. gpsd is set up correctly regardless.
        if any(is_nmea(line) for line in out.splitlines()) or '"class"' in out:
            return StepResult("verify_fix", True,
                              "GPS connected via gpsd — waiting for a fix "
                              "(needs clear sky; can take a minute cold).")
        return StepResult("verify_fix", False,
                          "gpsd is not reporting the GPS — check the wiring.")

    # -- drivers ----------------------------------------------------------

    _FLASH = ("_ensure_toolchain", "_build_firmware", "_ensure_single_board",
              "_flash")
    _SETUP = ("_detect_gps", "_configure_gpsd", "_verify_fix")

    def _run(self, names, on_progress):
        emit = on_progress or (lambda r: None)
        for name in names:
            result = getattr(self, name)()
            self.results.append(result)
            emit(result)
            if not result.success:
                break
        return self.results

    def flash_tracker(self, on_progress=None):
        """Build + flash the passthrough onto the connected Tracker."""
        return self._run(self._FLASH, on_progress)

    def setup_gpsd(self, on_progress=None):
        """After the Tracker is flashed + connected: find it, wire gpsd, verify."""
        return self._run(self._SETUP, on_progress)

    def run_all(self, on_progress=None):
        self.flash_tracker(on_progress)
        if self.results and not self.results[-1].success:
            return self.results
        return self.setup_gpsd(on_progress)
