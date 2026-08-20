"""Adversarial emulated scenarios for the nRF52 RTNode-2400 birth pipeline.

Every test here EXECUTES the real step functions in workflows/rtnode_build.py
against ``_Bench`` — a device-level emulation of the medic's shell that models
by-id names (case-sensitively, like Linux), USB PIDs, tty re-enumeration on
every reset, the Makefile's ACTUAL refusal messages (read off the live medic's
~/RTNode-2400/Makefile, 2026-08-20), and rnodeconf's ACTUAL behaviour (read
off the medic's installed rnodeconf: refusals go through ``graceful_exit()``
whose default exit code is **0**, so text — never exit codes — is the truth).

The FINDINGS (F1-F6) these scenarios uncovered are all FIXED; every test here is a hard regression gate.

F1 (HIGH)   _onboard_techo's ``_techo_raw_port(wf) or raw`` fallback: when the
            re-resolve returns "" (which means EITHER "gone" OR "more than
            one"), the step silently falls back to the STALE tty. If a second
            nRF board grabbed the vacated number, the firmware hash and TNC
            params are WRITTEN TO THE WRONG BOARD and the step reports the
            birth board configured.
F2 (MED)    "EEPROM checksum mismatch" (a locked+corrupt EEPROM — the bench
            RAK's own test-residue state) is a dead end: -r and the -i
            fallback both die on the mismatch, and no rtnode_build path ever
            reaches ``rnodeconf --eeprom-wipe`` (which exists, but only in
            workflows/rnode_flash.wipe_for_rebirth). The failure message
            gives no recovery advice.
F3 (LOW)    After rnodeconf -r's hard reset moves the tty, the workflow keeps
            using the fresh port but never writes it back: profile
            .connection_port / radio.serial_port and the birth certificate's
            serial_port stay stale.
F4 (LOW/UX) ui.screens.birth_screen._STEP_SECONDS paces the ring with the
            ESP32 estimates (wifi_onboarding=2 s, verify_beacon=25 s) while
            the nRF path's usb_setup alone is legally ~11 minutes — the ring
            pins near-full for most of the birth. (No outer timeout kills a
            step; the lie is cosmetic, not fatal.)
F5 (LOW)    Two nRF boards present at onboarding/verify entry produce "Board
            vanished from USB" — a refusal with a FALSE diagnosis (nothing
            vanished; there is one board too many).
F6 (MED)    A provisioned board whose device signature does NOT validate (a
            foreign/unsigned EEPROM — e.g. provisioned by another machine's
            key) fails with "EEPROM bootstrap failed: ...a valid EEPROM was
            already present. No changes are being made." — the signature
            verdict that CAUSED the failure is never mentioned.

Notes (verified, not failures):
N1  For nrf_dfu targets the operator-pinned board_port only gates detect; the
    flash/onboard steps act on whatever single device the identity globs
    resolve. Safe today (the medic's own boards are Espressif and never match
    the nRF patterns) but worth knowing.
N2  rnodeconf refusals exit 0 (graceful_exit) — every ``pipefail`` around an
    rnodeconf call is decorative; the pipeline survives because it checks
    positive phrases. Keep it that way.
N3  A dual-CDC nRF board (two by-id interfaces → two ttys) is refused as
    "more than one board". No such board is in the fleet today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
import workflows.rtnode_build as rb
from workflows.rtnode_build import (
    RTNODE_TARGETS,
    RTNodeBuildWorkflow,
    _flash_techo,
    _onboard_techo,
    _techo_raw_port,
    _verify_techo,
)

DEVICE_HASH = "b9a932741fd128ac7dc137bc733b1b9a9189a71eca95fd4ce55d4836de8196bc"
IDENTITY = "aabbccddeeff00112233445566778899"
DST = "99887766554433221100ffeeddccbbaa"

NRF_DFU = {"0029", "002a", "0071"}

#: Real by-id spellings per target: app/boot one capital apart (bench-observed).
_NAMES = {
    "techo": dict(
        app="usb-LilyGO_T-Echo_RTNode-2400_4631000000000003-if00",
        boot="usb-LilyGo_T-Echo_v1_4631000000000003-if00",
        stock="usb-Nordic_Semiconductor_nRF52840_4631000000000003-if00",
        make_glob="LilyGo_T-Echo",
    ),
    "rak4631": dict(
        app="usb-RAKwireless_RAK4631_RTNode-2400_50B1F3C2D4A5-if00",
        boot="usb-RAKWireless_WisBlock_RAK4631_50B1F3C2D4A5-if00",
        stock="usb-RAKwireless_WisBlock_RAK4631_50B1F3C2D4A5-if00",
        make_glob="RAK4631",
    ),
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rb, "_sleep", lambda s: None)


@dataclass
class _Dev:
    """One physical nRF board as USB sees it."""
    byid: str
    tty: str
    pid: str
    mode: str                      # "app" | "boot" | "stock"
    tag: str = "birth"             # "birth" | "victim" | "bystander"
    kiss: bool = True              # app answers CMD_DETECT
    provisioned: bool = False
    stored_hash: str = ""
    # Device.h:142: decided once, at boot — False for every maiden board
    # until the reboot that follows the hash write.
    sig_validated_at_boot: bool = False
    # The hard reset fires a beat AFTER the host's rnodeconf exits: the dying
    # tty lingers on the bus until the next touch (RAK third birth,
    # 2026-08-20 — a wait-for-identity passed instantly against the old
    # entry and a 90 s KISS pin then starved on the dead number).
    reset_pending: bool = False
    tnc: bool = False
    checksum_ok: bool = True
    signature_ok: bool = True
    identity: str = IDENTITY
    dst: str = DST


class _Bench(EmulatedConnection):
    """The medic's shell during an nRF birth, at device level.

    Substring rules can't model state (the board renames itself, moves tty
    numbers, and changes PID at every reset), so ``run`` interprets the
    pipeline's actual command lines against a device list. Globs and greps
    match CASE-SENSITIVELY like the medic's Linux; ``grep -qi`` in a command
    is honoured case-insensitively so a -qi regression is FELT, not hidden.
    Unmatched commands fail loudly.
    """

    def __init__(self, target: str = "techo"):
        super().__init__(default_code=1, default_stdout="",
                         default_stderr="bench: unmatched")
        self.target_key = target
        self.names = _NAMES[target]
        self.devices: List[_Dev] = []
        self._next_acm = 5
        self.udev_override: Optional[str] = None   # fake udevadm pipeline out
        self.touch_noop = False                    # touch fails to enter DFU
        self.flash_result = None                   # (code, out, err) override
        self.r_response: Optional[Callable[[_Dev], tuple]] = None
        self.after_touch: Optional[Callable[[], None]] = None
        self.after_flash: Optional[Callable[[], None]] = None
        self.after_r_reset: Optional[Callable[[], None]] = None
        self.after_hash_set: Optional[Callable[[], None]] = None
        self.stale_cmds: List[str] = []    # tty-targeted cmds hitting no device
        self.wrong_writes: List[tuple] = []  # writes landing on tag != birth

    # -- device management ----------------------------------------------
    def add(self, mode: str, tty: str = "/dev/ttyACM1", tag: str = "birth",
            byid: str = "", **state) -> _Dev:
        pid = "002a" if mode == "boot" else "8029"
        d = _Dev(byid=byid or self.names[mode], tty=tty, pid=pid, mode=mode,
                 tag=tag, **state)
        self.devices.append(d)
        return d

    def dev_at(self, tty: str) -> Optional[_Dev]:
        for d in self.devices:
            if d.tty == tty:
                return d
        return None

    def move(self, d: _Dev) -> None:
        """Re-enumeration: the kernel hands out a new ttyACM number."""
        d.tty = f"/dev/ttyACM{self._next_acm}"
        self._next_acm += 1

    def _write(self, kind: str, d: _Dev) -> None:
        if d.tag != "birth":
            self.wrong_writes.append((kind, d.tty, d.tag))

    # -- the shell --------------------------------------------------------
    def run(self, cmd, timeout: int = 30, **k):
        self.history.append(cmd)

        # _techo_raw_port: existence-checked globs | readlink -f | sort -u
        if "for f in /dev/serial/by-id/" in cmd:
            pats = re.findall(r"/dev/serial/by-id/\*([^*]+)\*", cmd)
            ttys = sorted({d.tty for d in self.devices
                           if any(p in d.byid for p in pats)})
            return 0, "".join(t + "\n" for t in ttys), ""

        # _techo_wait: ls by-id | grep -q[i] 'pattern'
        if "/dev/serial/by-id/" in cmd and "grep -q" in cmd:
            m = re.search(r"grep -q(i?) '([^']+)'", cmd)
            insensitive, pat = bool(m.group(1)), m.group(2)
            hit = any((pat.lower() in d.byid.lower()) if insensitive
                      else (pat in d.byid) for d in self.devices)
            return (0 if hit else 1), "", ""

        # detect_board: pinned-port existence, or the broad glob ls
        if cmd.startswith("ls /dev/ttyACM* "):
            out = " ".join(d.tty for d in self.devices)
            return 0, out, ""
        m = re.match(r"^ls (/dev/\S+) 2>/dev/null$", cmd)
        if m and "*" not in m.group(1):
            d = self.dev_at(m.group(1))
            return 0, (m.group(1) if d else ""), ""

        # the PID question (the command pipes udevadm|grep|cut — no pipefail,
        # so a missing device yields empty output at exit 0, like real bash)
        if "udevadm info" in cmd:
            tty = re.search(r"-n (\S+)", cmd).group(1)
            if self.udev_override is not None:
                return 0, self.udev_override, ""
            d = self.dev_at(tty)
            return 0, (d.pid if d else ""), ""

        if "techo_touch.py" in cmd:
            tty = re.search(r"techo_touch\.py (\S+)", cmd).group(1)
            d = self.dev_at(tty)
            if d is None:
                self.stale_cmds.append(cmd)
                return 1, "", "could not open port"
            if d.mode in ("app", "stock") and not self.touch_noop:
                self._write("touch", d)
                d.mode, d.byid, d.pid = "boot", self.names["boot"], "002a"
            if self.after_touch:
                self.after_touch()
            return 0, "", ""

        if "kiss_detect.py" in cmd:
            tty = re.search(r"kiss_detect\.py (\S+)", cmd).group(1)
            d = self.dev_at(tty)
            if d is None:
                self.stale_cmds.append(cmd)
                return 1, "", "no answer"
            if d.reset_pending:
                # the deferred hard reset lands during the probe window: the
                # old tty dies under the probe and the board comes back on a
                # NEW number — a probe pinned to this port never succeeds
                self.move(d)
                d.reset_pending = False
                d.sig_validated_at_boot = True
                return 1, "", "no answer"
            ok = d.mode == "app" and d.kiss
            return (0 if ok else 1), ("detect: answered" if ok else ""), ""

        if "techo_bootlog.py" in cmd:
            tty = re.search(r"techo_bootlog\.py (\S+)", cmd).group(1)
            d = self.dev_at(tty)
            if d is None:
                self.stale_cmds.append(cmd)
                return 1, "", "no port"
            if d.mode != "app":
                return 0, "", ""
            self._write("bootlog-reset", d)        # CMD_RESET reboots the node
            if d.provisioned and d.stored_hash == DEVICE_HASH:
                return 0, (f"[RTNode] identity={d.identity} dst={d.dst}\n"
                           "RNS is READY!"), ""
            return 0, ("[ERR] RNS is inoperable because hardware is not "
                       "ready!"), ""

        if "partition_hashes from_device" in cmd:
            tty = re.search(r"from_device (\S+)", cmd).group(1)
            d = self.dev_at(tty)
            if d is None:
                self.stale_cmds.append(cmd)
                return 1, "", ""
            if d.mode != "app":
                return 1, "", ""
            return 0, DEVICE_HASH, ""

        if re.search(r"make (flash-techo|flash-rak4631)", cmd):
            return self._make_flash(cmd)
        if "make firmware-" in cmd:
            return 0, "Sketch uses 614308 bytes (75%)", ""

        if "rnodeconf" in cmd:
            return self._rnodeconf(cmd)

        return 1, "", f"bench: unmatched command: {cmd}"

    # -- make flash-*: the medic Makefile's real logic + refusals ---------
    def _make_flash(self, cmd):
        if self.flash_result is not None:
            return self.flash_result
        pat = self.names["make_glob"]
        matches = sorted((d for d in self.devices if pat in d.byid),
                         key=lambda d: d.byid)
        if self.target_key == "rak4631":
            if len(matches) != 1:
                return 1, (f"REFUSING: need exactly ONE RAK4631 on the bus "
                           f"(saw {len(matches)})\n"
                           "make: *** [Makefile:402: flash-rak4631] Error 1"), ""
            d = matches[0]
            if d.pid not in NRF_DFU:
                return 1, "\n".join([
                    f"REFUSING: {d.tty} is a RUNNING APP (PID {d.pid}), "
                    "not the bootloader.",
                    "The app and bootloader names differ by ONE capital "
                    "letter — the",
                    "PID is the truth. Double-tap RESET (or let the birth "
                    "touch it).",
                    "make: *** [Makefile:402: flash-rak4631] Error 1"]), ""
        else:
            if not matches:
                return 1, ("REFUSING: no T-Echo on the bus (double-tap RESET "
                           "for DFU)\n"
                           "make: *** [Makefile:444: flash-techo] Error 1"), ""
            d = matches[0]                            # the Makefile's head -1
            if d.pid not in NRF_DFU:
                # flash-techo has NO PID gate — adafruit-nrfutil hits the app
                return 1, ("Failed to upgrade target. Error is: Bootloader "
                           "version does not match\n"
                           "make: *** [Makefile:444: flash-techo] Error 1"), ""
        self._write("dfu-flash", d)
        d.mode, d.byid, d.pid = "app", self.names["app"], "8029"
        if self.after_flash:
            self.after_flash()
        return 0, (f"target : {d.tty}\nimage  : 619321 bytes\n"
                   "Activating new firmware\nDevice programmed."), ""

    # -- rnodeconf: real texts, real graceful_exit(0) ----------------------
    def _rnodeconf(self, cmd):
        tty = re.search(r"rnodeconf (\S+)", cmd).group(1)
        d = self.dev_at(tty)
        if d is None:
            # rnodeconf's port check ends in graceful_exit() — EXIT CODE 0.
            self.stale_cmds.append(cmd)
            return 0, "That port does not exist, exiting now.", ""
        if d.reset_pending:
            # rnodeconf opens the dying port and the board resets under it —
            # the exact 13:04:58 failure, exit 0 as always
            self.move(d)
            d.reset_pending = False
            d.sig_validated_at_boot = True
            return 0, ("Serial port opened, but RNode did not respond. "
                       "Is a valid firmware installed?"), ""
        if d.mode != "app" or not d.kiss:
            return 0, ("Serial port opened, but RNode did not respond. "
                       "Is a valid firmware installed?"), ""
        if "--eeprom-wipe" in cmd:
            # PROVEN LIVE 2026-08-19: the wipe does NOT parse the EEPROM
            # first — it cured exactly this corrupt state on the bench.
            self._write("eeprom-wipe", d)
            d.checksum_ok = True
            d.provisioned = False
            self.move(d)          # the board re-enumerates after the wipe
            return 0, "WARNING: EEPROM is being wiped!", ""
        if not d.checksum_ok:
            # the EEPROM parse dies before ANY operation — also exit 0
            return 0, "Opening serial port " + tty + "...\nEEPROM checksum mismatch", ""
        if "--firmware-hash" in cmd:
            self._write("firmware-hash", d)
            d.stored_hash = re.search(r"--firmware-hash (\S+)", cmd).group(1)
            if not d.sig_validated_at_boot:
                # Device.h:142 — device_save_firmware_hash() ends in
                # hard_reset() on every maiden birth; and it fires AFTER
                # this command returns, with the old tty lingering
                # (RAK births two and three, 2026-08-20).
                d.reset_pending = True
            if self.after_hash_set:
                self.after_hash_set()
            return 0, ("EEPROM checksum correct\nDevice signature validated\n"
                       "Firmware hash set"), ""
        if " -K -L" in cmd:
            return 0, (f"The target firmware hash is: "
                       f"{d.stored_hash or '0' * 64}\n"
                       f"The actual firmware hash is: {DEVICE_HASH}"), ""
        if " -r " in cmd:
            self._write("bootstrap", d)
            if self.r_response is not None:
                return self.r_response(d)
            if d.provisioned:
                # graceful_exit BEFORE the hard reset — no re-enumeration
                return 0, ("EEPROM bootstrap was requested, but a valid "
                           "EEPROM was already present.\n"
                           "No changes are being made."), ""
            d.provisioned = True
            if self.after_r_reset:                    # the trailing hard reset
                self.after_r_reset()
            return 0, ("Bootstrapping device EEPROM\n"
                       "EEPROM Bootstrapping successful!"), ""
        if " -T " in cmd:
            self._write("tnc-params", d)
            d.tnc = True
            return 0, "Device set to TNC operating mode", ""
        if " -i" in cmd:
            if d.signature_ok:
                return 0, ("EEPROM checksum correct\n"
                           "Device signature validated\n"
                           "Firmware version   : 1.85"), ""
            return 0, ("EEPROM checksum correct\n"
                       "Device signature validation failed"), ""
        return 1, "", f"bench: unmatched rnodeconf: {cmd}"


def _wf(bench: _Bench, board_port: str = "/dev/ttyACM1") -> RTNodeBuildWorkflow:
    return RTNodeBuildWorkflow(bench, NodeProfile(), target=bench.target_key,
                               board_port=board_port, node_name="stormtest",
                               gps_reader=lambda: None)


# =========================================================================
# Sanity: the bench itself carries a full happy birth on BOTH nRF targets
# (also scenario 5's "run every _techo_* against a RAK medic")
# =========================================================================

@pytest.mark.parametrize("target,start_mode", [
    ("techo", "stock"), ("techo", "app"), ("techo", "boot"),
    ("rak4631", "stock"), ("rak4631", "app"), ("rak4631", "boot"),
])
def test_full_pipeline_happy_path_per_target_and_start_mode(target, start_mode):
    """Scenario 5/9: the whole run_all completes from stock, app (rerun) and
    bootloader (mode-unknown rerun) starts, for the T-Echo AND the RAK4631 —
    the RAK's one-capital-apart names must ride the same globs/greps/waits."""
    bench = _Bench(target)
    d = bench.add(start_mode)
    wf = _wf(bench, board_port=d.tty)
    results = wf.run_all()
    failed = [r for r in results if not r.success and not r.skipped]
    assert not failed, f"{target}/{start_mode}: {[(r.name, r.message) for r in failed]}"
    assert d.provisioned and d.stored_hash == DEVICE_HASH and d.tnc
    assert not bench.wrong_writes and not bench.stale_cmds


# =========================================================================
# Scenario 1 — the tty number moves at every re-enumeration
# =========================================================================

def _moving_bench(target="techo"):
    """A bench where EVERY reset re-enumerates onto a new ttyACM number."""
    bench = _Bench(target)
    d = bench.add("app")
    orig_touch, orig_flash = bench.after_touch, bench.after_flash
    bench.after_touch = lambda: bench.move(d)
    bench.after_flash = lambda: bench.move(d)
    bench.after_r_reset = lambda: bench.move(d)
    return bench, d


def test_s1_every_command_follows_the_board_across_tty_moves():
    """Scenario 1: detect pins ttyACM1; the board moves on DFU entry, on the
    post-flash reboot and on the -r reset. Every probe/rnodeconf call must
    land on a port that currently holds the board — none may hit a stale pin."""
    bench, d = _moving_bench()
    wf = _wf(bench, board_port="/dev/ttyACM1")
    results = wf.run_all()
    failed = [r for r in results if not r.success and not r.skipped]
    assert not failed, [(r.name, r.message) for r in failed]
    assert bench.stale_cmds == [], (
        "commands acted on a tty the board had left: " + str(bench.stale_cmds))


def test_s1_profile_and_certificate_carry_the_final_port():
    """Scenario 1 (paperwork): after the moves, the profile and the birth
    certificate must name the port the board actually sits on — the cert is
    what a later PROBE/repair session reads first."""
    bench, d = _moving_bench()
    wf = _wf(bench, board_port="/dev/ttyACM1")
    wf.run_all()
    assert wf.profile.connection_port == d.tty, (
        f"profile says {wf.profile.connection_port}, board is on {d.tty}")
    assert wf.birth_certificate["serial_port"] == d.tty


# =========================================================================
# Scenario 2 — the board drops off USB at every await point
# =========================================================================

def test_s2_vanish_after_touch_fails_in_flash_with_true_advice():
    """Scenario 2a: the board dies right after the 1200-baud touch. The flash
    step must fail (not hang, not lie) and hand the operator a real action."""
    bench = _Bench("techo")
    bench.add("app")
    bench.after_touch = lambda: bench.devices.clear()
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success and res.name == "flash_firmware"
    assert "RESET" in res.message and "build again" in res.message


def test_s2_vanish_after_dfu_write_is_attributed_to_the_flash_step():
    """Scenario 2b: DFU completes but the app never re-enumerates (crash-loop
    or yanked cable). flash_firmware must own the failure honestly."""
    bench = _Bench("techo")
    bench.add("app")
    bench.after_flash = lambda: bench.devices.clear()
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success and res.name == "flash_firmware"
    assert "did not come back" in res.message


def test_s2_vanish_before_onboarding_is_named_as_vanished():
    """Scenario 2c: the board is gone by the time usb_setup starts."""
    bench = _Bench("techo")                        # no devices at all
    res = _onboard_techo(_wf(bench))
    assert not res.success and res.name == "wifi_onboarding"
    # The fix improved on the original expectation: the strict resolver
    # refuses honestly instead of saying a bare "vanished".
    assert "not on usb right now" in res.message.lower()
    assert not bench.stale_cmds


def test_s2_vanish_during_the_r_reset_says_the_identity_survived():
    """Scenario 2d: -r landed, the trailing hard reset never came back. The
    message claims 'its identity is already saved; the retry is safe' — that
    claim must be TRUE (the bootstrap really did land before the drop)."""
    bench = _Bench("techo")
    d = bench.add("app")
    bench.after_r_reset = lambda: bench.devices.clear()
    res = _onboard_techo(_wf(bench))
    assert not res.success
    assert "not on USB after provisioning" in res.message
    assert "already written is saved" in res.message
    assert d.provisioned, "the message promises a saved identity — verify it"


def test_s2_vanish_between_hash_and_params_fails_and_names_the_port_state():
    """Scenario 2e: the board disappears after 'Firmware hash set' and before
    -T. The stale-port rnodeconf answers 'That port does not exist' (at exit
    0 — graceful_exit); the positive TNC check must still fail the step and
    surface that text so the cause is readable."""
    bench = _Bench("techo")
    bench.add("app")
    bench.after_hash_set = lambda: bench.devices.clear()
    res = _onboard_techo(_wf(bench))
    assert not res.success
    # The fix improved on this test's original expectation: the failure is
    # now the strict resolver's honest port-state refusal, not a params
    # failure blamed on a board that had merely vacated its tty.
    # The hash-set hard reset (Device.h:142) is now waited out via
    # _techo_wait, so a board that truly vanishes there fails at THAT wait
    # with the honest "did not return" message — and rnodeconf never
    # touches the stale tty (stronger than the original "does not exist"
    # expectation).
    assert "not on USB after the firmware-hash write" in res.message
    assert "already written is saved" in res.message
    assert not bench.stale_cmds


def test_s2_vanish_before_verify_is_named_as_vanished():
    """Scenario 2f: gone before verify_usb."""
    bench = _Bench("techo")
    res = _verify_techo(_wf(bench))
    assert not res.success and res.name == "verify_beacon"
    # The fix improved on this test's original expectation: the strict
    # resolver now refuses with the honest port-state message instead of
    # a bare "vanished".
    assert "not on usb right now" in res.message.lower()


# =========================================================================
# Scenario 3 — udevadm returns empty / garbage / a second line
# =========================================================================

@pytest.mark.parametrize("udev_out", ["", "zz9z", "8029\n002a", "  "])
def test_s3_unreadable_pid_fails_safe_toward_the_touch(udev_out):
    """Scenario 3: an unreadable PID must be read as 'running app' — the
    touch is harmless to a board already in the bootloader, while skipping
    the touch aims DFU at a running app. The flash must still succeed."""
    bench = _Bench("techo")
    bench.udev_override = udev_out
    d = bench.add("app")

    real_touch = bench.after_touch
    def _clear_override():
        bench.udev_override = None     # the fresh DFU device reads honestly
    bench.after_touch = _clear_override

    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert res.success, res.message
    assert any("techo_touch.py" in c for c in bench.history), (
        "an undecidable PID must fall to the safe side: touch first")


def test_s3_a_lying_boot_pid_on_a_running_app_dies_in_the_makefile_gate():
    """Scenario 3 (dangerous direction): udevadm claims 002a for a board that
    is actually a running app. The touch is skipped — the Makefile's own PID
    gate is the second wall, and its REFUSING text must reach the operator
    verbatim (rak4631; flash-techo has no PID gate and fails in nrfutil)."""
    bench = _Bench("rak4631")
    bench.udev_override = "002a"
    bench.add("app")                                  # truly a running app
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "REFUSING" in res.message and "RUNNING APP" in res.message
    assert not any("techo_touch.py" in c for c in bench.history)


# =========================================================================
# Scenario 4 — kiss passes, rnodeconf -r refuses with its REAL texts
# =========================================================================

def test_s4_checksum_mismatch_is_auto_cured_by_a_wipe():
    """The corrupt-checksum EEPROM (our own 2026-08-19 bench residue state)
    used to be a dead end: rnodeconf refuses -r AND -i, exit code 0, and no
    pipeline path reached the wipe. The pipeline now wipes ONCE — proven safe
    live: the wipe does not parse the store first, and a corrupt store's
    identity is already unreadable so nothing that exists is lost — retries
    the bootstrap, and the success message says a wipe happened."""
    bench = _Bench("rak4631")
    bench.add("app", checksum_ok=False, provisioned=True)
    res = _onboard_techo(_wf(bench))
    assert res.success, res.message
    assert "wiped" in res.message.lower()
    assert "corrupt" in res.message.lower()
    assert any("--eeprom-wipe" in c for c in bench.history), (
        "the cure must actually run")


def test_s4_persistent_corruption_names_what_was_tried():
    """If the store is corrupt AGAIN after the wipe, the failure must say the
    wipe already happened — otherwise the operator loops 'build again' on a
    board whose flash itself is suspect."""
    bench = _Bench("rak4631")
    d = bench.add("app", checksum_ok=False, provisioned=True)
    real_move = bench.move
    def sticky(dev):
        real_move(dev)
        dev.checksum_ok = False            # the wipe does not stick
    bench.move = sticky
    res = _onboard_techo(_wf(bench))
    assert not res.success
    low = res.message.lower()
    assert "wiped" in low or "wipe" in low, (
        f"dead-end message with no history of the cure: {res.message!r}")

def test_s4_signature_failure_is_named_not_masked_by_the_r_refusal():
    """Scenario 4b: a provisioned board whose signature does NOT validate
    (foreign EEPROM — e.g. bootstrapped by another machine's signing key).
    -r refuses as 'already present'; the -i check fails on the signature.
    The failure message must mention the SIGNATURE, not just quote -r's
    benign-sounding refusal inside a red box."""
    bench = _Bench("techo")
    bench.add("app", provisioned=True, signature_ok=False)
    res = _onboard_techo(_wf(bench))
    assert not res.success
    assert "signature" in res.message.lower(), res.message


def test_s4_garbled_bootstrap_output_recovers_through_the_read_back():
    """Scenario 4c: -r output arrives garbled (serial noise, cut line) on a
    board that is actually fine. The -i read-back must rescue the run and the
    step must complete."""
    bench = _Bench("techo")
    d = bench.add("app", provisioned=True)
    bench.r_response = lambda dev: (0, "Opening serial p\x00rt /dev/ttyA", "")
    res = _onboard_techo(_wf(bench))
    assert res.success, res.message
    assert d.stored_hash == DEVICE_HASH and d.tnc


# =========================================================================
# Scenario 5 — the RAK-vs-techo split: globs, case, sort -u
# =========================================================================

def test_s5_case_trap_a_failed_touch_is_not_read_as_bootloader():
    """Scenario 5: stock 'RAKwireless_WisBlock' vs bootloader 'RAKWireless_
    WisBlock' — ONE capital apart (the trap that bit twice). If the touch
    fails to enter DFU, the case-sensitive wait must say exactly that; a
    case-insensitive grep would read the stock app as in-bootloader and die
    later in the Makefile with a misleading message."""
    bench = _Bench("rak4631")
    bench.touch_noop = True
    bench.add("stock")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "touch didn't reach the bootloader" in res.message, res.message


def test_s5_sort_u_collapses_a_double_matched_single_board():
    """Scenario 5: a by-id name matching TWO patterns (an app rename that
    kept the bootloader's capitalisation) must resolve to ONE port — sort -u
    collapses the duplicate rather than double-counting the board into the
    more-than-one refusal."""
    bench = _Bench("techo")
    bench.add("app",
              byid="usb-LilyGo_T-Echo_RTNode-2400_4631000000000003-if00")
    assert _techo_raw_port(_wf(bench)) == "/dev/ttyACM1"


def test_s5_a_dual_interface_board_is_refused_not_guessed():
    """Scenario 5 (documented behaviour, N3): a single physical board that
    exposes two CDC interfaces (two by-id links, two ttys) reads as two
    boards and is refused. Refusal beats acting on if01 by luck — but the
    'unplug the others' advice would confuse; noted for the day such a board
    joins the fleet."""
    bench = _Bench("techo")
    bench.add("app", tty="/dev/ttyACM1",
              byid="usb-LilyGO_T-Echo_RTNode-2400_AAAA-if00")
    bench.add("app", tty="/dev/ttyACM2",
              byid="usb-LilyGO_T-Echo_RTNode-2400_AAAA-if02")
    assert _techo_raw_port(_wf(bench)) == ""
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success


def test_s5_rak_glob_command_carries_all_three_spellings():
    """Scenario 5: the resolver's one shell line must look for the app, the
    bootloader AND the factory spelling — a virgin RAK is only findable by
    its stock name."""
    bench = _Bench("rak4631")
    bench.add("stock")
    _techo_raw_port(_wf(bench))
    glob_cmd = next(c for c in bench.history
                    if "for f in /dev/serial/by-id/" in c)
    for pat in ("RAK4631_RTNode-2400", "RAKWireless_WisBlock",
                "RAKwireless_WisBlock"):
        assert pat in glob_cmd, f"missing {pat}"


# =========================================================================
# Scenario 6 — the make flash contract: real refusals, surfaced verbatim
# =========================================================================

def test_s6_no_image_refusal_reaches_the_operator_verbatim():
    """Scenario 6a: the Makefile's 'No image:' guard (missing build product)
    must arrive in the failure message, including its self-help hint."""
    bench = _Bench("rak4631")
    bench.add("boot")
    bench.flash_result = (
        1, "No image: /home/nodemedic/RTNode-2400/build/rak4631-noalloc/"
           "RNode_Firmware.ino.zip (make firmware-rak4631-noalloc)\n"
           "make: *** [Makefile:402: flash-rak4631] Error 1", "")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "No image:" in res.message
    assert "make firmware-rak4631-noalloc" in res.message


def test_s6_makefile_counts_devices_the_pipeline_cannot_see():
    """Scenario 6b: a Meshtastic-flashed RAK matches the Makefile's *RAK4631*
    glob but NONE of the pipeline's three identity patterns — so the pipeline
    sees one board, the Makefile sees two and refuses. The 'need exactly ONE'
    text must surface verbatim so the operator learns which extra device."""
    bench = _Bench("rak4631")
    bench.add("boot")                                # the birth board, in DFU
    bench.add("app", tag="bystander", tty="/dev/ttyACM3",
              byid="usb-RAKwireless_RAK4631_Meshtastic_9F00-if00")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "need exactly ONE RAK4631 on the bus (saw 2)" in res.message
    assert not bench.wrong_writes, "and nothing may have been flashed"


def test_s6_partial_nrfutil_output_is_surfaced_not_mistaken_for_success():
    """Scenario 6c: adafruit-nrfutil dies mid-transfer without printing
    'Device programmed.' — the step must fail and quote nrfutil's own words
    (they name the serial-level cause)."""
    bench = _Bench("techo")
    bench.add("boot")
    bench.flash_result = (
        1, "Starting DFU upgrade of type 4, SoftDevice size: 0, "
           "bootloader size: 0, application size: 619321\n"
           "Failed to upgrade target. Error is: No data received on serial "
           "port. Not able to proceed.\n"
           "make: *** [Makefile:444: flash-techo] Error 1", "")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "No data received on serial port" in res.message


def test_s6_a_zero_exit_without_programmed_is_still_a_failure():
    """Scenario 6d: 'programmed' is the positive proof; a make that exits 0
    without it (tail window ate it / nrfutil quirk) must not pass. (This is
    the contract's safe side — if a future Makefile prints trailing lines
    that push 'Device programmed.' out of tail -4, THIS is the behaviour
    that will fire, and the message will look like a failed flash of a
    healthy board.)"""
    bench = _Bench("techo")
    bench.add("boot")
    bench.flash_result = (0, "target : /dev/ttyACM1\nimage  : 619321 bytes", "")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "DFU flash failed" in res.message


# =========================================================================
# Scenario 7 — timeout arithmetic and the progress ring
# =========================================================================

def test_s7_every_inner_timeout_fits_inside_its_connection_timeout():
    """Scenario 7: each `timeout N <cmd>` wrapper must sit inside its
    connection.run(..., timeout=M) with headroom — otherwise the OUTER
    timeout kills a command the inner one would have finished, and the
    failure is misattributed. Scanned from the source so a new call can't
    regress silently."""
    import inspect
    src = inspect.getsource(rb)
    # pair every "timeout N ..." wrapper with the run(..., timeout=M) it sits in
    for m in re.finditer(r'timeout (\d+) [a-z]', src):
        inner = int(m.group(1))
        window = src[m.end():m.end() + 400]
        mm = re.search(r'timeout=(\d+)\)', window)
        assert mm, f"no outer timeout near: {src[m.start():m.start()+80]!r}"
        outer = int(mm.group(1))
        assert outer > inner, (
            f"outer timeout {outer} <= inner {inner} near "
            f"{src[m.start():m.start()+80]!r}")


def _nrf_onboard_worst_case_seconds() -> int:
    """Worst-case duration of a SUCCESSFUL _onboard_techo, from the code's
    own constants: kiss 90 + rnodeconf -r 150 + app wait 15*2 + kiss 90 +
    hash read 90 + hash set 60 + params 60."""
    return 90 + 150 + 15 * 2 + 90 + 90 + 60 + 60


def test_s7_progress_estimate_is_in_the_same_world_as_the_nrf_reality():
    """Scenario 7 (FINDING F4, fixed): the ESP32 estimates in
    birth_screen._STEP_SECONDS paced the nRF birth — wifi_onboarding=2 s vs
    a legal ~9.5 min success. The fix is PER-TARGET: nrf_dfu workflows carry
    a step_seconds override covering at least ONE _kiss_ready allowance
    (90 s), and the screen merges it over the static table. Both halves are
    pinned here (the screen side by source read — importing birth_screen
    drags Kivy into a headless test run)."""
    wf = _wf(_Bench("rak4631"))
    est = wf.step_seconds.get("wifi_onboarding", 0)
    assert est >= 90, (
        f"nrf_dfu step_seconds estimate {est}s vs worst-case successful "
        f"onboard ~{_nrf_onboard_worst_case_seconds()}s")

    import os
    path = os.path.join(os.path.dirname(rb.__file__), "..",
                        "ui", "screens", "birth_screen.py")
    with open(path) as f:
        src = f.read()
    assert "step_seconds" in src, (
        "birth_screen no longer consults the workflow's step_seconds "
        "override — the static ESP32 table would pace the nRF birth again")


# =========================================================================
# Scenario 9 — the hash-set hard reset (the second RAK bench failure)
# =========================================================================

def test_s9_hash_set_hard_reset_is_waited_out_before_params():
    """13:04:58, 2026-08-20, on the bench: Device.h:142 hard-resets the board
    after the maiden hash write, the tty moved ACM2→ACM1, and -T opened the
    dying port ("Serial port opened, but RNode did not respond"). The step
    must treat the hash write exactly like -r: wait for the app identity,
    re-resolve, and gate on KISS before the params call."""
    bench = _Bench("rak4631")
    d = bench.add("app")
    res = _onboard_techo(_wf(bench))
    assert res.success, res.message
    assert not bench.stale_cmds, bench.stale_cmds
    t_cmds = [c for c in bench.history if " -T " in c]
    assert t_cmds and d.tty in t_cmds[-1], (
        f"-T must target the post-reset tty {d.tty}: {t_cmds}")
    assert d.tnc


# =========================================================================
# Scenario 8 — two boards, every combination
# =========================================================================

@pytest.mark.parametrize("modes", [("app", "app"), ("boot", "boot"),
                                   ("app", "boot")])
def test_s8_two_boards_refuse_at_flash_in_every_combination(modes):
    """Scenario 8: app+app, boot+boot, app+boot — the flash step must refuse
    every combination and never silently pick one."""
    bench = _Bench("techo")
    bench.add(modes[0], tty="/dev/ttyACM1")
    bench.add(modes[1], tty="/dev/ttyACM2", tag="victim")
    res = _flash_techo(_wf(bench), "/dev/ttyACM1")
    assert not res.success
    assert "more than one" in res.message
    assert not bench.wrong_writes and not any(
        "make flash" in c for c in bench.history)


def test_s8_two_boards_at_onboarding_refuse():
    """Scenario 8: two boards at usb_setup entry — must refuse, not pick."""
    bench = _Bench("techo")
    bench.add("app", tty="/dev/ttyACM1")
    bench.add("app", tty="/dev/ttyACM2", tag="victim")
    res = _onboard_techo(_wf(bench))
    assert not res.success
    assert not bench.wrong_writes


def test_s8_two_boards_refusal_does_not_claim_a_vanished_board():
    """Scenario 8 (honesty): with two boards PRESENT the message must not
    say something vanished — the operator's next move (replug the board)
    is wrong for the actual state (unplug one)."""
    bench = _Bench("techo")
    bench.add("app", tty="/dev/ttyACM1")
    bench.add("app", tty="/dev/ttyACM2", tag="victim")
    res = _onboard_techo(_wf(bench))
    assert not res.success
    assert "vanished" not in res.message.lower(), res.message


def test_s8_a_second_board_stealing_the_vacated_tty_must_not_get_the_writes():
    """Scenario 8/2 (the storm case): during -r's hard reset the birth board
    re-enumerates onto a new number; a second nRF board plugged in that
    moment takes the freed ttyACM1. The re-resolve sees two boards and
    returns '' — and the fallback silently reuses ttyACM1, now the WRONG
    BOARD. Every write after that point lands on the intruder while the
    birth board ships unconfigured under a green message."""
    bench = _Bench("rak4631")
    birth = bench.add("app", tty="/dev/ttyACM1")

    def _steal_the_port():
        bench.move(birth)                       # reset: ACM1 is vacated
        bench.add("app", tag="victim", tty="/dev/ttyACM1",
                  provisioned=True, stored_hash=DEVICE_HASH)
    bench.after_r_reset = _steal_the_port

    res = _onboard_techo(_wf(bench))
    assert not bench.wrong_writes, (
        f"writes landed on the wrong board: {bench.wrong_writes} "
        f"(step said: success={res.success} {res.message!r})")
    if res.success:
        assert birth.stored_hash == DEVICE_HASH and birth.tnc, (
            "the step reported success but the birth board never got its "
            "hash/params")


# =========================================================================
# Scenario 9 — the provisioned-board rerun (the bench RAK's state today)
# =========================================================================

def test_s9_rerun_on_a_fully_birthed_board_completes():
    """Scenario 9a: identity + hash + TNC all present, app mode. -r refuses
    ('already present'), the read-back accepts, hash is re-set to the same
    value, -T re-applies. The rerun must complete, not trip on its history."""
    bench = _Bench("rak4631")
    d = bench.add("app", provisioned=True, stored_hash=DEVICE_HASH, tnc=True)
    wf = _wf(bench)
    results = wf.run_all()
    failed = [r for r in results if not r.success and not r.skipped]
    assert not failed, [(r.name, r.message) for r in failed]
    # the identity SURVIVED the rerun (rnodeconf refused the re-bootstrap and
    # the pipeline accepted the refusal via the -i read-back)
    assert d.provisioned and d.stored_hash == DEVICE_HASH and d.tnc
    assert any("rnodeconf" in c and " -i" in c for c in bench.history), (
        "the already-present refusal must be settled by the read-back")


def test_s9_rerun_repairs_the_identity_without_hash_state():
    """Scenario 9b: the exact shipped-dead state of 2026-08-20 — identity
    written, firmware hash never set (hw_ready false, e-paper FIRMWARE
    CORRUPT). A rerun of usb_setup must set the hash and finish; verify must
    then pass on the healed board."""
    bench = _Bench("techo")
    d = bench.add("app", provisioned=True, stored_hash="")
    wf = _wf(bench)
    on = _onboard_techo(wf)
    assert on.success, on.message
    assert d.stored_hash == DEVICE_HASH, "the repair IS the hash write"
    ver = _verify_techo(wf)
    assert ver.success, ver.message


def test_s9_onboarding_alone_against_a_bootloader_board_fails_with_true_advice():
    """Scenario 9c: mode unknown — the board is sitting in the bootloader
    (double-tapped, or a crashed app). usb_setup alone cannot proceed (the
    bootloader speaks no KISS); it must fail with advice that WORKS: 'run
    the build again' routes through flash_firmware, which handles DFU.
    (The message's 'first boot formats its filesystem' guess is wrong for
    this state, but the prescribed action is right — noted, not failed.)"""
    bench = _Bench("techo")
    bench.add("boot")
    res = _onboard_techo(_wf(bench))
    assert not res.success
    assert "build again" in res.message
    # and the advice is true: the full pipeline DOES recover this state
    bench2 = _Bench("techo")
    d2 = bench2.add("boot")
    results = _wf(bench2).run_all()
    failed = [r for r in results if not r.success and not r.skipped]
    assert not failed, [(r.name, r.message) for r in failed]
    assert d2.provisioned and d2.tnc


def test_s9_verify_alone_on_a_hashless_board_reports_the_dead_state():
    """Scenario 9d: verify_usb against the not-ready board (hash unset) must
    fail on the board's own verdict — already pinned by the 2026-08-20 test
    in test_rtnode_build; re-run here through the stateful bench to prove the
    boot-log path fires before the polite EEPROM reads."""
    bench = _Bench("rak4631")
    bench.add("app", provisioned=True, stored_hash="")
    res = _verify_techo(_wf(bench))
    assert not res.success
    assert "hardware not ready" in res.message
