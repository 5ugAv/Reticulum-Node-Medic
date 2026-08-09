"""Read a node's SD card and say whether it needs writing again.

THE QUESTION THIS ANSWERS. A Pi that will not come up leaves the operator with
nothing to act on: the board looks the same whether its card is fine, half
written, or was never booted from at all. Operator, 2026-08-09, with a Pi that
enumerated on USB and then wedged: "can you diagnose the sd card from here, this
is worth doing if it wont connect, to let the user know if the sd needs to be
re imaged."

READ-ONLY, ALWAYS. Nothing here writes, formats or repairs. It reports, and the
decision to spend four minutes writing the card again stays with the operator —
the same rule pi_imager.card_status already follows, and for the same reason: a
card that looks blank has held the evidence of the previous night before now.

WHAT IT CAN SEE, AND WHAT IT CANNOT.

The boot partition is FAT and readable without root, so most of this comes free.
Raspberry Pi OS leaves a very legible trail there:

  * ``cmdline.txt`` ships with ``systemd.run=/boot/firstrun.sh``. First boot runs
    that script and REWRITES cmdline.txt without it. So the marker still being
    present is proof the first boot never completed — the single most useful
    fact on the whole card.
  * ``firstrun.sh`` itself is deleted by that same run.
  * ``ssh`` (the empty flag file that enables sshd) is consumed on first boot.

Those three agree with each other, which is what makes them trustworthy: one
could be a fluke, three is a story.

The root partition is ext4 and needs root to mount, which the medic's scoped
sudo may refuse. Every root-partition check is therefore BEST EFFORT and its
absence is reported as "couldn't look", never as "nothing there" — the
difference between those two is the whole value of the report.

Pure logic + an injected runner, so the reasoning is tested without a card.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

Runner = Callable[[str], str]

#: Where a boot partition is mounted to be read, when it is not already.
#:
#: THE MEDIC'S SUDO IS SCOPED, and it whitelists mount by its FULL COMMAND LINE
#: — "/usr/bin/mount * /tmp/nm_sd_boot" and nothing else. This module invented
#: its own mount point, so every mount was silently refused and the report came
#: back "couldn't look" against a perfectly good card (live, 2026-08-09).
#: Reusing sd_edit's path is not tidiness; it is the only path that is allowed.
from provisioning.sd_edit import SD_MOUNT as INSPECT_MOUNT

#: The marker Raspberry Pi OS puts in cmdline.txt and removes once first boot
#: has completed. Its presence is the clearest "this has never finished booting"
#: signal a card can give.
FIRSTRUN_MARKER = "systemd.run="


def _default_run(command: str) -> str:
    try:
        p = subprocess.run(["bash", "-lc", command], capture_output=True,
                           text=True, timeout=30)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:                                        # noqa: BLE001
        return ""


@dataclass
class Check:
    """One thing looked at, and what it means in the operator's terms."""
    key: str
    #: "ok" | "bad" | "unknown" — unknown means WE COULD NOT LOOK, which is not
    #: the same as a clean result and must never be rendered as one.
    state: str
    detail: str = ""


@dataclass
class CardReport:
    checks: List[Check] = field(default_factory=list)
    boot_partition: Optional[str] = None
    disk: Optional[str] = None

    def _states(self, key: str) -> str:
        for c in self.checks:
            if c.key == key:
                return c.state
        return "unknown"

    def _unread(self) -> bool:
        """Was the card there but unreadable? Only true when that was actually
        RECORDED. An absent check means the question never arose (diagnose only
        adds "readable" when the mount fails) — treating absence as failure made
        every report claim the card could not be read."""
        return any(c.key == "readable" and c.state == "unknown"
                   for c in self.checks)

    @property
    def is_a_pi_card(self) -> bool:
        return self._states("pi_card") == "ok"

    @property
    def booted(self) -> Optional[bool]:
        """True / False / None(unknown) — has anything ever booted this card?"""
        s = self._states("first_boot")
        return {"ok": True, "bad": False}.get(s)

    @property
    def needs_reimaging(self) -> Optional[bool]:
        """The operator's actual question. None when we genuinely cannot say —
        which is an answer too, and a more useful one than a guess."""
        if not self.is_present:
            return None                      # nothing to judge
        if self._unread():
            # THE RULE THIS MODULE EXISTS TO KEEP, broken by its own first live
            # run: a failed mount made is_a_pi_card False, which fell through to
            # "nothing bootable on it at all" and returned True. That would have
            # told the operator to wipe a card that was fine. Not being able to
            # look is never a verdict.
            return None
        if not self.is_a_pi_card:
            return True                      # nothing bootable on it at all
        if self._states("gadget") == "bad":
            return True                      # it can never present a USB gadget
        if self.booted is False:
            # It has never finished a first boot. That is not proof the card is
            # bad — it may simply never have been powered — so it is only a
            # "yes" once we also know it HAS been given power, which the card
            # cannot tell us. Left to the caller, who knows.
            return None
        if self._states("applied") == "bad":
            # Written but never applied — the failure that looks exactly like a
            # working card until the node fails to appear.
            return True
        if self.booted is True:
            return False
        return None

    @property
    def is_present(self) -> bool:
        return self._states("present") == "ok"

    @property
    def headline(self) -> str:
        # BEFORE anything about the card: is there one? Caught the moment this
        # first ran against an empty reader, which reported "This card has no
        # Raspberry Pi system on it" — a confident claim about a card that was
        # not there.
        if not self.is_present:
            return "There is no card in Node Medic's reader."
        if self._unread():
            if self.is_a_pi_card:
                return ("There is a Raspberry Pi system on this card, but Node "
                        "Medic could not read into it to check any further.")
            return ("Node Medic could not read this card — that is not the same "
                    "as the card being bad.")
        if not self.is_a_pi_card:
            return "This card has no Raspberry Pi system on it."
        if self._states("gadget") == "bad":
            return ("The system is there, but the cable-link settings are "
                    "missing — this card can never appear over USB.")
        if self.booted is False:
            return "The card is written, but nothing has ever booted from it."
        if self._states("applied") == "bad":
            return ("It has booted, but its Wi-Fi settings were never applied — "
                    "a first boot that started and did not finish.")
        if self.booted is True:
            return "This card has booted before — the system on it is sound."
        return "The card is written; whether it has ever booted is unclear."


def _read(run: Runner, path: str) -> Optional[str]:
    """File contents, or None when it is absent/unreadable. The distinction
    matters: absent is evidence, unreadable is not."""
    out = run(f"test -f {path} && cat {path} 2>/dev/null; echo __rc=$?")
    if "__rc=0" not in out:
        return None
    return out.rsplit("__rc=", 1)[0]


def _exists(run: Runner, path: str) -> Optional[bool]:
    out = run(f"test -e {path}; echo __rc=$?").strip()
    if "__rc=" not in out:
        return None
    return out.rsplit("__rc=", 1)[1].strip() == "0"


def first_boot_completed(cmdline_text: str, firstrun_present: Optional[bool],
                         ssh_flag_present: Optional[bool]) -> Optional[bool]:
    """Has a first boot run to completion on this card?

    Pure, and deliberately conservative: it says True or False only when the
    signals AGREE. Raspberry Pi OS rewrites cmdline.txt to drop the
    ``systemd.run=`` marker, deletes firstrun.sh, and consumes the ``ssh`` flag
    file — all during that first boot. One of those could be a quirk of an
    image; together they are a story.
    """
    if cmdline_text is None:
        return None
    marker = FIRSTRUN_MARKER in cmdline_text
    if marker:
        # The marker is the strongest single signal, and it is still there.
        return False
    # Marker gone. Corroborate before claiming a boot.
    votes = [v for v in (firstrun_present, ssh_flag_present) if v is not None]
    if votes and all(v is False for v in votes):
        return True
    if not votes:
        return True          # marker gone is itself the rewrite; nothing contradicts
    return None              # signals disagree — say so rather than pick one


def diagnose(run: Runner = _default_run, pi_key: str = "") -> CardReport:
    """Look at whatever node card is in the medic's reader and report.

    Never touches the medic's own disk — find_pi_boot_partition refuses to
    return one, and refuses entirely if it cannot tell which disk is the
    medic's.
    """
    from provisioning.sd_edit import find_pi_boot_partition

    rep = CardReport()
    part, disk = find_pi_boot_partition(run)
    rep.boot_partition, rep.disk = part, disk
    if not part:
        rep.checks.append(Check(
            "present", "bad",
            "No node card in Node Medic's reader — slide the card in and try "
            "again. (Node Medic's own disk is never inspected.)"))
        return rep
    rep.checks.append(Check("present", "ok", f"Card found at {part}."))

    # Use an existing mount if the desktop already auto-mounted it, so nothing
    # here needs root for the part that matters most.
    mnt = (run(f"findmnt -n -o TARGET {part} 2>/dev/null | head -1") or "").strip()
    mounted_here = False
    if not mnt:
        run(f"mkdir -p {INSPECT_MOUNT}")
        run(f"sudo -n mount -o ro {part} {INSPECT_MOUNT} 2>/dev/null")
        chk = (run(f"findmnt -n -o TARGET {part} 2>/dev/null | head -1") or "").strip()
        if chk:
            mnt, mounted_here = chk, True
    if not mnt:
        rep.checks.append(Check(
            "readable", "unknown",
            "The card is there, but Node Medic could not open its boot "
            "partition to read it — so nothing below could be checked. This "
            "says nothing about whether the card is good."))
        # Say what CAN still be said. lsblk already named the partitions, and
        # Raspberry Pi OS labels them "bootfs" and "rootfs" — weaker evidence
        # than reading the files, but real, and far better than silence.
        labels = (run(f"lsblk -n -o LABEL {disk and '/dev/' + disk or ''} "
                      "2>/dev/null") or "").lower()
        if "bootfs" in labels and "rootfs" in labels:
            rep.checks.append(Check(
                "pi_card", "ok",
                "Its partitions are labelled bootfs and rootfs, which is how a "
                "Raspberry Pi system is written — so there IS a system on it."))
        return rep

    try:
        cfg = _read(run, f"{mnt}/config.txt")
        cmdline = _read(run, f"{mnt}/cmdline.txt")
        if cfg is None or cmdline is None:
            rep.checks.append(Check(
                "pi_card", "bad",
                "No config.txt / cmdline.txt on it — this is not a Raspberry Pi "
                "boot card. It needs writing."))
            return rep
        rep.checks.append(Check("pi_card", "ok",
                                "A Raspberry Pi boot partition, as expected."))

        # THE CABLE LINK. Section-aware, because config.txt is sectioned and a
        # line under [cm5] applies to nothing on a 3A+ — one of the two silent
        # bake bugs that cost a night (see provisioning.gadget).
        try:
            from provisioning.gadget import config_txt_has_gadget
            ok_gadget = config_txt_has_gadget(cfg, pi_key)
        except Exception:                                    # noqa: BLE001
            ok_gadget = None
        if ok_gadget is None:
            rep.checks.append(Check("gadget", "unknown",
                                    "Couldn't check the cable-link settings."))
        elif ok_gadget:
            rep.checks.append(Check(
                "gadget", "ok",
                "The cable-link settings are on it — this card can appear over "
                "USB."))
        else:
            rep.checks.append(Check(
                "gadget", "bad",
                "The cable-link settings are missing or written under a section "
                "for a different Pi, so this card can never appear over USB. "
                "Write it again."))

        # WHO IS THIS CARD FOR, AND CAN IT EVER GET ON WI-FI?
        #
        # Both live in the same place — Raspberry Pi Imager's custom.toml, or
        # the firstrun.sh generated from it — and both are questions the
        # operator ends up asking a card in the dark. On 2026-08-09 a Pi ran
        # perfectly off its own supply and never appeared on the network, and
        # answering "is Wi-Fi even written on this card" took a LAN sweep and
        # twenty minutes, with the card by then back inside the Pi.
        toml = (_read(run, f"{mnt}/custom.toml")
                or _read(run, f"{mnt}/firstrun.sh") or "")
        name = ""
        for line in toml.splitlines():
            t = line.strip()
            if t.startswith("hostname") and "=" in t:
                name = t.split("=", 1)[1].strip().strip('"').strip("'")
                break
            if "set_hostname" in t and "'" in t:
                name = t.split("'")[1]
                break
        if name:
            rep.checks.append(Check("node_name", "ok",
                                    f"This card was written for '{name}'."))
        has_wifi = ("[wlan]" in toml or "ssid" in toml.lower()
                    or "wpa_supplicant" in toml)
        if not toml:
            rep.checks.append(Check(
                "wifi", "unknown",
                "Couldn't read the setup file, so whether Wi-Fi is configured "
                "is unknown."))
        elif has_wifi:
            rep.checks.append(Check(
                "wifi", "ok",
                "Wi-Fi details are on this card, so the node can come back on "
                "your network as well as over the cable."))
        else:
            rep.checks.append(Check(
                "wifi", "bad",
                "No Wi-Fi on this card — this node can ONLY ever be reached "
                "over the cable."))

        firstrun = _exists(run, f"{mnt}/firstrun.sh")
        sshflag = _exists(run, f"{mnt}/ssh")
        booted = first_boot_completed(cmdline, firstrun, sshflag)
        if booted is True:
            rep.checks.append(Check(
                "first_boot", "ok",
                "It has booted at least once: the first-run marker is gone from "
                "cmdline.txt, which only that boot removes."))
        elif booted is False:
            rep.checks.append(Check(
                "first_boot", "bad",
                "Nothing has ever finished booting from this card — cmdline.txt "
                "still carries its first-run marker. If the Pi has been powered "
                "and still got here, suspect the Pi or its power, not the "
                "writing."))
        else:
            rep.checks.append(Check(
                "first_boot", "unknown",
                "Whether it has booted is unclear — the signals on it disagree."))
    finally:
        if mounted_here:
            run(f"sudo -n umount {INSPECT_MOUNT} 2>/dev/null")

    # THE ROOT PARTITION: was the setup on the boot partition ever APPLIED?
    #
    # The two are different questions and they came apart on 2026-08-09. A card
    # said "Wi-Fi details are on it" AND "it has booted" — and the node never
    # appeared on the network. Both boot-partition facts were true; what they
    # cannot see is a first boot that STARTED, cleared its own marker, and then
    # died on a browning-out rail before writing the network config. Reading the
    # rootfs is the only way to tell "configured" from "configured and applied".
    _check_rootfs(run, rep, disk)
    return rep


#: Where Raspberry Pi OS keeps the network settings once first boot has applied
#: them. NetworkManager on Bookworm and later; wpa_supplicant before that.
_APPLIED_WIFI_PATHS = ("/etc/NetworkManager/system-connections",
                       "/etc/wpa_supplicant/wpa_supplicant.conf")


def _root_partition_for(run: Runner, disk: Optional[str]) -> Optional[str]:
    """The ext4 partition on the same card as the boot partition."""
    if not disk:
        return None
    out = run(f"lsblk -n -o PATH,FSTYPE /dev/{disk} 2>/dev/null")
    for line in out.splitlines():
        bits = line.split()
        if len(bits) >= 2 and bits[1].lower() in ("ext4", "ext3", "ext2"):
            return bits[0]
    return None


def _check_rootfs(run: Runner, rep: CardReport, disk: Optional[str]) -> None:
    part = _root_partition_for(run, disk)
    if not part:
        rep.checks.append(Check("applied", "unknown",
                                "No system partition found to look inside."))
        return
    mnt = (run(f"findmnt -n -o TARGET {part} 2>/dev/null | head -1") or "").strip()
    mounted_here = False
    if not mnt:
        run(f"mkdir -p {INSPECT_MOUNT}")
        run(f"sudo -n mount -o ro {part} {INSPECT_MOUNT} 2>/dev/null")
        chk = (run(f"findmnt -n -o TARGET {part} 2>/dev/null | head -1") or "").strip()
        if chk:
            mnt, mounted_here = chk, True
    if not mnt:
        rep.checks.append(Check(
            "applied", "unknown",
            "Couldn't open the system partition, so whether the settings were "
            "ever applied is unknown."))
        return
    try:
        found = []
        for path in _APPLIED_WIFI_PATHS:
            # A DIRECTORY IS NOT A FILE, and `test -s` on one always succeeds —
            # a directory has non-zero size whether or not anything is in it.
            # That single mistake made an EMPTY system-connections directory
            # read as "the Wi-Fi settings were applied", and the report told the
            # operator their card was fine while the node sat there unable to
            # join anything (live, 2026-08-09). A false OK is worse than no
            # answer: it ends the search.
            out = run(f"if [ -d {mnt}{path} ]; then "
                      f"  ls -A {mnt}{path} 2>/dev/null | head -3; "
                      f"elif [ -s {mnt}{path} ]; then echo __file; fi")
            if out.strip():
                found.append(path)
        if found:
            rep.checks.append(Check(
                "applied", "ok",
                "The Wi-Fi settings were applied to the system — this node "
                "should join your network when it is powered."))
        else:
            rep.checks.append(Check(
                "applied", "bad",
                "The Wi-Fi details are on the card but were NEVER APPLIED to "
                "the system. A first boot that starts and then dies — on a "
                "browning-out supply, say — clears its own marker without "
                "finishing the job. Give the Pi a supply of its own and write "
                "the card again."))
    finally:
        if mounted_here:
            run(f"sudo -n umount {INSPECT_MOUNT} 2>/dev/null")
