"""Birth a Pi + radio with BOTH plugged into Node Medic — no WiFi, no hub.

The operator's problem (2026-08-01): a Pi Zero 2 W can't reliably power a
Heltec V3 from its own USB port, and nobody carries a powered hub into the
field. Their answer: plug the Pi into one Node Medic port and the radio into
another, let the medic — which has a real 5 V/5 A supply and a 1600 mA USB
budget — feed both, and only join them at the end.

That works, and it dissolves a constraint this codebase had baked in.
``provisioning.reachability`` sends a Zero down the UART path because its single
USB controller is *"occupied hosting the RNode radio"*. True once the node is
DEPLOYED — but during birth the radio is on the medic, so the Pi's OTG port is
free to be a gadget-ethernet link. Hence two different links for two different
moments, which is why this module exists alongside ``reachability``:

    BIRTH-time link   : USB gadget ethernet (this module) — radio is on the medic
    DEPLOYED-time link: UART console (reachability) — radio owns the USB port

The flow, all of it offline:

  1. Medic images the SD card and bakes the gadget link into it (``bake_*``).
  2. Card into the Pi; Pi's DATA port into the medic. One cable carries power
     AND the link — the Zero boots at GADGET_USB_IP with no WiFi involved.
  3. Radio into a different medic port; the medic flashes it with its own proven
     RNode path (full power, ``local_board_ports`` pinning, RobustFlasher).
  4. Medic provisions the Pi over the cable, writing its Reticulum config to
     point at the radio's STABLE ``/dev/serial/by-id/`` path — learned while
     flashing it, and identical once the radio is moved to the Pi.
  5. Operator unplugs both and joins them. Nothing has to be re-detected.

Everything here is pure string/plan building so it is unit-testable with no
hardware; the callers apply the returned commands.
"""

from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

from provisioning.by_id import (          # the ONE serial reader, shared
    PLACEHOLDER_SERIALS, by_id_serial, is_uniquely_identified)
from provisioning.gadget import (GADGET_USB_IP, HOST_USB_IP, USB_PREFIX,
                                 GADGET_USB0_SERVICE, GADGET_SERVICE_PATH,
                                 NM_UNMANAGED_PATH, NM_UNMANAGED_CONF,
                                 cmdline_with_gadget, config_txt_with_gadget)
from node_profile import NodeHardware

#: Boards whose OTG port can be a gadget link DURING BIRTH — i.e. every Pi whose
#: USB port is free while the radio sits on the medic. The Zero 2 W and 3A+ are
#: here even though ``reachability`` (rightly) gives them UART for deployed life.
CABLE_BIRTH_BOARDS = {NodeHardware.PI_ZERO_2W, NodeHardware.PI_3A_PLUS,
                      NodeHardware.PI_5}

#: Where the gadget unit is symlinked so systemd starts it without `enable`
#: (we're editing a cold rootfs — there is no running systemd to ask).
_WANTS_DIR = "/etc/systemd/system/multi-user.target.wants"
_WANTS_LINK = f"{_WANTS_DIR}/nodemedic-gadget-ip.service"


def supports_cable_birth(hardware: NodeHardware) -> bool:
    """Can this board be birthed over a USB cable to the medic?"""
    return hardware in CABLE_BIRTH_BOARDS


# --------------------------------------------------------------------------- #
# Baking the link into a cold SD card
# --------------------------------------------------------------------------- #

def _tee(path: str, content: str) -> str:
    """Write *content* to *path* as root, without a here-doc — the gadget unit
    contains ``$`` and quotes that a here-doc would mangle."""
    b64 = base64.b64encode(content.encode()).decode()
    return f"echo {shlex.quote(b64)} | base64 -d | sudo tee {shlex.quote(path)} >/dev/null"


def boot_partition_commands(mnt: str) -> List[str]:
    """Commands to bake gadget ethernet into a boot partition mounted at *mnt*.

    Reads the card's own config.txt/cmdline.txt and rewrites them through the
    tested pure transforms, so this can never drift from ``provisioning.gadget``
    — the rules live in exactly one place. Idempotent, and a no-op on a card
    that already has the link.
    """
    script = (
        "import os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from provisioning.gadget import cmdline_with_gadget, config_txt_with_gadget\n"
        "mnt = sys.argv[2]\n"
        "for name, fn in (('cmdline.txt', cmdline_with_gadget),\n"
        "                 ('config.txt', config_txt_with_gadget)):\n"
        "    p = os.path.join(mnt, name)\n"
        "    if not os.path.exists(p):\n"
        "        print('gadget: MISSING ' + name); continue\n"
        "    old = open(p).read()\n"
        "    new = fn(old)\n"
        "    if new != old:\n"
        "        open(p, 'w').write(new); print('gadget: baked ' + name)\n"
        "    else:\n"
        "        print('gadget: ' + name + ' already had it')\n"
    )
    b64 = base64.b64encode(script.encode()).decode()
    return [
        f"test -f {shlex.quote(mnt)}/config.txt && test -f {shlex.quote(mnt)}/cmdline.txt",
        f"echo {shlex.quote(b64)} | base64 -d | sudo python3 - "
        f"{shlex.quote(repo_root())} {shlex.quote(mnt)}",
    ]


def rootfs_commands(mnt: str) -> List[str]:
    """Commands to install the gadget static-IP unit into a rootfs at *mnt*.

    Without this the gadget enumerates but has no address, leaving discovery to
    guess. We can't ``systemctl enable`` a cold filesystem, so the wants-symlink
    is created directly — exactly what enable would have done.

    The NetworkManager drop-in is not optional garnish: NM claims usb0 the
    moment it appears and FLUSHES the address this unit sets, which is a link
    that enumerates perfectly and carries nothing (bench, 2026-08-06).
    """
    nm_dir = NM_UNMANAGED_PATH.rsplit("/", 1)[0]
    return [
        f"sudo mkdir -p {shlex.quote(mnt)}{_WANTS_DIR}",
        _tee(f"{mnt}{GADGET_SERVICE_PATH}", GADGET_USB0_SERVICE),
        f"sudo ln -sf {shlex.quote(GADGET_SERVICE_PATH)} "
        f"{shlex.quote(mnt + _WANTS_LINK)}",
        f"sudo mkdir -p {shlex.quote(mnt + nm_dir)}",
        _tee(f"{mnt}{NM_UNMANAGED_PATH}", NM_UNMANAGED_CONF),
    ]


def repo_root() -> str:
    """This checkout's root — so the baking script can import the real
    transforms rather than a copy of the rules."""
    import os
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bake_commands(device_path: str, boot_mnt: str = "/tmp/rnm-piboot",
                  root_mnt: str = "/tmp/rnm-piroot") -> List[str]:
    """The full mount → bake → unmount plan for a card at *device_path*.

    Partition naming follows the imager's convention: ``p1``/``p2`` for
    mmcblk-style devices, ``1``/``2`` for ``sdX``. The caller is responsible for
    having checked the device is a safe removable target — this only builds
    strings.
    """
    suffix = "p" if device_path[-1].isdigit() else ""
    boot = shlex.quote(f"{device_path}{suffix}1")
    root = shlex.quote(f"{device_path}{suffix}2")
    bq, rq = shlex.quote(boot_mnt), shlex.quote(root_mnt)
    cmds = [f"sudo partprobe {shlex.quote(device_path)} 2>/dev/null; sleep 1",
            f"sudo mkdir -p {bq} && sudo mount {boot} {bq}"]
    cmds += boot_partition_commands(boot_mnt)
    cmds += [f"sudo sync && sudo umount {bq}",
             f"sudo mkdir -p {rq} && sudo mount {root} {rq}"]
    cmds += rootfs_commands(root_mnt)
    cmds += [f"sudo sync && sudo umount {rq}"]
    return cmds


# --------------------------------------------------------------------------- #
# The birth plan the operator is walked through
# --------------------------------------------------------------------------- #

@dataclass
class CableBirthPlan:
    """What the operator does, and what the medic does, in order."""
    pi_name: str
    board_name: str
    steps: List[dict] = field(default_factory=list)

    @property
    def titles(self) -> List[str]:
        return [s["title"] for s in self.steps]


def plan(pi_name: str, board_name: str, radio_by_id: str = "") -> CableBirthPlan:
    """The cable-birth walkthrough. ``who`` is 'operator' or 'medic' so the UI
    can show who is acting; the operator only ever plugs and unplugs."""
    radio_note = (f"Its port ({radio_by_id}) is written into the Pi's config, so "
                  f"it still works once you move it."
                  if radio_by_id else
                  "The medic notes the radio's permanent port name so the Pi "
                  "still finds it after you move it.")
    steps = [
        {"who": "operator", "title": f"Plug the {pi_name} into Node Medic",
         "body": "Use the Pi's DATA USB port — the one marked USB, not PWR IN. "
                 "That single cable carries both power and the link, so no Wi-Fi "
                 "and no separate power supply are needed."},
        {"who": "medic", "title": "Wait for the Pi to come up",
         "body": f"The Pi appears on the cable at {GADGET_USB_IP}. First boot "
                 f"takes a minute or two."},
        {"who": "operator", "title": f"Plug the {board_name} into Node Medic too",
         "body": "A different USB port on the medic. The medic powers it, so the "
                 "Pi never has to."},
        {"who": "medic", "title": f"Flash the {board_name} as an RNode",
         "body": "Done here, on full power — the moment that would have browned "
                 "out an under-powered Pi. " + radio_note},
        {"who": "medic", "title": f"Set up the {pi_name}",
         "body": "Reticulum, the message store, services, health reporting and "
                 "the birth certificate — all over the cable."},
        {"who": "operator", "title": "Unplug both and join them",
         "body": f"Take both off the medic, plug the {board_name} into the "
                 f"{pi_name}'s USB port, and power the Pi from its PWR IN port. "
                 f"It comes up and announces itself on the mesh."},
    ]
    return CableBirthPlan(pi_name=pi_name, board_name=board_name, steps=steps)


def power_note(pi_name: str, board_name: str) -> str:
    """Why this path sidesteps the powered-hub warning entirely."""
    return (f"Because the {board_name} is powered by Node Medic and not by the "
            f"{pi_name}, the powered-USB-hub problem doesn't arise during the "
            f"build. Once you join them the {pi_name} does power the radio, so "
            f"the pairing's own limits still apply in the field.")


# --------------------------------------------------------------------------- #
# Carrying the radio's identity from the medic to the Pi
# --------------------------------------------------------------------------- #

def stable_port_for(port: str, runner=None) -> str:
    """The ``/dev/serial/by-id/`` path for a device currently at *port*.

    This is what makes the hand-off work. The medic flashes the radio while it
    is plugged into ITSELF, where it might be ``/dev/ttyUSB0``; on the Pi it
    could be anything. The by-id name is derived from the USB chip's own vendor
    /product/serial, so it is IDENTICAL on both hosts — write that into the Pi's
    Reticulum config and the radio is found the instant it's moved across.

    Falls back to *port* unchanged when there's no by-id entry (some CH340s
    report no serial number), which is still correct for a Pi with one radio.
    """
    import os
    import subprocess
    by_id = "/dev/serial/by-id"

    def _default(argv):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            return (p.returncode, p.stdout)
        except Exception:                              # noqa: BLE001
            return (1, "")
    run = runner or _default
    rc, out = run(["ls", "-l", by_id])
    if rc != 0 or not out.strip():
        return port
    target = os.path.basename(port)
    for line in out.splitlines():
        # "lrwxrwxrwx 1 root root 13 ... usb-Silicon_Labs_...-if00-port0 -> ../../ttyUSB0"
        if "->" not in line:
            continue
        name, _, dest = line.rpartition(" -> ")
        if os.path.basename(dest.strip()) != target:
            continue
        link = name.split()[-1]
        return f"{by_id}/{link}"
    return port


# by_id_serial / is_uniquely_identified / PLACEHOLDER_SERIALS now live in
# provisioning.by_id (imported at the top) — one reader, shared with
# rnode_flash and rtnode_build, which used to carry diverging copies. The names
# stay in this module's namespace via that import, so callers and tests keyed
# to ``cable_birth.by_id_serial`` keep working.


def unmoved_warning(by_id_path: str) -> str:
    """Wording for the one failure this hand-off can still have: the operator
    plugs a DIFFERENT radio into the Pi from the one that was flashed.

    Only claims the radio is pinned when the serial really is unique — a
    factory-default serial like the Heltec V3's "0001" would match any board of
    that model, and saying otherwise would be a promise we can't keep.
    """
    if is_uniquely_identified(by_id_path):
        return ("The Pi is configured for this exact radio, so plug in the one "
                "you just flashed — another board of the same model won't be "
                "picked up.")
    if by_id_path.startswith("/dev/serial/by-id/"):
        return ("This radio uses its maker's default serial number, so the Pi "
                "can't tell it apart from another board of the same model. "
                "Plug in only the radio you just flashed.")
    return ("The radio reports no unique serial, so the Pi will use the "
            "first radio it finds. Make sure only the radio you just "
            "flashed is plugged into it.")
