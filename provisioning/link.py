"""Find a plugged-in node and bootstrap key-based access to it.

The on-site flow: plug a Node-Medic-imaged Pi into Node Medic over USB (no WiFi).
Gadget ethernet (provisioning.gadget) brings it up at GADGET_USB_IP. Node Medic:

  1. discover_peer()      — watch for the usb interface, claim HOST_USB_IP, probe
  2. (UI asks for the node's password — typed once on the touchscreen)
  3. bootstrap_access()   — install our SSH key + passwordless sudo using that
                            password ONCE; from then on it's key auth, no prompts
  4. hand the returned SSHConnection to BuildWorkflow — provision as usual

Discovery / networking touch real hardware; the parsing and the bootstrap command
sequence are pure and unit-tested via an injected runner.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from provisioning.gadget import GADGET_USB_IP, HOST_USB_IP, USB_PREFIX
from transport.connection import SSHConnection

#: runner(argv, input=None, env=None, timeout=int) -> (rc, stdout, stderr)
Runner = Callable[..., tuple]


def _default_runner(argv: List[str], input: Optional[str] = None,
                    env: Optional[dict] = None, timeout: int = 30) -> tuple:
    import os
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        p = subprocess.run(argv, input=input, env=full_env, timeout=timeout,
                           capture_output=True, text=True)
        return (p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return (255, "", "timed out")
    except FileNotFoundError:
        return (255, "", f"{argv[0]}: not found")


def parse_usb_interfaces(ip_link_output: str) -> List[str]:
    """Interface names that look like a USB-gadget peer, from ``ip -o link``.
    A Pi gadget shows up as ``usb0`` or an ``enx<mac>`` CDC device on the host."""
    names: List[str] = []
    for line in ip_link_output.splitlines():
        # "3: usb0: <BROADCAST,...> mtu 1500 ..."  -> field 1 (0-indexed) is "usb0:"
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[1].rstrip(":").split("@")[0]
        if name.startswith("usb") or name.startswith("enx"):
            names.append(name)
    return names


def _port_open(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover_peer(runner: Optional[Runner] = None, timeout: float = 90.0,
                  poll: float = 2.0, sleep=time.sleep,
                  now=time.monotonic, probe=_port_open) -> Optional[str]:
    """Wait (up to *timeout* s) for a gadget node to appear on USB and return its
    address (GADGET_USB_IP), or None. Claims HOST_USB_IP on the usb interface as
    it appears, then probes the gadget's SSH port. Idempotent to re-run."""
    runner = runner or _default_runner
    deadline = now() + timeout
    while now() < deadline:
        rc, out, _ = runner(["ip", "-o", "link"], timeout=5)
        for ifc in parse_usb_interfaces(out):
            # Claim our end of the /29 (harmless if already assigned) + bring up.
            runner(["sudo", "-n", "ip", "addr", "add",
                    f"{HOST_USB_IP}/{USB_PREFIX}", "dev", ifc], timeout=5)
            runner(["sudo", "-n", "ip", "link", "set", ifc, "up"], timeout=5)
            if probe(GADGET_USB_IP, 22):
                return GADGET_USB_IP
        sleep(poll)
    return None


@dataclass
class BootstrapResult:
    key_installed: bool
    sudo_ok: bool
    message: str

    @property
    def ok(self) -> bool:
        return self.key_installed and self.sudo_ok


def bootstrap_access(host: str, user: str, password: str,
                     runner: Optional[Runner] = None, timeout: int = 30
                     ) -> BootstrapResult:
    """Turn a password-only node into a key + passwordless-sudo node, using the
    password EXACTLY ONCE. After this, SSHConnection(host, user) works with no
    prompts. The password is passed via the SSHPASS env (never argv) for the key
    copy, and via stdin to ``sudo -S`` (never echoed) for the sudoers write."""
    runner = runner or _default_runner
    env = {"SSHPASS": password}
    base = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8"]

    # 1) install Node Medic's public key into the node's authorized_keys
    runner(["sshpass", "-e", "ssh-copy-id", *base, f"{user}@{host}"],
           env=env, timeout=timeout)
    # 2) confirm key auth now works (no password)
    rc_key, _, _ = runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                           f"{user}@{host}", "true"], timeout=timeout)
    key_ok = rc_key == 0

    # 3) grant passwordless sudo. Over key auth now; the password goes to sudo -S
    #    on stdin (not echoed, not in argv). One sudo call writes + locks the file.
    sudoers = f"{user} ALL=(ALL) NOPASSWD:ALL"
    dst = "/etc/sudoers.d/010-nodemedic-nopasswd"
    remote = ("sudo -S -p '' bash -c " + _shq(
        f"echo {_shq(sudoers)} > {dst} && chmod 440 {dst} && visudo -cf {dst}"))
    runner(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            f"{user}@{host}", remote], input=password + "\n", timeout=timeout)
    # 4) verify passwordless sudo took
    rc_sudo, _, _ = runner(["ssh", "-o", "BatchMode=yes", f"{user}@{host}",
                            "sudo -n true"], timeout=timeout)
    sudo_ok = rc_sudo == 0

    if key_ok and sudo_ok:
        msg = f"Access bootstrapped — {user}@{host} now uses key auth + passwordless sudo."
    elif not key_ok:
        msg = "Could not install the SSH key (wrong password, or SSH refused)."
    else:
        msg = "Key installed, but passwordless sudo did not take (check the password / sudoers)."
    return BootstrapResult(key_ok, sudo_ok, msg)


def connect(host: str, user: str) -> SSHConnection:
    """A key-auth SSHConnection to a bootstrapped node, ready for BuildWorkflow."""
    return SSHConnection(host=host, user=user)


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
