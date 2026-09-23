"""Build workflow — provisions a node from bare hardware to running node.

Steps are registered with ``@build_step`` and run in definition order by
``BuildWorkflow``. A failed step stops the run and does *not* advance, so the
operator can fix the cause and ``resume_from`` that step. Each step returns a
``StepResult``; ``skipped`` steps (e.g. flashing when there is no RNode) count
as success and the run continues.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from monitor.health_reply import REPLY_IDENTITY_PATH
from node_profile import NodeHardware, NodeProfile, NodeRole, RadioConfig
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
    def _mine(port: str) -> bool:
        """The medic's OWN board? Its hints match Jonesey exactly, so an
        unfiltered first-match hands out the medic's radio (verified live
        2026-08-01 — the PROBE mis-target). Best-effort: a remote/dev host
        has no roster and nothing to protect."""
        try:
            from ui.onboard_roster import is_onboard
            return bool(is_onboard(port))
        except Exception:
            return False

    listing = connection.run("ls /dev/serial/by-id/ 2>/dev/null")[1]
    for name in listing.split():
        if any(h.lower() in name.lower() for h in _RNODE_ID_HINTS):
            resolved = connection.run(
                f"readlink -f /dev/serial/by-id/{name}")[1].strip()
            if resolved.startswith("/dev/") and not _mine(resolved):
                return resolved
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        found = [p for p in connection.run(f"ls {pattern} 2>/dev/null")[1].split()
                 if p.startswith("/dev/") and not _mine(p)]
        if found:
            return found[0]
    return None


#: Debian ships pip's own wheel here for python3-venv's benefit, even on images
#: that do NOT install pip itself. A wheel is a zip, and pip is importable from
#: inside it — so this is a complete OFFLINE pip, on the node already.
#: Verified on Raspberry Pi OS Trixie: /usr/share/python-wheels/pip-25.1.1-*.whl
PIP_WHEEL_GLOB = "/usr/share/python-wheels/pip-*.whl"


def _ensure_pip(wf: "BuildWorkflow") -> "tuple[bool, str]":
    """Find a usable pip and record it as ``wf.pip_cmd``.

    Raspberry Pi OS Lite ships NO pip3 (found birthing HOPE, 2026-08-01, where
    it stopped the build dead). It does ship pip's own wheel for python3-venv,
    and pip can be run straight out of it — which is a fully offline bootstrap
    needing no apt, no internet and no extra carried file.

    Order matters: the offline sources are tried FIRST. Reaching for apt on a
    node with no internet costs up to ~17 minutes of timeouts before failing,
    and a field node is offline by definition — that is the normal case here,
    not the exception.
    """
    for probe, cmd, note in (
            ("command -v pip3", "pip3", ""),
            ("python3 -m pip --version", "python3 -m pip", " (python3 -m pip)")):
        if wf.connection.run(probe)[0] == 0:
            wf.pip_cmd = cmd
            return True, note

    # Offline: run pip directly from the wheel Debian already put on the node.
    code, out, _ = wf.connection.run(f"ls {PIP_WHEEL_GLOB} 2>/dev/null | head -1")
    wheel = (out or "").strip().splitlines()
    wheel = wheel[0].strip() if wheel else ""
    if code == 0 and wheel.endswith(".whl"):
        if wf.connection.run(f"python3 {wheel}/pip --version")[0] == 0:
            wf.pip_cmd = f"python3 {wheel}/pip"
            return True, " (pip from the image's own wheel, offline)"

    # Last resort, and only useful with connectivity.
    wf.connection.run(wf.priv("apt-get update -o Acquire::Retries=2") + " || true",
                      timeout=420)
    wf.connection.run(wf.priv("apt-get install -y python3-pip"), timeout=600)
    if wf.connection.run("command -v pip3")[0] == 0:
        wf.pip_cmd = "pip3"
        return True, " (installed python3-pip)"
    return False, ("No pip on the node: none installed, no pip wheel in "
                   f"{PIP_WHEEL_GLOB}, and apt could not fetch one.")


def _ensure_rnodeconf(wf: "BuildWorkflow") -> "tuple[bool, str]":
    """``rnodeconf`` (from the ``rns`` package) must exist for detect + flash, but
    on a stock Pi it isn't installed until install_software_stack (a later step).
    So a truly fresh Pi couldn't detect/flash its radio. Install ``rns`` up front
    (carried wheels offline, else online pip). No-op when it's already present
    (the common case: an imaged node). Returns (ok, note)."""
    if wf.connection.run("command -v rnodeconf")[0] == 0:
        return True, ""
    pip_ok, pip_note = _ensure_pip(wf)
    if not pip_ok:
        return False, pip_note
    _push_dir(wf, PACKAGE_DIR, REMOTE_PACKAGE_DIR)
    if wf.connection.run(f"ls {REMOTE_PACKAGE_DIR}/*.whl")[0] == 0:
        cmd = (f"{wf.pip_cmd} install --no-index --find-links {REMOTE_PACKAGE_DIR} "
               f"--break-system-packages --user rns")
        source = "carried wheels"
    elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
        cmd = f"{wf.pip_cmd} install --break-system-packages --user rns"
        source = "online pip"
    else:
        return False, ("rnodeconf missing and no carried wheels / no internet to "
                       "install rns — carry the wheelhouse for a field flash.")
    # pip needs minutes on a fresh Pi — the default 30s timeout KILLED the
    # install mid-flight every time (found by the 2026-07-30 sandbox proof).
    code, out, err = wf.connection.run(cmd, timeout=420)
    if wf.connection.run("command -v rnodeconf")[0] == 0:
        return True, f" (installed rns from {source})"
    tail = ((err or out) or "").strip()[-220:]
    return False, (f"installed rns but rnodeconf still not found "
                   f"(pip exit {code}: {tail})")


def _medic_evidence(run=None) -> dict:
    """What the MEDIC can say about why a link to the node died.

    Two independent readings, both cheap, both on the medic itself:
      * ``vcgencmd get_throttled`` — bit 0 is undervoltage NOW, bit 16 is "it
        has happened since boot". Clear means the rail held.
      * ``NETDEV WATCHDOG`` in dmesg — the USB gadget's transmit queue stopped
        being serviced. The link is wedged; the Pi is not necessarily unwell.
    """
    if run is None:
        import subprocess

        def run(argv):
            try:
                return subprocess.run(argv, capture_output=True, text=True,
                                      timeout=15).stdout
            except Exception:                                  # noqa: BLE001
                return ""
    throttled = (run(["vcgencmd", "get_throttled"]) or "").strip()
    dmesg = run(["dmesg"]) or ""
    uptime = run(["cat", "/proc/uptime"]) or ""
    return {
        "undervolted": ("throttled=0x0" not in throttled) and bool(throttled),
        "wedged": _wedged_recently(dmesg, uptime),
    }


#: How far back a kernel complaint still counts as "this is happening now".
#: A build's first contact is seconds old; anything older belongs to a
#: different story.
WEDGE_WINDOW_S = 300.0


def _wedged_recently(dmesg: str, uptime: str, window_s: float = WEDGE_WINDOW_S) -> bool:
    """Did the USB gadget wedge WITHIN THE LAST FEW MINUTES?

    THE WHOLE RING BUFFER IS NOT EVIDENCE ABOUT NOW. The first version grepped
    all of dmesg for NETDEV WATCHDOG, so once a medic had wedged even once it
    blamed the link for every timeout thereafter — including two builds AFTER
    the NetworkManager fix had stopped it happening, where the real cause was a
    dead cable address behind an ambiguous mDNS name (2026-08-11).

    That is the same fault this function was added to prevent, one level down:
    a confident sentence outrunning its evidence. dmesg timestamps are seconds
    since boot and /proc/uptime is the same clock, so "recently" is arithmetic
    rather than a guess.
    """
    try:
        now = float((uptime or "").split()[0])
    except (IndexError, ValueError):
        return False            # cannot date it -> cannot claim it
    import re
    newest = None
    for line in (dmesg or "").splitlines():
        if "NETDEV WATCHDOG" not in line:
            continue
        m = re.match(r"\s*\[\s*(\d+\.\d+)\]", line)
        if m:
            t = float(m.group(1))
            if newest is None or t > newest:
                newest = t
    return newest is not None and (now - newest) <= window_s


def _link_died_reason(run=None) -> str:
    """Name which of the two killed the link, from evidence, not from a guess."""
    ev = _medic_evidence(run)
    if ev["undervolted"]:
        return ("The Pi stopped answering and Node Medic's own supply sagged "
                "while it did — this is power. The Pi is drawing from Node "
                "Medic, and the pair of them are more than the supply can "
                "carry. A bigger supply, or a powered hub between them.")
    if ev["wedged"]:
        return ("The USB link wedged — the kernel logged NETDEV WATCHDOG, the "
                "cable's transmit queue stopped being serviced. Node Medic's "
                "own power is FINE, so this is the link and not the Pi. "
                "Unplug the Pi, count to five, plug it back in; if it keeps "
                "happening, try a different A-to-A cable.")
    return ("The Pi stopped answering part-way through, and Node Medic cannot "
            "see why from its own side — its power is fine and the USB link "
            "logged no fault. Check the Pi's power light and its cable.")


def first_contact_reason(code: int, err: str) -> str:
    """Why the very first command on the node came back with nothing.

    "Is the node reachable?" was the old answer, and on 2026-08-10 it was
    printed a second after the walkthrough's own gate had proved the node
    reachable — it had opened a TCP session to sshd on 10.55.0.1 to get there.
    Both statements were about the same cable, one of them was wrong, and the
    operator was left holding a contradiction instead of a cause.

    They are different questions. The gate asks whether sshd ANSWERS; this asks
    whether it will let us IN. ``ssh`` says which, in its stderr, and
    ``cmd_output`` was throwing that away. So read it, and name the failure the
    operator actually has: a key that is not on the card is a different evening
    from a Pi that has browned out.
    """
    e = (err or "").lower()
    if "permission denied" in e or "no supported authentication" in e:
        return ("The Pi answered, but refused the login — Node Medic's SSH key "
                "isn't authorised on it. That key is written onto the card at "
                "imaging time, so the card is the thing to check: put it in "
                "Node Medic's reader and it will say. Writing the card again "
                "fixes it.")
    if "connection refused" in e:
        return ("Nothing is listening for SSH on the Pi. It is powered and on "
                "the network, but its SSH service is not running — the card "
                "was written without it, or first boot has not finished.")
    if "timed out" in e or "no route to host" in e or "unreachable" in e:
        # A TIMEOUT HAS TWO VERY DIFFERENT CAUSES AND THE MEDIC CAN TELL THEM
        # APART. It was guessing "brown out" at both, and said so on SkyFinger
        # while its own rail sat at 5.08 V with the undervoltage flag clear and
        # the kernel had logged the real reason three lines away (2026-08-11).
        # Sending an operator to check a power supply that is provably fine is
        # the same failure as the old "is the node reachable?" — a plausible
        # sentence in place of a reading.
        return _link_died_reason()
    if "host key verification failed" in e or "remote host identification" in e:
        return ("The Pi's SSH identity has changed since Node Medic last saw "
                "it — expected after re-imaging, but it will not connect until "
                "the old key is cleared.")
    tail = (err or "").strip()[-200:]
    return ("Could not read /proc/cpuinfo from the node"
            + (f" (ssh exit {code}: {tail})" if tail else
               f" (ssh exit {code}) — is it reachable?"))


def _expected_birth_token(wf: "BuildWorkflow") -> str:
    """The one-time token the imager recorded — but only when THIS build is
    for the node that card was written for (hostname match). Identity is
    PROVEN at first contact, not assumed (operator, 2026-08-14); a token
    recorded for some other card proves nothing about this node and must
    not fail it."""
    try:
        from provisioning.pi_discover import last_imaged_pi
        rec = last_imaged_pi()
        want = (getattr(wf.profile, "hostname", "") or "").strip().lower()
        if want and (rec.get("hostname") or "").strip().lower() == want:
            return (rec.get("birth_token") or "").strip()
        return ""
    except Exception:                                              # noqa: BLE001
        return ""


def _road_of(host: str) -> str:
    """How the medic reached *host*, for failure messages that can be
    diagnosed: the cable /29 is the cable; anything else came off the
    operator's network (mDNS name or DHCP address)."""
    return "over the cable" if (host or "").startswith("10.55.0.")         else "on your network"


def _clear_stale_host_key(host: str) -> bool:
    """Drop the medic's stored (unpinned) SSH key for *host*. Rebirth makes a
    new identity, and DHCP can hand the reborn node an address whose old key
    belongs to an earlier tenant — imaging-time clearing cannot foresee a
    future lease (EVERYWHERE, 2026-08-14). The PINNED tamper store is never
    touched here; this is the user known_hosts only."""
    try:
        import subprocess
        subprocess.run(["ssh-keygen", "-R", host], capture_output=True,
                       timeout=10)
        return True
    except Exception:                                              # noqa: BLE001
        return False


def _rotate_pin_on_token_proof(host: str, expected_token: str,
                               _read_token=None, _unpin=None,
                               _clear_user=None) -> bool:
    """Rotate a stale PINNED host key — on birth-token proof, never on less.

    Both SKYFINGER runs (2026-08-14 night) died at first contact: the pinned
    store remembered 192.168.1.2 from ELSEWHERE's build, DHCP handed
    skyfinger that address, and the changed-key refusal blocked a legitimate
    rebirth twice. The standing rule — never drop pinned trust on
    unauthenticated name resolution — HOLDS: the token is not name
    resolution. The medic minted it, baked it onto this build's card, and
    recorded it exactly once; a machine that quotes it back over a
    host-key-blind read IS the card the medic wrote. That proof outranks a
    pin describing a DHCP tenancy that no longer exists. No token, wrong
    token, or no recorded expectation -> the pin stays and the build fails
    with the honest message.
    """
    if not (host and (expected_token or "").strip()):
        return False
    try:
        if _read_token is None:
            from provisioning.pi_discover import read_birth_token
            _read_token = read_birth_token
        tok = _read_token(host)
        if (tok or "").strip() != expected_token.strip():
            return False
        if _unpin is None:
            from provisioning.host_keys import forget_host_key
            _unpin = forget_host_key
        if _clear_user is None:
            _clear_user = _clear_stale_host_key
        _unpin(host)
        _clear_user(host)
        return True
    except Exception:                                              # noqa: BLE001
        return False


@build_step
def detect_hardware(wf: "BuildWorkflow") -> StepResult:
    # NOT cmd_output: it returns "" for every kind of failure alike, and this is
    # the FIRST thing the build says to the node — the one place where knowing
    # which failure it was is worth most.
    code, cpuinfo, err = wf.connection.run("cat /proc/cpuinfo")
    e = (err or "").lower()
    if (code != 0 or not cpuinfo) and (
            "host key verification failed" in e
            or "remote host identification" in e):
        # A changed key here is the expected result of the re-imaging this
        # very build follows — or of DHCP handing this node an address whose
        # PIN belongs to an earlier tenant (both SKYFINGER runs, 2026-08-14).
        # Clear the unpinned entry freely; rotate the PINNED one only when
        # the machine proves itself with this build's birth token. Then try
        # once more — and say so, because a silent second attempt is how
        # mysteries are made.
        host = getattr(wf.connection, "host", "") or ""
        if host:
            _clear_stale_host_key(host)
            _rotate_pin_on_token_proof(host, _expected_birth_token(wf))
            code, cpuinfo, err = wf.connection.run("cat /proc/cpuinfo")
    if code != 0 or not cpuinfo:
        host = getattr(wf.connection, "host", "") or "the node"
        return StepResult(
            "detect_hardware", False,
            f"At {host} ({_road_of(host)}): "
            + first_contact_reason(code, err))

    # THE BIRTH TOKEN: prove this is the Pi the card was written for BEFORE
    # provisioning anything (operator, 2026-08-14). The imager bakes a
    # one-time token onto the boot partition and records it; a mismatch is a
    # WRONG MACHINE — likely a name collision or a stale address — and the
    # honest move is to stop, not to provision a stranger. No recorded
    # token, or an old card without the file, is "could not check".
    expected = _expected_birth_token(wf)
    if expected:
        tok = (wf.connection.run(
            "cat /boot/firmware/nodemedic-birth-token")[1] or "").strip()
        if tok and tok != expected:
            host = getattr(wf.connection, "host", "") or "the node"
            return StepResult(
                "detect_hardware", False,
                f"At {host} ({_road_of(host)}): this is not the Pi this "
                f"card was written for — it answers with a different card's "
                f"birth token. Another machine may hold this name or "
                f"address; check what else on the network answers to it.")

    # rnodeconf must be present before we probe/flash the radio (a fresh Pi has
    # none until the later install step) — ensure it up front.
    tooling_ok, tooling_note = _ensure_rnodeconf(wf)
    if not tooling_ok:
        return StepResult("detect_hardware", False, tooling_note)

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
    # NO PORT CLAIM WITHOUT A RADIO (build-lens, 2026-08-13): with nothing
    # attached, the port here is the untouched dataclass default — printed as
    # if observed, and wrong for every native-USB board. The certificate
    # reads this line months later.
    where = (f" on {wf.profile.radio.serial_port}" if wf.profile.rnode_present
             else "")
    return StepResult("detect_hardware", True,
                      f"Detected {wf.profile.hardware.value}{where}; "
                      f"RNode={rnode_state}{tooling_note}")


@build_step
def confirm_radio_parameters(wf: "BuildWorkflow") -> StepResult:
    # If the profile's radio is still the untouched factory default, apply the
    # SAVED tool-wide defaults (Settings ▸ Default radio parameters) — that's
    # how a regional operator's settings reach a headless Pi build. If the
    # BIRTH form already customised it, KEEP the operator's values (this step
    # used to stomp them with hardcoded 915.125 — fixed 2026-07-31).
    r = wf.profile.radio
    keys = ("frequency_mhz", "bandwidth_khz", "spreading_factor",
            "coding_rate", "tx_power_dbm")
    factory = RadioConfig()
    if all(getattr(r, k) == getattr(factory, k) for k in keys):
        try:
            from provisioning.radio_defaults import load_defaults
            d = load_defaults()
            r.frequency_mhz = d["freq"]
            r.bandwidth_khz = d["bw"]
            r.spreading_factor = int(d["sf"])
            r.coding_rate = int(d["cr"])
            r.tx_power_dbm = int(d["txp"])
        except Exception:
            pass                            # factory canonical stays
    return StepResult("confirm_radio_parameters", True,
                      f"Radio parameters: {r.frequency_mhz:g} MHz, "
                      f"BW{r.bandwidth_khz:g}, SF{r.spreading_factor}, "
                      f"CR{r.coding_rate}, {r.tx_power_dbm} dBm.")


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
        return StepResult(
            "flash_rnode_firmware", False,
            "A blank radio board is attached but no board was named for this "
            "build, so Node Medic will not guess which image to flash. Name "
            "the board and run the build again."
            if not wf.profile.rnode_board_key else
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
def enable_board_bluetooth(wf: "BuildWorkflow") -> StepResult:
    """BLE at birth (operator policy, 2026-08-28): enabled explicitly as a
    provisioning step, never a firmware default — keeper boards get working
    Bluetooth out of the box, wild nodes' firmware stays silent unless
    deliberately switched on. (The 2026-08-27 T-Echo finding: BLE was
    compiled and healthy on every nRF build; nothing ever enabled it.)"""
    if not wf.profile.has_rnode:
        return StepResult("enable_board_bluetooth", True, "No RNode present.",
                          skipped=True)
    from workflows.radio_params import enable_bluetooth_at_birth
    ok, detail = enable_bluetooth_at_birth(
        wf.connection, wf.profile.radio.serial_port)
    return StepResult("enable_board_bluetooth", ok, detail)


@build_step
def write_reticulum_config(wf: "BuildWorkflow") -> StepResult:
    rendered = wf.render_config()
    wf.rendered_config = rendered
    wf.connection.run("mkdir -p ~/.reticulum")
    heredoc = f"cat > ~/.reticulum/config <<'RTTEOF'\n{rendered}\nRTTEOF"
    code, out, err = wf.connection.run(heredoc)
    if code != 0:
        return StepResult("write_reticulum_config", False,
                          f"Could not write config: {err or out}")
    # READ IT BACK. rc=0 from a heredoc through a remote shell says the shell
    # lived, not that the file holds our config (2026-08-12 handover: this was
    # one of the writes never checked). The section header is the marker every
    # rendered template carries.
    check = wf.connection.run("cat ~/.reticulum/config")[1] or ""
    if "[reticulum]" not in check:
        return StepResult(
            "write_reticulum_config", False,
            "Wrote the Reticulum config but reading it back did not find it — "
            "the write did not take.")
    return StepResult("write_reticulum_config", True,
                      "Wrote Reticulum config and read it back.")


@build_step
def install_software_stack(wf: "BuildWorkflow") -> StepResult:
    # Idempotent: only install what is actually missing. RNS is required; LXMF
    # is optional (a pure transport node needs only rnsd) but installed if
    # absent so a propagation node works too.
    have_rns = wf.connection.run("python3 -c 'import RNS'")[0] == 0
    have_lxmf = wf.connection.run("python3 -c 'import LXMF'")[0] == 0
    missing = ([] if have_rns else ["rns"]) + ([] if have_lxmf else ["lxmf"])

    if missing:
        # Resolve pip FIRST — a stock Pi OS Lite has none, and every branch
        # below is a pip invocation (found birthing HOPE, 2026-08-01).
        pip_ok, pip_note = _ensure_pip(wf)
        if not pip_ok:
            return StepResult("install_software_stack", False, pip_note)
        # Prefer carried wheels (the field build has no internet); stage them
        # onto the node and install --no-index. If none are carried, fall back
        # to online pip when the node has connectivity.
        _push_dir(wf, PACKAGE_DIR, REMOTE_PACKAGE_DIR)
        have_wheels = wf.connection.run(f"ls {REMOTE_PACKAGE_DIR}/*.whl")[0] == 0
        pkgs = " ".join(missing)
        if have_wheels:
            cmd = (f"{wf.pip_cmd} install --no-index --find-links {REMOTE_PACKAGE_DIR} "
                   f"--break-system-packages --user {pkgs}")
            source = "carried wheels (offline)"
        elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
            cmd = f"{wf.pip_cmd} install --break-system-packages --user {pkgs}"
            source = "online pip (no wheels carried)"
        else:
            return StepResult(
                "install_software_stack", False,
                f"Cannot install {pkgs}: no wheels in assets/packages and the "
                f"node has no internet. Carry the wheels for a field build.")
        code, out, err = wf.connection.run(cmd, timeout=600)
        if code != 0 and have_wheels and                 wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
            # An INCOMPLETE wheelhouse (e.g. rns carried, lxmf not) must not
            # kill the birth when the node has internet (2026-07-30 proof).
            source = "online pip (wheelhouse incomplete)"
            cmd = f"{wf.pip_cmd} install --break-system-packages --user {pkgs}"
            code, out, err = wf.connection.run(cmd, timeout=600)
        if code != 0:
            return StepResult("install_software_stack", False,
                              f"pip install failed ({source}): {err or out}")
        installed = f"Installed {pkgs} from {source}{pip_note}."
    else:
        installed = "Reticulum and LXMF already installed."

    # lrzsz is only needed for the serial file-push path; best-effort.
    wf.connection.run(wf.priv("apt-get install -y lrzsz") + " || true", timeout=300)
    return StepResult("install_software_stack", True, installed)


#: The stable name the node's Reticulum config points its radio at.
#:
#: NOT a detected port. On a Pi 3 A+ the radio CANNOT be attached while the
#: build runs — the board has exactly one USB-A socket, and the medic's cable is
#: in it. So detect_rnode_port() looks for a radio that is physically absent,
#: finds nothing, and NodeProfile's /dev/ttyUSB0 default survives into the
#: node's config. A Heltec V4 is native USB and comes up as /dev/ttyACM0, so
#: rnsd opened a device that would never exist and the radio was never touched:
#: born, powered, antenna on, and mute. Two nodes went out that way on
#: 2026-08-10 before the operator spotted it on the RNode's own screen — a
#: working radio reads "On @ 1.8kbps" with a filled bar, theirs sat on the
#: version screen with an empty one, which is the display saying "no host has
#: opened me".
#:
#: A udev symlink removes the question. Whatever port the radio lands on,
#: whenever it is plugged in, it is /dev/rnode.
RNODE_SYMLINK = "/dev/rnode"

#: USB vendors of the radios this tool flashes, as udev sees them. Used ONLY as
#: the fallback when the build could not read the attached radio's own serial —
#: see rnode_udev_rules. Vendor alone is a wide net: 0403 is every FTDI cable in
#: the world, 1a86 every CH340 Arduino clone. On a node with one USB socket that
#: is nearly harmless; on a Pi with four it means the first serial gadget anyone
#: plugs in becomes /dev/rnode and rnsd opens it instead of the radio.
_RNODE_USB_VENDORS = (
    ("303a", "Espressif native USB (ESP32-S3: Heltec V3/V4, T-Beam Supreme)"),
    ("10c4", "Silicon Labs CP210x (older Heltec, LilyGO)"),
    ("1a86", "WCH CH340/CH9102"),
    ("0403", "FTDI"),
    ("239a", "Adafruit/Nordic CDC (RAK4631 nRF52840)"),
)


#: Where a radio's USB serial came from — the CODES the profile and the
#: certificate's radio_rule carry, and the English the certificate prints.
#: VITALS translates the code; the sentence never crosses a hand-off.
SERIAL_SOURCES = {
    "node": "read from the radio on this node",
    "flash": "read by the medic when it flashed the board",
    "medic_usb": "read by the medic from the radio on its own USB",
}

#: How the certificate's board was known, same shape. A tap on a photo card
#: is not a reading; the record says which it was (2026-09-22).
BOARD_SOURCES = {
    "usb": "identified on Node Medic's USB and confirmed by the operator",
    "operator": "named by the operator from the catalogue; the radio was "
                "never on Node Medic",
}

#: What a USB serial looks like. Anything else is NOT written into a udev
#: match: `ATTRS{serial}=="*"` is a glob that hands /dev/rnode to every tty
#: while the step, the certificate and VITALS all say "this radio only"
#: (break review, 2026-09-22).
_SERIAL_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def usable_serial(serial: str) -> str:
    """*serial* if it can be matched literally by udev, else ""."""
    serial = (serial or "").strip()
    return serial if _SERIAL_RE.match(serial) else ""


def rnode_udev_rules(serial: str = "", source: str = "") -> str:
    """The udev rules that give the node's radio a stable name.

    TAG+="systemd" makes systemd see the radio as dev-rnode.device, and
    SYSTEMD_WANTS pulls rnsd in when it appears. Together with the unit's own
    ordering that is the whole mechanism: no radio, no rnsd; radio arrives, rnsd
    starts with the device already there.

    MATCHES THE RADIO'S OWN SERIAL WHEN WE KNOW IT. At birth the medic has the
    board in front of it and can read its serial, so the rule can name that one
    device. Falling back to "any tty from these five vendors" was the original
    version and it is a wide net: an FTDI cable or a CH340 Arduino plugged into
    the finished node would take /dev/rnode, and rnsd would open a device that
    is not a radio. The fallback stays for the case where the serial could not
    be read — a rule that is too broad still beats a node that cannot find its
    radio at all — and it says so in the file.

    THIS REPLACED A systemctl CALL FROM INSIDE THE RULE, and the reason is worth
    keeping. The first version ran `systemctl try-restart rnsd` from RUN+=.
    Calling systemctl from udev is discouraged precisely because udev runs in
    its own mount namespace, and whether it works is not something to find out
    in the field. Worse, try-restart only acts on a unit that is ALREADY
    running — and the failure being fixed is rnsd sitting up holding a dead
    interface, which is exactly the state where a restart is needed and exactly
    the state a shrugging daemon is in. Binding the service to the device is
    deterministic and documented; the previous version was a hope.
    """
    name = RNODE_SYMLINK.rsplit("/", 1)[-1]
    tail = f'SYMLINK+="{name}", TAG+="systemd", ENV{{SYSTEMD_WANTS}}="rnsd.service"'
    lines = [
        "# Node Medic: give this node's radio a stable name.",
        "# Written at birth. See workflows/build.py (RNODE_SYMLINK).",
    ]
    serial = usable_serial(serial)
    if serial:
        # The file says where the serial came from, in the words the
        # certificate uses — not "read from the board at birth", which was
        # only sometimes true (2026-09-22).
        how = SERIAL_SOURCES.get(source or "", source or "")
        lines += [
            "# Matched by the radio's OWN serial" + (f" ({how})" if how else "")
            + ":",
            "# no other USB serial device on this node can take the name.",
            f'SUBSYSTEM=="tty", ATTRS{{serial}}=="{serial}", {tail}',
        ]
        return "\n".join(lines) + "\n"
    lines += [
        "# FALLBACK — the build could not read the radio's serial, so this",
        "# matches by USB VENDOR, which is wide: any tty from these makers wins",
        "# the name. If this node ever carries a second USB serial device, pin",
        "# the rule to the radio's serial by hand (udevadm info -a -n /dev/...).",
    ]
    for vid, why in _RNODE_USB_VENDORS:
        lines.append(f"# {why}")
        lines.append(f'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{vid}", {tail}')
    return "\n".join(lines) + "\n"


#: What the certificate says for a board the medic was never told about.
#: On the guide's "I already have a working radio" road the radio never
#: touches the medic; until 2026-09-22 that road never asked which board it
#: was either, so NodeProfile's DEFAULT key (heltec32_v4) reached the
#: certificate as "Heltec LoRa32 v4" — printed under "Built by this medic"
#: for skyfinger, which carries a RAK4631. A default is not a fact.
UNKNOWN_BOARD_TEXT = "unknown — radio attached after the build, board not named"


def board_text(board_key: str) -> str:
    """The board's catalogue name for the certificate, or the honest unknown.

    Never the profile's hardware value: that is the Pi model (or its default),
    and printing it as the radio board was the second half of the same lie.
    """
    board = get_board(board_key or "")
    return board.display_name if board else UNKNOWN_BOARD_TEXT


def radio_port_text(rule: Optional[dict]) -> str:
    """The port line for the certificate: what the udev rule ACTUALLY does.

    The node's config points at the symlink (see render_config), so the only
    truthful port is /dev/rnode plus how udev decides which device gets that
    name — by the radio's own serial (and where the medic learned it), or by
    the vendor net. *rule* is what install_radio_rule recorded after reading
    the file back; None means that step left no record, and the text says so
    rather than falling back to a port name nothing ever opened.
    """
    if not rule:
        return f"{RNODE_SYMLINK} (udev rule not recorded by this build)"
    if rule.get("by") == "serial" and rule.get("serial"):
        code = rule.get("source") or ""
        source = SERIAL_SOURCES.get(code, code)
        return (f"{RNODE_SYMLINK} (udev, by serial {rule['serial']}"
                + (f" — {source}" if source else "") + ")")
    return (f"{RNODE_SYMLINK} (udev, by vendor — any tty from "
            f"{len(_RNODE_USB_VENDORS)} makers; no serial known)")


def attached_radio_serial(wf: "BuildWorkflow") -> str:
    """The serial of the radio attached to the node right now, or "".

    Read from udev's own view (ID_SERIAL_SHORT), which is the same attribute the
    rule matches on — asking the thing itself rather than deriving it.
    """
    port = getattr(getattr(wf.profile, "radio", None), "serial_port", "") or ""
    if not port:
        return ""
    code, out, _ = wf.connection.run(f"udevadm info -q property -n {port}")
    if code != 0:
        return ""
    for line in (out or "").splitlines():
        if line.startswith("ID_SERIAL_SHORT="):
            return line.split("=", 1)[1].strip()
    return ""


@build_step
def install_radio_rule(wf: "BuildWorkflow") -> StepResult:
    """Give the node a stable name for its radio, before the services start.

    Runs whether or not a radio is attached — the whole point is that it works
    for the radio that is not here yet.
    """
    serial = usable_serial(attached_radio_serial(wf))
    came_from = "node" if serial else ""              # a SERIAL_SOURCES code
    unusable = ""
    if not serial:
        # THE SERIAL THE MEDIC ALREADY HOLDS. On every Pi birth the radio is
        # attached AFTER the build — flashed on the medic at step 0, carried in
        # the operator's pocket while the card bakes — so the probe above finds
        # nothing, every time. The "exception" fallback below was the rule
        # (2026-08-12 handover). The flash captured the board's USB serial; the
        # profile carries it here so the rule can still name that one device.
        # NOTHING ELSE fills it: a serial the medic never read is not carried
        # (the ledger lookup of 2026-09-22 was a guess printed as a reading).
        carried = (getattr(wf.profile.radio, "usb_serial", "") or "").strip()
        serial = usable_serial(carried)
        if carried and not serial:
            unusable = (f" The carried value {carried!r} is not a usable "
                        "serial, so it was not written.")
        # The flash hand-back predates the provenance code; its serial has
        # always meant "read at the flash".
        came_from = ((getattr(wf.profile.radio, "usb_serial_source", "") or "")
                     .strip() or "flash") if serial else ""
    rules = rnode_udev_rules(serial, came_from)
    code, out, err = wf.connection.run(
        _write_remote_file(wf, "/etc/udev/rules.d/60-rnode.rules", rules))
    if code != 0:
        return StepResult("install_radio_rule", False,
                          f"Could not write the radio's udev rule: {err or out}")
    # AND READ IT BACK. A write that is not read back is a claim, not a fact —
    # and this file failing to land is the mute-node class of failure: nothing
    # complains until the assembled node cannot find its radio.
    check = wf.connection.run("cat /etc/udev/rules.d/60-rnode.rules")[1] or ""
    want = (f'ATTRS{{serial}}=="{serial}"' if serial else 'ATTRS{idVendor}')
    name = RNODE_SYMLINK.rsplit("/", 1)[-1]
    if want not in check or f'SYMLINK+="{name}"' not in check:
        return StepResult(
            "install_radio_rule", False,
            "Wrote the radio's udev rule but reading it back found something "
            "else — the node would not name its radio. Nothing else is wrong; "
            "this is the one write that did not take.")
    # Reload so a radio plugged in later is named without a reboot. A node that
    # boots with its radio attached does not need this; one being assembled on
    # the bench does.
    wf.connection.run(wf.priv("udevadm control --reload-rules"))
    wf.connection.run(wf.priv("udevadm trigger --subsystem-match=tty"))
    # WHAT WAS WRITTEN AND READ BACK, for the certificate. The cert step
    # cannot see this file (it asks the node about other things) and must
    # not reconstruct it from the profile: the record is made here, after
    # the read-back, so the certificate's port line is what the node holds
    # (2026-09-22 — skyfinger's cert printed the profile default instead).
    wf.radio_rule = {"by": "serial" if serial else "vendor",
                     "serial": serial, "source": came_from}
    how = (f"this radio only (serial {serial}, "
           f"{SERIAL_SOURCES.get(came_from, came_from)})" if serial
           else "a radio from any of the makers this tool flashes — no serial "
                "was available to pin it tighter" + unusable)
    return StepResult("install_radio_rule", True,
                      f"The node will answer to {RNODE_SYMLINK} for {how}, "
                      "whenever it is plugged in and on whichever port it "
                      "lands, checked on the card.")


@build_step
def configure_services(wf: "BuildWorkflow") -> StepResult:
    user = wf.run_user()
    home = (wf.connection.run("echo $HOME")[1].strip()
            or ("/root" if user == "root" else f"/home/{user}"))
    # Only configure services whose binary actually exists — LXMF/lxmd may not
    # be installed (RNS alone is enough for a transport node). ExecStart must be
    # the *resolved* absolute path (pip --user -> ~/.local/bin), and User=/HOME=
    # must point at the account whose ~/.reticulum holds the config we wrote.
    # lxmd runs as an LXMF Propagation Node (-p) — the role a Pi + RNode fills —
    # and starts After rnsd so it joins rnsd's shared Reticulum instance rather
    # than trying to own the radio itself (which rnsd already holds). Monitoring
    # attaches to the same shared instance, so both roles run side by side.
    # NO BindsTo=dev-rnode.device HERE, AND THAT IS A DELIBERATE RETREAT.
    #
    # It was added on 2026-08-10 to make rnsd start when the radio appears, and
    # reverted the same evening. Two reasons, and the second is the important
    # one. First: it was never verified that systemd really publishes a
    # dev-rnode.device alias for a SYMLINK+= rule on this image — and if it does
    # not, BindsTo means rnsd never starts AT ALL, which is worse than the bug
    # it was meant to fix. Second: for the case that actually matters in the
    # field — a node powered up with its radio already attached — nothing extra
    # is needed. udev creates the symlink during USB enumeration, long before
    # rnsd starts after network-online.target, so the port simply exists.
    #
    # The symlink is the fix. This was scaffolding around it, added blind
    # because the assembled node could not be reached to test anything, and
    # shipping untested scaffolding into a birth path is how a node ends up
    # worse than before. See task #84.
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
            # systemd's DEFAULT start limit is kept on purpose. Removing it was
            # part of the same disproved change as panic_on_interface_error: the
            # pair would turn a flapping radio into an unbounded 5-second
            # restart loop that never parks, on a node whose scarcest shared
            # resource is airtime. A unit that lands in `failed` is visible;
            # one that restarts forever is not.
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
    # A FRESH lxmd PROPAGATION IDENTITY PER NODE (2026-08-22). lxmd mints its
    # lxmf.propagation identity into ~/.lxmd/identity on first start — but a Pi
    # imaged from a GOLDEN card can carry a PREVIOUS node's file, so two physical
    # nodes would announce the SAME propagation destination and fold into one
    # machine in VITALS. The rnsd identity is minted fresh per card at imaging;
    # lxmd's has no such step, so delete any carried-over file here, BEFORE lxmd
    # first starts, and it mints its own. Only when lxmd is actually configured.
    if "lxmd" in services:
        wf.connection.run("rm -f ~/.lxmd/identity")
    wf.connection.run(wf.priv("systemctl daemon-reload"))
    for svc in services:
        wf.connection.run(wf.priv(f"systemctl enable {svc}"))
        wf.connection.run(wf.priv(f"systemctl start {svc}"))
    # AND ASK SYSTEMD WHETHER THEY ARE RUNNING. The enable/start results were
    # discarded, so lxmd — the thing that makes this a propagation node — was
    # never verified (2026-08-12 handover). A beat first: Type=simple units
    # report active immediately, but the shell round-trip is not free.
    wf.connection.run("sleep 2")
    dead = []
    for svc in services:
        state = wf.connection.run(f"systemctl is-active {svc}")[1].strip()
        if state != "active":
            why = wf.connection.run(
                f"journalctl -u {svc} -n 3 --no-pager 2>/dev/null")[1].strip()[-160:]
            dead.append(f"{svc} is {state or 'unknown'}"
                        + (f" ({why})" if why and why != "ok" else ""))
    if dead:
        return StepResult("configure_services", False,
                          "Installed but not running: " + "; ".join(dead))
    return StepResult("configure_services", True,
                      f"Installed, started and verified running: "
                      f"{', '.join(services)}.")


#: The node's own health identity — a stable file the reporter reuses across
#: restarts (the medic maps its rtnode.health destination to the node profile).
_HEALTH_IDENTITY_PATH = "~/.reticulum-node-medic/pi_health_identity"

#: Reporter modules pushed onto a propagation node (self-contained package, run
#: with PYTHONPATH so ``python3 -m monitor.pi_health_reporter`` resolves).
#: ``http_status`` and ``pi_status_server`` ride along for the /status endpoint —
#: the endpoint imports the contract constants from http_status rather than
#: keeping a second copy of them on the far side of a USB cable.
_HEALTH_MODULES = ("health_beacon.py", "ups.py", "pi_health_reporter.py",
                   "http_status.py", "pi_status_server.py",
                   # the unicast health reply (2026-09-21) and the path warmer
                   # the reporter borrows from the medic's own poll code
                   "health_reply.py", "health_poll.py",
                   # time over the mesh (2026-09-23): the node's clock policy
                   "node_time.py")


def _push_health_package(wf: "BuildWorkflow") -> "tuple[str, str, str]":
    """Stage the reporter package on the node; return (user, home, pkg_dir).

    Shared by the two services that run out of it. Pushing is idempotent, so the
    second caller costs a re-copy and nothing else — which is cheaper than the
    alternative, where one step's package silently depends on another step
    having run first and a resumed build installs a service with no code under it.
    """
    user = wf.run_user()
    home = (wf.connection.run("echo $HOME")[1].strip()
            or ("/root" if user == "root" else f"/home/{user}"))
    pkg_dir = f"{home}/.rnm-health/monitor"
    mon_dir = os.path.join(os.path.dirname(__file__), os.pardir, "monitor")

    wf.connection.run(f"mkdir -p {pkg_dir}")
    wf.connection.run(f"touch {pkg_dir}/__init__.py")
    for name in _HEALTH_MODULES:
        local = os.path.join(mon_dir, name)
        if os.path.isfile(local):
            wf.connection.push_file(local, f"{pkg_dir}/{name}")
    return user, home, pkg_dir


def _power_source(profile: NodeProfile) -> str:
    """What BIRTH stamps as the node's power source, from the profile hardware.

    "unknown" when nothing was declared — nothing in the flow ASKS yet, and
    stamping "mains" by default put a false claim in every beacon of every
    solar node (build-lens, 2026-08-13). Unknown is an honest answer; a
    power question can join the prelude when the operator wants one.
    """
    if profile.has_solar_controller:
        return "solar"
    if profile.has_battery_bank:
        return "battery"
    return "unknown"


#: Read the health reporter's rtnode.health destination hash off the node (after
#: the service has created its identity file). Mirrors _RETICULUM_ADDR_CMD.
_HEALTH_DST_CMD = (
    "python3 -c \"import RNS, os; "
    f"p=os.path.expanduser('{_HEALTH_IDENTITY_PATH}'); "
    "i=RNS.Identity.from_file(p) if os.path.exists(p) else None; "
    "RNS.Reticulum() if i else None; "
    "d=RNS.Destination(i, RNS.Destination.IN, RNS.Destination.SINGLE, "
    "'rtnode', 'health') if i else None; "
    "print(RNS.hexrep(d.hash, delimit=False) if d else '')\" 2>/dev/null")


@build_step
def install_health_reporter(wf: "BuildWorkflow") -> StepResult:
    """Install the propagation-node health reporter so a Pi + RNode node beacons
    its health — battery + transmission — on ``rtnode.health``, the same beacon
    the medic already decodes from RTNode-2400s.

    Only a PROPAGATION node needs this: an RTNode-2400 reports from its own C++
    firmware, and a pure transport node runs only rnsd. Other roles skip. The
    reporter attaches to rnsd's shared Reticulum instance (it does not own the
    radio), so it runs beside rnsd/lxmd, and starts after them.
    """
    if wf.profile.role != NodeRole.PROPAGATION:
        return StepResult("install_health_reporter", True,
                          "Not a propagation node — health reporter not needed.",
                          skipped=True)

    user, home, _pkg_dir = _push_health_package(wf)

    src = _power_source(wf.profile)
    code, py, _ = wf.connection.run("command -v python3")
    py = py.strip() or "/usr/bin/python3"
    unit = (
        "[Unit]\n"
        "Description=rnm-health (propagation-node health beacon)\n"
        "After=rnsd.service network-online.target\n"
        "Wants=rnsd.service\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"Environment=HOME={home}\n"
        f"Environment=PYTHONPATH={home}/.rnm-health\n"
        f"WorkingDirectory={home}/.rnm-health\n"
        f"ExecStart={py} -m monitor.pi_health_reporter --power-source {src}\n"
        "Restart=always\n"
        "RestartSec=15\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    heredoc = (
        f"{wf.priv('tee /etc/systemd/system/rnm-health.service')} "
        f">/dev/null <<'RTTEOF'\n{unit}\nRTTEOF"
    )
    code, out, err = wf.connection.run(heredoc)
    if code != 0:
        return StepResult("install_health_reporter", False,
                          f"Could not write rnm-health.service: {err or out}")
    wf.connection.run(wf.priv("systemctl daemon-reload"))
    wf.connection.run(wf.priv("systemctl enable rnm-health"))
    wf.connection.run(wf.priv("systemctl start rnm-health"))

    # ASK WHETHER IT IS ACTUALLY RUNNING (it never was asked — 2026-08-12
    # handover). Not a failed step when it isn't: a failed step strands the
    # USB hand-back, the same deliberate reasoning as install_status_server.
    # But the message may not claim a heartbeat nobody checked, and
    # final_verification will hold this unit to "active" as well.
    wf.connection.run("sleep 2")
    state = wf.connection.run("systemctl is-active rnm-health")[1].strip()
    if state != "active":
        why = wf.connection.run(
            "journalctl -u rnm-health -n 5 --no-pager 2>/dev/null")[1].strip()[-200:]
        return StepResult(
            "install_health_reporter", True,
            f"Installed the health reporter but it is NOT running "
            f"(systemd says '{state or 'unknown'}'). The node will not beacon "
            f"its health until this is fixed."
            + (f" Last words: {why}" if why and why != "ok" else ""))

    # Give the service a moment to create its identity + first announce, then
    # capture the health destination hash so BIRTH can roster the node under it
    # (that hash is the registry key — without it the beacon shows as anonymous).
    wf.connection.run("sleep 3")
    dst = wf.connection.run(_HEALTH_DST_CMD)[1].strip().splitlines()
    dst = dst[-1].strip() if dst else ""
    if len(dst) == 32 and all(c in "0123456789abcdef" for c in dst.lower()):
        wf.profile.health_dst_hash = dst.lower()
        where = f" health dst {dst.lower()[:8]}…"
    else:
        where = " (health dst not captured yet — until it is rostered, its first beacon shows as an unnamed neighbour)"

    return StepResult("install_health_reporter", True,
                      f"Health reporter installed and started ({src} power);{where}")


# -- time over the mesh (docs/HEALTH_REPLY_UNICAST.md, 2026-09-23) -------------
#
# A solar Pi that dies overnight boots with no clock. Its reporter asks THIS
# medic for the time over LoRa and accepts only a time signed by the medic's
# health-reply identity. Birth writes three things onto the node: the trust
# file naming that identity, the root helper that is the reporter's only road
# to the clock, and the sudoers line that opens exactly that road.

NM_SETTIME_PATH = "/usr/local/sbin/nm-settime"
NM_SETTIME_SUDOERS_PATH = "/etc/sudoers.d/nm-settime"
#: sudoers.d skips names containing a '.', so a staged file is inert until
#: visudo has accepted it and it is moved into place.
_NM_SETTIME_SUDOERS_STAGE = "/etc/sudoers.d/nm-settime.tmp"
#: The medic's own health-reply identity file — monitor.health_reply owns
#: the literal; a module name here so a test can point it at a temp dir
#: instead of the developer's real home.
_REPLY_IDENTITY_PATH = REPLY_IDENTITY_PATH
#: What the three artefacts must read back as (`stat -c '%U:%G %a'`).
NM_SETTIME_OWNER_MODE = "root:root 755"
NM_SETTIME_SUDOERS_OWNER_MODE = "root:root 440"

#: The helper, verbatim. One argument — a 10-digit epoch inside
#: 2026-01-01..2100-01-01 (monitor.node_time.EPOCH_MIN/EPOCH_MAX) — then it
#: sets the clock and prints the UTC read-back. Nothing else: this is the
#: whole of what NOPASSWD root buys the reporter user.
NM_SETTIME_SCRIPT = """#!/bin/bash
# nm-settime - Node Medic's clock helper, installed at birth (workflows/build.py,
# 2026-09-23). The health reporter's ONLY privileged road to the clock:
# exactly one argument, a 10-digit epoch inside 2026-01-01..2100-01-01,
# then the clock is set and the UTC read-back printed. sudoers: /etc/sudoers.d/nm-settime
set -eu
if [ "$#" -ne 1 ]; then
  echo "usage: nm-settime <epoch, 10 digits>" >&2
  exit 2
fi
e="$1"
case "$e" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) echo "nm-settime: not a 10-digit epoch" >&2; exit 2 ;;
esac
if [ "$e" -lt 1767225600 ] || [ "$e" -ge 4102444800 ]; then
  echo "nm-settime: epoch outside 2026-01-01..2100-01-01" >&2
  exit 2
fi
date -s "@$e" >/dev/null
date -u
"""

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


def nm_settime_sudoers(user: str) -> str:
    """`<user> ALL=(root) NOPASSWD: /usr/local/sbin/nm-settime` — the one
    line. A user name that is not a user name never reaches sudoers."""
    if not _USER_RE.match(user or ""):
        raise ValueError(f"{user!r} is not a user name")
    return f"{user} ALL=(root) NOPASSWD: {NM_SETTIME_PATH}\n"


def medic_time_anchor(name: Optional[str] = None) -> dict:
    """What a node must trust to take the time from THIS medic: the hash of
    the medic's health-reply identity and the hash of the reply destination
    it announces (the node recalls the identity from that announce). LOAD-
    ONLY (review, 2026-09-23): the identity is created by the UI's
    _setup_health_reply and nowhere else — a build run on a machine that
    is not the medic must not mint a fresh identity and hand nodes an
    anchor nothing will ever announce. Without RNS, or without the file,
    this raises and the caller says so."""
    try:
        import RNS
    except Exception as e:                                         # noqa: BLE001
        raise RuntimeError("RNS is not importable on this medic, so its time-trust "
                           "anchor cannot be computed: %s" % e)
    import datetime
    import os as _os
    import socket
    from monitor.health_reply import REPLY_APP, REPLY_ASPECTS
    from monitor.node_time import trust_anchor
    path = _os.path.expanduser(_REPLY_IDENTITY_PATH)
    ident = RNS.Identity.from_file(path) if _os.path.isfile(path) else None
    if ident is None:
        raise RuntimeError("this machine holds no Node Medic health-reply identity — "
                           "run this from the medic")
    reply_dest = RNS.Destination.hash(ident, REPLY_APP, *REPLY_ASPECTS)
    return trust_anchor(ident.hash.hex(), reply_dest.hex(),
                        name or socket.gethostname(), datetime.date.today().isoformat())


def _b64_write_cmd(content: str, path: str, priv=None) -> str:
    """Base64 through a pipe into tee — the idiom _write_remote_file uses,
    with the privilege wrapper injected (the push tool has its own)."""
    import base64
    import shlex
    b64 = base64.b64encode(content.encode()).decode()
    tee = f"tee {shlex.quote(path)}"
    if priv is not None:
        tee = priv(tee)
    return f"echo {shlex.quote(b64)} | base64 -d | {tee} >/dev/null"


def _owner_mode(conn, priv, path: str) -> str:
    """`user:group mode` of *path* as the node reports it, "" if unreadable."""
    try:
        code, out, _err = conn.run(priv(f"stat -c '%U:%G %a' {path}"))
    except Exception:                                                  # noqa: BLE001
        return ""
    return (out or "").strip() if code == 0 else ""


def time_trust_readback(conn, priv, user: str, home: str, anchor: Optional[dict]
                        ) -> "tuple[list, list, list]":
    """Read the three time-trust artefacts back — content AND owner/mode —
    and say which are right, which are wrong, and which could not be
    checked. Shared by the installer, final_verification and the reporter
    push, so all three claim the same thing from the same evidence.
    Returns (verified, problems, unchecked) as lists of sentences. *anchor*
    None = the medic's anchor is unavailable here: the trust file is then
    checked for being a valid anchor, not for naming THIS medic."""
    from monitor.node_time import trust_anchor, trust_anchor_json
    verified, problems, unchecked = [], [], []
    trust_path = f"{home}/.rnm-health/trusted_medic.json"
    code, out, _ = conn.run(f"cat {trust_path}")
    if code != 0 or not (out or "").strip():
        problems.append("trust file missing (%s)" % trust_path)
    elif anchor is not None:
        if out == trust_anchor_json(anchor):
            verified.append("trust file names this medic")
        else:
            problems.append("trust file does not read back as this medic's anchor")
    else:
        try:
            data = json.loads(out)
            trust_anchor(data.get("identity_hash"), data.get("reply_dest"),
                         data.get("name"), data.get("since"))
            unchecked.append("trust file is a valid anchor but this machine cannot "
                             "say whether it names the medic")
        except (ValueError, AttributeError, TypeError):
            problems.append("trust file is not a valid anchor")
    code, out, _ = conn.run(f"cat {NM_SETTIME_PATH}")
    if code != 0 or out != NM_SETTIME_SCRIPT:
        problems.append("clock helper %s" % ("missing" if code != 0 or not out
                                              else "does not read back as written"))
    else:
        om = _owner_mode(conn, priv, NM_SETTIME_PATH)
        if om == NM_SETTIME_OWNER_MODE:
            verified.append("clock helper in place (%s)" % om)
        elif om:
            problems.append("clock helper is %s, not %s" % (om, NM_SETTIME_OWNER_MODE))
        else:
            unchecked.append("clock helper owner/mode could not be read")
    try:
        want = nm_settime_sudoers(user)
    except ValueError:
        want = None
    code, out, _ = conn.run(priv(f"cat {NM_SETTIME_SUDOERS_PATH}"))
    if code != 0 or not (out or "").strip():
        problems.append("sudoers line missing (%s)" % NM_SETTIME_SUDOERS_PATH)
    elif want is not None and out != want:
        problems.append("sudoers line does not read back as written")
    else:
        om = _owner_mode(conn, priv, NM_SETTIME_SUDOERS_PATH)
        if om == NM_SETTIME_SUDOERS_OWNER_MODE:
            verified.append("sudoers line in place (%s)" % om)
        elif om:
            problems.append("sudoers file is %s, not %s" % (om, NM_SETTIME_SUDOERS_OWNER_MODE))
        else:
            unchecked.append("sudoers owner/mode could not be read")
    return verified, problems, unchecked


def _unwind_time_trust(conn, priv, home: str, what: list) -> None:
    """Remove what earlier stages wrote, so the node never holds a trust
    file with no road to the clock (or a helper nobody may run)."""
    if "trust" in what:
        conn.run(f"rm -f {home}/.rnm-health/trusted_medic.json")
    if "helper" in what:
        conn.run(priv(f"rm -f {NM_SETTIME_PATH}"))
    conn.run(priv(f"rm -f {_NM_SETTIME_SUDOERS_STAGE}"))


def install_node_time_trust(conn, priv, user: str, home: str, anchor: dict
                            ) -> "tuple[bool, str]":
    """Put the trust file, the helper and the sudoers line on a node and READ
    ALL THREE BACK (content, owner and mode). Shared by birth
    (install_time_trust) and the reporter push (workflows.pi_reporter_push).
    *priv* wraps a command in sudo when needed. The sudoers line is staged,
    held to `visudo -c -f`, and only then moved in. Any failure UNWINDS the
    earlier stages — a node must never hold a trust file with no road to
    the clock — and the message names exactly what did not land."""
    from monitor.node_time import trust_anchor_json
    try:
        sudoers = nm_settime_sudoers(user)
    except ValueError as e:
        return False, f"Cannot write a sudoers line: {e}; nothing was written."
    trust_text = trust_anchor_json(anchor)
    trust_dir = f"{home}/.rnm-health"
    trust_path = f"{trust_dir}/trusted_medic.json"

    # 1. the trust file — the reporter user's own, no root needed
    conn.run(f"mkdir -p {trust_dir}")
    conn.run(_b64_write_cmd(trust_text, trust_path))
    if conn.run(f"cat {trust_path}")[1] != trust_text:
        _unwind_time_trust(conn, priv, home, ["trust"])
        return False, (f"The trust file did not read back as written ({trust_path}) "
                       "and was removed; the helper and sudoers line were not written.")

    # 2. the helper — root, 0755
    conn.run(_b64_write_cmd(NM_SETTIME_SCRIPT, NM_SETTIME_PATH, priv))
    conn.run(priv(f"chown root:root {NM_SETTIME_PATH}"))
    conn.run(priv(f"chmod 0755 {NM_SETTIME_PATH}"))
    if conn.run(f"cat {NM_SETTIME_PATH}")[1] != NM_SETTIME_SCRIPT:
        _unwind_time_trust(conn, priv, home, ["trust", "helper"])
        return False, (f"The clock helper did not read back as written ({NM_SETTIME_PATH}); "
                       "it and the trust file were removed; the sudoers line was not written.")

    # 3. the sudoers line — staged, checked, then moved in; root, 0440
    stage = _NM_SETTIME_SUDOERS_STAGE
    conn.run(_b64_write_cmd(sudoers, stage, priv))
    conn.run(priv(f"chown root:root {stage}"))
    conn.run(priv(f"chmod 0440 {stage}"))
    code, out, err = conn.run(priv(f"visudo -c -f {stage}"))
    if code != 0:
        _unwind_time_trust(conn, priv, home, ["trust", "helper"])
        if code == 127:
            # Not a rejection: the check itself could not run. Without
            # visudo's word a sudoers file is never moved in (a bad one
            # locks sudo for every operator on the node).
            return False, ("The sudoers line could not be checked (visudo not found), so "
                           "it was not installed; the trust file and helper were removed.")
        return False, ("visudo rejected the sudoers line, so it was removed — and the "
                       "trust file and helper with it: %s" % ((err or out or "").strip()
                                                              or "no output"))
    conn.run(priv(f"mv {stage} {NM_SETTIME_SUDOERS_PATH}"))
    verified, problems, unchecked = time_trust_readback(conn, priv, user, home, anchor)
    if problems:
        conn.run(priv(f"rm -f {NM_SETTIME_SUDOERS_PATH}"))
        _unwind_time_trust(conn, priv, home, ["trust", "helper"])
        return False, ("Read-back after install disagreed (%s); all three were removed."
                       % "; ".join(problems))
    tail = (" Not checked: " + "; ".join(unchecked) + "." if unchecked else "")
    return True, ("Trust file, clock helper and sudoers line written and read back "
                  "(%s): the node takes the time only from this medic (reply destination "
                  "%s…, identity %s…).%s" % ("; ".join(verified), anchor["reply_dest"][:8],
                                            anchor["identity_hash"][:8], tail))


@build_step
def install_time_trust(wf: "BuildWorkflow") -> StepResult:
    """Tell the node which medic to believe about the time, and open its one
    road to the clock (docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh").

    Its own step, and one that NEVER FAILS THE BUILD (review, 2026-09-23):
    the birth's step 10 sits before hand_the_usb_port_back, and a failed
    step stops run_all — a node born with a working radio but no time
    trust is a node; a node that cannot see its own radio is not
    (WORKING_METHOD Part 3). So every failure returns success with a
    message naming exactly what did not land and that the node will not
    take the time from this medic; the installer has already unwound the
    stages that did land, and final_verification reads the three artefacts
    back so the certificate cannot claim them. The reporter, already
    running, loads the trust file lazily on each ask, so no restart is
    needed here.
    """
    if wf.profile.role != NodeRole.PROPAGATION:
        return StepResult("install_time_trust", True,
                          "Not a propagation node — no clock to feed.", skipped=True)
    try:
        anchor = medic_time_anchor()
    except Exception as e:                                         # noqa: BLE001
        return StepResult("install_time_trust", True,
                          f"NOT installed: Node Medic could not load its own time-trust "
                          f"anchor, so nothing was written to the node and the node will "
                          f"not take the time from this medic: {e}")
    user = wf.run_user()
    home = (wf.connection.run("echo $HOME")[1].strip()
            or ("/root" if user == "root" else f"/home/{user}"))
    try:
        ok, msg = install_node_time_trust(wf.connection, wf.priv, user, home, anchor)
    except Exception as e:                                         # noqa: BLE001
        ok, msg = False, f"the installer raised: {e}"
    if ok:
        return StepResult("install_time_trust", True, msg)
    return StepResult("install_time_trust", True,
                      "NOT installed — the node will not take the time from this medic. "
                      + msg)


@build_step
def install_status_server(wf: "BuildWorkflow") -> StepResult:
    """Give the node an HTTP ``/status`` the medic can read, like an RTNode-2400.

    WITHOUT THIS A BIRTHED PI CAN ONLY EVER SHOW A LORA CHIP. VITALS' WIFI and
    NET states come from an HTTP poll or a decoded health beacon and from nothing
    else (``registry._capabilities``); the beacon is twenty bytes every six
    hours; so between births the medic had no evidence about a Pi node at all and
    drew it grey. Grey was honest. It was also useless, and the answer to it is
    never to assume — it is to give the medic something to read. See
    :mod:`monitor.pi_status_server`.

    Runs as its own unit rather than inside the health reporter. The reporter
    needs RNS and stops when RNS does; the endpoint whose whole job is to answer
    "what is wrong with you" must survive exactly the moments when the mesh side
    is unhappy.
    """
    if wf.profile.role != NodeRole.PROPAGATION:
        return StepResult("install_status_server", True,
                          "Not a propagation node — no /status endpoint needed.",
                          skipped=True)

    from monitor.http_status import STATUS_PATH, STATUS_PORT

    user, home, _pkg_dir = _push_health_package(wf)
    py = (wf.connection.run("command -v python3")[1].strip() or "/usr/bin/python3")
    unit = (
        "[Unit]\n"
        "Description=rnm-status (propagation-node /status endpoint)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"Environment=HOME={home}\n"
        f"Environment=PYTHONPATH={home}/.rnm-health\n"
        f"WorkingDirectory={home}/.rnm-health\n"
        f"ExecStart={py} -m monitor.pi_status_server --port {STATUS_PORT}\n"
        # Port 80 so ONE contract, ONE LAN sweep and ONE parser cover both an
        # RTNode-2400 and a Pi. Binding it needs a capability, NOT root: this is
        # a network-facing server answering unauthenticated requests, and there
        # is no reason for it to be able to do anything else on the machine.
        "AmbientCapabilities=CAP_NET_BIND_SERVICE\n"
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n"
        "NoNewPrivileges=yes\n"
        "PrivateTmp=yes\n"
        "Restart=always\n"
        "RestartSec=15\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    code, out, err = wf.connection.run(
        _write_remote_file(wf, "/etc/systemd/system/rnm-status.service", unit))
    if code != 0:
        return StepResult("install_status_server", False,
                          f"Could not write rnm-status.service: {err or out}")
    wf.connection.run(wf.priv("systemctl daemon-reload"))
    wf.connection.run(wf.priv("systemctl enable rnm-status"))
    wf.connection.run(wf.priv("systemctl restart rnm-status"))

    # READ IT BACK, FROM THE NODE'S OWN SIDE. A unit that was written and
    # enabled is a claim; a unit that answers is a fact. This project has been
    # bitten four times in two days by writes that were never checked, and this
    # one fails in a way nobody would notice for weeks — the node is fine, the
    # medic simply never sees it. Asking over the loopback separates "the
    # service is not running" from "the medic cannot reach this node", which are
    # different problems with different fixes.
    wf.connection.run("sleep 2")
    probe = wf.connection.run(
        f"curl -fsS -m5 http://127.0.0.1:{STATUS_PORT}{STATUS_PATH}")
    if probe[0] == 0 and "fork" in (probe[1] or ""):
        wf.profile.serves_status = True
        return StepResult("install_status_server", True,
                          "The node serves its status on port 80 and answered "
                          "when asked — Node Medic can read it over the network.")

    # NOT A FAILED STEP, and that is deliberate. A failed step STOPS the build,
    # and the step that runs last on this path is the one that hands the USB
    # socket back to the radio — stopping short of it leaves a Pi 3 A+ that
    # cannot see its own radio at all. Losing the dashboard is a bad evening;
    # shipping a mute node is a wasted trip. So the birth carries on and this
    # says plainly what did not work.
    active = wf.connection.run("systemctl is-active rnm-status")[1].strip()
    why = wf.connection.run(
        "journalctl -u rnm-status -n 5 --no-pager 2>/dev/null")[1].strip()[-200:]
    return StepResult(
        "install_status_server", True,
        f"Installed the /status service, but the node did not answer its own "
        f"request (systemd says '{active or 'unknown'}'). The node is otherwise "
        f"built; what is lost is Node Medic's live view of its wifi and network."
        + (f" Last words: {why}" if why else ""))


@build_step
def apply_system_hardening(wf: "BuildWorkflow") -> StepResult:
    """Log2Ram from the carried .deb, and the Pi's own hardware watchdog.

    THIS STEP NEVER FAILS THE BUILD, deliberately (2026-08-12, node 'soon'):
    the first honest version failed when nothing landed and stranded a real
    birth five steps short of its certificate — hostname, verification, the
    report proof and the USB hand-back never ran, for a nice-to-have. Same
    reasoning as install_health_reporter: the message may not claim what did
    not land, and it names every gap; but the build carries on.

    THE WATCHDOG IS SYSTEMD'S OWN. `systemctl enable watchdog` needed a
    daemon package no build ever carried, so under the old `|| true` the
    watchdog had NEVER landed once — the blanket success hid it for a month.
    The Pi's bcm2835 watchdog needs no package: a RuntimeWatchdogSec drop-in
    arms it, offline. The step reads its write back and then believes only
    the value systemd REPORTS.
    """
    landed, gaps = [], []

    # -- Log2Ram, staged from the medic's carried packages -------------------
    wf.connection.run(f"mkdir -p {REMOTE_ASSET_DIR}")
    pushed = wf.connection.push_file(
        os.path.join(PACKAGE_DIR, "log2ram.deb"),
        f"{REMOTE_ASSET_DIR}/log2ram.deb")
    if pushed:
        wf.connection.run(wf.priv(f"dpkg -i {REMOTE_ASSET_DIR}/log2ram.deb") + " || true")
        wf.connection.run(wf.priv("systemctl enable log2ram") + " || true")
        state = wf.connection.run("systemctl is-enabled log2ram")[1].strip()
        if state == "enabled":
            landed.append("Log2Ram")
        else:
            gaps.append(f"Log2Ram ({state or 'unknown'})")
    else:
        # A push that fails silently is exactly how this step lied for a
        # month — push_file's return value used to be dropped on the floor.
        gaps.append("Log2Ram (the .deb did not reach the node — is "
                    "assets/packages/log2ram.deb on this medic?)")

    # -- the hardware watchdog, through systemd itself -----------------------
    wf.connection.run(wf.priv("mkdir -p /etc/systemd/system.conf.d"))
    wf.connection.run(_write_remote_file(
        wf, "/etc/systemd/system.conf.d/10-nodemedic-watchdog.conf",
        "[Manager]\nRuntimeWatchdogSec=15\n"))
    check = wf.connection.run(
        "cat /etc/systemd/system.conf.d/10-nodemedic-watchdog.conf")[1] or ""
    if "RuntimeWatchdogSec=15" in check:
        wf.connection.run(wf.priv("systemctl daemon-reexec"))
        usec = wf.connection.run(
            "systemctl show -p RuntimeWatchdogUSec --value")[1].strip()
        if usec and usec not in ("0", "infinity"):
            landed.append(f"hardware watchdog (systemd, {usec})")
        else:
            gaps.append("hardware watchdog (drop-in written but systemd "
                        "reports it unarmed)")
    else:
        gaps.append("hardware watchdog (the drop-in did not read back)")

    if not landed:
        return StepResult(
            "apply_system_hardening", True,
            "No hardening landed — the node runs without it. Did not land: "
            + "; ".join(gaps) + ".")
    msg = "Hardening verified: " + ", ".join(landed) + "."
    if gaps:
        msg += " Did not land: " + "; ".join(gaps) + "."
    return StepResult("apply_system_hardening", True, msg)


@build_step
def configure_bluetooth(wf: "BuildWorkflow") -> StepResult:
    """Apply the birth answer about the node's Bluetooth radio.

    ON means the stock OS is left alone — nothing to do, and the message says
    only that. OFF is real work, done in both tenses: dtoverlay=disable-bt in
    the boot config so the radio is never powered again from the next boot
    (the whole power saving), plus an immediate rfkill block and the services
    off so the choice means something before that reboot. The boot config is
    transformed in Python and written back whole, then read back — the house
    rule for privileged writes. A gap is named and the build carries on: a
    power tweak must never strand a birth (apply_system_hardening's lesson,
    same day).
    """
    if wf.profile.bluetooth_enabled:
        # ON is NOT "leave the stock OS alone": the carried image ships its
        # radios rfkill-SOFT-BLOCKED, exactly like the card Wi-Fi that never
        # joined anything. "Bluetooth on" at birth left the block in place, and
        # the node's first honest beacon reported BT known-but-DOWN (the orange
        # chip, ELSEWHERE 2026-08-25). Unblock it and make sure the service
        # runs — best-effort with " || true", a radio tweak never strands a
        # birth.
        wf.connection.run(wf.priv("rfkill unblock bluetooth") + " || true")
        wf.connection.run(
            wf.priv("systemctl enable --now bluetooth") + " || true")
        return StepResult("configure_bluetooth", True,
                          "Bluetooth on, as chosen at birth: rfkill "
                          "unblocked and the service enabled.")
    gaps = []
    conf = "/boot/firmware/config.txt"
    code, body, _ = wf.connection.run(f"cat {conf}")
    if code != 0:                     # pre-bookworm images keep it in /boot
        conf = "/boot/config.txt"
        code, body, _ = wf.connection.run(f"cat {conf}")
    if code != 0:
        gaps.append("could not read the boot config, so Bluetooth is only "
                    "blocked, not unpowered")
    elif "dtoverlay=disable-bt" not in (body or ""):
        new_body = (body or "").rstrip("\n") + (
            "\n\n# Node Medic: Bluetooth off, chosen at birth (power).\n"
            "dtoverlay=disable-bt\n")
        wf.connection.run(_write_remote_file(wf, conf, new_body))
        check = wf.connection.run(f"cat {conf}")[1] or ""
        if "dtoverlay=disable-bt" not in check:
            gaps.append("the boot-config write did not read back")
    wf.connection.run(wf.priv("rfkill block bluetooth") + " || true")
    wf.connection.run(
        wf.priv("systemctl disable --now bluetooth hciuart") + " || true")
    if gaps:
        return StepResult("configure_bluetooth", True,
                          "Bluetooth off was chosen, but: " + "; ".join(gaps)
                          + ". It is rfkill-blocked for now.")
    return StepResult("configure_bluetooth", True,
                      "Bluetooth off, as chosen at birth: blocked now, and "
                      "never powered from the next boot (dtoverlay=disable-bt, "
                      "read back).")


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


_TZ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")


@build_step
def set_node_timezone(wf: "BuildWorkflow") -> StepResult:
    """Give the node the medic's own timezone, and read it back.

    A stock Pi OS image is Europe/London; NTP then sets the CLOCK right and
    every timestamp the node prints is still nine hours off for an
    Australian operator (ELSEWHERE and SKYFINGER, 2026-09-23). The card's
    custom.toml carries the zone for first boot; this step covers a card
    imaged elsewhere and proves the result. The zone is the medic's own,
    read at birth — when the medic could not read its own, the node keeps
    the image default and this step says so rather than guessing one.
    """
    tz = (wf.profile.timezone or "").strip()
    if not tz:
        return StepResult("set_node_timezone", True,
                          "Node Medic could not read its own timezone, so the "
                          "node keeps the image default (Europe/London on a "
                          "stock Pi OS image).", skipped=True)
    if not _TZ_RE.match(tz):
        return StepResult("set_node_timezone", False,
                          f"{tz!r} is not a timezone name; nothing was changed.")
    code, out, err = wf.connection.run(wf.priv(f"timedatectl set-timezone {tz}"))
    if code != 0:
        return StepResult("set_node_timezone", False,
                          f"Could not set the timezone: {err or out}")
    got = (wf.connection.run("timedatectl show -p Timezone --value")[1] or "").strip()
    if got != tz:
        return StepResult("set_node_timezone", False,
                          f"Set the timezone to {tz} but the node reads back "
                          f"{got or 'nothing'}.")
    return StepResult("set_node_timezone", True,
                      f"Timezone {tz} — the medic's own — set and read back.")


@build_step
def final_verification(wf: "BuildWorkflow") -> StepResult:
    """The check-over that must not be passable by a mute node.

    Until 2026-08-12 this asked two questions — "is rnsd active?" and "does the
    config exist?" — and rnsd stays active holding a dead interface
    (panic_on_interface_error = No), which is exactly how two nodes shipped
    mute on 10 August. Now every service this build installed must report
    active, the config must read back as ours, and the radio is checked when
    one is attached — and when one is NOT attached (every Pi birth: the radio
    is fitted after the build), that is said in so many words. "Could not
    check" is its own answer; it is never promoted to "working".
    """
    problems, verified, unchecked = [], [], []

    if wf.connection.run("systemctl is-active rnsd")[0] != 0:
        problems.append("rnsd not active")
    else:
        verified.append("rnsd running")

    if wf.connection.run("test -f ~/.reticulum/config")[0] != 0:
        problems.append("config missing")
    else:
        body = wf.connection.run("cat ~/.reticulum/config")[1] or ""
        if "[reticulum]" in body:
            verified.append("config in place")
        else:
            problems.append("config file present but does not read back as a "
                            "Reticulum config")

    # Every unit this build writes must be RUNNING, not merely written. Asking
    # the node which exist (rather than trusting our own memory of the run)
    # keeps a resumed build honest.
    for svc in ("lxmd", "rnm-health", "rnm-status"):
        if wf.connection.run(f"test -f /etc/systemd/system/{svc}.service")[0] != 0:
            continue
        state = wf.connection.run(f"systemctl is-active {svc}")[1].strip()
        if state == "active":
            verified.append(f"{svc} running")
        else:
            problems.append(f"{svc} installed but {state or 'not running'}")

    # THE RADIO. Only a radio that is here can be checked; on the Pi paths it
    # is fitted after the build, and claiming anything about it here would be
    # the exact lie this tool exists not to tell.
    if wf.profile.rnode_present:
        raw = wf.connection.run("rnstatus --json")[1] or ""
        try:
            import json as _json
            ifaces = _json.loads(raw).get("interfaces", [])
            rnodes = [i for i in ifaces if i.get("type") == "RNodeInterface"]
            if rnodes and all(i.get("status") is True for i in rnodes):
                verified.append("radio interface up")
            elif rnodes:
                problems.append("the RNode interface is DOWN — rnsd is up but "
                                "holding a dead radio (the mute-node failure)")
            else:
                unchecked.append("no RNode interface in rnstatus yet")
        except (ValueError, AttributeError):
            unchecked.append("radio attached but rnstatus gave no readable "
                             "answer")
    else:
        unchecked.append("the radio was NOT checked — it is not attached "
                         "during a Pi build; its link is proven when the "
                         "assembled node first boots")

    # THE TIME TRUST (2026-09-23): install_time_trust never fails the build,
    # so THIS is where a Pi node's missing trust file / helper / sudoers
    # line becomes a stated problem rather than a green row. Content and
    # owner:mode, read from the node; the medic's anchor when it can be
    # loaded here, else the file is checked for being an anchor at all.
    if wf.profile.role == NodeRole.PROPAGATION:
        try:
            anchor = medic_time_anchor()
        except Exception:                                          # noqa: BLE001
            anchor = None
        user = wf.run_user()
        home = (wf.connection.run("echo $HOME")[1].strip()
                or ("/root" if user == "root" else f"/home/{user}"))
        t_ok, t_bad, t_unchecked = time_trust_readback(
            wf.connection, wf.priv, user, home, anchor)
        verified.extend(t_ok)
        unchecked.extend(t_unchecked)
        if t_bad:
            problems.append("node will not take the time from Node Medic ("
                            + "; ".join(t_bad) + ")")

    tail = ("  Not checked: " + "; ".join(unchecked) + "." if unchecked else "")
    if problems:
        return StepResult("final_verification", False,
                          "Verification failed: " + "; ".join(problems) + "."
                          + tail)
    return StepResult("final_verification", True,
                      "Verified: " + "; ".join(verified) + "." + tail)


def node_addresses(wf: "BuildWorkflow") -> List[str]:
    """Addresses the MEDIC could try to reach this node on, best first.

    Ordered by how long they will still be true. A LAN address survives the
    operator unplugging the cable and walking away, which is when the dashboard
    starts to matter; the USB-gadget address (10.55.0.x) stops existing minutes
    from now, and is reused by the NEXT node built after this one. Both are worth
    trying and it is worth knowing which one answered — reaching a node down the
    build cable proves the cable, which we already knew.
    """
    lan: List[str] = []
    cable: List[str] = []
    for addr in wf.cmd_output("hostname -I").split():
        if ":" in addr or addr.startswith("127."):
            continue
        (cable if addr.startswith("10.55.") else lan).append(addr)
    return lan + cable


@build_step
def prove_the_node_reports(wf: "BuildWorkflow") -> StepResult:
    """Ask the node to report on itself, and record what actually came back.

    THE CERTIFICATE MUST NOT SAY "WORKING NODE" WHEN THE MEDIC HAS HEARD
    NOTHING. Birth proves the node is CONFIGURED, and #77 added a proof that it
    is AUDIBLE. Neither proves it will TELL the medic anything, and that is what
    VITALS is made of. SkyFinger passed every step and then sat as a grey
    LoRa-only row for days with the operator quite reasonably assuming it was
    broken; it was in perfect health and nobody had ever asked it a question.

    Reports the two channels APART — see :mod:`workflows.report_proof` — and is
    never fatal. "Answered on the network, not yet tested on the radio" is the
    normal and correct result for a Pi 3 A+, whose radio is attached after the
    build because the medic's cable is in its only USB socket.
    """
    if wf.profile.role != NodeRole.PROPAGATION:
        return StepResult("prove_the_node_reports", True,
                          "Not a propagation node — nothing here reports health.",
                          skipped=True)

    from workflows.report_proof import live_beacon_probe, prove_reporting

    hear = None
    reason = ""
    dst = wf.profile.health_dst_hash or ""
    if not dst:
        reason = ("Node Medic never captured this node's health address, so "
                  "there is nothing to call.")
    elif not wf.profile.has_rnode:
        # THE PI 3 A+ CASE, AND IT IS THE NORMAL ONE. The radio goes on after
        # the build, so at this moment there is provably nothing on the air to
        # hear. Calling that a failed test would print a false negative on a
        # birth certificate, which reads worse than a false positive: the
        # operator is told their good node is deaf (Y2K8 and SolarLove,
        # 2026-08-10, and the same lesson as radio_proof).
        reason = ("the radio goes onto this node after the build, so there was "
                  "nothing on the air yet. Once it is attached and powered, "
                  "tap the node in VITALS and choose Ping node now — that "
                  "commands a beacon and the readings arrive in seconds.")
    elif wf.beacon_probe is not None:
        hear = wf.beacon_probe
    else:
        try:
            from ui.app import _local_run    # LOGIN shell: rnpath is in ~/.local/bin
            hear = live_beacon_probe(dst, _local_run)
        except Exception:
            reason = ("Node Medic could not reach its own mesh tools to run the "
                      "radio check.")

    proof = prove_reporting(
        node_addresses(wf), wf.http_poll, hear_beacon=hear,
        beacon_reason=reason, node_name=wf.profile.hostname or "")
    wf.report_proof = proof
    return StepResult("prove_the_node_reports", True, proof.summary)


#: What the card bakes so Node Medic can reach the Pi over the cable, and what
#: has to replace it once that is no longer needed.
_GADGET_OVERLAY = "dtoverlay=dwc2,dr_mode=peripheral"
_HOST_OVERLAY = "dtoverlay=dwc2,dr_mode=host"


def _write_remote_file(wf: "BuildWorkflow", path: str, content: str) -> str:
    """Command that puts *content* at *path* on the node, as root.

    BASE64 THROUGH A PIPE, NEVER A HEREDOC. The heredoc form this replaced took
    content READ BACK FROM THE NODE and re-sent it between <<'RTTEOF' … RTTEOF —
    so a line of the node's own /boot/firmware/config.txt equal to the
    terminator would end the document early and hand the rest to the node's
    shell, which the card gave passwordless sudo. Reaching it needs the card or
    root already, so it was never the shortest road in; it was simply the one
    heredoc in this codebase whose payload came from the far end.

    The project already had the right idiom and the scar to go with it — see
    provisioning/rootfs_wifi._write, written after the card-prepare injection
    (task #50). This is that idiom, applied where it was missed.
    """
    import base64
    import shlex
    b64 = base64.b64encode(content.encode()).decode()
    return (f"echo {shlex.quote(b64)} | base64 -d | "
            f"{wf.priv(f'tee {shlex.quote(path)}')} >/dev/null")


def _clean_gadget_cmdline(wf: "BuildWorkflow") -> str:
    """Drop the g_ether module load from the node's cmdline.txt; read back.
    Returns "" when clean (or nothing to do), else the failure sentence.
    Shared by the hand-back's main path AND its already-host skip path, so a
    retry after a partial first run still finishes the dirty half."""
    for cmd in ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
        if wf.connection.run(f"test -f {cmd}")[0] != 0:
            continue
        cl = wf.connection.run(f"cat {cmd}")[1]
        if "modules-load=dwc2,g_ether" in cl:
            cl2 = cl.replace("modules-load=dwc2,g_ether", "modules-load=dwc2")
            rc = wf.connection.run(_write_remote_file(wf, cmd, cl2))[0]
            if rc != 0 or "g_ether" in wf.connection.run(f"cat {cmd}")[1]:
                return ("Handed the USB port back but could not drop the "
                        "gadget module from cmdline.txt.")
        return ""
    return ""


@build_step
def hand_the_usb_port_back(wf: "BuildWorkflow") -> StepResult:
    """Stop being a gadget; start being able to host a radio.

    THE LAST BUG IN THE PI BIRTH, and the one that made every earlier fix
    unreachable. The card bakes ``dr_mode=peripheral`` so the medic can talk to
    the Pi over USB during the build. A Pi 3 A+ has ONE dwc2 controller driving
    its ONE USB-A socket — so in peripheral mode that port can only ever BE a
    device. ``lsusb`` on the finished node returned nothing at all. It could not
    see the radio plugged into it: no ttyACM0, no /dev/rnode, nothing for rnsd
    to open, and an RNode sitting on its version screen forever.

    The setting that makes the birth POSSIBLE is the setting that makes the
    finished node USELESS, and nothing ever switched it back. Two nodes went out
    that way on 2026-08-10 before it was found by asking the node itself what
    USB devices it could see, and being told: none.

    RUNS LAST, DELIBERATELY. Everything before it talks over that cable. After
    this the cable link is gone — which is correct, because the node is finished
    and reachable over Wi-Fi from here on, and because the socket is now needed
    for the radio it was built to carry.

    Takes effect on the node's next boot, which is the reboot it gets when the
    operator unplugs it and gives it its own power.
    """
    boot = "/boot/firmware/config.txt"
    if wf.connection.run(f"test -f {boot}")[0] != 0:
        boot = "/boot/config.txt"
    code, before, _ = wf.connection.run(f"cat {boot}")
    if code != 0 or not before:
        return StepResult("hand_the_usb_port_back", True,
                          "No Pi boot config here — nothing to hand back.",
                          skipped=True)
    from provisioning.gadget import config_txt_applies_to_all as _in_force

    def _rogue_dwc2(text):
        """Every in-force dwc2 line that is not exactly our host overlay: the
        peripheral one, and a BARE "dtoverlay=dwc2" — bare means dr_mode=otg,
        the USB-A socket has no ID pin, and config.txt is last-entry-wins
        (break-lens, 2026-08-13)."""
        return [l for l in _in_force(text)
                if l.strip().startswith("dtoverlay=dwc2")
                and l.strip() != _HOST_OVERLAY]

    if not _rogue_dwc2(before):
        # Config already clean — but NOT a pure skip: a mid-step death on a
        # previous run can leave config.txt at host with cmdline.txt still
        # loading g_ether, and this branch used to declare victory over the
        # clean half while the dirty half survived (break-lens, 2026-08-13).
        leftover = _clean_gadget_cmdline(wf)
        if leftover:
            return StepResult("hand_the_usb_port_back", False, leftover)
        return StepResult("hand_the_usb_port_back", True,
                          "This node was never put in gadget mode.", skipped=True)

    # TRANSFORM IN PYTHON, WRITE WITH tee. The first version ran a sed
    # expression through ssh and it silently did nothing: rc=0, no output, file
    # unchanged, and the step reported success (SkyFinger, 2026-08-11 — the
    # node came up still a gadget, still unable to see its own radio, and the
    # operator had to power-cycle it a second time). A regex full of | and ^
    # crossing shlex.quote, bash -c and the remote shell has three chances to
    # arrive as something else; a whole file sent as base64 through a pipe
    # (_write_remote_file) has none.
    # Rewrite EVERY in-force rogue line, section-aware — a plain replace of
    # the peripheral literal missed bare lines and anything a section hid.
    section, out_lines = "all", []
    for line in before.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
        elif (section == "all" and s.startswith("dtoverlay=dwc2")
                and s != _HOST_OVERLAY):
            pad = line[:len(line) - len(line.lstrip())]
            nl = "\n" if line.endswith("\n") else ""
            line = f"{pad}{_HOST_OVERLAY}{nl}"
        out_lines.append(line)
    after = "".join(out_lines)
    code, out, err = wf.connection.run(_write_remote_file(wf, boot, after))
    if code != 0:
        return StepResult("hand_the_usb_port_back", False,
                          f"Could not hand the USB port back: {err or out}")

    # AND READ IT BACK. A write that is not read back is a claim, not a fact —
    # this project has now been bitten by that four times in two days (the Wi-Fi
    # country file, a `test -s` on a directory, a `test -f` through safe_shell,
    # and this). The whole point of the step is that the operator will not find
    # out until the node is assembled and mute.
    check = wf.connection.run(f"cat {boot}")[1]
    # VERIFY WHAT IS IN FORCE, not a substring. config.txt is sectioned and
    # last-entry-wins: node ttt's card carried dr_mode=host mid-file and a
    # peripheral block appended below it, so "host present, peripheral
    # absent"-style checks can pass on a card that still boots as a gadget.
    from provisioning.gadget import config_txt_applies_to_all
    host_in_force = _HOST_OVERLAY in _in_force(check)
    if _rogue_dwc2(check) or not host_in_force:
        return StepResult(
            "hand_the_usb_port_back", False,
            "Wrote the USB mode back but the card still says gadget — the node "
            "would come up unable to see its own radio. Nothing else is wrong "
            "with it; this is the one edit that did not take.")

    leftover = _clean_gadget_cmdline(wf)
    if leftover:
        return StepResult("hand_the_usb_port_back", False, leftover)
    return StepResult("hand_the_usb_port_back", True,
                      "USB port handed back and checked on the card — it hosts "
                      "its radio from its next boot, which is the one it gets "
                      "when you move the radio onto it and power it up.")


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

#: THE THIRD IDENTITY A PI RELAY WEARS (2026-08-22). A propagation node's lxmd
#: announces its ``lxmf.propagation`` aspect from an identity kept in its OWN
#: file (~/.lxmd/identity) — unrelated to rnsd's transport identity and to the
#: health reporter's. Nothing on the mesh links the three, so lxmd's destination
#: orphaned in VITALS as an anonymous "Propagation relay" beside the same
#: machine's named rows (the double-SkyFinger root cause). Read the destination
#: hash off the node at birth — the ONE moment the medic holds all three at once
#: — so it can be rostered under the node's name. Mirrors _HEALTH_DST_CMD: build
#: the SAME destination lxmd announces (RNS.Destination(id, IN, SINGLE, 'lxmf',
#: 'propagation')). Prints nothing when lxmd is not installed (no identity file),
#: so a non-propagation node contributes no hash — never a fabricated one.
_LXMD_DST_CMD = (
    "python3 -c \"import RNS, os; "
    "p=os.path.expanduser('~/.lxmd/identity'); "
    "i=RNS.Identity.from_file(p) if os.path.exists(p) else None; "
    "RNS.Reticulum() if i else None; "
    "d=RNS.Destination(i, RNS.Destination.IN, RNS.Destination.SINGLE, "
    "'lxmf', 'propagation') if i else None; "
    "print(RNS.hexrep(d.hash, delimit=False) if d else '')\" 2>/dev/null")


def _pin_this_node(wf: "BuildWorkflow") -> str:
    """Pin the built node's SSH host key; return the fingerprint or "".

    Only for a real SSH connection to a real host — a serial or local build has
    no host key, and an emulated one has no node.
    """
    host = getattr(wf.connection, "host", "") or ""
    if not host or type(wf.connection).__name__ != "SSHConnection":
        return ""
    try:
        from provisioning import host_keys
        return host_keys.pin_host(host) or ""
    except Exception:
        return ""


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
        # THE BOARD THE MEDIC WAS TOLD, OR "UNKNOWN". This line used to fall
        # back to wf.profile.hardware.value — and get_board() of the profile
        # DEFAULT key is a real board, so skyfinger's certificate named a
        # Heltec V4 the medic had never seen (2026-09-22). board_text prints
        # the catalogue name for a key the profile actually carries and an
        # honest unknown otherwise; board_key travels raw so a repair or
        # rebuild can act on it without parsing the sentence.
        "board": board_text(wf.profile.rnode_board_key),
        "board_key": board.key if board else None,
        # HOW the board was known — "usb" (read on the medic, confirmed) or
        # "operator" (named from the catalogue, never on the medic). A code
        # (BOARD_SOURCES has the words): VITALS translates it, and a reader
        # months on can tell a reading from a naming (2026-09-22).
        "board_source": ((wf.profile.rnode_board_source or None)
                         if board else None),
        "rnode_firmware": FIRMWARE_VERSION if wf.profile.has_rnode else None,
        "rgb_led_pin": wf.profile.rnode_rgb_pin,
        "frequency_mhz": r.frequency_mhz,
        "bandwidth_khz": r.bandwidth_khz,
        "spreading_factor": r.spreading_factor,
        "coding_rate": r.coding_rate,
        "tx_power_dbm": r.tx_power_dbm,
        # THE PORT THE NODE'S CONFIG NAMES, and how udev fills it — not
        # r.serial_port, which on every Pi birth is the NodeProfile default
        # (the radio is never attached during the build; see RNODE_SYMLINK).
        # radio_rule is install_radio_rule's own record of the file it
        # wrote and read back; the serial is kept OUT of "usb_serial", the
        # key save_cert retires other certificates by — the radio's own
        # flash certificate would be deleted the moment this one was saved.
        "serial_port": radio_port_text(getattr(wf, "radio_rule", None)),
        "radio_rule": dict(wf.radio_rule) if getattr(wf, "radio_rule", None)
                      else None,
        "session_id": wf.profile.session_id,
        # The zone the node prints its own timestamps in (set_node_timezone).
        "timezone": wf.profile.timezone or None,
        # THE ANSWER TRAVELS WITH THE NODE. Whoever inherits this node — the
        # whole point of a birth certificate — can see whether it tells the
        # world roughly where it is, without having to read its config or guess
        # from a map. And a rebuild from the certificate restores the decision
        # its operator made rather than the tool's default.
        "location_sharing": wf.profile.share_location,
    }
    if wf.location_sharing_notes:
        wf.birth_certificate["location_sharing_notes"] = list(
            wf.location_sharing_notes)
    # A propagation node beacons from its own rtnode.health destination; carry it
    # so the node is rostered under the hash its health beacon actually announces
    # (the registry key), not just its main rnsd identity.
    if wf.profile.health_dst_hash:
        wf.birth_certificate["health_dst"] = wf.profile.health_dst_hash
    # AND ITS THIRD IDENTITY: lxmd's lxmf.propagation destination, read the same
    # way — live off the node, after the services are up. Only for a machine that
    # actually runs lxmd (the command prints nothing without an ~/.lxmd/identity),
    # and only a well-formed 32-hex hash is trusted — a blank or garbled read is
    # dropped, never rostered as a fabricated identity. Without this the relay's
    # announce has nothing in the roster to fold it under, and it sat in VITALS as
    # an orphaned "Propagation relay" beside the same Pi's named rows (2026-08-22).
    lxmd_lines = out(_LXMD_DST_CMD).splitlines()
    lxmd_dst = lxmd_lines[-1].strip().lower() if lxmd_lines else ""
    if len(lxmd_dst) == 32 and all(c in "0123456789abcdef" for c in lxmd_dst):
        wf.birth_certificate["lxmd_dst"] = lxmd_dst
    # WHAT THE MEDIC ACTUALLY HEARD BACK, per channel, in the node's own record.
    # A certificate that states a working node the medic has never heard from is
    # the failure this project keeps meeting; these fields are the answer to
    # "and did you check?".
    if wf.report_proof is not None:
        wf.birth_certificate.update(wf.report_proof.cert_fields())

    # PIN THE NODE'S SSH HOST KEY (audit C1). This is the one moment the medic
    # can be sure which machine it is talking to: it just built this one. From
    # here on a connection to this address VERIFIES the key instead of accepting
    # whatever answers — and a rebirth deliberately clears the pin, so the
    # replacement gets a fresh one rather than a refusal.
    #
    # Best-effort and never fatal: a node that is built and working must not be
    # failed over a hardening step, and a node that could not be pinned is
    # exactly where it already was.
    pinned = _pin_this_node(wf)
    if pinned:
        wf.birth_certificate["host_key_fingerprint"] = pinned
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
        #: How to invoke pip on THIS node — resolved by _ensure_pip,
        #: which may find no pip3 at all and fall back to running it
        #: out of the image's own wheel.
        self.pip_cmd = "pip3"
        self.rendered_config = ""
        #: Plain-language findings from writing (or refusing to write) the map
        #: sharing block — surfaced rather than swallowed, so "sharing was
        #: chosen but this node has nothing to announce on" is visible.
        self.location_sharing_notes: List[str] = []
        self.birth_certificate: Optional[dict] = None
        #: How the medic reads a node's ``/status``. An attribute rather than a
        #: direct call so ``prove_the_node_reports`` can be driven in tests
        #: without a LAN — and so a caller that already has a poller (the
        #: monitor service does) can hand its own in.
        from monitor.http_status import poll_status
        self.http_poll = poll_status
        #: How the medic commands and hears a health beacon. ``None`` means "work
        #: it out from the live mesh"; tests inject.
        self.beacon_probe = None
        #: Filled by ``prove_the_node_reports``: what the node was actually heard
        #: to say, per channel. Read onto the birth certificate.
        self.report_proof = None
        #: install_radio_rule's record of the udev rule it wrote AND read
        #: back: {"by": "serial"|"vendor", "serial", "source"}. None until
        #: that step succeeds; the certificate prints it (2026-09-22).
        self.radio_rule: Optional[dict] = None
        self._root: Optional[bool] = None
        self._user: Optional[str] = None

    #: Steps that only act on a Pi carrying its radio DURING the build. On
    #: the built path the radio is flashed on the medic and fitted last, so
    #: these always reported "(skipped)" — and grey rows read as something
    #: wrong (operator, 2026-08-12). They stay in ``steps`` — a self-built Pi
    #: with its radio attached still gets them, surfacing as unplanned rows
    #: the moment they do real work — but the promised checklist omits them.
    UNPLANNED_ON_BUILT_PATH = ("flash_rnode_firmware",
                               "set_firmware_radio_parameters")

    def planned_step_names(self):
        """What the progress checklist should PROMISE for this run."""
        return [n for n, _f in self.steps
                if n not in self.UNPLANNED_ON_BUILT_PATH]

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
            # THE SYMLINK, NOT r.serial_port. See RNODE_SYMLINK: on the board
            # this tool is built around, the radio cannot be attached while the
            # build runs, so r.serial_port is whatever the default happened to
            # be — and it was wrong for every Pi ever birthed.
            "{{SERIAL_PORT}}": RNODE_SYMLINK,
            "{{FREQUENCY}}": str(int(r.frequency_mhz * 1_000_000)),
            "{{BANDWIDTH}}": str(int(r.bandwidth_khz * 1000)),
            "{{SF}}": str(r.spreading_factor),
            "{{CR}}": str(r.coding_rate),
            "{{TXPOWER}}": str(r.tx_power_dbm),
        }
        for k, v in subs.items():
            template = template.replace(k, v)
        return self._apply_location_sharing(template)

    def _apply_location_sharing(self, template: str) -> str:
        """Fold the operator's birth-time map decision into the node's config.

        HIDDEN IS NOT AN ABSENCE OF CODE HERE, IT IS A RESULT. The template
        carries no discovery keys, so a hidden node is already silent — but this
        still runs, so that a rebuild of a node whose config once shared strips
        the block instead of leaving it. "Off" has to be something the tool
        actively produces, or turning it off only works on paper.

        The coordinates written are FUZZED (monitor.location_share ->
        monitor.geo.fuzz_location). The exact ones never leave the medic.
        """
        from monitor import location_share
        loc = self.profile.location or (None, None)
        text, notes = location_share.apply_to_reticulum_config(
            template,
            policy=self.profile.share_location,
            name=self.profile.hostname or "",
            lat=loc[0], lon=loc[1],
            node_key=(self.profile.reticulum_identity_hash
                      or self.profile.hostname or ""))
        self.location_sharing_notes = list(notes)
        return text

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
            from workflows.step_log import log_step
            log_step(result)
            emit(result)
            if not result.success and not result.skipped:
                break  # stop; do NOT advance current_index
            self.current_index += 1
        return self.results
