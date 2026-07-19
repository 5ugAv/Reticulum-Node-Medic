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

#: Sets the gadget's static address the instant usb0 appears — independent of
#: NetworkManager / dhcpcd / networkd, which differ across Pi OS releases.
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
ExecStart=/sbin/ip addr add {GADGET_USB_IP}/{USB_PREFIX} dev usb0
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


def config_txt_with_gadget(text: str) -> str:
    """Return *text* (a Pi ``config.txt``) with ``dtoverlay=dwc2`` present.
    Idempotent — a no-op if the overlay is already declared."""
    for line in text.splitlines():
        if line.strip() == _DWC2_OVERLAY:
            return text
    sep = "" if text.endswith("\n") or text == "" else "\n"
    return f"{text}{sep}\n# USB gadget ethernet (Node Medic provisioning link)\n{_DWC2_OVERLAY}\n"


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

    # Write both boot files (via the priv wrapper) and the static-IP service.
    steps = []
    if new_cfg != cfg:
        steps.append(_tee(f"{boot_dir}/config.txt", new_cfg))
    if new_cmd != cmd:
        steps.append(_tee(f"{boot_dir}/cmdline.txt", new_cmd))
    steps.append(_tee(GADGET_SERVICE_PATH, GADGET_USB0_SERVICE))
    steps.append("systemctl enable nodemedic-gadget-ip.service")

    for step in steps:
        res = conn.run(_priv(step))
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
    """A heredoc `tee` that writes *content* to *path* without quoting hell."""
    marker = "NM_GADGET_EOF"
    return f"tee {path} > /dev/null <<'{marker}'\n{content}\n{marker}"


def _priv(command: str) -> str:
    """Run *command* as root via passwordless sudo (matches the build workflow's
    priv wrapper). The provisioning bootstrap must have set NOPASSWD first."""
    return f"sudo -n bash -c {_shq(command)}"


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
