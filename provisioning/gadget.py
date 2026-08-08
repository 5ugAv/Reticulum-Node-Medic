"""USB-gadget ethernet enablement — the plug-in link for on-site provisioning.

A Raspberry Pi only presents a network interface over its USB port if its image
enables USB-gadget mode (``dwc2`` overlay + the ``g_ether`` module). Stock
Raspberry Pi OS does NOT, so a bone-stock Pi plugged into Node Medic shows up as
nothing. This module bakes gadget-ethernet into a node image (or enables it on an
already-reachable Pi), so any node Node Medic images comes up as a ``usb0`` link
with a DETERMINISTIC address the moment it's plugged in — no WiFi required.

The link uses a tiny static /29 so discovery is dead simple: the gadget always
sits at GADGET_USB_IP and the host claims HOST_USB_IP on whatever ``usbN``/``enxN``
interface appears (see provisioning.link). Everything here is pure string / config
transforms (unit-tested); ``enable_gadget`` applies them over a Connection.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import List

from transport.connection import Connection

#: Deterministic point-to-point link over the USB cable. /29 = 6 usable hosts,
#: tiny and unlikely to collide with any real LAN the medic is also on.
GADGET_USB_IP = "10.55.0.1"     # the node being provisioned
HOST_USB_IP = "10.55.0.2"       # Node Medic's end
USB_PREFIX = 29

#: Kernel modules the cmdline must load for the OTG port to enumerate as a CDC
#: ethernet gadget. Order matters: dwc2 (the OTG controller) before g_ether.
_GADGET_MODULES = "modules-load=dwc2,g_ether"

#: config.txt line that binds the OTG-capable USB controller in peripheral mode.
_DWC2_OVERLAY = "dtoverlay=dwc2"

#: Boards whose OTG port CANNOT work out for itself that it should be a device,
#: mapped to the ``dr_mode`` they must be told explicitly.
#:
#: Bare ``dtoverlay=dwc2`` leaves ``dr_mode=otg``, and otg means "look at the ID
#: pin and decide". That works on the boards where there IS an ID pin to look
#: at: a Pi Zero's micro-USB and a Pi 4/5's USB-C both carry one, which is why
#: the Zero 2 W route has always worked (HOPE, 2026-08-01).
#:
#: A Pi 3A+ exposes its OTG controller on a full-size USB-A socket, and USB-A
#: has NO ID pin — the port is wired as a host and otg resolves to host, every
#: time. Watched live on 2026-08-07: 3A+ powered, booting off a card we had just
#: written, plugged into a self-powered hub with the rail steady at 4.92 V, and
#: the medic logged not one USB event. Nothing was wrong with the card, the
#: cable or the power. The board was never going to be a device.
#:
#: ``dr_mode=peripheral`` overrides the pin and forces the controller into
#: device mode. That is the whole point of the parameter.
#:
#: This does NOT make the 3A+ route work on its own — see #72. An A-to-A cable
#: carries VBUS at both ends, so a separately-powered 3A+ and the host both
#: drive 5 V onto the same rail. That needs a VBUS-cut cable and is the
#: operator's half of the problem. This is ours.
DR_MODE_BY_BOARD = {
    "pi_3a_plus": "peripheral",
}


def dwc2_overlay_for(pi_key: str = "") -> str:
    """The ``dtoverlay=dwc2`` line this board needs, with ``dr_mode`` if any.

    Unknown or empty *pi_key* gets the plain overlay — the behaviour every
    board had before, so a board we have not characterised is never made worse.
    """
    mode = DR_MODE_BY_BOARD.get((pi_key or "").strip())
    return f"{_DWC2_OVERLAY},dr_mode={mode}" if mode else _DWC2_OVERLAY

#: Tell NetworkManager to keep its hands off the gadget link.
#:
#: THIS IS NOT OPTIONAL. NM manages every ethernet device it sees, and usb0 is
#: an ethernet device the moment g_ether enumerates. When NM claims a device it
#: FLUSHES the addresses already on it and starts its own autoconnect/DHCP — so
#: a bare `ip addr add` from a oneshot service is set, then silently wiped, and
#: the link goes quiet with nothing in the file looking wrong.
#:
#: Watched happen live on 2026-08-06: a static 10.55.0.2/29 set by hand on the
#: MEDIC disappeared on its own, and the node end of that same link never
#: answered even an IPv6 all-nodes ping — its usb0 was down, not just
#: unaddressed. The old comment here claimed this service was "independent of
#: NetworkManager"; being independent of NM does not stop NM undoing your work.
NM_UNMANAGED_PATH = "/etc/NetworkManager/conf.d/99-nodemedic-usb0.conf"
NM_UNMANAGED_CONF = """\
# Node Medic provisioning link. usb0 carries a fixed point-to-point address set
# by nodemedic-gadget-ip.service; NetworkManager must not claim it, because
# claiming it flushes that address and the medic then waits for a link that
# will never answer.
[keyfile]
unmanaged-devices=interface-name:usb0
"""

#: Sets the gadget's static address once usb0 exists.
#:
#: `addr replace` rather than `addr add`: add fails if the address is already
#: present, which turns a harmless re-run into a unit failure.
GADGET_USB0_SERVICE = f"""\
[Unit]
Description=USB gadget link static IP (Node Medic provisioning)
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
# usb0 may take a moment to enumerate after g_ether loads; wait briefly for it.
ExecStartPre=/bin/sh -c 'for i in $(seq 1 20); do ip link show usb0 && exit 0; sleep 0.5; done; exit 0'
ExecStart=/sbin/ip addr replace {GADGET_USB_IP}/{USB_PREFIX} dev usb0
ExecStart=/sbin/ip link set usb0 up

[Install]
WantedBy=multi-user.target
"""

GADGET_SERVICE_PATH = "/etc/systemd/system/nodemedic-gadget-ip.service"


def cmdline_with_gadget(text: str) -> str:
    """Return *text* (a Pi ``cmdline.txt`` — one space-separated line) with the
    gadget modules present. Inserts ``modules-load=dwc2,g_ether`` right after
    ``rootwait`` (must precede the rootfs handoff); merges into an existing
    ``modules-load=`` if the image already has one. Idempotent."""
    trailing_nl = "\n" if text.endswith("\n") else ""
    tokens = text.split()

    # Already has our exact token — nothing to do.
    if _GADGET_MODULES in tokens:
        return text

    # Merge into a pre-existing modules-load= (rare on stock Pi OS).
    for i, tok in enumerate(tokens):
        if tok.startswith("modules-load="):
            existing = tok[len("modules-load="):].split(",")
            for mod in ("dwc2", "g_ether"):
                if mod not in existing:
                    existing.append(mod)
            tokens[i] = "modules-load=" + ",".join(existing)
            return " ".join(tokens) + trailing_nl

    # Otherwise insert straight after rootwait, or append if there's no rootwait.
    out: List[str] = []
    inserted = False
    for tok in tokens:
        out.append(tok)
        if tok == "rootwait" and not inserted:
            out.append(_GADGET_MODULES)
            inserted = True
    if not inserted:
        out.append(_GADGET_MODULES)
    return " ".join(out) + trailing_nl


def config_txt_with_gadget(text: str, pi_key: str = "") -> str:
    """Return *text* (a Pi ``config.txt``) with ``dtoverlay=dwc2`` present.
    Idempotent — a no-op if the overlay is already declared.

    *pi_key* selects the ``dr_mode`` this board needs (see
    :func:`dwc2_overlay_for`). An applicable dwc2 line that is not the one this
    board needs is UPGRADED IN PLACE — never duplicated, and never left alone.

    That last part changed on 2026-08-08. The rule used to be "a card already
    carrying a dwc2 line is left exactly as it is", on the reasoning that
    rewriting an inferred mode is riskier than the mode being wrong. On a Pi 3A+
    that reasoning inverts: a bare ``dtoverlay=dwc2`` means dr_mode=otg, "read
    the ID pin", and USB-A has no ID pin — so leaving it is not caution, it is a
    guaranteed silent failure. We only rewrite where DR_MODE_BY_BOARD gives this
    board a known required mode; for any board without an entry our overlay IS
    the bare line, so an existing one already matches and nothing is touched.

    The appended block opens with ``[all]``. config.txt is SECTIONED, and an
    overlay inherits whichever section it falls under: stock Raspberry Pi OS
    ends with ``[all]`` (verified on a real card, 2026-08-01) but it also ships
    ``[cm4]``/``[cm5]``/``[pi5]`` blocks, and any image whose file ended on one
    of those would have swallowed our line into a board filter that the target
    board doesn't match — dwc2 silently absent, gadget silently dead, and
    nothing to see in the file. Re-opening ``[all]`` costs one line and makes
    the overlay apply wherever the card is booted.
    """
    overlay = dwc2_overlay_for(pi_key)
    if config_txt_has_gadget(text, pi_key):
        return text
    # An applicable dwc2 line that is not the one we need gets UPGRADED IN
    # PLACE, never duplicated. Two dwc2 overlays in force is ambiguous, and
    # leaving a bare one is not the conservative choice it looks like: bare
    # means dr_mode=otg, "read the ID pin", and a USB-A socket has none — so
    # the gadget silently never comes up. We only rewrite when this board has a
    # KNOWN required mode; for a board with no entry our overlay IS the bare
    # line, so an existing one already matches and nothing is touched.
    at = _applicable_dwc2_line(text, _DWC2_OVERLAY)
    if at is not None:
        lines = text.splitlines(keepends=True)
        pad = lines[at][:len(lines[at]) - len(lines[at].lstrip())]
        nl = "\n" if lines[at].endswith("\n") else ""
        lines[at] = f"{pad}{overlay}{nl}"
        return "".join(lines)
    sep = "" if text.endswith("\n") or text == "" else "\n"
    return (f"{text}{sep}\n# USB gadget ethernet (Node Medic provisioning link)\n"
            f"[all]\n{overlay}\n")



def _applicable_dwc2_line(text, base):
    """Index of a dwc2 overlay line that is IN FORCE for every board, or None.

    Only lines outside board-filtered sections count — a [cm5] line is not in
    force on a 3A+ and must never be treated as ours."""
    section = "all"
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if section == "all" and (s == base or s.startswith(base + ",")):
            return i
    return None

def config_txt_applies_to_all(text: str) -> list:
    """The lines of *text* in force on EVERY board, headers stripped.

    Lines before any ``[section]`` apply to all boards, and ``[all]`` returns to
    that state; anything under ``[pi5]``/``[cm4]``/``[cm5]``/``[board-type=…]``
    applies only there."""
    out, section = [], "all"
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if section == "all":
            out.append(s)
    return out


def config_txt_has_gadget(text: str, pi_key: str = "") -> bool:
    """Is OUR overlay present in a section that applies to this board?

    THE BUG THIS REPLACES, caught on a real card 2026-08-08. The check used to
    scan every line regardless of section. Raspberry Pi OS ships::

        [cm5]
        dtoverlay=dwc2,dr_mode=host

    and ``startswith("dtoverlay=dwc2,")`` matched it, so a Pi 3A+ card was
    declared already-prepared and returned untouched — on the strength of a
    Compute Module 5 line, saying dr_mode=HOST. cmdline.txt still received its
    half, so the card carried ``modules-load=dwc2,g_ether`` with no dwc2 in the
    device tree: the Pi booted perfectly and presented no USB device at all.
    Indistinguishable from a bad cable, and it cost most of a bench session.

    Both conditions matter. The section, because a [cm5] line is not in force on
    a 3A+; and the exact overlay, because a bare ``dtoverlay=dwc2`` leaves
    dr_mode=otg — "read the ID pin" — and a USB-A socket has no ID pin."""
    return dwc2_overlay_for(pi_key) in config_txt_applies_to_all(text)


@dataclass
class GadgetResult:
    ok: bool
    message: str
    changed: bool = False


def enable_gadget(conn: Connection, boot_dir: str = "/boot/firmware") -> GadgetResult:
    """Enable USB-gadget ethernet on the Pi behind *conn* (idempotent). Edits
    ``<boot_dir>/config.txt`` + ``cmdline.txt`` and installs the static-IP
    service. Requires root (the connection's priv wrapper / passwordless sudo).
    Takes effect on the node's next reboot. *boot_dir* is ``/boot/firmware`` on
    Bookworm, ``/boot`` on older images — the caller can override."""
    try:
        cfg = conn.run_checked(f"cat {boot_dir}/config.txt")
        cmd = conn.run_checked(f"cat {boot_dir}/cmdline.txt")
    except Exception as e:  # pragma: no cover - network/hardware failure
        return GadgetResult(False, f"Could not read boot config: {e}")

    new_cfg = config_txt_with_gadget(cfg)
    new_cmd = cmdline_with_gadget(cmd)
    changed = new_cfg != cfg or new_cmd != cmd

    # Write both boot files and the static-IP service, then enable it. Each step
    # is a single argument-pinned privileged command (`sudo -n tee <path>` /
    # `sudo -n systemctl enable <unit>`) — NOT a `sudo bash -c "..."`, so the
    # node's scoped sudoers can whitelist each exact command with no arbitrary
    # shell surface.
    steps = []
    if new_cfg != cfg:
        steps.append(_tee(f"{boot_dir}/config.txt", new_cfg))
    if new_cmd != cmd:
        steps.append(_tee(f"{boot_dir}/cmdline.txt", new_cmd))
    steps.append(_tee(GADGET_SERVICE_PATH, GADGET_USB0_SERVICE))
    steps.append("sudo -n systemctl enable nodemedic-gadget-ip.service")

    for step in steps:
        res = conn.run(step)
        if res[0] != 0:
            return GadgetResult(False, f"Failed applying gadget config: {res[2] or res[1]}",
                                changed)
    return GadgetResult(
        True,
        ("USB-gadget ethernet enabled — reboot the node, then it links over USB "
         f"at {GADGET_USB_IP}." if changed
         else "USB-gadget ethernet already enabled."),
        changed)


def _tee(path: str, content: str) -> str:
    """A privileged write of *content* to *path* without quoting hell. The
    privileged token is exactly ``sudo -n tee <path>`` (whitelistable, no shell).

    base64 rather than a heredoc: a quoted marker stops variable expansion but
    NOT early termination, so a line in the content equal to the marker ends the
    heredoc and the rest runs as shell. Content here is tool-generated rather
    than attacker-supplied, but the three _tee helpers are copies of each other
    and the one in sd_edit.py IS reachable from a crafted SD card — so they move
    together rather than leaving a safe-looking twin to be copied again."""
    b64 = base64.b64encode(content.encode()).decode()
    return f"echo {b64} | base64 -d | sudo -n tee {path} > /dev/null"
