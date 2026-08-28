"""THE COMPLETE JOURNEY: a factory-fresh nRF52 board walked through the real
RTNode-2400 birth pipeline, in emulation, end to end.

Where test_rtnode_build.py exercises individual steps against a mostly-static
_TechoMedic, this file models the BOARD ITSELF as a state machine — USB
identity, PID, bootloader-vs-app, port renumbering on every re-enumeration,
and a first-boot LittleFS-format silence window on a virtual clock — and then
runs ``RTNodeBuildWorkflow.run_all`` over it. Every rnodeconf/DFU interaction
is gated on the state a REAL board would be in, so a pipeline that talks too
early, to the wrong port, or to the wrong mode has to fail here the way it
failed on the bench.

The two bench failures of 2026-08-20 are reproduced as traps the emulation
enforces:

(a) The one-capital trap. A stock RAK4631 in APP mode presents
    "RAKwireless_WisBlock_RAK4631" (PID 8029); its bootloader presents
    "RAKWireless_WisBlock_RAK4631" (PID 002a) — one capital apart. The old
    name-based check read the app as already-in-bootloader, skipped the
    1200-baud touch, and DFU hit the application. Here, ``make flash-*``
    REFUSES unless the modelled board is genuinely in its bootloader.

(b) Enumeration is not readiness. After the maiden flash the port enumerates
    but the KISS handler stays silent for tens of seconds while setup()
    formats LittleFS. Here, every rnodeconf/partition_hashes call made while
    the modelled board is still inside that window returns rnodeconf's real
    "Serial port opened, but RNode did not respond." AND records a violation.
    A green walk therefore PROVES the pipeline held for the firmware's own
    answer before provisioning.

All device-side strings are the REAL ones: rnodeconf's messages were read out
of the installed RNS.Utilities.rnodeconf source (2026-08-20), the bootlog
lines match the firmware's own "[RTNode] identity=… dst=…" / "[STACK]" prints,
and adafruit-nrfutil's "Device programmed." closes a successful DFU.
"""

from __future__ import annotations

import re

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from workflows.rtnode_build import RTNodeBuildWorkflow, RTNODE_TARGETS

# Ground truths the modelled device reports (shapes match the real thing:
# 64-hex firmware hash, 32-hex truncated RNS hashes).
RUNNING_HASH = "b9a932741fd128ac7dc137bc733b1b9a9189a71eca95fd4ce55d4836de8196bc"
IDENTITY = "aabbccddeeff00112233445566778899"
DST = "99887766554433221100ffeeddccbbaa"
FW_VERSION = "1.85"

EXPECTED_STEPS = ["detect_board", "flash_firmware", "wifi_onboarding",
                  "verify_beacon", "verify_sd_overflow", "birth_certificate"]

#: Constants from scripts/techo_bootlog.py, used by the timing-budget test:
#: 0.5 s post-reset sleep + a 30 s re-enumeration deadline before the capture.
BOOTLOG_PRE_CAPTURE_S = 0.5 + 30
#: scripts/kiss_detect.py worst single-lap overshoot past its own deadline:
#: open (~1 s) + 2.5 s read window + 1.5 s sleep.
KISS_LAP_OVERSHOOT_S = 5


def _log(msg):
    """rnodeconf output arrives through RNS.log — timestamp-prefixed."""
    return f"[2026-08-20 10:00:00] [Notice] {msg}"


class NrfBoard(EmulatedConnection):
    """A medic with ONE factory-fresh (or half-provisioned) nRF52 board.

    States: "app_stock" (as shipped), "bootloader" (after the 1200-baud touch
    or a double-tap), "app_rtnode" (after a successful DFU). Every transition
    re-enumerates: the ttyACM number MOVES and the by-id name changes, so any
    code holding a stale port meets the same wall it would on the bench.
    """

    def __init__(self, key, stock_name, boot_name, app_name,
                 app_pid="8029", boot_pid="002a", state="app_stock",
                 format_seconds=45.0, provisioned=False, stored_hash=None):
        super().__init__(default_code=0, default_stdout="")
        self.key = key
        self.names = {"app_stock": stock_name, "bootloader": boot_name,
                      "app_rtnode": app_name}
        self.pids = {"app_stock": app_pid, "bootloader": boot_pid,
                     "app_rtnode": app_pid}
        self.state = state
        self.port_n = 1
        self.clock = 0.0            # virtual seconds
        self.busy = 0.0             # KISS-silent seconds remaining after a boot
        self.format_seconds = float(format_seconds)  # maiden LittleFS format
        self.littlefs_formatted = provisioned or stored_hash is not None
        self.eeprom_provisioned = provisioned
        self.stored_hash = stored_hash    # the expectation written to EEPROM
        # Device.h:142: fw_signature_validated is decided ONCE, at boot. A
        # maiden board boots with no stored hash, so it is False until the
        # reboot that follows the hash write.
        self.sig_validated_at_boot = (provisioned
                                      and stored_hash == RUNNING_HASH)
        self.reset_pending = False   # deferred hash-write hard reset
        self.tnc_params = None
        self.tnc_actually_saved = provisioned and stored_hash == RUNNING_HASH
        self.violations = []        # talked-too-early / wrong-mode records
        self.events = []            # (name, virtual clock) for order asserts
        self.calls = []             # (command, outer timeout) for budgets

    # -- device truth ------------------------------------------------------
    @property
    def dev(self):
        return f"/dev/ttyACM{self.port_n}"

    @property
    def byid(self):
        return self.names[self.state]

    @property
    def hw_ready(self):
        """The firmware's own gate: EEPROM provisioned AND the stored hash
        matches the running image. Unmet, RNS never starts (bench 2026-08-20:
        'RNS is inoperable because hardware is not ready')."""
        return self.eeprom_provisioned and self.stored_hash == RUNNING_HASH

    def _reenumerate(self, new_state, quiet=0.0):
        self.state = new_state
        self.port_n += 1
        self.busy = float(quiet)

    def _event(self, name):
        self.events.append((name, self.clock))

    def when(self, name):
        hits = [t for n, t in self.events if n == name]
        assert hits, f"event {name!r} never happened: {self.events}"
        return hits[0]

    # -- the emulated shell --------------------------------------------------
    def run(self, command, timeout=30):
        self.history.append(command)
        self.calls.append((command, timeout))

        if "techo_touch.py" in command:
            port = re.search(r"techo_touch\.py (\S+)", command).group(1)
            if port != self.dev:
                return 1, "", f"touch: could not open {port}"
            self._event("touch")
            if self.state != "bootloader":
                self._reenumerate("bootloader")
            return 0, "touch sent", ""

        if "kiss_detect.py" in command:
            m = re.search(r"kiss_detect\.py (\S+) (\d+)", command)
            port, budget = m.group(1), float(m.group(2))
            if self.reset_pending:
                # deferred hash-write reset lands under the probe: this
                # probe's port dies; the board is back on a NEW number
                self.reset_pending = False
                self._reenumerate("app_rtnode", quiet=8.0)
                self.sig_validated_at_boot = True
                self.clock += 2
                return 1, "", "detect: no answer"
            # The script POLLS for its whole budget: a board mid-format
            # answers the moment setup() finishes, IF the budget covers it.
            if port != self.dev or self.state != "app_rtnode":
                self.clock += budget
                return 1, "", "detect: no answer"
            if self.busy > budget:
                self.clock += budget
                self.busy -= budget
                return 1, "", "detect: no answer"
            self.clock += self.busy
            self.busy = 0.0
            self.littlefs_formatted = True
            self._event("kiss_answered")
            return 0, "detect: answered", ""

        if "udevadm info" in command:
            port = re.search(r"-n (\S+)", command).group(1)
            self._event("pid_checked")
            # The command pipes through grep|cut; return what the PIPELINE
            # prints. A stale port yields nothing, exactly like the bench.
            return 0, (self.pids[self.state] if port == self.dev else ""), ""

        if "for f in /dev/serial/by-id/" in command:
            # Faithful glob semantics: bash globs are CASE-SENSITIVE, which is
            # the entire point of the one-capital identities.
            import fnmatch
            pats = [p.rstrip(";") for p in
                    re.findall(r"/dev/serial/by-id/(\S+)", command)]
            hit = any(fnmatch.fnmatchcase(self.byid, p) for p in pats)
            return 0, (self.dev + "\n") if hit else "", ""

        if re.search(r"ls /dev/serial/by-id/ .*grep -q", command):
            pat = re.search(r"grep -q '([^']+)'", command).group(1)
            return (0 if pat in self.byid else 1), "", ""

        if command.startswith("ls /dev/tty"):
            named = command.split()[1]
            return 0, (named if named == self.dev else ""), ""

        if " make firmware-" in command:
            self._event("built")
            return 0, "Sketch uses 614308 bytes (75%) of program storage space.", ""

        if "make flash-" in command:
            # TRAP (a): DFU aimed at a running APPLICATION answers garbage.
            if self.state != "bootloader":
                self.violations.append("DFU flash attempted outside the bootloader")
                return 1, "Failed to upgrade target. Bootloader version does not match", ""
            self._event("dfu_flash")
            quiet = 3.0 if self.littlefs_formatted else self.format_seconds
            self._reenumerate("app_rtnode", quiet=quiet)
            self._event("app_boot")
            return 0, "Device programmed.", ""

        if "partition_hashes from_device" in command:
            port = re.search(r"from_device (\S+)", command).group(1)
            gate = self._talk_gate("partition_hashes", port)
            if gate:
                return gate
            self._event("hash_read")
            return 0, RUNNING_HASH, ""

        if "techo_bootlog.py" in command:
            port = re.search(r"techo_bootlog\.py (\S+)", command).group(1)
            if port != self.dev:
                return 1, "BOOTLOG: board did not re-enumerate", ""
            self._event("bootlog")
            self._reenumerate("app_rtnode", quiet=3.0)
            # The 40 s capture window rides out the (already-formatted) boot.
            self.clock += BOOTLOG_PRE_CAPTURE_S + 40
            self.busy = 0.0
            if not self.hw_ready:
                # The exact shipped-dead state of 2026-08-20.
                return 0, ("[EEPROM] provisioning gate failed\n"
                           "RNS is inoperable because hardware is not ready"), ""
            return 0, ("[STACK] loop task headroom: 3136 / 4096 bytes free\n"
                       f"[RTNode] identity={IDENTITY} dst={DST}\n"
                       "RNS is READY"), ""

        if "rnodeconf" in command:
            return self._rnodeconf(command)
        return 0, "", ""

    # -- rnodeconf, honestly -------------------------------------------------
    def _talk_gate(self, what, port):
        """Anything KISS-shaped talking to a board that cannot answer yet is a
        recorded violation AND the failure the real tool prints."""
        if self.reset_pending:
            # the deferred hard reset lands NOW, under whatever touched the
            # port: old tty dies, new number, fresh boot quiet
            self.reset_pending = False
            self._reenumerate("app_rtnode", quiet=8.0)
            self.sig_validated_at_boot = True
            return 1, _log("Serial port opened, but RNode did not respond. "
                           "Is a valid firmware installed?"), ""
        if port != self.dev:
            self.violations.append(f"{what} aimed at stale port {port} (board on {self.dev})")
            return 1, _log(f"Could not find specified port {port}, exiting now"), ""
        if self.state != "app_rtnode":
            self.violations.append(f"{what} while board is in {self.state}")
            return 1, _log("Serial port opened, but RNode did not respond. "
                           "Is a valid firmware installed?"), ""
        if self.busy > 0:
            # rnodeconf waits ~3 s and gives up — TRAP (b).
            self.violations.append(
                f"{what} during first-boot silence ({self.busy:.0f}s of format left)")
            self.clock += 3
            return 1, _log("Serial port opened, but RNode did not respond. "
                           "Is a valid firmware installed?"), ""
        return None

    def _rnodeconf(self, command):
        port = re.search(r"rnodeconf (\S+)", command).group(1)
        gate = self._talk_gate("rnodeconf", port)
        if gate:
            return gate
        if "--bluetooth-on" in command:
            # the BLE-at-birth step (2026-08-28). Real rnodeconf logs the
            # line and sends CMD_BT_CTRL fire-and-forget — no ack echoed.
            self._event("bluetooth_enabled")
            return 0, _log("Enabling Bluetooth..."), ""
        if "--firmware-hash" in command:
            self.stored_hash = re.search(r"--firmware-hash (\S+)", command).group(1)
            self.tnc_actually_saved = self.stored_hash == RUNNING_HASH
            self._event("hash_set")
            if not self.sig_validated_at_boot:
                # Device.h:142 — device_save_firmware_hash() ends in
                # hard_reset() when the signature was NOT validated at boot,
                # i.e. on EVERY maiden birth. The reset fires AFTER this
                # command returns and the dying tty LINGERS until the next
                # touch (RAK births two and three, 2026-08-20).
                self.reset_pending = True
            return 0, _log("Firmware hash set"), ""
        if " -r " in command:
            if self.eeprom_provisioned:
                self._event("eeprom_refused")   # identity SURVIVES; exit 0
                return 0, (_log("EEPROM bootstrap was requested, but a valid "
                                "EEPROM was already present.") + "\n"
                           + _log("No changes are being made.")), ""
            self.eeprom_provisioned = True
            self._event("eeprom_bootstrapped")
            self._reenumerate("app_rtnode", quiet=3.0)   # -r ends in a reset
            return 0, (_log("Device signature validated") + "\n"
                       + _log("EEPROM Bootstrapping successful!")), ""
        if " -T " in command:
            m = re.search(r"--freq (\d+) --bw (\d+) --sf (\d+) --cr (\d+) --txp (\d+)",
                          command)
            if not m:
                return 1, _log("Please input"), ""    # a missing flag prompts
            self.tnc_params = tuple(int(g) for g in m.groups())
            # THE HOST-SIDE ILLUSION, modelled deliberately: with the hash
            # gate failed, eeprom_conf_save() refuses ON THE DEVICE while
            # rnodeconf still prints success. Only the bootlog tells the truth.
            self.tnc_actually_saved = self.hw_ready
            self._event("tnc")
            return 0, _log("Device set to TNC operating mode"), ""
        if " -K -L" in command:
            self._event("hash_compared")
            tgt = self.stored_hash or "0" * 64
            return 0, (_log(f"The target firmware hash is: {tgt}") + "\n"
                       + _log(f"The actual firmware hash is: {RUNNING_HASH}")), ""
        if " -i" in command:
            self._event("eeprom_read")
            if not self.eeprom_provisioned:
                return 0, _log("EEPROM is invalid, no further information available"), ""
            return 0, (_log(f"Current firmware version: {FW_VERSION}") + "\n"
                       + _log("EEPROM checksum correct") + "\n"
                       + _log("Device signature validated") + "\n"
                       f"\tFirmware version   : {FW_VERSION}"), ""
        return 0, "", ""


def fresh_rak(**kw):
    """A RAK4631 exactly as the bench met it on 2026-08-20: stock app running
    (PID 8029), stock by-id ONE CAPITAL from the bootloader's."""
    return NrfBoard(
        "rak4631",
        stock_name="usb-RAKwireless_WisBlock_RAK4631_39F1A2B4C5D6-if00",
        boot_name="usb-RAKWireless_WisBlock_RAK4631_39F1A2B4C5D6-if00",
        app_name="usb-RAKwireless_RAK4631_RTNode-2400_39F1A2B4C5D6-if00",
        **kw)


def fresh_techo(**kw):
    return NrfBoard(
        "techo",
        stock_name="usb-Nordic_Semiconductor_nRF52_Device_C0FFEE-if00",
        boot_name="usb-LilyGo_T-Echo_v1_C0FFEE-if00",
        app_name="usb-LilyGO_T-Echo_RTNode-2400_C0FFEE-if00",
        **kw)


def walk(board, target, node_name="ROOK"):
    """Run the ENTIRE pipeline through run_all, GPS honestly absent (JONESEY
    carries the medic's GPS — while it is away every fix is None)."""
    wf = RTNodeBuildWorkflow(board, NodeProfile(), target=target,
                             board_port=board.dev, node_name=node_name,
                             gps_reader=lambda: None)
    results = wf.run_all()
    return wf, results


def assert_no_false_words(results, forbid=("T-Echo", "portal", "RTNode-Setup",
                                           "10.0.0.1", "SSID")):
    """No step message may borrow another board's name or claim WiFi work.
    The one honest WiFi mention — stating the board HAS none — is allowed."""
    for r in results:
        msg = r.message
        for word in forbid:
            assert word not in msg, f"{r.name}: {word!r} leaked into: {msg}"
        cleaned = msg.replace("no WiFi on this board", "")
        assert "WiFi" not in cleaned, f"{r.name} claims WiFi work: {msg}"


# =============================================================================
# 1. The factory-fresh RAK4631, whole journey
# =============================================================================

def test_factory_fresh_rak_full_walk_is_green_and_honest():
    board = fresh_rak()
    wf, results = walk(board, "rak4631")

    # Every step green; the SD-overflow step is the only skip.
    assert [r.name for r in results] == EXPECTED_STEPS
    for r in results:
        assert r.success, f"{r.name} failed: {r.message}"
    assert [r.name for r in results if r.skipped] == ["verify_sd_overflow"]

    # Nothing talked to the board before it could answer, ever.
    assert board.violations == [], board.violations

    # The journey in the right order, on the board's own clock.
    order = ["pid_checked", "touch", "dfu_flash", "app_boot", "kiss_answered",
             "eeprom_bootstrapped", "hash_read", "hash_set", "tnc",
             "bootlog", "hash_compared", "eeprom_read"]
    seq = [n for n, _ in board.events]
    idx = [seq.index(e) for e in order]        # first occurrence of each
    assert idx == sorted(idx), list(zip(order, idx))
    times = [board.when(e) for e in order]
    assert times == sorted(times), list(zip(order, times))

    # Provisioning bytes from the firmware's own Boards.h, in the -r line.
    r_cmds = [c for c in board.history if "rnodeconf" in c and " -r " in c]
    assert len(r_cmds) == 1
    assert "--product 10 --model 12 --hwrev 1" in r_cmds[0]

    # Message honesty, step by step.
    assert_no_false_words(results)
    by_name = {r.name: r for r in results}
    assert "RAK4631" in by_name["detect_board"].message
    flash_msg = by_name["flash_firmware"].message
    assert "answering" in flash_msg and board.busy == 0, (
        "'back on USB and answering' may only be said once KISS answered")
    onboard_msg = by_name["wifi_onboarding"].message
    assert "firmware hash set" in onboard_msg
    assert board.stored_hash == RUNNING_HASH, (
        "the message claims the hash is set; the device must agree")
    assert "915.125 MHz SF9 CR5 17 dBm" in onboard_msg
    assert board.tnc_params == (915125000, 125000, 9, 5, 17)
    assert board.tnc_actually_saved, (
        "TNC params only persist device-side when the hash gate passed")
    verify_msg = by_name["verify_beacon"].message
    assert IDENTITY[:12] in verify_msg, "the identity the node announced"
    assert "No over-the-air" in verify_msg, "USB-only scope stated honestly"

    # The certificate: every field from something actually read.
    cert = wf.birth_certificate
    assert cert["board"] == "RAK4631"
    from workflows.rnode_boards import key_for_board_name
    assert key_for_board_name(cert["board"]) == "rak4631", (
        "the cert's board name must resolve back to the catalogue key, or the "
        "guided flow loses the board photo (the 2026-08-18 lesson)")
    assert cert["identity_hash"] == IDENTITY
    assert cert["reticulum_address"] == DST
    assert cert["firmware"] == FW_VERSION
    assert cert["build_env"] == "firmware-rak4631-noalloc"
    assert cert["frequency_mhz"] == 915.125 and cert["spreading_factor"] == 9
    assert cert["location"] is None, (
        "no GPS fix is a recorded absence, never a fake position")
    assert wf.gps_fix is None


def test_walk_reruns_are_deterministic_and_single_flash():
    """One journey = one build, one touch, one DFU, one -r — no hidden retries
    on the intended path."""
    board = fresh_rak()
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results)
    for needle, n in ((" make firmware-", 1), ("make flash-", 1),
                      ("techo_touch.py", 1), (" -r ", 1), (" -T ", 1),
                      ("--firmware-hash", 1)):
        hits = [c for c in board.history if needle in c]
        assert len(hits) == n, f"{needle!r}: {len(hits)} calls\n" + "\n".join(hits)


# =============================================================================
# 2. Bench failure (a): the one-capital stock identity must get the touch
# =============================================================================

def test_stock_rak_app_is_touched_because_the_pid_says_so():
    board = fresh_rak()
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results)

    hist = board.history
    i_pid = next(i for i, c in enumerate(hist) if "udevadm info" in c)
    i_touch = next(i for i, c in enumerate(hist) if "techo_touch.py" in c)
    i_flash = next(i for i, c in enumerate(hist) if "make flash-" in c)
    assert i_pid < i_touch < i_flash, (
        "the bootloader question must be answered by PID, then the touch, "
        "then DFU — the 2026-08-20 #1 order")
    assert not any("grep -qi" in c for c in hist), (
        "case-insensitive identity greps are how the app got read as the bootloader")


def test_the_one_capital_trap_is_real_in_this_model():
    """Document WHY names cannot decide: the stock app's by-id equals the
    bootloader's identity case-insensitively and differs case-sensitively —
    any -qi style check reads a RUNNING board as in-DFU."""
    board = fresh_rak()
    t = RTNODE_TARGETS["rak4631"]
    stock = board.names["app_stock"]
    assert t.usb_boot_id.lower() in stock.lower()
    assert t.usb_boot_id not in stock


def test_dfu_aimed_at_a_running_app_fails_in_the_model():
    """The would-have-failed-then half of bench failure (a): had the pipeline
    skipped the touch (the old name-based check), the flash step could not
    have gone green — the model answers with the bench's own error."""
    board = fresh_rak()          # app mode, PID 8029
    code, out, _ = board.run("cd ~/RTNode-2400 && make flash-rak4631 VARIANT=noalloc")
    assert code != 0 and "Bootloader version does not match" in out
    assert board.violations, "the model records the wrong-mode DFU"


def test_a_board_already_in_the_bootloader_is_not_touched():
    """Double-tapped by hand before the build: PID 002a answers the question,
    the touch is skipped, and the walk still lands green."""
    board = fresh_rak(state="bootloader")
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results), [r.message for r in results]
    assert not any("techo_touch.py" in c for c in board.history)
    assert board.violations == []


# =============================================================================
# 3. Bench failure (b): first-boot silence is waited out, not failed
# =============================================================================

@pytest.mark.parametrize("format_seconds", [45.0, 85.0])
def test_first_boot_silence_is_held_for_not_failed(format_seconds):
    board = fresh_rak(format_seconds=format_seconds)
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results), [r.message for r in results]
    assert board.violations == [], (
        "nothing may provision during the LittleFS format window: "
        f"{board.violations}")

    # The wait actually happened, on the virtual clock.
    waited = board.when("kiss_answered") - board.when("app_boot")
    assert waited >= format_seconds

    # And the pipeline's DECLARED budget covers the window — read the budget
    # out of the command it really issued, don't assume one.
    kiss = next(c for c in board.history if "kiss_detect.py" in c)
    budget = float(re.search(r"kiss_detect\.py \S+ (\d+)", kiss).group(1))
    assert budget > format_seconds, (
        f"kiss budget {budget}s must cover the bench's format window")

    # rnodeconf only ever ran after the firmware itself answered.
    first_talk = min(board.when("eeprom_bootstrapped"), board.when("hash_read"))
    assert first_talk >= board.when("kiss_answered")


def test_provisioning_during_the_silence_window_would_have_failed():
    """The would-have-failed-then half of bench failure (b): rnodeconf's own
    ~3 s patience against a board mid-format gets the real refusal."""
    board = fresh_rak()
    board.state = "app_rtnode"
    board.busy = 40.0             # LittleFS format in progress
    code, out, _ = board.run(
        f"timeout 150 rnodeconf {board.dev} -r --product 10 --model 12 --hwrev 1")
    assert code != 0 and "RNode did not respond" in out
    assert any("first-boot silence" in v for v in board.violations)


# =============================================================================
# 4. The T-Echo regression: the proven flow still walks green
# =============================================================================

def test_factory_fresh_techo_full_walk_still_green():
    board = fresh_techo()
    wf, results = walk(board, "techo", node_name="FAITH2")
    assert [r.name for r in results] == EXPECTED_STEPS
    for r in results:
        assert r.success, f"{r.name} failed: {r.message}"
    assert board.violations == []
    assert any("techo_touch.py" in c for c in board.history), (
        "a stock Nordic app is in APP mode: it gets the touch")
    r_cmd = next(c for c in board.history if " -r " in c and "rnodeconf" in c)
    assert "--product 15 --model 17 --hwrev 1" in r_cmd
    cert = wf.birth_certificate
    assert cert["board"] == "LilyGO T-Echo"
    from workflows.rnode_boards import key_for_board_name
    assert key_for_board_name(cert["board"]) == "techo"
    assert cert["identity_hash"] == IDENTITY
    assert cert["reticulum_address"] == DST
    assert cert["build_env"] == "firmware-techo-noalloc"


# =============================================================================
# 5. The RERUN from the bench's half-provisioned state
# =============================================================================

def test_rerun_over_a_provisioned_board_completes_green():
    """The board on the bench NOW: identity written, hash set, TNC baked. A
    re-run must reflash, take the -r refusal as the identity surviving, set
    the (identical) hash again, repeat -T — and end green with no message
    claiming work that was refused."""
    board = fresh_rak(provisioned=True, stored_hash=RUNNING_HASH)
    wf, results = walk(board, "rak4631")
    assert [r.name for r in results] == EXPECTED_STEPS
    for r in results:
        assert r.success, f"{r.name} failed: {r.message}"
    assert board.violations == []

    # The -r really was REFUSED (exit 0, real sentence) and the pipeline
    # verified the surviving identity by read-back instead of re-minting it.
    names = [n for n, _ in board.events]
    assert names.index("eeprom_refused") < names.index("eeprom_read")
    assert not any(n == "eeprom_bootstrapped" for n, _ in board.events)

    # Idempotent tail: the hash written back equals the one already stored,
    # and TNC ran again with the same five parameters.
    assert board.stored_hash == RUNNING_HASH
    assert board.tnc_params == (915125000, 125000, 9, 5, 17)

    # No message may claim the bootstrap happened this run.
    for r in results:
        assert "Bootstrapping successful" not in r.message
    assert_no_false_words(results)
    cert = wf.birth_certificate
    assert cert["identity_hash"] == IDENTITY and cert["reticulum_address"] == DST


def test_rerun_where_only_the_identity_landed_repairs_the_hash():
    """Half-provisioned exactly as a bench abort leaves it: EEPROM identity
    written, firmware hash NEVER set (the shipped-dead state). The rerun must
    set the hash and end green — and the model's bootlog would have said
    'hardware is not ready' had the pipeline skipped that repair."""
    board = fresh_rak(provisioned=True, stored_hash=None)
    board.state = "app_rtnode"
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results), [r.message for r in results]
    assert board.stored_hash == RUNNING_HASH, "the hash repair is the point"
    assert board.hw_ready and board.tnc_actually_saved


# =============================================================================
# 6. Step-name / narration honesty for the RAK target
# =============================================================================

def test_rak_step_names_and_narration_carry_no_techo_or_wifi():
    wf = RTNodeBuildWorkflow(fresh_rak(), NodeProfile(), target="rak4631")
    assert wf.step_display.get("wifi_onboarding") == "usb_setup"
    assert wf.step_display.get("verify_beacon") == "verify_usb"
    for label in wf.step_phase_labels.values():
        assert "T-Echo" not in label
        cleaned = label.replace("no WiFi on this board", "")
        assert "WiFi" not in cleaned, f"narration claims WiFi: {label}"


# =============================================================================
# 7. The UI gates end to end for a RAK
# =============================================================================

def test_ui_gates_admit_the_rak_stock_and_renamed():
    from ui.board_detect import firmware_options, nrf52_board_key
    from ui.rtnode_choice import identified_target, blocked_board, target_options

    # Detection: both the STOCK product string and the image's RENAMED one
    # must land on the same key — the rename must never orphan the board.
    assert nrf52_board_key("WisCore RAK4631 Board") == "rak4631"
    assert nrf52_board_key("RAK4631 RTNode-2400") == "rak4631"

    # The build offer keys on the registry, not a hardcoded list.
    assert firmware_options("nrf52840", "rak4631") == ["rtnode2400", "rnode"]
    assert firmware_options("nrf52840", None) == ["rnode"]

    # The birth screen's decisions for a positively identified RAK.
    det = {"board_key": "rak4631", "chip": "nrf52840", "port": "/dev/ttyACM1"}
    assert identified_target(det) == "rak4631"
    assert blocked_board(det) is None
    assert target_options(det) == ["rak4631"]


# =============================================================================
# 8. Certificate hashes reach the kin roster's collection order
# =============================================================================

def test_cert_carries_both_hashes_in_kin_collection_order():
    board = fresh_rak()
    wf, results = walk(board, "rak4631")
    assert all(r.success for r in results)
    cert = wf.birth_certificate

    # _register_kin (ui/screens/birth_screen.py) folds these keys, in this
    # order, into ONE roster device. Replicate its collection here — the UI
    # side itself needs Kivy and is exercised on the medic, not in this suite.
    hashes = []
    for key in ("health_dst", "reticulum_address", "identity_hash"):
        value = cert.get(key)
        if value and value not in hashes:
            hashes.append(value)
    assert hashes == [DST, IDENTITY], (
        "an nRF cert has no health_dst; the announced destination leads and "
        "the identity rides with it, so either sighting folds the node")

    # And the source of _register_kin still collects in that order.
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert '("health_dst", "reticulum_address", "identity_hash")' in src


# =============================================================================
# 9. Timing sanity: no inner wait can outlive its outer budget
# =============================================================================

def test_every_inner_timeout_fits_its_outer_budget():
    """Computed from the commands the pipeline REALLY issued, worst case:
    a shell `timeout N` (or a script's own polling deadline) must finish —
    including its worst final lap — inside the connection.run timeout, or the
    transport kills a step that was still legitimately waiting."""
    board = fresh_rak(format_seconds=85.0)     # worst bench-plausible waits
    _, results = walk(board, "rak4631")
    assert all(r.success for r in results)

    for cmd, outer in board.calls:
        m = re.search(r"timeout (\d+) rnodeconf", cmd)
        if m:
            inner = int(m.group(1))
            assert outer > inner, f"outer {outer} <= inner {inner}: {cmd}"
        if "kiss_detect.py" in cmd:
            budget = int(re.search(r"kiss_detect\.py \S+ (\d+)", cmd).group(1))
            assert outer >= budget + KISS_LAP_OVERSHOOT_S, (
                f"kiss budget {budget}s + a worst lap must fit in {outer}s: {cmd}")
        m = re.search(r"timeout (\d+) python3 \S+techo_bootlog\.py \S+ (\d+)", cmd)
        if m:
            shell_to, capture = int(m.group(1)), int(m.group(2))
            worst = BOOTLOG_PRE_CAPTURE_S + capture + 1   # +1: final 1s read
            assert worst < shell_to, (
                f"bootlog worst case {worst}s vs shell timeout {shell_to}s: {cmd}")
            assert outer > shell_to, f"outer {outer} <= shell {shell_to}: {cmd}"
