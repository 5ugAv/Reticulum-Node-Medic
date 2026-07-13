"""Build workflow — provisions a node from bare hardware to running node.

Steps are registered with ``@build_step`` and run in definition order by
``BuildWorkflow``. A failed step stops the run and does *not* advance, so the
operator can fix the cause and ``resume_from`` that step. Each step returns a
``StepResult``; ``skipped`` steps (e.g. flashing when there is no RNode) count
as success and the run continues.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from node_profile import NodeHardware, NodeProfile
from transport.connection import Connection

CONFIG_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "assets", "configs")
PACKAGE_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "assets", "packages")

# Where tool-carried assets are staged ON THE NODE. Commands that install from
# local packages must reference these remote paths, not the tool's PACKAGE_DIR
# (which does not exist on the target node).
REMOTE_ASSET_DIR = "/tmp/rnm-assets"
REMOTE_PACKAGE_DIR = REMOTE_ASSET_DIR + "/packages"


def _push_dir(wf: "BuildWorkflow", local_dir: str, remote_dir: str) -> int:
    """Copy every non-hidden file from a tool-local dir onto the node.

    Returns the number of files pushed. Assets have to physically reach the
    node before a ``--no-index`` install can find them.
    """
    wf.connection.run(f"mkdir -p {remote_dir}")
    count = 0
    if os.path.isdir(local_dir):
        for name in sorted(os.listdir(local_dir)):
            local_path = os.path.join(local_dir, name)
            if os.path.isfile(local_path) and not name.startswith("."):
                wf.connection.push_file(local_path, f"{remote_dir}/{name}")
                count += 1
    return count


@dataclass
class StepResult:
    name: str
    success: bool
    message: str = ""
    skipped: bool = False


#: Ordered registry of (name, func) build steps.
_BUILD_STEPS: List[Tuple[str, Callable]] = []


def build_step(func: Callable) -> Callable:
    _BUILD_STEPS.append((func.__name__, func))
    return func


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


#: Hints in /dev/serial/by-id/ names that identify an RNode's USB serial.
_RNODE_ID_HINTS = ("RNode", "Espressif", "USB_JTAG", "usbserial", "CP2102",
                   "CH340", "SLAB", "FTDI", "T-Beam", "Heltec")


def detect_rnode_port(connection) -> Optional[str]:
    """Find the RNode's serial device on the node.

    Modern ESP32-S3 RNodes enumerate as ``/dev/ttyACM*`` (native USB); older
    USB-UART ones as ``/dev/ttyUSB*`` — so a hardcoded ``/dev/ttyUSB0`` is wrong
    for many boards. Prefer the stable ``/dev/serial/by-id/`` mapping (verified
    format: ``usb-Espressif_USB_JTAG_serial_debug_unit_<mac>-if00 -> ttyACM0``),
    then fall back to the first ttyACM/ttyUSB device.
    """
    listing = connection.run("ls /dev/serial/by-id/ 2>/dev/null")[1]
    for name in listing.split():
        if any(h.lower() in name.lower() for h in _RNODE_ID_HINTS):
            resolved = connection.run(
                f"readlink -f /dev/serial/by-id/{name}")[1].strip()
            if resolved.startswith("/dev/"):
                return resolved
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        found = [p for p in connection.run(f"ls {pattern} 2>/dev/null")[1].split()
                 if p.startswith("/dev/")]
        if found:
            return found[0]
    return None


@build_step
def detect_hardware(wf: "BuildWorkflow") -> StepResult:
    cpuinfo = wf.cmd_output("cat /proc/cpuinfo")
    if not cpuinfo:
        return StepResult("detect_hardware", False,
                          "Could not read /proc/cpuinfo — is the node reachable?")

    if "Raspberry Pi 5" in cpuinfo:
        wf.profile.hardware = NodeHardware.PI_5
    elif "Zero 2" in cpuinfo:
        wf.profile.hardware = NodeHardware.PI_ZERO_2W
    elif "Raspberry Pi 3" in cpuinfo and ("A Plus" in cpuinfo or "3A+" in cpuinfo):
        wf.profile.hardware = NodeHardware.PI_3A_PLUS
    else:
        wf.profile.hardware = NodeHardware.UNKNOWN

    # Detect the real RNode serial port (ttyACM0 on ESP32-S3, not ttyUSB0).
    port = detect_rnode_port(wf.connection)
    if port:
        wf.profile.radio.serial_port = port
        wf.profile.connection_port = port

    info = wf.cmd_output(f"rnodeconf {wf.profile.radio.serial_port} --info")
    # A real rnodeconf --info reports "Firmware version : .../Product : Heltec..."
    # and NEVER the literal "RNode"; a BLANK board replies "RNode did not respond"
    # — so `"RNode" in info` was inverted (blank->yes, real RNode->no, verified on
    # a flashed Heltec V3). Key off "Firmware version", as radio_firmware does.
    wf.profile.has_rnode = "Firmware version" in info
    return StepResult("detect_hardware", True,
                      f"Detected {wf.profile.hardware.value} on "
                      f"{wf.profile.radio.serial_port}; "
                      f"RNode={'yes' if wf.profile.has_rnode else 'no'}")


@build_step
def confirm_radio_parameters(wf: "BuildWorkflow") -> StepResult:
    r = wf.profile.radio
    r.frequency_mhz = 915.125
    r.bandwidth_khz = 125.0
    r.spreading_factor = 9
    r.coding_rate = 5
    r.tx_power_dbm = 17
    return StepResult("confirm_radio_parameters", True,
                      "Applied Australian defaults (915.125 MHz, BW125, SF9, "
                      "CR5, 17 dBm).")


@build_step
def flash_rnode_firmware(wf: "BuildWorkflow") -> StepResult:
    if not wf.profile.has_rnode:
        return StepResult("flash_rnode_firmware", True,
                          "No RNode present — nothing to flash.", skipped=True)
    port = wf.profile.radio.serial_port
    wf.connection.push_file(
        os.path.join(PACKAGE_DIR, "rnodeconf"), "/tmp/rnodeconf")
    code, out, err = wf.connection.run(f"rnodeconf {port} --autoinstall")
    ok = code == 0
    return StepResult("flash_rnode_firmware", ok,
                      "Flashed RNode firmware." if ok
                      else f"Flash failed: {err or out}")


@build_step
def set_firmware_radio_parameters(wf: "BuildWorkflow") -> StepResult:
    if not wf.profile.has_rnode:
        return StepResult("set_firmware_radio_parameters", True,
                          "No RNode present.", skipped=True)
    r = wf.profile.radio
    # Real rnodeconf 2.5.0 has no --set-firmware-hash flag (verified via --help
    # on hardware; -H/--firmware-hash takes an explicit hash argument). TNC
    # radio params are applied with --freq/--bw/--sf/--cr/--txp; the firmware
    # hash is written during --autoinstall (the flash step).
    cmd = (
        f"rnodeconf {r.serial_port} "
        f"--freq {int(r.frequency_mhz * 1_000_000)} "
        f"--bw {int(r.bandwidth_khz * 1000)} "
        f"--sf {r.spreading_factor} --cr {r.coding_rate} "
        f"--txp {r.tx_power_dbm}"
    )
    code, out, err = wf.connection.run(cmd)
    ok = code == 0
    if ok:
        r.firmware_hash_set = True
    return StepResult("set_firmware_radio_parameters", ok,
                      "Applied radio parameters to the RNode." if ok
                      else f"rnodeconf failed: {err or out}")


@build_step
def write_reticulum_config(wf: "BuildWorkflow") -> StepResult:
    rendered = wf.render_config()
    wf.rendered_config = rendered
    wf.connection.run("mkdir -p ~/.reticulum")
    heredoc = f"cat > ~/.reticulum/config <<'RTTEOF'\n{rendered}\nRTTEOF"
    code, out, err = wf.connection.run(heredoc)
    ok = code == 0
    return StepResult("write_reticulum_config", ok,
                      "Wrote Reticulum config." if ok
                      else f"Could not write config: {err or out}")


@build_step
def install_software_stack(wf: "BuildWorkflow") -> StepResult:
    # Idempotent: only install what is actually missing. RNS is required; LXMF
    # is optional (a pure transport node needs only rnsd) but installed if
    # absent so a propagation node works too.
    have_rns = wf.connection.run("python3 -c 'import RNS'")[0] == 0
    have_lxmf = wf.connection.run("python3 -c 'import LXMF'")[0] == 0
    missing = ([] if have_rns else ["rns"]) + ([] if have_lxmf else ["lxmf"])

    if missing:
        # Prefer carried wheels (the field build has no internet); stage them
        # onto the node and install --no-index. If none are carried, fall back
        # to online pip when the node has connectivity.
        _push_dir(wf, PACKAGE_DIR, REMOTE_PACKAGE_DIR)
        have_wheels = wf.connection.run(f"ls {REMOTE_PACKAGE_DIR}/*.whl")[0] == 0
        pkgs = " ".join(missing)
        if have_wheels:
            cmd = (f"pip3 install --no-index --find-links {REMOTE_PACKAGE_DIR} "
                   f"--break-system-packages --user {pkgs}")
            source = "carried wheels (offline)"
        elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
            cmd = f"pip3 install --break-system-packages --user {pkgs}"
            source = "online pip (no wheels carried)"
        else:
            return StepResult(
                "install_software_stack", False,
                f"Cannot install {pkgs}: no wheels in assets/packages and the "
                f"node has no internet. Carry the wheels for a field build.")
        code, out, err = wf.connection.run(cmd)
        if code != 0:
            return StepResult("install_software_stack", False,
                              f"pip install failed ({source}): {err or out}")
        installed = f"Installed {pkgs} from {source}."
    else:
        installed = "Reticulum and LXMF already installed."

    # lrzsz is only needed for the serial file-push path; best-effort.
    wf.connection.run(wf.priv("apt-get install -y lrzsz") + " || true")
    return StepResult("install_software_stack", True, installed)


@build_step
def configure_services(wf: "BuildWorkflow") -> StepResult:
    user = wf.run_user()
    home = "/root" if user == "root" else f"/home/{user}"
    # Only configure services whose binary actually exists — LXMF/lxmd may not
    # be installed (RNS alone is enough for a transport node). ExecStart must be
    # the *resolved* absolute path (pip --user -> ~/.local/bin), and User=/HOME=
    # must point at the account whose ~/.reticulum holds the config we wrote.
    services: List[str] = []
    for svc, tool, args in (("rnsd", "rnsd", ""),
                            ("lxmd", "lxmd", " --service")):
        path = wf.tool_path(tool)
        if not path:
            continue
        unit = (
            "[Unit]\n"
            f"Description={svc} (Reticulum Node Medic)\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"Environment=HOME={home}\n"
            f"ExecStart={path}{args}\n"
            "Restart=always\n"
            "RestartSec=5\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        # Write as root via `sudo tee` (a plain `> /etc/...` redirect happens in
        # the unprivileged shell before sudo can help).
        heredoc = (
            f"{wf.priv(f'tee /etc/systemd/system/{svc}.service')} "
            f">/dev/null <<'RTTEOF'\n{unit}\nRTTEOF"
        )
        code, out, err = wf.connection.run(heredoc)
        if code != 0:
            return StepResult("configure_services", False,
                              f"Could not write {svc}.service: {err or out}")
        services.append(svc)

    if not services:
        return StepResult("configure_services", False,
                          "Neither rnsd nor lxmd is installed on the node.")
    wf.connection.run(wf.priv("systemctl daemon-reload"))
    for svc in services:
        wf.connection.run(wf.priv(f"systemctl enable {svc}"))
        wf.connection.run(wf.priv(f"systemctl start {svc}"))
    return StepResult("configure_services", True,
                      f"Installed and started: {', '.join(services)}.")


@build_step
def apply_system_hardening(wf: "BuildWorkflow") -> StepResult:
    # Log2Ram installed from a local .deb (no internet in the field). Stage the
    # .deb onto the node first, then install from the remote path.
    wf.connection.run(f"mkdir -p {REMOTE_ASSET_DIR}")
    wf.connection.push_file(
        os.path.join(PACKAGE_DIR, "log2ram.deb"),
        f"{REMOTE_ASSET_DIR}/log2ram.deb")
    wf.connection.run(wf.priv(f"dpkg -i {REMOTE_ASSET_DIR}/log2ram.deb") + " || true")
    wf.connection.run(wf.priv("systemctl enable log2ram") + " || true")
    wf.connection.run(wf.priv("systemctl enable watchdog") + " || true")
    return StepResult("apply_system_hardening", True,
                      "Applied Log2Ram, log rotation and hardware watchdog.")


@build_step
def set_hostname(wf: "BuildWorkflow") -> StepResult:
    if not wf.profile.hostname:
        suffix = wf.profile.session_id[-6:]
        wf.profile.hostname = f"rtt-node-{suffix}"
    code, out, err = wf.connection.run(
        wf.priv(f"hostnamectl set-hostname {wf.profile.hostname}"))
    ok = code == 0
    return StepResult("set_hostname", ok,
                      f"Hostname set to {wf.profile.hostname}." if ok
                      else f"Could not set hostname: {err or out}")


@build_step
def final_verification(wf: "BuildWorkflow") -> StepResult:
    problems = []
    if wf.connection.run("systemctl is-active rnsd")[0] != 0:
        problems.append("rnsd not active")
    if wf.connection.run("test -f ~/.reticulum/config")[0] != 0:
        problems.append("config missing")
    ok = not problems
    return StepResult("final_verification", ok,
                      "Node verified and running." if ok
                      else "Verification failed: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Workflow driver
# ---------------------------------------------------------------------------


class BuildWorkflow:
    def __init__(self, connection: Connection, profile: NodeProfile):
        self.connection = connection
        self.profile = profile
        self.steps: List[Tuple[str, Callable]] = list(_BUILD_STEPS)
        self.current_index = 0
        self.results: List[StepResult] = []
        self.rendered_config = ""
        self._root: Optional[bool] = None
        self._user: Optional[str] = None

    # -- helpers -----------------------------------------------------------

    def cmd_output(self, command: str) -> str:
        code, out, _ = self.connection.run(command)
        return out if code == 0 else ""

    def _is_root(self) -> bool:
        """True if the session already runs as root (cached)."""
        if self._root is None:
            code, out, _ = self.connection.run("id -u")
            self._root = (code == 0 and out.strip() == "0")
        return self._root

    def priv(self, command: str) -> str:
        """Prefix ``sudo -n`` when not root. Build runs many privileged steps
        (writing units, systemctl, hostnamectl, dpkg); over SSH as the login
        user these need sudo. ``-n`` fails fast instead of hanging on a prompt.
        """
        return command if self._is_root() else f"sudo -n {command}"

    def run_user(self) -> str:
        """The account the node is being built as (cached). Services must run
        as this user so rnsd reads *its* ~/.reticulum, not root's."""
        if self._user is None:
            self._user = self.cmd_output("id -un").strip() or "pi"
        return self._user

    def tool_path(self, name: str) -> str:
        """Absolute path to an installed console script, or "" if absent.
        pip --user installs rnsd/lxmd into ~/.local/bin, so a systemd unit must
        use the resolved absolute path — not a hardcoded /usr/local/bin."""
        return self.cmd_output(f"command -v {name}").strip()

    def _template_name(self) -> str:
        if self.profile.has_meshtastic_bridge:
            return "reticulum_transport_meshtastic.conf"
        hw = self.profile.hardware
        if hw is NodeHardware.PI_5:
            return "reticulum_transport_pi5.conf"
        if hw in (NodeHardware.PI_ZERO_2W, NodeHardware.PI_3A_PLUS):
            return "reticulum_transport_pi_zero.conf"
        return "reticulum_transport_default.conf"

    def render_config(self) -> str:
        with open(os.path.join(CONFIG_DIR, self._template_name())) as fh:
            template = fh.read()
        r = self.profile.radio
        subs = {
            "{{SERIAL_PORT}}": r.serial_port,
            "{{FREQUENCY}}": str(int(r.frequency_mhz * 1_000_000)),
            "{{BANDWIDTH}}": str(int(r.bandwidth_khz * 1000)),
            "{{SF}}": str(r.spreading_factor),
            "{{CR}}": str(r.coding_rate),
            "{{TXPOWER}}": str(r.tx_power_dbm),
        }
        for k, v in subs.items():
            template = template.replace(k, v)
        return template

    # -- driving -----------------------------------------------------------

    def resume_from(self, step_name: str) -> None:
        for idx, (name, _) in enumerate(self.steps):
            if name == step_name:
                self.current_index = idx
                return
        raise ValueError(f"Unknown build step: {step_name}")

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        while self.current_index < len(self.steps):
            _, func = self.steps[self.current_index]
            result = func(self)
            self.results.append(result)
            emit(result)
            if not result.success and not result.skipped:
                break  # stop; do NOT advance current_index
            self.current_index += 1
        return self.results
