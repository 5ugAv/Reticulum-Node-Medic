#!/usr/bin/python3 -I
"""Write the medic's OWN radio services. RUNS AS ROOT.

Installed to /usr/local/lib/nodemedic/radio-units, owned by root, by the
parent's clone flow (workflows/clone.py, install_radio_helper), and granted ONE
narrow NOPASSWD entry in provisioning/sudoers.d/nodemedic (NM_RADIO_SETUP):

    sudo -n /usr/local/lib/nodemedic/radio-units --port /dev/serial/by-id/<board>

WHY THIS EXISTS
---------------
A new medic sets up its own radio (workflows/medic_radio.py, the first-run
Tracker set-up) AFTER its clone flow has scoped its sudo. That set-up writes
three systemd units — the serial splitter, rnsd, lxmd — and the old way was to
`install` a temp file the app had written into /etc/systemd/system. Allowing
that without a password would let whoever runs the app write ANY unit, with any
User= and ExecStart= — root by another name. So the unit text lives HERE, fixed,
and the caller supplies only which board: a /dev/serial/by-id link.

WHY IT DUPLICATES TEXT FROM THE REPO
------------------------------------
It imports NOTHING from ~/reticulum-tool: that tree is writable by the app user,
and a root program importing user-writable code is an escalation with extra
steps (the same boundary as prepare_card.py). The unit text is therefore a copy
of workflows/medic_radio.py's; tests/test_privileged_commands.py holds the two
equal, so they cannot drift.

THE TRUST BOUNDARY IS HERE
--------------------------
sudo matches its arguments as one string and the rule ends in `*`, so this
program trusts nothing it is given: exactly `--port <path>`, the path a
/dev/serial/by-id link of plain characters that resolves to a USB serial tty,
and the user is the one sudo names (SUDO_USER, which the caller cannot set), a
normal account with a home. Anything else is refused before a byte is written.
"""

from __future__ import annotations

import os
import pwd
import re
import subprocess
import sys

UNIT_DIR = "/etc/systemd/system"
SYSTEMCTL = "/usr/bin/systemctl"
#: Kept in step with workflows/medic_radio.py (a test pins them together).
SPLIT_PORT = "/tmp/rnode-jonesey"
GPS_STATE = "/dev/shm/nodemedic-gps.json"
KIOSK_UNIT = "reticulum-node-medic.service"
HANDOVER_UNIT = "nm-radio-handover.service"

#: A by-id link name: no '/', no quote, no space, no newline — nothing that could
#: end the Python string or the unit line it is written into.
BY_ID = re.compile(r"^/dev/serial/by-id/[A-Za-z0-9][A-Za-z0-9_.:+-]{0,200}$")
#: ...and what it must point at: the USB serial tty of a board.
USB_TTY = re.compile(r"^/dev/tty(ACM|USB)[0-9]{1,3}$")
NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def fail(msg: str, code: int = 2) -> None:
    print(f"RADIO_FAIL: {msg}")
    sys.exit(code)


def say(msg: str) -> None:
    print(f"RADIO: {msg}", flush=True)


# --------------------------------------------------------------------------- #
# The unit text — a copy of workflows/medic_radio.py (pinned by a test)
# --------------------------------------------------------------------------- #

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


def handover_unit() -> str:
    """Stop the app, restart the mesh with the radio, start the app again — as a
    unit of its own, so it outlives the app it stops. Started with
    `systemctl start --no-block`; never enabled. Names no user and takes no
    input, so it is the same on every medic."""
    return (
        "[Unit]\n"
        "Description=Node Medic: hand the radio over to rnsd (restarts the app)\n\n"
        "[Service]\nType=oneshot\n"
        f"ExecStart=/bin/sh -c 'sleep 3; systemctl stop {KIOSK_UNIT}; "
        "systemctl restart rnsd.service lxmd.service; sleep 5; "
        f"systemctl start {KIOSK_UNIT}'\n")


# --------------------------------------------------------------------------- #
# Checking the caller — the whole reason this runs as root and not the app
# --------------------------------------------------------------------------- #

def checked_port(argv) -> str:
    if len(argv) != 2 or argv[0] != "--port":
        fail("usage: radio-units --port /dev/serial/by-id/<board> (nothing else)")
    port = argv[1]
    if not BY_ID.match(port):
        fail(f"not a serial by-id link: {port!r}")
    if not os.path.islink(port):
        fail(f"{port} is not plugged in (no such link)")
    real = os.path.realpath(port)
    if not USB_TTY.match(real):
        fail(f"{port} points at {real}, not a USB serial port")
    return port


def calling_user():
    name = os.environ.get("SUDO_USER", "")
    if not NAME.match(name) or name == "root":
        fail("run this through sudo, as the medic's app user")
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        fail(f"no account called {name}")
    if pw.pw_uid < 1000 or pw.pw_uid == 65534:
        fail(f"{name} is not a normal account")
    home = pw.pw_dir
    if not home.startswith("/home/") or not os.path.isdir(home) or "\n" in home:
        fail(f"{name} has no usable home directory")
    return name, home


def write_unit(name: str, text: str) -> None:
    path = os.path.join(UNIT_DIR, name)
    tmp = os.path.join(UNIT_DIR, f".{name}.nm-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.chown(tmp, 0, 0)
    os.replace(tmp, path)
    say(f"wrote {path}")


def main(argv) -> int:
    if os.geteuid() != 0:
        fail("must run as root (through sudo)")
    port = checked_port(argv)
    user, home = calling_user()
    write_unit("rnode-splitter.service", splitter_unit(port, user, home))
    write_unit("rnsd.service", rnsd_unit(user, home))
    write_unit("lxmd.service", lxmd_unit(user, home))
    write_unit(HANDOVER_UNIT, handover_unit())
    p = subprocess.run([SYSTEMCTL, "daemon-reload"], capture_output=True, text=True)
    if p.returncode != 0:
        fail("systemctl daemon-reload failed: "
             + (p.stderr or p.stdout or "").strip()[-160:])
    say(f"radio services written for {user} on {port}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
