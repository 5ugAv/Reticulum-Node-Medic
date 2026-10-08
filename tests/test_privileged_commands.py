"""Every privileged command in the code is accounted for, and every one the
medic runs ON ITSELF is granted by its scoped sudoers.

The keeper, 2026-10-08: every clone ends with its parent's hardening. From
then on a clone's sudo is the scoped policy (provisioning/sudoers.d/nodemedic,
rendered for its user, pi), so a privileged command the app runs that the
policy does not grant is a feature that silently stops working on every clone
— the way the scoped original medic once nearly lost RTNode birth
(rtnode_portal, missed by a hand-made inventory).

So this test FINDS the privileged calls rather than trusting a list: every
`sudo ...` command string, every ["sudo", ...] argv and every priv()/_priv()
call in the code. Each one must appear below, classified:

  MEDIC     run on the medic ITSELF by its app user — the original medic, a
            clone, and a medic while it clones ANOTHER one (discovery,
            imaging). Each carries the argv sudo receives (absolute paths, as
            secure_path resolves them), and must be granted for nodemedic AND
            for a clone's pi.
  ELSEWHERE not the medic's own sudo, one reason per group: run on a NODE over
            its connection; run on a NEW medic during a clone, before the
            clone's last steps lock it (it still has its card's full sudo);
            the full-sudo fallback of a set-up that has a scoped road; code no
            screen reaches; the definition of a sudo-prefix helper (its
            callers are listed one by one); an emulator.

A new privileged call fails here until it is classified — and, when it runs on
the medic, granted in the policy with exact arguments. A call that moves or is
reworded fails too, so the list cannot rot.
"""

import ast
import os
import re
import shutil
import subprocess

import pytest

from tests.sudoersutil import ROOT, TEMPLATE, Cmnd, Policy, render

# --------------------------------------------------------------------------- #
# Finding the privileged calls
# --------------------------------------------------------------------------- #

_SKIP_DIRS = {".git", "tests", ".claude", "sandbox", "__pycache__", "node_modules",
              "build", "dist", ".venv", "venv"}
#: `sudo` as a COMMAND: at the start of a string or after a shell operator, and
#: followed by something. Prose ("passwordless sudo did not take", "(sudo
#: refused: ...)", "`sudo apt install ...`") does not match.
_SUDO_CMD = re.compile(r"(?:^|[|;&]\s*|\$\(\s*)sudo\s+\S")
#: The sudo-prefix helpers: wf.priv (clone, build), self._priv (diagnostics,
#: gps_setup, medic_radio), priv/_priv (build, pi_reporter_push).
_PRIV_CALLS = {"priv", "_priv"}


def _norm(text):
    return " ".join(text.split())


def privileged_sites():
    """{(path, normalised source)} of every privileged call in the code."""
    found = set()
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            rel = os.path.relpath(path, ROOT)
            text = open(path, encoding="utf-8").read()
            tree = ast.parse(text)
            skip = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    skip.add(id(node.value))            # docstrings and bare prose
                if isinstance(node, ast.JoinedStr):
                    for part in node.values:
                        skip.add(id(part))              # judged as the whole f-string
            for node in ast.walk(tree):
                seg = None
                if isinstance(node, (ast.List, ast.Tuple)) and node.elts and \
                        isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "sudo":
                    seg = ast.get_source_segment(text, node)
                elif isinstance(node, ast.JoinedStr):
                    literal = "".join(p.value if isinstance(p, ast.Constant) else "\x00"
                                      for p in node.values)
                    if _SUDO_CMD.search(literal):
                        seg = ast.get_source_segment(text, node)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in skip and _SUDO_CMD.search(node.value):
                    seg = ast.get_source_segment(text, node)
                elif isinstance(node, ast.Call):
                    f = node.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in _PRIV_CALLS:
                        seg = ast.get_source_segment(text, node)
                if seg:
                    found.add((rel, _norm(seg)))
    return found


# --------------------------------------------------------------------------- #
# MEDIC: run on the medic itself -> the argv sudo receives must be granted.
# "{user}" is the app user: nodemedic on the original medic, pi on a clone.
# --------------------------------------------------------------------------- #

_TEE_BACKLIGHT = ["/usr/bin/tee", "/sys/class/backlight/panel_backlight@1/brightness"]
_TIMEDATECTL = "/usr/bin/timedatectl"
_SYSTEMCTL = "/usr/bin/systemctl"
_NMCLI = "/usr/bin/nmcli"
#: A card mount, as provisioning/card_mount.py builds it: a root-owned folder
#: under /run/nodemedic, made by `install -d`, mounted with the pinned options.
_RUN = "/run/nodemedic"
_OPTS = "nosymfollow,nodev,nosuid,noexec"


def _make_dir(name):
    return ["/usr/bin/install", "-d", "-m", "0755", "-o", "root", "-g", "root",
            _RUN, f"{_RUN}/{name}"]


def _mount(part, name):
    return ["/usr/bin/mount", "-o", _OPTS, part, f"{_RUN}/{name}"]


MEDIC = {
    # the screen's brightness slider (the device is rendered per machine)
    ("provisioning/brightness.py", '["sudo", "-n", "tee", target]'): [_TEE_BACKLIGHT],
    # Settings > Wi-Fi
    ("provisioning/wifi.py", '["sudo", "-n", "iw", "dev", iface, "scan"]'): [
        ["/usr/sbin/iw", "dev", "wlan0", "scan"]],
    ("provisioning/wifi.py",
     '["sudo", "-n", "nmcli", "connection", "modify", ssid, "connection.autoconnect", '
     '"yes" if enabled else "no"]'): [
        [_NMCLI, "connection", "modify", "Home Net", "connection.autoconnect", "yes"],
        [_NMCLI, "connection", "modify", "HomeNet", "connection.autoconnect", "no",
         "connection.autoconnect-priority", "10"]],
    ("provisioning/wifi.py", '["sudo", "-n", "nmcli", "radio", "wifi", state]'): [
        [_NMCLI, "radio", "wifi", "off"], [_NMCLI, "radio", "wifi", "on"]],
    # RTNode-2400 birth: hop to the board's AP and back
    ("workflows/rtnode_portal.py", '["sudo", "-n", "iw", "dev", iface, "scan"]'): [
        ["/usr/sbin/iw", "dev", "wlan0", "scan"]],
    ("workflows/rtnode_portal.py",
     '["sudo", "-n", "nmcli", "device", "wifi", "rescan", "ssid", ssid]'): [
        [_NMCLI, "device", "wifi", "rescan", "ssid", "RTNode-Setup"]],
    ("workflows/rtnode_portal.py", '["sudo", "-n", "nmcli", "device", "wifi", "connect", ssid]'): [
        [_NMCLI, "device", "wifi", "connect", "RTNode-Setup"]],
    ("workflows/rtnode_portal.py",
     '["sudo", "-n", "nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", '
     '"show", name]'): [
        [_NMCLI, "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "HomeNet"]],
    # the clock: Settings > Date & time, and GPS discipline
    ("provisioning/tool_datetime.py", '"sudo -n timedatectl set-ntp false"'): [
        [_TIMEDATECTL, "set-ntp", "false"]],
    ("provisioning/tool_datetime.py", "f'sudo -n timedatectl set-time \"{stamp}\"'"): [
        [_TIMEDATECTL, "set-time", "2026-10-08 03:04:05"]],
    ("provisioning/tool_datetime.py", "f'sudo -n timedatectl set-time \"{stamp} UTC\"'"): [
        [_TIMEDATECTL, "set-time", "2026-10-08 03:04:05 UTC"]],
    ("provisioning/tool_datetime.py", "f'sudo -n timedatectl set-timezone \"{tz}\"'"): [
        [_TIMEDATECTL, "set-timezone", "Australia/Melbourne"]],
    ("monitor/gps_clock.py", '["sudo", "-n", "timedatectl", "set-ntp", "false"]'): [
        [_TIMEDATECTL, "set-ntp", "false"]],
    ("monitor/gps_clock.py", '["sudo", "-n", "timedatectl", "set-ntp", "true"]'): [
        [_TIMEDATECTL, "set-ntp", "true"]],
    ("monitor/gps_clock.py", '["sudo", "-n", "timedatectl", "set-time", stamp]'): [
        [_TIMEDATECTL, "set-time", "2026-10-08 03:04:05"]],
    # power, the mesh services, Self Diagnose's repairs
    ("provisioning/power.py", '["sudo", "-n", "systemctl", "poweroff"]'): [
        [_SYSTEMCTL, "poweroff"]],
    ("workflows/node_mode.py", '"sudo -n systemctl restart rnsd"'): [
        [_SYSTEMCTL, "restart", "rnsd"]],
    ("workflows/node_mode.py", '"sudo -n systemctl restart lxmd"'): [
        [_SYSTEMCTL, "restart", "lxmd"]],
    ("provisioning/medic_radio.py", '["sudo", "-n", "systemctl", "restart", "rnsd"]'): [
        [_SYSTEMCTL, "restart", "rnsd"]],
    ("monitor/self_diagnose_runtime.py", '"sudo -n systemctl restart rnode-splitter 2>&1"'): [
        [_SYSTEMCTL, "restart", "rnode-splitter"]],
    ("monitor/self_diagnose_runtime.py", '"sudo -n systemctl restart rnsd 2>&1"'): [
        [_SYSTEMCTL, "restart", "rnsd"]],
    ("monitor/self_diagnose_runtime.py", '"sudo -n systemctl restart lxmd 2>&1"'): [
        [_SYSTEMCTL, "restart", "lxmd"]],
    # USB power-cycling a wedged board during a flash
    ("workflows/robust_flash.py", '"sudo -n uhubctl"'): [["/usr/sbin/uhubctl"]],
    ("workflows/robust_flash.py",
     'f"sudo -n uhubctl -l {self.hub} -p {self.hub_port} -a off"'): [
        ["/usr/sbin/uhubctl", "-l", "3", "-p", "1", "-a", "off"]],
    ("workflows/robust_flash.py",
     'f"sudo -n uhubctl -l {self.hub} -p {self.hub_port} -a on"'): [
        ["/usr/sbin/uhubctl", "-l", "3", "-p", "1", "-a", "on"]],
    # a node's card in the medic's reader: baking the wired link
    ("provisioning/sd_edit.py",
     'f"sudo -n {card_mount.make_dir(mount)} && " '
     'f"sudo -n {card_mount.mount(part, mount)}"'): [
        _make_dir("sd_boot"), _mount("/dev/sda1", "sd_boot"),
        _mount("/dev/mmcblk1p1", "sd_boot")],
    ("provisioning/sd_edit.py", 'f"sudo -n umount {mount}"'): [
        ["/usr/bin/umount", f"{_RUN}/sd_boot"]],
    ("provisioning/sd_edit.py", 'f"echo {b64} | base64 -d | sudo -n tee {path} > /dev/null"'): [
        ["/usr/bin/tee", f"{_RUN}/sd_boot/config.txt"],
        ["/usr/bin/tee", f"{_RUN}/sd_boot/cmdline.txt"]],
    # imaging a card (a node's — or the NEXT medic's, when this one clones)
    ("provisioning/pi_imager.py",
     'f"xzcat {img} | sudo dd of={dev} bs=4M conv=fsync status=progress " f"&& sync"'): [
        ["/usr/bin/dd", "of=/dev/sda", "bs=4M", "conv=fsync", "status=progress"]],
    ("provisioning/pi_imager.py",
     'f"sudo -n {PREPARE_CARD} --device {shlex.quote(device_path)} " f"--config {q}"'): [
        ["/usr/local/lib/nodemedic/prepare-card", "--device", "/dev/sda",
         "--config", "/tmp/nm-card-config.json"]],
    ("workflows/mitosis_card.py",
     'f"sudo -n {card_mount.make_dir(mnt)} && sudo -n {card_mount.mount(dev, mnt)} " '
     'f"&& {{ {reads} ; }} ; rc=$? ; sudo -n sync ; " '
     'f"sudo -n umount {mnt} && echo \'{marker}__UMOUNT_OK__\' ; exit $rc"'): [
        _make_dir("sd_boot"), _make_dir("piboot"),
        _mount("/dev/sda1", "sd_boot"), _mount("/dev/sda2", "piboot"),
        ["/usr/bin/sync"],
        ["/usr/bin/umount", f"{_RUN}/sd_boot"], ["/usr/bin/umount", f"{_RUN}/piboot"]],
    ("provisioning/pi_usbboot.py", '["sudo", "-n", "rpiboot", "-d", MSD_PAYLOAD_64]'): [
        ["/usr/bin/rpiboot", "-d", "mass-storage-gadget64"]],
    ("provisioning/pi_usbboot.py", '["sudo", "-n", "rpiboot"]'): [["/usr/bin/rpiboot"]],
    # the wired links: USB gadget to a node, the patch cable to a new medic
    ("provisioning/link.py",
     '["sudo", "-n", IP_BIN, "addr", "add", f"{HOST_USB_IP}/{USB_PREFIX}", "dev", ifc]'): [
        ["/usr/bin/ip", "addr", "add", "10.55.0.2/29", "dev", "usb0"]],
    ("provisioning/link.py", '["sudo", "-n", IP_BIN, "link", "set", ifc, "up"]'): [
        ["/usr/bin/ip", "link", "set", "usb0", "up"]],
    ("provisioning/direct_link.py",
     '["sudo", "-n", IP_BIN, "addr", "add", f"{MEDIC_ETH_IP}/{ETH_PREFIX}", "dev", ifc]'): [
        ["/usr/bin/ip", "addr", "add", "10.55.0.2/29", "dev", "eth0"]],
    ("provisioning/direct_link.py", '["sudo", "-n", IP_BIN, "link", "set", ifc, "up"]'): [
        ["/usr/bin/ip", "link", "set", "eth0", "up"]],
    # a CLONE cloning the next medic lets go of its own lifelong 10.55.0.1
    ("provisioning/direct_link.py",
     '["sudo", "-n", IP_BIN, "addr", "del", f"{PEER_ETH_IP}/{ETH_PREFIX}", "dev", ifc]'): [
        ["/usr/bin/ip", "addr", "del", "10.55.0.1/29", "dev", "eth0"]],
    # gpsd on the medic itself
    ("workflows/gps_setup.py", 'self._priv("apt-get install -y gpsd gpsd-clients")'): [
        ["/usr/bin/apt-get", "install", "-y", "gpsd", "gpsd-clients"]],
    ("workflows/gps_setup.py", "self._priv('tee /etc/default/gpsd')"): [
        ["/usr/bin/tee", "/etc/default/gpsd"]],
    ("workflows/gps_setup.py", 'self._priv("systemctl enable gpsd.socket gpsd")'): [
        [_SYSTEMCTL, "enable", "gpsd.socket", "gpsd"]],
    ("workflows/gps_setup.py", 'self._priv("systemctl restart gpsd.socket gpsd")'): [
        [_SYSTEMCTL, "restart", "gpsd.socket", "gpsd"]],
    # diagnostics and their auto-fixes (also run on nodes, where the node's
    # sudoers decides; on the medic, these)
    ("diagnostics/power_hardware.py", 'self._priv("dmesg --level=emerg,alert,crit,err,warn")'): [
        ["/usr/bin/dmesg", "--level=emerg,alert,crit,err,warn"]],
    ("diagnostics/system_health.py", 'self._priv("dmesg --level=emerg,alert,crit,err,warn")'): [
        ["/usr/bin/dmesg", "--level=emerg,alert,crit,err,warn"]],
    ("diagnostics/reticulum_software.py", 'self._priv("ss -tlnp | grep 37428")'): [
        ["/usr/bin/ss", "-tlnp"]],
    ("diagnostics/reticulum_software.py", 'f"sudo setfacl -m u:{user}:rw {target}"'): [
        ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/ttyACM0"],
        ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/ttyUSB0"],
        ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/ttyAMA10"]],
    ("diagnostics/reticulum_software.py", 'f"sudo usermod -aG dialout {user}"'): [
        ["/usr/sbin/usermod", "-aG", "dialout", "{user}"]],
    # a CLONE sets up its own radio after it is locked: the helper road
    ("workflows/medic_radio.py",
     'self._priv( f"{RADIO_HELPER} --port {shlex.quote(self.by_id)}")'): [
        ["/usr/local/lib/nodemedic/radio-units", "--port",
         "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_3C:0F:02:AA:BB:CC-if00"]],
    ("workflows/medic_radio.py",
     'self._priv( "systemctl enable rnode-splitter.service rnsd.service " "lxmd.service")'): [
        [_SYSTEMCTL, "enable", "rnode-splitter.service", "rnsd.service", "lxmd.service"]],
    ("workflows/medic_radio.py", 'self._priv( "systemctl restart rnode-splitter.service")'): [
        [_SYSTEMCTL, "restart", "rnode-splitter.service"]],
    ("workflows/medic_radio.py", 'self._priv( f"systemctl start --no-block {HANDOVER_UNIT}")'): [
        [_SYSTEMCTL, "start", "--no-block", "nm-radio-handover.service"]],
    ("workflows/medic_radio.py", 'self._priv( "systemctl restart rnsd.service lxmd.service")'): [
        [_SYSTEMCTL, "restart", "rnsd.service", "lxmd.service"]],
}

#: Not in the code at all, but run on a CLONE by its keeper: its app is the
#: kiosk unit configure_autostart installs (the original medic restarts its UI
#: another way, scripts/restart_ui.sh).
KEEPER_ON_A_CLONE = [[_SYSTEMCTL, "restart", "reticulum-node-medic.service"]]

#: The two checks harden_new_medic makes on a NEW medic once its sudo is
#: scoped: (constant in workflows/clone.py, argv, must the policy allow it?)
CLONE_CHECKS = {
    ("workflows/clone.py", '"sudo -n /usr/bin/ss -tlnp"'): (["/usr/bin/ss", "-tlnp"], True),
    ("workflows/clone.py", '"sudo -n /usr/bin/true"'): (["/usr/bin/true"], False),
}

# --------------------------------------------------------------------------- #
# ELSEWHERE: not the medic's own sudo. (group, why, {(path, source)})
# --------------------------------------------------------------------------- #

ELSEWHERE = [
    ("github setup",
     "scripts/setup_medic.py configures a fresh Pi OS Lite card from the "
     "repository before anything on it is locked down. It refuses to start "
     "without the card's full sudo, the position the clone's own steps are in "
     "before harden_new_medic. On a medic that has since been locked, typing "
     "the password first does not help: sudo never remembers it for the app "
     "account (timestamp_timeout=0), so the keeper lifts the lock "
     "(rollback_sudoers.sh) for the run and applies it again after.",
     {("workflows/medic_setup.py", s) for s in (
         '"sudo -n true"',
         's.priv("apt-get -o DPkg::Lock::Timeout=300 update")',
         's.priv("env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 " "install -y --no-install-recommends " + " ".join(missing))',
         's.priv("rpi-eeprom-config")',
         's.priv("sh -c \'systemctl daemon-reload && " "systemctl enable --now world-map-fill\'")',
         's.priv("timedatectl set-ntp true")',
         's.priv(f"tee -a {BOOT_CONFIG}")',
         's.priv(f"tee {WORLD_MAP_UNIT}")',
         'self.wf.priv(command)',
     )}),
    ("node",
     "BuildWorkflow, the reporter push and node scoping run on the NODE being "
     "born or looked after, over that node's SSH connection: the node's sudoers.",
     {("workflows/build.py", s) for s in (
         'f"sudo -n {command}"', 'priv(f"cat {NM_SETTIME_SUDOERS_PATH}")',
         'priv(f"chmod 0440 {stage}")', 'priv(f"chmod 0755 {NM_SETTIME_PATH}")',
         'priv(f"chown root:root {NM_SETTIME_PATH}")', 'priv(f"chown root:root {stage}")',
         'priv(f"mv {stage} {NM_SETTIME_SUDOERS_PATH}")', 'priv(f"rm -f {NM_SETTIME_PATH}")',
         'priv(f"rm -f {NM_SETTIME_SUDOERS_PATH}")', 'priv(f"rm -f {_NM_SETTIME_SUDOERS_STAGE}")',
         'priv(f"stat -c \'%U:%G %a\' {path}")', 'priv(f"visudo -c -f {stage}")', 'priv(tee)',
         'wf.priv("apt-get install -y lrzsz")', 'wf.priv("apt-get install -y python3-pip")',
         'wf.priv("apt-get update -o Acquire::Retries=2")',
         'wf.priv("mkdir -p /etc/systemd/system.conf.d")', 'wf.priv("rfkill block bluetooth")',
         'wf.priv("rfkill unblock bluetooth")', 'wf.priv("systemctl daemon-reexec")',
         'wf.priv("systemctl daemon-reload")',
         'wf.priv("systemctl disable --now bluetooth hciuart")',
         'wf.priv("systemctl enable --now bluetooth")', 'wf.priv("systemctl enable log2ram")',
         'wf.priv("systemctl enable rnm-health")', 'wf.priv("systemctl enable rnm-status")',
         'wf.priv("systemctl restart rnm-status")', 'wf.priv("systemctl start rnm-health")',
         'wf.priv("udevadm control --reload-rules")',
         'wf.priv("udevadm trigger --subsystem-match=tty")',
         "wf.priv('tee /etc/systemd/system/rnm-health.service')",
         'wf.priv(f"dpkg -i {REMOTE_ASSET_DIR}/log2ram.deb")',
         'wf.priv(f"hostnamectl set-hostname {wf.profile.hostname}")',
         'wf.priv(f"systemctl enable {svc}")', 'wf.priv(f"systemctl start {svc}")',
         'wf.priv(f"timedatectl set-timezone {tz}")',
         "wf.priv(f'tee /etc/systemd/system/{svc}.service')",
         "wf.priv(f'tee {shlex.quote(path)}')")}
     | {("workflows/pi_reporter_push.py", s) for s in (
         '_priv(conn, c)', '_priv(conn, f"systemctl restart {SERVICE}")',
         'f"sudo -n {command}"', 'priv("systemctl daemon-reload")', 'priv(f"mkdir -p {d}")',
         "priv(f'tee {UNBUFFERED_DROPIN_PATH}')")}
     | {("provisioning/node_sudoers.py", s) for s in (
         '"sudo -n systemctl --version"', '"sudo -n systemctl is-active rnsd"',
         '"sudo -n visudo -c"', 'f"echo {b64} | base64 -d | sudo -n tee {_TMP_PATH} >/dev/null"',
         'f"sudo -n install -m 440 -o root -g root {_TMP_PATH} {SCOPED_PATH}"',
         'f"sudo -n rm -f {BLANKET_PATH}"', 'f"sudo -n rm -f {SCOPED_PATH}"',
         'f"sudo -n visudo -cf {_TMP_PATH}"')}
     | {("provisioning/gadget.py", '"sudo -n systemctl enable nodemedic-gadget-ip.service"'),
        ("provisioning/gadget.py", 'f"echo {b64} | base64 -d | sudo -n tee {path} > /dev/null"'),
        ("provisioning/uart_console.py", '"sudo -n systemctl enable serial-getty@ttyS0.service"'),
        ("provisioning/uart_console.py",
         'f"echo {b64} | base64 -d | sudo -n tee {path} > /dev/null"'),
        ("provisioning/decommission.py", '"sudo -n sync"'),
        ("provisioning/decommission.py",
         'f"sudo -n dd if=/dev/zero of={device} bs=1M count={int(megabytes)} " '
         'f"conv=fsync status=none"'),
        ("provisioning/decommission.py",
         'f"sudo -n dd if={device} bs=1M count={int(megabytes)} status=none " '
         'f"| tr -d \'\\\\000\' | wc -c"'),
        # the one-time, password-authenticated bootstrap of a NEW node, and its check
        ("provisioning/link.py", '"sudo -S -p \'\' bash -c "'),
        ("provisioning/link.py", '"sudo -n true"'),
        # location sharing edits a NODE's config and restarts ITS rnsd
        ("monitor/location_share.py", '"sudo -n systemctl restart rnsd"'),
        # a node's reporter sets its own clock through its own root helper
        ("monitor/node_time.py", '["sudo", "-n", SETTIME_HELPER, str(int(epoch))]')}),
    ("clone-child",
     "Sent to a NEW medic during the clone flow, before harden_new_medic locks "
     "it: it still has its card's NOPASSWD:ALL then. (The two lock steps say "
     "nothing on its screen: _QUIET_ON_NEW_MEDIC.)",
     {("workflows/clone.py", s) for s in (
         '"sudo -n "', 'f"sudo -n {cmd}"',
         '"sudo -n install -m 644 /tmp/nm-usb0.conf " '
         '"/etc/NetworkManager/conf.d/99-nodemedic-usb0.conf"',
         '"sudo -n timedatectl set-ntp true"', 'f"sudo -n date -u -s @{int(now)}"',
         'self.priv( "dd if=/tmp/nm-frame.raw of=/dev/fb0 bs=1M status=none")',
         'self.priv( "sh -c " + shlex.quote("printf \'%s\' " + shlex.quote(line) + " > /dev/tty1"))',
         'self.priv( "sh -c \'chvt 8; printf \\"\\\\033[?25l\\" > /dev/tty8; " '
         '"echo 0 > /sys/class/graphics/fbcon/cursor_blink\'")',
         'self.priv("chvt 1")',
         'wf.priv( f"install -d -m 755 -o root -g root {HARDENING_DIR} " '
         'f"{HARDENING_DIR}/sshd_config.d {HARDENING_DIR}/nftables")',
         'wf.priv( f"install -m {mode} -o root -g root {stage}/{dest} " f"{HARDENING_DIR}/{dest}")',
         'wf.priv( f"systemd-run --unit={unit} --collect --quiet /bin/bash " '
         'f"{HARDENING_DIR}/apply_all.sh --user {shlex.quote(user)} " f"--window {HARDEN_WINDOW_S}")',
         'wf.priv("rpi-eeprom-config")',
         'wf.priv("sh -c \'nohup sh -c \\"sleep 3; reboot\\" " ">/dev/null 2>&1 &\'")',
         'wf.priv(f"install -D -m 755 -o root -g root " f"/tmp/nm-prepare-card {PREPARE_CARD}")',
         'wf.priv(f"install -D -m 755 -o root -g root " f"/tmp/nm-radio-units {RADIO_HELPER}")',
         'wf.priv(f"sh -c \\"{script}\\"")',
         'wf.priv(offline_install_command("/tmp/nm-debs"))',
         'wf.priv(offline_install_command(cache))')}),
    ("full-sudo-fallback",
     "MedicRadioSetup on a medic WITHOUT the root radio helper (cloned before "
     "2026-10-08, still on full sudo). Installing a unit the app wrote, `sh -c` "
     "and systemd-run can never be argument-pinned — so they are deliberately NOT "
     "granted, and a scoped medic always has the helper road (MEDIC above).",
     {("workflows/medic_radio.py", s) for s in (
         'self._priv(cmd)',
         'self._priv( "sh -c \'systemctl daemon-reload && systemctl enable " '
         '"rnode-splitter.service rnsd.service lxmd.service && " '
         '"systemctl restart rnode-splitter.service\'")')}),
    ("unwired",
     "No screen reaches these (only tests call them): the old card-bake "
     "builders that prepare-card replaced, and card forensics. Each would be "
     "refused on a scoped medic — classify and grant before wiring one in.",
     {("provisioning/cable_birth.py", s) for s in (
         'f"echo {shlex.quote(b64)} | base64 -d | sudo python3 - " '
         'f"{shlex.quote(repo_root())} {shlex.quote(mnt)}"',
         'f"echo {shlex.quote(b64)} | base64 -d | sudo tee {shlex.quote(path)} >/dev/null"',
         'f"sudo ln -sf {shlex.quote(GADGET_SERVICE_PATH)} " f"{shlex.quote(mnt + _WANTS_LINK)}"',
         'f"sudo {card_mount.make_dir(boot_mnt)} && " f"sudo {card_mount.mount(boot, boot_mnt)}"',
         'f"sudo {card_mount.make_dir(root_mnt)} && " f"sudo {card_mount.mount(root, root_mnt)}"',
         'f"sudo mkdir -p {shlex.quote(mnt + nm_dir)}"',
         'f"sudo mkdir -p {shlex.quote(mnt)}{_WANTS_DIR}"',
         'f"sudo partprobe {shlex.quote(device_path)} 2>/dev/null; sleep 1"',
         'f"sudo sync && sudo umount {bq}"', 'f"sudo sync && sudo umount {rq}"')}
     | {("provisioning/card_forensics.py", s) for s in (
         'f"sudo -n {card_mount.make_dir(INSPECT_MOUNT)}"',
         'f"sudo -n mount -o ro,{card_mount.OPTIONS} {part} {INSPECT_MOUNT} 2>/dev/null"',
         'f"sudo -n umount {INSPECT_MOUNT} 2>/dev/null"')}
     | {("provisioning/pi_imager.py", s) for s in (
         '"sudo partprobe "',
         'f"echo {shlex.quote(b64)} | base64 -d | sudo tee " f"{mnt}/{name} >/dev/null"',
         'f"echo {shlex.quote(b64)} | base64 -d | sudo tee " f"{q}/{name} >/dev/null"',
         'f"echo {shlex.quote(toml_b64)} | base64 -d | sudo tee {mnt}/custom.toml >/dev/null"',
         'f"sudo {card_mount.make_dir(mnt)} && sudo {card_mount.mount(part, mnt)}"',
         'f"sudo partprobe {shlex.quote(device_path)} 2>/dev/null; sleep 1"',
         'f"sudo sync && sudo umount {mnt}"', 'f"sudo sync && sudo umount {q}"',
         'f"sudo touch {mnt}/ssh"', 'f"sudo touch {q}/ssh"')}
     | {("provisioning/rootfs_user.py", s) for s in (
         'f"echo {shlex.quote(b64)} | base64 -d | sudo python3 - {args}"',
         'f"sudo grep \'^{username}:\' {q}/etc/shadow | cut -d: -f1,2 | cut -c1-40"',
         'f"sudo ls -l {q}/etc/sudoers.d/010_{username}-nopasswd 2>/dev/null " '
         'f"|| echo \'NO sudoers drop-in\'"',
         'f"sudo ls -la {q}/home/{username}/.ssh/authorized_keys 2>/dev/null " '
         'f"|| echo \'NO authorized_keys\'"')}
     | {("provisioning/rootfs_wifi.py", s) for s in (
         'f"echo {shlex.quote(b64)} | base64 -d | sudo tee {target} >/dev/null"',
         'f"sudo chmod {mode} {target}"', 'f"sudo chown 0:0 {target}"',
         'f"sudo mkdir -p {shlex.quote(mnt + path.rsplit(\'/\', 1)[0])}"')}),
    ("wrapper",
     "The definition of a sudo-prefix helper; every CALL of it is listed by itself.",
     {("diagnostics/base.py", 'f"sudo -n {command}"'),
      ("workflows/gps_setup.py", 'f"sudo -n {cmd}"'),
      ("workflows/medic_radio.py", 'f"sudo -n {cmd}"')}),
    ("emulator",
     "The demo clone's emulated new medic (ui/app.py): nothing is run.",
     {("ui/app.py", '"sudo -n /usr/bin/true"')}),
]


def _classified():
    out = set(MEDIC) | set(CLONE_CHECKS)
    for _group, _why, sites in ELSEWHERE:
        out |= sites
    return out


def test_the_scan_still_finds_the_calls_it_was_written_for():
    found = privileged_sites()
    assert len(found) > 150, "the scan stopped matching the code's patterns"
    assert ("provisioning/brightness.py", '["sudo", "-n", "tee", target]') in found
    assert any(p == "workflows/build.py" and s.startswith("wf.priv(") for p, s in found)
    # prose is not a command
    assert not any("did not take" in s or "(sudo refused" in s for _p, s in found)


def test_every_privileged_call_is_accounted_for():
    found, known = privileged_sites(), _classified()
    new = sorted(found - known)
    gone = sorted(known - found)
    assert not new, (
        "privileged call(s) nobody has classified — if it runs on the medic "
        "itself, add its sudo argv to MEDIC and grant it EXACTLY in "
        "provisioning/sudoers.d/nodemedic; otherwise add it to ELSEWHERE with "
        "its reason:\n" + "\n".join(f"  {p}: {s}" for p, s in new))
    assert not gone, ("classified call(s) no longer in the code — update the "
                      "list:\n" + "\n".join(f"  {p}: {s}" for p, s in gone))
    groups = [set(sites) for _g, _w, sites in ELSEWHERE] + [set(MEDIC), set(CLONE_CHECKS)]
    total = sum(len(g) for g in groups)
    assert total == len(set().union(*groups)), "a call is classified twice"


@pytest.fixture(scope="module")
def policies(tmp_path_factory):
    out = {}
    for user in ("nodemedic", "pi"):
        path = str(tmp_path_factory.mktemp("policy") / f"sudoers-{user}")
        out[user] = (render(user, path), path)
    return out


def _for(user, argv):
    return [a.replace("{user}", user) for a in argv]


@pytest.mark.parametrize("user", ["nodemedic", "pi"])
def test_every_command_the_medic_runs_on_itself_is_granted(policies, user):
    policy = Policy(policies[user][0], user)
    refused = [(site, argv) for site, argvs in MEDIC.items() for argv in argvs
               if not policy.allows(_for(user, argv))]
    assert not refused, "the policy refuses what the app runs:\n" + "\n".join(
        f"  {s[0]}: {' '.join(a)}" for s, a in refused)
    for argv in KEEPER_ON_A_CLONE:
        assert policy.allows(argv), " ".join(argv)


@pytest.mark.parametrize("user", ["nodemedic", "pi"])
def test_the_clone_flows_two_sudo_checks_mean_what_they_say(policies, user):
    """harden_new_medic confirms only after a whitelisted command RUNS and an
    unlisted one is REFUSED on the new medic: the two must really be one of
    each under the policy it installs."""
    from workflows import clone
    policy = Policy(policies[user][0], user)
    assert clone.HARDEN_ALLOWED_PROBE == "sudo -n /usr/bin/ss -tlnp"
    assert clone.HARDEN_REFUSED_PROBE == "sudo -n /usr/bin/true"
    for (_path, _src), (argv, allowed) in CLONE_CHECKS.items():
        assert policy.allows(argv) is allowed, (argv, allowed)


#: Each must be REFUSED — the shapes of past escalations and their cousins.
_MUST_REFUSE = [
    ["/bin/bash"], ["/usr/bin/bash", "-c", "id"], ["/usr/bin/sh", "-c", "id"], ["/usr/bin/su"],
    ["/usr/bin/true"],
    ["/usr/bin/tee", "/etc/sudoers.d/zz"], ["/usr/bin/tee", "/etc/shadow"],
    ["/usr/bin/tee", "/tmp/nm_sd_boot/../../etc/sudoers.d/zz"],
    ["/usr/bin/tee", f"{_RUN}/sd_boot/../../../etc/sudoers.d/zz"],
    ["/usr/bin/systemctl", "restart", "ssh"],
    ["/usr/bin/systemctl", "restart", "reticulum-node-medic.service", "ssh"],
    ["/usr/bin/systemctl", "stop", "reticulum-node-medic.service"],
    ["/usr/bin/systemctl", "start", "--no-block", "ssh.service"],
    ["/usr/bin/systemctl", "enable", "rnode-splitter.service", "rnsd.service",
     "lxmd.service", "evil.service"],
    # dd: one card, never the medic's disk, never a second target
    ["/usr/bin/dd", "of=/dev/mmcblk0", "bs=4M", "conv=fsync", "status=progress"],
    ["/usr/bin/dd", "of=/dev/sda", "if=/tmp/x", "of=/etc/sudoers.d/zz", "bs=4M",
     "conv=fsync", "status=progress"],
    # setfacl: this user, one tty, nothing after it
    ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/ttyACM0", "/dev/mmcblk0"],
    ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/mmcblk0"],
    ["/usr/bin/setfacl", "-m", "u:root:rw", "/dev/ttyACM0"],
    ["/usr/bin/setfacl", "-m", "u:{user}:rw", "/dev/serial/by-id/x"],
    # usermod: this user, dialout, nothing else
    ["/usr/sbin/usermod", "-aG", "dialout", "root", "-p", "x"],
    ["/usr/sbin/usermod", "-aG", "dialout", "{user}", "-o", "-u", "0"],
    ["/usr/sbin/usermod", "-aG", "sudo", "{user}"],
    # dmesg: the one level list, no file to read
    ["/usr/bin/dmesg", "--level=err", "-F", "/etc/shadow"],
    ["/usr/bin/dmesg", "--level=emerg,alert,crit,err,warn", "-F", "/etc/shadow"],
    # the cable: only the clone's own peer address may be released
    ["/usr/bin/ip", "addr", "del", "10.55.0.2/29", "dev", "eth0"],
    ["/usr/bin/ip", "addr", "add", "10.0.0.1/8", "dev", "eth0"],
    # the radio helper: a by-id link only
    ["/usr/local/lib/nodemedic/radio-units", "--port", "/dev/ttyACM0"],
    ["/usr/local/lib/nodemedic/radio-units", "--handover"],
    # the medic's own disk, by any rule
    ["/usr/bin/mount", "/dev/mmcblk0p1", "/tmp/nm_sd_boot"],
    _mount("/dev/mmcblk0p1", "sd_boot"), _mount("/dev/mmcblk0p2", "piboot"),
    # nothing in /tmp any more (tests/test_card_mounts.py has the full set)
    ["/usr/bin/mount", "/dev/sda1", "/tmp/nm_sd_boot"],
    ["/usr/bin/mkdir", "-p", "/tmp/rnm-piboot"],
    ["/usr/bin/tee", "/tmp/rnm-piboot/custom.toml"], ["/usr/bin/touch", "/tmp/rnm-piboot/ssh"],
]


@pytest.mark.parametrize("user", ["nodemedic", "pi"])
def test_what_must_be_refused_is_refused(policies, user):
    policy = Policy(policies[user][0], user)
    allowed = [a for a in _MUST_REFUSE if policy.allows(_for(user, a))]
    assert not allowed, "granted but must not be:\n" + "\n".join(
        "  " + " ".join(_for(user, a)) for a in allowed)


#: Rules whose arguments still carry a wildcard — which spans arguments, so the
#: caller can append their own — and why each is harmless. A NEW wildcard
#: rule fails here until someone has thought it through and written it down.
REVIEWED_WILDCARDS = {
    "/usr/sbin/iw dev * scan":
        "iw has no way to write a file or run a program",
    "/usr/bin/nmcli connection modify * connection.autoconnect *":
        "edits NetworkManager connection settings only (no file, no program)",
    "/usr/bin/nmcli connection modify * connection.autoconnect * "
    "connection.autoconnect-priority *":
        "the same",
    "/usr/bin/nmcli device wifi rescan ssid *": "a Wi-Fi scan",
    "/usr/bin/nmcli device wifi connect *": "joins a network",
    "/usr/bin/nmcli -s -g 802-11-wireless-security.psk connection show *":
        "reads Wi-Fi keys, which the RTNode birth needs by design",
    "/usr/bin/timedatectl set-time *": "sets the clock",
    "/usr/bin/timedatectl set-timezone *": "sets the zone",
    "/usr/sbin/uhubctl": "USB port power only (no arguments = any; uhubctl writes no file)",
    "/usr/sbin/uhubctl -l * -p * -a *": "the same",
    "/usr/bin/sync": "flushes buffers (no arguments = any)",
    "/usr/sbin/partprobe /dev/sd*": "re-reads partition tables",
    "/usr/local/lib/nodemedic/prepare-card --device /dev/sd* --config /tmp/*":
        "the root helper re-checks its device itself and refuses the medic's disk",
    "/usr/bin/rpiboot": "pushes a boot payload to a Pi on USB (no arguments = any)",
    "/usr/bin/rpiboot -d *": "the same",
    "/usr/local/bin/rpiboot": "the same",
    "/usr/local/bin/rpiboot -d *": "the same",
    "/usr/bin/ip addr add 10.55.0.2/29 dev *": "one address; ip writes no file",
    "/usr/bin/ip addr del 10.55.0.1/29 dev *": "one address; ip writes no file",
    "/usr/bin/ip link set * up": "interface state; ip writes no file",
    "/usr/local/lib/nodemedic/radio-units --port /dev/serial/by-id/*":
        "the root helper accepts exactly `--port <by-id link>` and refuses the rest",
}


def test_every_spanning_wildcard_is_a_reviewed_one(policies):
    policy = Policy(policies["nodemedic"][0], "nodemedic")
    spanning = {c.spec for c in policy.commands if c.spans}
    new = sorted(spanning - set(REVIEWED_WILDCARDS))
    gone = sorted(set(REVIEWED_WILDCARDS) - spanning)
    assert not new, ("rule(s) whose wildcard lets the caller add arguments — "
                     "pin them, or review and list them here:\n  " + "\n  ".join(new))
    assert not gone, "listed but no longer in the policy:\n  " + "\n  ".join(gone)


def test_rendering_for_a_clone_moves_only_the_user(policies):
    """Only four lines name a user: the grant line, the account's Defaults,
    the setfacl ACL and the usermod group add. Nothing else may change — in
    particular not the paths that merely contain the word
    (/usr/local/lib/nodemedic/..., /run/nodemedic/...)."""
    original = policies["nodemedic"][0].splitlines()
    clone = policies["pi"][0].splitlines()
    assert len(original) == len(clone)
    changed = [(a, b) for a, b in zip(original, clone) if a != b]
    assert changed
    for a, b in changed:
        assert (a.startswith("nodemedic ALL=(root) NOPASSWD:")
                or a == "Defaults:nodemedic timestamp_timeout=0"
                or "setfacl -m u\\:nodemedic\\:rw" in a
                or a.strip() == "/usr/sbin/usermod -aG dialout nodemedic"), (a, b)
        assert a.replace("nodemedic", "pi") == b, (a, b)
    assert "/usr/local/lib/nodemedic/prepare-card" in policies["pi"][0]
    assert "/run/nodemedic/sd_boot" in policies["pi"][0]
    assert not any(ln.startswith("nodemedic ") for ln in clone)


def test_the_original_medics_policy_is_the_file_itself(policies):
    """Default user + the pinned panel: a re-run on the original medic installs
    exactly what is in the repo."""
    with open(TEMPLATE, encoding="utf-8") as fh:
        assert policies["nodemedic"][0] == fh.read()


@pytest.mark.parametrize("bad", ["", "Pi", "root;id", "-u", "1pi", "a b", "x" * 40,
                                 "pi\nnodemedic ALL=(ALL) NOPASSWD:ALL"])
def test_render_refuses_an_odd_user(tmp_path, bad):
    from tests.sudoersutil import RENDER
    out = tmp_path / "out"
    r = subprocess.run(["bash", RENDER, TEMPLATE, str(out), "panel_backlight@1", bad],
                       capture_output=True, text=True)
    if bad == "":
        # an empty 4th argument means the default user, nodemedic
        assert r.returncode == 0 and out.read_text() == open(TEMPLATE).read()
    else:
        assert r.returncode != 0 and not out.exists(), bad


@pytest.mark.skipif(shutil.which("visudo") is None and not os.path.exists("/usr/sbin/visudo"),
                    reason="visudo not installed here")
@pytest.mark.parametrize("user", ["nodemedic", "pi"])
def test_the_rendered_policy_parses(policies, user):
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    r = subprocess.run([visudo, "-cf", policies[user][1]], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_policy_names_no_rule_for_the_medics_own_disk(policies):
    for user in ("nodemedic", "pi"):
        policy = Policy(policies[user][0], user)
        assert not any("/dev/mmcblk0" in c.spec for c in policy.commands)


def test_the_matcher_follows_sudo_not_intuition():
    """The two sudoers(5) rules the test depends on, pinned."""
    assert Cmnd("/bin/cat /var/log/messages*").matches(
        ["/bin/cat", "/var/log/messages", "/etc/shadow"])          # the man page's own
    assert Cmnd("/usr/sbin/uhubctl").matches(["/usr/sbin/uhubctl", "-a", "off"])
    assert not Cmnd('/usr/bin/true ""').matches(["/usr/bin/true", "x"])
    assert Cmnd("/usr/bin/dd of=/dev/sd[a-z] bs=4M").matches(["/usr/bin/dd", "of=/dev/sdb", "bs=4M"])
    assert not Cmnd("/usr/bin/dd of=/dev/sd[a-z] bs=4M").matches(
        ["/usr/bin/dd", "of=/dev/sdb", "of=/x", "bs=4M"])
    assert Cmnd("/usr/bin/dmesg --level=emerg\\,err").matches(
        ["/usr/bin/dmesg", "--level=emerg,err"])
