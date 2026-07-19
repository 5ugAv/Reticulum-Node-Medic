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
from workflows.rnode_boards import get_board
from workflows.radio_params import set_params_at_birth
from workflows.updater import (
    sync_firmware, has_connectivity, RNODE_UPDATE_DIR)

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
    # A board can be attached but BLANK (present, not yet provisioned): a serial
    # port exists, or --info responded. Keep this distinct from has_rnode so the
    # flash step births a blank board instead of skipping it as "no RNode".
    wf.profile.rnode_present = wf.profile.has_rnode or port is not None
    if wf.profile.has_rnode:
        rnode_state = "provisioned"
    elif wf.profile.rnode_present:
        rnode_state = "blank (will flash)"
    else:
        rnode_state = "none"
    return StepResult("detect_hardware", True,
                      f"Detected {wf.profile.hardware.value} on "
                      f"{wf.profile.radio.serial_port}; RNode={rnode_state}")


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
    """Birth a BLANK attached board as an RNode.

    A board can be physically attached but unflashed (``rnode_present`` and not
    ``has_rnode``); the old code mistook that for "no RNode" and skipped it,
    leaving a propagation node with a radio that never comes up. Reuse the
    proven offline flash primitive (pre-fed ``--autoinstall`` from the firmware
    cache) so a blank board is provisioned in place. An already-provisioned
    board is left alone; params are (re)baked in the next step regardless.
    """
    # Lazy import: rnode_flash / rnode_v4_rgb import StepResult/detect_rnode_port
    # from this module, so importing them at module scope would be a cycle.
    from workflows.rnode_flash import birth_flash, FIRMWARE_VERSION
    from workflows.rnode_v4_rgb import (
        V4_BOARD_KEY, NEOPIXEL_PIN, rgb_firmware_available, flash_rgb_carried)

    if not wf.profile.rnode_present:
        return StepResult("flash_rnode_firmware", True,
                          "No RNode attached — nothing to flash.", skipped=True)
    if wf.profile.has_rnode:
        return StepResult("flash_rnode_firmware", True,
                          "RNode already provisioned — no flash needed.",
                          skipped=True)

    # Blank board present: provision it. Ensure the firmware is available
    # (sync online, else use the carried cache), then flash the selected board
    # for the chosen band via the hardware-verified non-interactive sequence.
    port = wf.profile.radio.serial_port
    board = get_board(wf.profile.rnode_board_key)
    if board is None:
        return StepResult("flash_rnode_firmware", False,
                          f"Unknown RNode board '{wf.profile.rnode_board_key}'.")
    if has_connectivity(wf.connection):
        sync_firmware(wf.connection)
    elif wf.connection.run(
            f"ls {RNODE_UPDATE_DIR}/{FIRMWARE_VERSION}/*.zip")[0] != 0:
        return StepResult(
            "flash_rnode_firmware", False,
            f"Blank board attached but offline with no cached firmware "
            f"{FIRMWARE_VERSION}. Connect WiFi once to seed the cache.")

    # Prefer the RGB NeoPixel build for a V4 whenever the medic has it compiled
    # (build() run once): carry the .bin to the target and overlay it, so
    # Pi+RNode radios get the status LED too. Fall back to stock when the RGB
    # firmware isn't built here, so the build never blocks on it.
    if wf.profile.rnode_board_key == V4_BOARD_KEY and rgb_firmware_available():
        ok, detail, rgb_applied = flash_rgb_carried(
            wf.connection, port, wf.profile.rnode_band_mhz, FIRMWARE_VERSION)
        if ok:
            wf.profile.has_rnode = True
            if rgb_applied:
                wf.profile.rnode_rgb_pin = NEOPIXEL_PIN   # RGB LED signal wire GPIO
        # A working radio without the LED is still a SUCCESS — the status LED is
        # an enhancement, not a requirement, so it never shows the operator a
        # scary red failure on an otherwise-provisioned node.
        return StepResult("flash_rnode_firmware", ok,
                          f"Flashed {board.display_name}: {detail}" if ok
                          else f"Flash failed: {detail}")

    # birth_flash makes the fresh-board second pass part of the process.
    ok, msg, _already = birth_flash(wf.connection, board, port,
                                    wf.profile.rnode_band_mhz, FIRMWARE_VERSION)
    if ok:
        wf.profile.has_rnode = True
    note = (" (stock — RGB firmware not built on this medic; run the V4 RGB "
            "build once to enable the status LED)"
            if wf.profile.rnode_board_key == V4_BOARD_KEY else "")
    return StepResult("flash_rnode_firmware", ok,
                      f"Flashed {board.display_name} as an RNode from the "
                      f"firmware cache{note} — {msg}." if ok
                      else f"Flash failed: {msg}")


@build_step
def set_firmware_radio_parameters(wf: "BuildWorkflow") -> StepResult:
    """Bake the canonical radio params into the board AT BIRTH.

    rnodeconf only writes the radio flags when a mode flag (``--tnc``/``-N``)
    rides along — the previous ``--freq/--bw/--sf`` line had none and was a
    silent no-op, leaving the board on autoinstall's 250/SF11 default and
    tripping rnsd's "Radio state mismatch". Delegate to the shared helper,
    which writes the params in TNC mode then returns the board to
    host-controlled so rnsd drives it.
    """
    if not wf.profile.has_rnode:
        return StepResult("set_firmware_radio_parameters", True,
                          "No RNode present.", skipped=True)
    ok, detail = set_params_at_birth(wf.connection, wf.profile.radio.serial_port,
                                     wf.profile.radio)
    if ok:
        wf.profile.radio.firmware_hash_set = True
    return StepResult("set_firmware_radio_parameters", ok, detail)


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
    # lxmd runs as an LXMF Propagation Node (-p) — the role a Pi + RNode fills —
    # and starts After rnsd so it joins rnsd's shared Reticulum instance rather
    # than trying to own the radio itself (which rnsd already holds). Monitoring
    # attaches to the same shared instance, so both roles run side by side.
    services: List[str] = []
    for svc, tool, args, after in (
            ("rnsd", "rnsd", "", "network-online.target"),
            ("lxmd", "lxmd", " -p --service", "rnsd.service network-online.target")):
        path = wf.tool_path(tool)
        if not path:
            continue
        unit = (
            "[Unit]\n"
            f"Description={svc} (Reticulum Node Medic)\n"
            f"After={after}\n"
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


#: RNS reads the node's own identity hash straight off disk (no networking, no
#: clash with the running rnsd) — try the client identity, then the transport
#: instance identity (a transport-only node has only the latter).
_RETICULUM_ADDR_CMD = (
    "python3 -c \"import RNS, os; "
    "cands=['~/.reticulum/storage/identity', "
    "'~/.reticulum/storage/transport_identity']; "
    "p=next((os.path.expanduser(x) for x in cands "
    "if os.path.exists(os.path.expanduser(x))), None); "
    "i=RNS.Identity.from_file(p) if p else None; "
    "print(RNS.hexrep(i.hash, delimit=False) if i else '')\" 2>/dev/null")


@build_step
def birth_certificate(wf: "BuildWorkflow") -> StepResult:
    """Assemble a photographable birth certificate for the node.

    The details an operator needs months later to find, reach and rebuild it:
    SSH name/address, MAC, the node's Reticulum address, and how it was
    built (board, firmware, radio params, RGB LED signal-wire GPIO). Read live
    from the node after the services are up (so the identity exists); the RNode
    is NOT queried over serial here — rnsd holds the port — so radio values come
    from the profile we just provisioned.
    """
    from workflows.rnode_flash import FIRMWARE_VERSION

    def out(cmd: str) -> str:
        code, o, _ = wf.connection.run(cmd)
        return o.strip() if code == 0 else ""

    hostname = out("hostname") or (wf.profile.hostname or "")
    ips = out("hostname -I").split()
    iface = (out("ip route get 1.1.1.1 2>/dev/null | awk '{print $5; exit}'")
             or "eth0")
    mac = out(f"cat /sys/class/net/{iface}/address 2>/dev/null")
    ret_addr = out(_RETICULUM_ADDR_CMD).splitlines()
    ret_addr = ret_addr[-1].strip() if ret_addr else ""
    if ret_addr:
        wf.profile.reticulum_identity_hash = ret_addr

    board = get_board(wf.profile.rnode_board_key)
    r = wf.profile.radio
    wf.birth_certificate = {
        "hostname": hostname,
        "ssh_address": f"{hostname}.local" if hostname
                       else (ips[0] if ips else ""),
        "ip_addresses": ips,
        "primary_interface": iface,
        "mac_address": mac,
        "reticulum_address": ret_addr or None,
        "role": wf.profile.role.value,
        "board": board.display_name if board else wf.profile.hardware.value,
        "rnode_firmware": FIRMWARE_VERSION if wf.profile.has_rnode else None,
        "rgb_led_pin": wf.profile.rnode_rgb_pin,
        "frequency_mhz": r.frequency_mhz,
        "bandwidth_khz": r.bandwidth_khz,
        "spreading_factor": r.spreading_factor,
        "coding_rate": r.coding_rate,
        "tx_power_dbm": r.tx_power_dbm,
        "serial_port": r.serial_port,
        "session_id": wf.profile.session_id,
    }
    return StepResult(
        "birth_certificate", True,
        f"Birth certificate ready — {hostname or 'node'} @ "
        f"{ret_addr or 'no Reticulum address'} (photograph / keep for records).")


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
        self.birth_certificate: Optional[dict] = None
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
