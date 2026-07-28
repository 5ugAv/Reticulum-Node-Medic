"""Security-hardening pass — regression + coverage tests.

Two guarantees:
  1. NO refactored runtime module still shells out through `sudo bash -c` /
     `sudo sh -c` (an un-whitelistable arbitrary-command surface).
  2. The scoped sudoers (provisioning/sudoers.d/nodemedic) actually COVERS every
     privileged command the app runs on the medic — a machine-checked mapping so
     a new call site or a narrowed alias can't silently drift apart.
"""

import fnmatch
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUDOERS = os.path.join(REPO, "provisioning", "sudoers.d", "nodemedic")


# --------------------------------------------------------------------------- #
# 1. No arbitrary-shell sudo left in the app's runtime modules.
# --------------------------------------------------------------------------- #

# Modules that issue privileged commands the medic runs on ITSELF. link.py's
# `sudo -S bash -c` is the ONE allowed exception: it is the one-time, password-
# authenticated bootstrap that writes a NEW node's sudoers (not passwordless,
# not the medic's own) — documented as a residual gap, not a runtime path.
_RUNTIME_MODULES = [
    "provisioning/brightness.py",
    "provisioning/gadget.py",
    "provisioning/uart_console.py",
    "provisioning/sd_edit.py",
    "provisioning/wifi.py",
    "provisioning/tool_datetime.py",
    "provisioning/power.py",
    "provisioning/pi_imager.py",
    "workflows/node_mode.py",
    "workflows/robust_flash.py",
    "workflows/gps_setup.py",
    "monitor/self_diagnose_runtime.py",
]

_BAD = re.compile(r"sudo(\s+-\S+)*\s+(bash|sh)\s+-c")


def test_no_sudo_bash_or_sh_dash_c_in_runtime_modules():
    offenders = []
    for rel in _RUNTIME_MODULES:
        with open(os.path.join(REPO, rel)) as fh:
            for i, line in enumerate(fh, 1):
                code = line.split("#", 1)[0]          # ignore explanatory comments
                if _BAD.search(code):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, "arbitrary-shell sudo remains:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# 2. The scoped sudoers covers every inventoried medic-local call site.
# --------------------------------------------------------------------------- #

def _parse_sudoers_cmnds(path):
    """Return the list of Cmnd strings from every Cmnd_Alias in the file."""
    text = open(path).read()
    text = text.replace("\\\n", " ")                 # join line continuations
    cmnds = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"Cmnd_Alias\s+\w+\s*=\s*(.+)$", line)
        if not m:
            continue
        for c in m.group(1).split(","):
            c = c.strip()
            if c:
                cmnds.append(c)
    return cmnds


def _tokens(s):
    return s.split()


def _covered(argv, cmnds):
    """True if some sudoers Cmnd matches *argv* the way sudo would: identical
    executable path, equal argument count, each arg fnmatch-ing its pattern.
    (`\\:` in a pattern is sudo's escaped literal colon.)"""
    for c in cmnds:
        pt = _tokens(c.replace("\\:", ":"))
        if len(pt) != len(argv):
            continue
        if pt[0] != argv[0]:
            continue
        if all(fnmatch.fnmatch(a, p) for a, p in zip(argv[1:], pt[1:])):
            return True
    return False


# Each entry: (call site, the argv sudo receives after secure_path resolution).
# Bare command names the app calls (iw, uhubctl, usermod, partprobe) resolve via
# sudo's secure_path to /usr/sbin on this merged-usr Debian.
_INVENTORY = [
    ("brightness.set_brightness",
     ["/usr/bin/tee", "/sys/class/backlight/panel_backlight@1/brightness"]),
    ("wifi.scan_networks",        ["/usr/sbin/iw", "dev", "wlan0", "scan"]),
    ("wifi.set_autoconnect",
     ["/usr/bin/nmcli", "connection", "modify", "MyNet",
      "connection.autoconnect", "yes"]),
    ("wifi.set_autoconnect.priority",
     ["/usr/bin/nmcli", "connection", "modify", "MyNet",
      "connection.autoconnect", "yes", "connection.autoconnect-priority", "10"]),
    # RTNode-2400 birth AP-hop (rtnode_portal.py, wired in ui/app.py) — medic-local.
    ("rtnode_portal.rescan",
     ["/usr/bin/nmcli", "device", "wifi", "rescan", "ssid", "RTNode-Setup"]),
    ("rtnode_portal.connect",
     ["/usr/bin/nmcli", "device", "wifi", "connect", "RTNode-Setup"]),
    ("rtnode_portal.read_own_psk",
     ["/usr/bin/nmcli", "-s", "-g", "802-11-wireless-security.psk",
      "connection", "show", "HomeNet-5g"]),
    ("tool_datetime.set_datetime.ntp",
     ["/usr/bin/timedatectl", "set-ntp", "false"]),
    ("tool_datetime.set_datetime",
     ["/usr/bin/timedatectl", "set-time", "2026-07-08 03:04:05"]),
    ("tool_datetime.set_timezone",
     ["/usr/bin/timedatectl", "set-timezone", "America/New_York"]),
    ("power.power_off",           ["/usr/bin/systemctl", "poweroff"]),
    ("node_mode.set_mode.rnsd",   ["/usr/bin/systemctl", "restart", "rnsd"]),
    ("node_mode.set_mode.lxmd",   ["/usr/bin/systemctl", "restart", "lxmd"]),
    ("self_diagnose.restart_splitter",
     ["/usr/bin/systemctl", "restart", "rnode-splitter"]),
    ("robust_flash.find_hub_port", ["/usr/sbin/uhubctl"]),
    ("robust_flash.power_cycle",
     ["/usr/sbin/uhubctl", "-l", "3", "-p", "1", "-a", "off"]),
    ("sd_edit.mount",             ["/usr/bin/mount", "/dev/sda1", "/tmp/nm_sd_boot"]),
    ("sd_edit.umount",            ["/usr/bin/umount", "/tmp/nm_sd_boot"]),
    ("sd_edit.tee.config",        ["/usr/bin/tee", "/tmp/nm_sd_boot/config.txt"]),
    ("sd_edit.tee.cmdline",       ["/usr/bin/tee", "/tmp/nm_sd_boot/cmdline.txt"]),
    ("pi_imager.dd",
     ["/usr/bin/dd", "of=/dev/sda", "bs=4M", "conv=fsync", "status=progress"]),
    ("pi_imager.partprobe",       ["/usr/sbin/partprobe", "/dev/sda"]),
    ("pi_imager.mkdir",           ["/usr/bin/mkdir", "-p", "/tmp/rnm-piboot"]),
    ("pi_imager.mount",           ["/usr/bin/mount", "/dev/sda1", "/tmp/rnm-piboot"]),
    ("pi_imager.tee.toml",        ["/usr/bin/tee", "/tmp/rnm-piboot/custom.toml"]),
    ("pi_imager.touch.ssh",       ["/usr/bin/touch", "/tmp/rnm-piboot/ssh"]),
    ("pi_imager.sync",            ["/usr/bin/sync"]),
    ("pi_imager.umount",          ["/usr/bin/umount", "/tmp/rnm-piboot"]),
    ("link.discover_peer.addr",
     ["/usr/bin/ip", "addr", "add", "10.55.0.2/29", "dev", "usb0"]),
    ("link.discover_peer.up",     ["/usr/bin/ip", "link", "set", "usb0", "up"]),
    ("gps_setup.tee.gpsd",        ["/usr/bin/tee", "/etc/default/gpsd"]),
    ("gps_setup.apt",
     ["/usr/bin/apt-get", "install", "-y", "gpsd", "gpsd-clients"]),
    ("gps_setup.enable",
     ["/usr/bin/systemctl", "enable", "gpsd.socket", "gpsd"]),
    ("gps_setup.restart",
     ["/usr/bin/systemctl", "restart", "gpsd.socket", "gpsd"]),
    ("diagnostics.dmesg",
     ["/usr/bin/dmesg", "--level=emerg,alert,crit,err,warn"]),
    ("diagnostics.ss",            ["/usr/bin/ss", "-tlnp"]),
    ("diagnostics.setfacl",
     ["/usr/bin/setfacl", "-m", "u:nodemedic:rw", "/dev/ttyACM0"]),
    ("diagnostics.usermod",
     ["/usr/sbin/usermod", "-aG", "dialout", "nodemedic"]),
]


def test_every_inventoried_call_site_is_whitelisted():
    cmnds = _parse_sudoers_cmnds(SUDOERS)
    missing = [name for name, argv in _INVENTORY if not _covered(argv, cmnds)]
    assert not missing, "sudoers does NOT cover: " + ", ".join(missing)


def test_sudoers_does_not_grant_blanket_all():
    text = open(SUDOERS).read()
    # The grant line must reference only NM_ aliases, never `ALL` as a command.
    grant = [l for l in text.splitlines()
             if re.match(r"\s*nodemedic\s+ALL=", l) or l.strip().startswith("NM_")]
    joined = " ".join(grant)
    assert "NOPASSWD: ALL" not in joined and "NOPASSWD:ALL" not in joined
    # Runas is pinned to root, not (ALL).
    assert "ALL=(root)" in text


def test_setfacl_is_pinned_to_serial_ports_not_block_devices():
    """setfacl must reach SERIAL ports only. A wildcard like /dev/* also matches
    block devices and /dev/mem, letting the app grant itself rw on the medic's own
    root disk — a full-root escalation. It must be denied for those, allowed for
    the serial forms the code actually uses (ttyACM/USB and /dev/serial by-id)."""
    cmnds = _parse_sudoers_cmnds(SUDOERS)

    def setfacl(path):
        return ["/usr/bin/setfacl", "-m", "u:nodemedic:rw", path]

    # MUST be allowed — the legitimate serial-port targets.
    for ok in ("/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyAMA10",
               "/dev/serial/by-id/usb-Espressif_USB_JTAG-if00"):
        assert _covered(setfacl(ok), cmnds), f"serial port wrongly denied: {ok}"

    # MUST be denied — block devices, memory, and arbitrary paths (escalation).
    for bad in ("/dev/mmcblk0", "/dev/sda", "/dev/vda", "/dev/nvme0n1",
                "/dev/mem", "/dev/kmem", "/etc/shadow", "/dev/loop0"):
        assert not _covered(setfacl(bad), cmnds), f"escalation path allowed: {bad}"
