#!/usr/bin/env python3
"""Prepare a freshly-imaged Raspberry Pi card. RUNS AS ROOT.

Installed to /usr/local/lib/nodemedic/prepare-card, owned by root, and granted
a single narrow NOPASSWD entry. Everything the birth flow needs to do to a card
happens HERE, in one root operation, instead of a dozen separate sudo calls.

WHY THIS EXISTS
---------------
The medic's sudo policy is a scoped allow-list — specific mounts, `tee` to named
files, `dd of=/dev/*`. Card preparation had grown past it: baking the USB-gadget
link, writing cloud-init seeds and activating the shipped-but-disabled account
all ran as `sudo python3 -` with a script piped in. Permitting THAT would mean
granting NOPASSWD for python3, which is unrestricted root by another name and
would quietly dismantle the allow-list. So: one root-owned program, one entry.

WHY IT DUPLICATES LOGIC FROM THE REPO
-------------------------------------
It deliberately imports NOTHING from ~/reticulum-tool. That tree is writable by
the `nodemedic` user, and a root program importing user-writable code is a
privilege escalation with extra steps — anyone who could edit the repo could
execute as root. The duplication IS the security boundary. Keep this file
self-contained and stdlib-only.

THE TRUST BOUNDARY IS HERE
--------------------------
Because sudo must allow arbitrary arguments, this script cannot trust its
caller. It validates the target device itself: refuses the medic's own system
disk, refuses anything that is not a present removable USB disk. A caller that
asks for /dev/mmcblk0 gets refused, not obeyed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

BOOT_MNT = "/tmp/nm-prep-boot"
ROOT_MNT = "/tmp/nm-prep-root"

GADGET_MODULES = "modules-load=dwc2,g_ether"
DWC2_OVERLAY = "dtoverlay=dwc2"

# Boards whose OTG port cannot work out on its own that it should be a DEVICE.
# Bare dwc2 leaves dr_mode=otg, which means "read the ID pin". A Pi Zero's
# micro-USB and a Pi 4/5's USB-C both have one. A Pi 3A+ exposes its OTG
# controller on a full-size USB-A socket, which has NO ID pin, so otg resolves
# to host every time. Watched live 2026-08-07: 3A+ powered and booting off a
# freshly written card, in a self-powered hub, rail steady at 4.92 V, and the
# medic logged not one USB event. dr_mode=peripheral overrides the pin.
# Kept in step with provisioning.gadget.DR_MODE_BY_BOARD (a test pins them
# together — this file is a standalone root helper and cannot import it).
DR_MODE_BY_BOARD = {
    "pi_3a_plus": "peripheral",
}


def dwc2_overlay_for(pi_key=""):
    mode = DR_MODE_BY_BOARD.get((pi_key or "").strip())
    return "%s,dr_mode=%s" % (DWC2_OVERLAY, mode) if mode else DWC2_OVERLAY
GADGET_UNIT_PATH = "/etc/systemd/system/nodemedic-gadget-ip.service"
WANTS_DIR = "/etc/systemd/system/multi-user.target.wants"
GADGET_USB_IP = "10.55.0.1"
USB_PREFIX = 29

GADGET_UNIT = f"""\
[Unit]
Description=USB gadget link static IP (Node Medic provisioning)
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sh -c 'for i in $(seq 1 20); do ip link show usb0 && exit 0; sleep 0.5; done; exit 0'
ExecStart=/sbin/ip addr replace {GADGET_USB_IP}/{USB_PREFIX} dev usb0
ExecStart=/sbin/ip link set usb0 up

[Install]
WantedBy=multi-user.target
"""

#: NetworkManager must not claim usb0. It manages every ethernet device it sees,
#: and claiming one FLUSHES the addresses already on it — so the static address
#: above is set and then silently wiped, leaving a link that never answers and a
#: card with nothing wrong-looking on it. Observed live 2026-08-06.
#: Duplicated from provisioning.gadget on purpose: this file imports nothing
#: from the user-writable repo (see the module docstring).
NM_CONF_DIR = "/etc/NetworkManager/conf.d"
NM_UNMANAGED_PATH = f"{NM_CONF_DIR}/99-nodemedic-usb0.conf"
NM_UNMANAGED_CONF = """\
# Node Medic provisioning link. usb0 carries a fixed point-to-point address set
# by nodemedic-gadget-ip.service; NetworkManager must not claim it, because
# claiming it flushes that address and the medic then waits for a link that
# will never answer.
[keyfile]
unmanaged-devices=interface-name:usb0
"""

PI_USER_GROUPS = ["adm", "dialout", "cdrom", "sudo", "audio", "video",
                  "plugdev", "games", "users", "input", "netdev", "gpio",
                  "i2c", "spi"]


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def fail(msg, code=2):
    print(f"PREPARE_FAIL: {msg}")
    sys.exit(code)


def say(msg):
    print(f"PREPARE: {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Target validation — the whole reason this runs as root and not the caller
# --------------------------------------------------------------------------- #

def system_disk() -> str:
    p = run(["findmnt", "-no", "SOURCE", "/"])
    src = (p.stdout or "").strip()
    if not src:
        return ""
    q = run(["lsblk", "-no", "PKNAME", src])
    pk = [l.strip() for l in (q.stdout or "").splitlines() if l.strip()]
    return pk[-1] if pk else os.path.basename(src).rstrip("0123456789p")


def assert_safe_target(device: str) -> None:
    """Refuse anything that isn't a present, removable USB disk."""
    name = os.path.basename((device or "").rstrip("/"))
    if not name:
        fail("no device given")
    sysd = system_disk()
    if name == sysd:
        fail(f"{device} is Node Medic's own system disk")
    p = run(["lsblk", "-dno", "NAME,TYPE,TRAN,RM", f"/dev/{name}"])
    if p.returncode != 0:
        fail(f"{device} is not a present block device")
    parts = (p.stdout or "").split()
    if len(parts) < 4:
        fail(f"could not read {device}")
    _n, dtype, tran, rm = parts[0], parts[1], parts[2], parts[3]
    if dtype != "disk":
        fail(f"{device} is a {dtype}, not a whole disk")
    if tran != "usb" and rm != "1":
        fail(f"{device} is neither USB nor removable — refusing")


def partition(device: str, n: int) -> str:
    return f"{device}p{n}" if device[-1].isdigit() else f"{device}{n}"


# --------------------------------------------------------------------------- #
# Boot partition: firstboot config + the USB-gadget link
# --------------------------------------------------------------------------- #

def cmdline_with_gadget(text: str) -> str:
    trailing = "\n" if text.endswith("\n") else ""
    tokens = text.split()
    if GADGET_MODULES in tokens:
        return text
    for i, tok in enumerate(tokens):
        if tok.startswith("modules-load="):
            have = tok[len("modules-load="):].split(",")
            for mod in ("dwc2", "g_ether"):
                if mod not in have:
                    have.append(mod)
            tokens[i] = "modules-load=" + ",".join(have)
            return " ".join(tokens) + trailing
    out, done = [], False
    for tok in tokens:
        out.append(tok)
        if tok == "rootwait" and not done:
            out.append(GADGET_MODULES)
            done = True
    if not done:
        out.append(GADGET_MODULES)
    return " ".join(out) + trailing



def cmdline_with_regdom(text: str, country: str) -> str:
    """*text* with the wireless regulatory domain set on the kernel command line.

    THE PIECE THAT WAS MISSING FOR MONTHS. On this image the Wi-Fi radio ships
    SOFT-BLOCKED by rfkill — "Wi-Fi disabled by radio killswitch" — and a
    blocked radio joins nothing however perfect its credentials. Every previous
    theory was about credentials, and the keyfile on the card was flawless
    (found on SolarLove, 2026-08-10, after two nodes went out mute).

    ``cfg80211.ieee80211_regdom=`` is what releases it, and putting it on the
    KERNEL COMMAND LINE is what makes the release survive a power cut: read at
    every boot, needing nothing to have shut down cleanly. It is what
    raspi-config's do_wifi_country writes — read out of raspi-config rather
    than guessed, then proven on the node (cold boot, phy0 soft=0, nothing run
    by hand).
    """
    trailing = "\n" if text.endswith("\n") else ""
    if not country:
        return text
    tokens = [t for t in text.split()
              if not t.startswith("cfg80211.ieee80211_regdom=")]
    tokens.append("cfg80211.ieee80211_regdom=" + country.upper())
    return " ".join(tokens) + trailing


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
    """The lines of *text* that apply to EVERY board, with their section.

    config.txt is sectioned. Lines before any header apply to all boards, and
    ``[all]`` returns to that state; anything under ``[pi5]``, ``[cm4]``,
    ``[cm5]``, ``[board-type=...]`` and friends applies only to those.
    """
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
    """Is the gadget overlay present AND actually in force for this board?

    THE BUG THIS EXISTS TO KILL (found on a real card, 2026-08-08). The old
    check scanned every line for ``dtoverlay=dwc2`` with no regard for which
    section it sat in. Raspberry Pi OS ships this in its stock config.txt::

        [cm5]
        dtoverlay=dwc2,dr_mode=host

    ``startswith("dtoverlay=dwc2,")`` matched it, so the writer concluded the
    card was already prepared and returned it untouched — on a Pi 3A+, for
    which a [cm5] line means precisely nothing, and saying dr_mode=HOST at
    that. cmdline.txt still got its half of the edit, so the card carried
    ``modules-load=dwc2,g_ether`` with no dwc2 in the device tree to bind to:
    the Pi booted perfectly and presented no USB device at all, which is
    indistinguishable from a bad cable and cost most of a bench session.

    Two things must both hold: the line must be OUR overlay (a bare
    ``dtoverlay=dwc2`` leaves dr_mode=otg, which reads an ID pin the 3A+'s
    USB-A does not have), and it must be in a section that applies here.
    """
    want = dwc2_overlay_for(pi_key)
    return want in config_txt_applies_to_all(text)


def _applicable_dwc2_line(text, base):
    """Index of a dwc2 overlay line IN FORCE for every board, or None.
    Only lines outside board-filtered sections count — a [cm5] line is not in
    force on a 3A+ and must never be treated as ours. (Kept in step with
    provisioning.gadget — a test pins the two copies together.)"""
    section = "all"
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if section == "all" and (s == base or s.startswith(base + ",")):
            return i
    return None


def config_txt_with_gadget(text: str, pi_key: str = "") -> str:
    overlay = dwc2_overlay_for(pi_key)
    if config_txt_has_gadget(text, pi_key):
        return text
    # UPGRADE IN PLACE, NEVER DUPLICATE — the 2026-08-08 rule the repo-side
    # copy learned and this one did not, until node ttt (2026-08-12) shipped
    # with dr_mode=host mid-file and a fresh peripheral block appended below
    # it: config.txt is last-entry-wins, so the append UNDID the build's
    # hand-back and a third node assembled mute.
    at = _applicable_dwc2_line(text, DWC2_OVERLAY)
    if at is not None:
        lines = text.splitlines(keepends=True)
        pad = lines[at][:len(lines[at]) - len(lines[at].lstrip())]
        nl = "\n" if lines[at].endswith("\n") else ""
        lines[at] = f"{pad}{overlay}{nl}"
        return "".join(lines)
    sep = "" if text.endswith("\n") or text == "" else "\n"
    # [all] re-opened deliberately: config.txt is sectioned, and an append that
    # lands under [cm5]/[pi5] silently applies to nothing on other boards.
    return (f"{text}{sep}\n# USB gadget ethernet (Node Medic provisioning link)\n"
            f"[all]\n{overlay}\n")


def write_boot(mnt: str, cfg: dict) -> None:
    for name in ("config.txt", "cmdline.txt"):
        if not os.path.isfile(os.path.join(mnt, name)):
            fail(f"{mnt} has no {name} — not a Pi boot partition")

    if cfg.get("birth_token"):
        # One-time identity proof: first contact reads this back and refuses
        # to provision a machine carrying a different card's token
        # (operator, 2026-08-14 — identity is proven, not assumed).
        _write(os.path.join(mnt, "nodemedic-birth-token"),
               cfg["birth_token"].strip() + "\n")
    if cfg.get("custom_toml"):
        _write(os.path.join(mnt, "custom.toml"), cfg["custom_toml"])
    if cfg.get("user_data"):
        _write(os.path.join(mnt, "user-data"), cfg["user_data"])
    if cfg.get("meta_data"):
        _write(os.path.join(mnt, "meta-data"), cfg["meta_data"])
    if cfg.get("network_config"):
        _write(os.path.join(mnt, "network-config"), cfg["network_config"])
    open(os.path.join(mnt, "ssh"), "a").close()      # enable sshd on first boot
    say("wrote first-boot config")

    if cfg.get("cable_link", True):
        # NOT fatal. A card without the cable link still boots and joins WiFi,
        # so a failure here degrades to "birth this one over WiFi" rather than
        # throwing away a good card. Account activation below IS fatal, because
        # without it the Pi boots and refuses every login.
        try:
            pi_key = cfg.get("pi_key", "")
            country = cfg.get("wifi_country", "AU")
            for name, fn in (("cmdline.txt", cmdline_with_gadget),
                             ("config.txt", config_txt_with_gadget)):
                q = os.path.join(mnt, name)
                before = open(q).read()
                after = (fn(before, pi_key) if name == "config.txt"
                         else fn(before))
                # AND THE REGULATORY DOMAIN, on the same file and in the same
                # breath. Without it the node's Wi-Fi radio stays rfkill-blocked
                # and joins nothing, which is not a credentials failure and was
                # mistaken for one for months.
                if name == "cmdline.txt":
                    after = cmdline_with_regdom(after, country)
                if after != before:
                    _write(q, after)
            # READ IT BACK. Both halves are needed and they fail independently:
            # cmdline.txt loads the modules, config.txt puts dwc2 in the device
            # tree for them to bind to. A card with only the first boots
            # perfectly and presents nothing — the 2026-08-08 bench failure.
            # Never again assert this from the fact that write() returned.
            missing = []
            cmdline_txt = open(os.path.join(mnt, "cmdline.txt")).read()
            if GADGET_MODULES not in cmdline_txt:
                missing.append("cmdline.txt: " + GADGET_MODULES)
            if country and "cfg80211.ieee80211_regdom=" not in cmdline_txt:
                missing.append("cmdline.txt: the wireless regulatory domain "
                               "(without it the radio stays blocked)")
            cfg_txt = open(os.path.join(mnt, "config.txt")).read()
            if not config_txt_has_gadget(cfg_txt, pi_key):
                missing.append("config.txt: " + dwc2_overlay_for(pi_key)
                               + " (in a section that applies to this board)")
            if missing:
                print("PREPARE_WARN: cable link INCOMPLETE — " + "; ".join(missing)
                      + ". The Pi will boot and join WiFi, but it will NOT appear "
                        "over the USB cable. Birth this one over WiFi.")
            elif not pi_key:
                # "I don't know which Pi this is" and "this Pi needs no dr_mode"
                # produced IDENTICAL output — a bare dtoverlay=dwc2 — and the
                # first of those is a silent failure on any board whose OTG port
                # has no ID pin to read. A Pi 3A+ written this way booted
                # perfectly and presented nothing (2026-08-08). Say which case
                # this is.
                print("PREPARE_WARN: no Pi model was given, so the gadget "
                      "overlay was written WITHOUT a dr_mode. That is correct "
                      "for a Zero/4/5 (their OTG ports have an ID pin) and "
                      "WRONG for a 3A+, whose USB-A socket has none — it will "
                      "boot fine and never appear over the cable.")
                say("baked the USB-cable link (no dr_mode — model unknown)")
            else:
                say("baked the USB-cable link")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: cable link not baked ({exc}) — "
                  f"this card can still be birthed over WiFi")


def _write(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# Root partition: the gadget service + turning the shipped account back on
# --------------------------------------------------------------------------- #

def write_rootfs(mnt: str, cfg: dict) -> None:
    if not os.path.isdir(os.path.join(mnt, "etc")):
        fail(f"{mnt} has no /etc — not a Pi root partition")

    if cfg.get("cable_link", True):
        try:
            _write(os.path.join(mnt, GADGET_UNIT_PATH.lstrip("/")), GADGET_UNIT)
            wants = os.path.join(mnt, WANTS_DIR.lstrip("/"))
            os.makedirs(wants, exist_ok=True)
            link = os.path.join(wants, "nodemedic-gadget-ip.service")
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            # a cold filesystem has no systemd to `enable`, so make the
            # wants-link by hand — exactly what enable would have done
            os.symlink(GADGET_UNIT_PATH, link)
            say("installed the gadget link service")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: gadget service not installed ({exc})")

        # Without this the service above is pointless: NetworkManager claims
        # usb0 the moment it appears and flushes the address the unit just set.
        try:
            os.makedirs(os.path.join(mnt, NM_CONF_DIR.lstrip("/")), exist_ok=True)
            _write(os.path.join(mnt, NM_UNMANAGED_PATH.lstrip("/")),
                   NM_UNMANAGED_CONF)
            say("told NetworkManager to leave the gadget link alone")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: NetworkManager may claim usb0 and wipe the "
                  f"link address ({exc})")

    write_wifi(mnt, cfg.get("wifi_ssid", ""), cfg.get("wifi_psk", ""),
               cfg.get("wifi_country", ""))

    user = cfg.get("user") or ""
    if not user:
        return
    activate_account(mnt, user, cfg.get("pwhash", ""), cfg.get("keys") or [])


#: Mirrors provisioning/rootfs_wifi.py. This helper runs as ROOT and
#: deliberately imports nothing from the repo — that is the privilege boundary —
#: so the content is duplicated here on purpose. A test pins the two together so
#: they cannot drift.
NM_DIR = "etc/NetworkManager/system-connections"
WPA_CONF = "etc/wpa_supplicant/wpa_supplicant.conf"


def wifi_connection_text(ssid: str, psk: str) -> str:
    """A NetworkManager keyfile for this network."""
    import uuid as _uuid
    uid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "nodemedic-wifi-" + ssid))
    return "\n".join([
        "[connection]", "id=" + ssid, "uuid=" + uid, "type=wifi",
        "autoconnect=true", "",
        "[wifi]", "mode=infrastructure", "ssid=" + ssid, "",
        "[wifi-security]", "key-mgmt=wpa-psk", "psk=" + psk, "",
        "[ipv4]", "method=auto", "",
        "[ipv6]", "method=auto", "addr-gen-mode=default", "",
    ])


def wifi_country_text(country: str) -> str:
    return "\n".join([
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev",
        "update_config=1",
        "country=" + country.upper(),
        "",
    ])


def write_wifi(mnt: str, ssid: str, psk: str, country: str) -> None:
    """Put the Wi-Fi ON the card, because asking the image to do it is a no-op.

    custom.toml's [wlan] and cloud-init's network-config are both inert on this
    image — the same reason activate_account exists. Two cards in a row carried
    correct Wi-Fi details and joined nothing (2026-08-09).

    NetworkManager REFUSES a connection file that is not 0600 root-owned, and
    complains only in its own log, so the permissions are the feature. The
    country matters too: 5 GHz is unusable until the regulatory domain is set,
    and a card aimed at a 5 GHz-only network joins nothing without it.
    """
    if not ssid:
        return                          # cable-birth cards carry no PSK at all
    safe = ssid.replace("/", "_")
    # SEPARATE ATTEMPTS ON PURPOSE. These were one try/except, and running the
    # installed helper against a real filesystem showed why that is wrong: a
    # failing chown aborted the rest, so the country file was never written AND
    # a connection file was left behind with the wrong owner. NetworkManager
    # ignores such a file without a word — a decoy is worse than nothing, since
    # the next person finds "the Wi-Fi is on the card" and believes it.
    try:
        d = os.path.join(mnt, NM_DIR)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, safe + ".nmconnection")
        _write(path, wifi_connection_text(ssid, psk))
        os.chmod(path, 0o600)
        os.chown(path, 0, 0)
        say("wrote the Wi-Fi connection onto the card (" + safe + ")")
        # AND TELL NETWORKMANAGER ITS RADIO IS ON. Its own log on the failing
        # node read "Wi-Fi disabled by radio killswitch; disabled by state
        # file" — two separate refusals, and this is the second. The regdom on
        # the kernel command line unblocks the hardware; this stops NM
        # remembering a previous "wireless off" and quietly leaving it there.
        try:
            nmd = os.path.join(mnt, "var/lib/NetworkManager")
            os.makedirs(nmd, exist_ok=True)
            st = os.path.join(nmd, "NetworkManager.state")
            _write(st, "[main]\nNetworkingEnabled=true\n"
                       "WirelessEnabled=true\nWWANEnabled=true\n")
            os.chown(st, 0, 0)
            say("told NetworkManager its Wi-Fi radio is enabled")
        except Exception as exc:                        # noqa: BLE001
            print("PREPARE_WARN: could not set NetworkManager's radio state "
                  "(" + str(exc) + ") - the node may come up with Wi-Fi off")
    except Exception as exc:                            # noqa: BLE001
        # Leave NO half-made file: unreadable-by-NM is indistinguishable from
        # working right up until the node fails to appear.
        try:
            os.remove(path)
        except Exception:                               # noqa: BLE001
            pass
        print("PREPARE_WARN: could not write the Wi-Fi connection onto the card "
              "(" + str(exc) + ") - the node will NOT join Wi-Fi")
    if not country:
        return
    try:
        wc = os.path.join(mnt, WPA_CONF)
        os.makedirs(os.path.dirname(wc), exist_ok=True)
        _write(wc, wifi_country_text(country))
        os.chmod(wc, 0o600)
        os.chown(wc, 0, 0)
    except Exception as exc:                            # noqa: BLE001
        print("PREPARE_WARN: could not set the wireless country on the card "
              "(" + str(exc) + ") - 5 GHz networks will be unusable")


def activate_account(mnt: str, user: str, pwhash: str, keys: list) -> None:
    """Turn the image's shipped-but-DISABLED account into a usable login.

    Raspberry Pi OS ships `pi` with a nologin shell and a locked password; the
    first-boot mechanisms that would enable it are inert on this image. Without
    this the Pi boots and refuses every login.
    """
    pw_file = os.path.join(mnt, "etc/passwd")
    lines = open(pw_file).read().splitlines(True)
    uid = gid = None
    found = False
    for i, line in enumerate(lines):
        parts = line.rstrip("\n").split(":")
        if len(parts) < 7 or parts[0] != user:
            continue
        found, uid, gid = True, parts[2], parts[3]
        if parts[6] != "/bin/bash":
            parts[6] = "/bin/bash"
            lines[i] = ":".join(parts) + "\n"
        break
    if not found:
        fail(f"no '{user}' account in the image")
    _write(pw_file, "".join(lines))

    if pwhash:
        sh_file = os.path.join(mnt, "etc/shadow")
        lines = open(sh_file).read().splitlines(True)
        days = str(int(time.time() // 86400))
        seen = False
        for i, line in enumerate(lines):
            parts = line.rstrip("\n").split(":")
            if not parts or parts[0] != user:
                continue
            seen = True
            parts[1], parts[2] = pwhash, days
            lines[i] = ":".join(parts) + "\n"
            break
        if not seen:
            lines.append(f"{user}:{pwhash}:{days}:0:99999:7:::\n")
        _write(sh_file, "".join(lines))
        os.chmod(sh_file, 0o640)

    if keys:
        home = os.path.join(mnt, "home", user)
        ssh_dir = os.path.join(home, ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        ak = os.path.join(ssh_dir, "authorized_keys")
        existing = open(ak).read().splitlines() if os.path.exists(ak) else []
        merged = list(existing)
        for k in keys:
            if k.strip() and k.strip() not in [e.strip() for e in merged]:
                merged.append(k.strip())
        _write(ak, "\n".join(merged) + "\n")
        os.chmod(ssh_dir, 0o700)
        os.chmod(ak, 0o600)
        if uid is not None:
            # sshd ignores an authorized_keys the account does not own
            for p in (home, ssh_dir, ak):
                os.chown(p, int(uid), int(gid))

    # BuildWorkflow runs every privileged step with `sudo -n`; Pi OS writes this
    # during the first-boot setup we are replacing.
    sd = os.path.join(mnt, "etc/sudoers.d")
    if os.path.isdir(sd):
        f = os.path.join(sd, f"010_{user}-nopasswd")
        _write(f, f"{user} ALL=(ALL) NOPASSWD: ALL\n")
        os.chmod(f, 0o440)          # sudo refuses to run at all if group-writable
        os.chown(f, 0, 0)
    say(f"activated the '{user}' account")


# --------------------------------------------------------------------------- #

def mount(part: str, mnt: str) -> None:
    os.makedirs(mnt, exist_ok=True)
    p = run(["mount", part, mnt])
    if p.returncode != 0:
        fail(f"could not mount {part}: {(p.stderr or p.stdout).strip()[:120]}")


def umount(mnt: str) -> None:
    run(["sync"])
    run(["umount", mnt])


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare an imaged Pi card (root)")
    ap.add_argument("--device", required=True)
    ap.add_argument("--config", required=True,
                    help="path to a JSON file with the card configuration")
    a = ap.parse_args()

    if os.geteuid() != 0:
        fail("must run as root")

    assert_safe_target(a.device)
    try:
        cfg = json.load(open(a.config))
    except Exception as exc:                            # noqa: BLE001
        fail(f"unreadable config: {exc}")

    run(["partprobe", a.device])
    time.sleep(1)

    boot, root = partition(a.device, 1), partition(a.device, 2)
    mount(boot, BOOT_MNT)
    try:
        write_boot(BOOT_MNT, cfg)
    finally:
        umount(BOOT_MNT)

    mount(root, ROOT_MNT)
    try:
        write_rootfs(ROOT_MNT, cfg)
    finally:
        umount(ROOT_MNT)

    print("PREPARE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
