import pytest

from transport.connection import EmulatedConnection
from workflows.robust_flash import (
    RobustFlasher, Region, FlashTier, CHUNK_FILE, DEFAULT_LADDER, find_hub_port,
)

UHUBCTL_OUT = """Current status for hub 4 [1d6b:0003 xhci-hcd.1, USB 3.00, 1 ports, ppps]
  Port 1: 02a0 power 5gbps Rx.Detect
Current status for hub 3 [1d6b:0002 xhci-hcd.1, USB 2.00, 2 ports, ppps]
  Port 1: 0103 power enable connect [303a:1001 Espressif USB JTAG/serial debug unit F8:5B:1B:A6:85:00]
  Port 2: 0100 power
"""


def test_find_hub_port_locates_board():
    conn = EmulatedConnection(default_code=0, default_stdout="").rule(
        "uhubctl", 0, UHUBCTL_OUT)
    assert find_hub_port(conn, "F8:5B:1B:A6:85:00") == ("3", 1)


def test_find_hub_port_absent_board():
    conn = EmulatedConnection(default_code=0, default_stdout="").rule(
        "uhubctl", 0, UHUBCTL_OUT)
    assert find_hub_port(conn, "DE:AD:BE:EF:00:00") == (None, None)

NOSLEEP = lambda *_: None
FIXED = [Region(0x0, "BL"), Region(0x8000, "PT"), Region(0xe000, "B0")]
APP = Region(0x10000, "APPIMG")


def rf(conn, **kw):
    kw.setdefault("hub", "3")
    kw.setdefault("hub_port", 1)
    return RobustFlasher(conn, "/dev/ttyACM0", sleep=NOSLEEP, **kw)


def base_conn():
    # default success; stat gives a 2-chunk app at 256 KB
    return EmulatedConnection(default_code=0, default_stdout="ok").rule(
        "stat -c %s", 0, "524288")


# ---- happy path -----------------------------------------------------------

def test_whole_flash_succeeds_at_first_tier():
    conn = base_conn()
    res = rf(conn).flash(FIXED, APP)
    assert res.success is True
    assert res.tier == "full @460800"
    # no chunking needed -> the app was never sliced
    assert not any("dd if=" in c for c in conn.history)
    assert not any(CHUNK_FILE in c for c in conn.history)


def test_every_write_is_read_back_verified():
    conn = base_conn()
    rf(conn).flash(FIXED, APP)
    assert any("verify_flash 0x10000 APPIMG" in c for c in conn.history)
    assert any("verify_flash 0x0 BL" in c for c in conn.history)


def test_bootloader_patches_header_but_app_keeps_it():
    conn = base_conn()
    rf(conn).flash(FIXED, APP)
    bl = next(c for c in conn.history if "write_flash" in c and "0x0 BL" in c)
    app = next(c for c in conn.history if "write_flash" in c and "0x10000 APPIMG" in c)
    assert "--flash_mode dio" in bl and "--flash_size 16MB" in bl
    assert "--flash_size keep" in app  # a chunk/app write must not repatch the header


# ---- escalation -----------------------------------------------------------

def test_escalates_to_chunks_when_whole_write_drops():
    conn = base_conn().rule("APPIMG", 1, "")   # any whole-image APP write drops
    res = rf(conn).flash(FIXED, APP)
    assert res.success is True
    assert res.tier == "256KB chunks @460800"
    assert any(CHUNK_FILE in c and "write_flash" in c for c in conn.history)


def test_escalation_emits_progress():
    conn = base_conn().rule("APPIMG", 1, "")
    events = []
    rf(conn).flash(FIXED, APP, on_progress=events.append)
    kinds = [e.kind for e in events]
    assert "tier_start" in kinds
    assert "tier_fail" in kinds        # the whole-image tiers failed
    assert "tier_ok" in kinds          # a chunked tier landed it
    assert kinds[-1] == "done"


# ---- autonomous recovery --------------------------------------------------

def test_power_cycle_uses_uhubctl_for_the_boards_port():
    conn = base_conn().rule("APPIMG", 1, "")
    rf(conn).flash(FIXED, APP)
    assert any("uhubctl -l 3 -p 1 -a off" in c for c in conn.history)
    assert any("uhubctl -l 3 -p 1 -a on" in c for c in conn.history)


def test_soft_reset_fallback_when_port_not_power_switchable():
    conn = base_conn()
    r = RobustFlasher(conn, "/dev/ttyACM0", sleep=NOSLEEP)  # no hub/hub_port
    assert r.can_power_cycle is False
    r.flash(FIXED, APP)
    assert not any("uhubctl" in c for c in conn.history)
    assert any("read_mac" in c for c in conn.history)  # DTR/RTS soft reset


class _FlakyChunk(EmulatedConnection):
    """Fails the first N chunk writes, then succeeds — to exercise per-chunk retry."""
    def __init__(self, fail_first=1):
        super().__init__(default_code=0, default_stdout="ok")
        self.rule("stat -c %s", 0, "262144")   # exactly one 256 KB chunk
        self._n = 0
        self._fail_first = fail_first

    def run(self, command, timeout=30):
        if "write_flash" in command and CHUNK_FILE in command:
            self._n += 1
            if self._n <= self._fail_first:
                self.history.append(command)
                return (1, "", "drop")
        return super().run(command, timeout)


def test_chunk_retries_with_power_cycle_then_succeeds():
    conn = _FlakyChunk(fail_first=2)
    events = []
    ok, failed = rf(conn)._flash_chunked(APP, 256 * 1024, 460800, 4, events.append)
    assert ok is True and failed is None
    # it recovered by power-cycling between the two failed attempts
    assert sum("uhubctl" in c and "-a off" in c for c in conn.history) >= 2
    assert any(e.kind == "chunk_retry" for e in events)
    assert any(e.kind == "chunk_ok" for e in events)


def test_chunk_gives_up_after_retries_exhausted():
    conn = base_conn().rule("write_flash", 1, "")  # every write drops forever
    ok, failed = rf(conn)._flash_chunked(APP, 256 * 1024, 460800, 3, lambda p: None)
    assert ok is False
    assert failed == 0x10000


# ---- hardware-limit classification ---------------------------------------

def test_all_tiers_fail_reports_overcurrent_diagnosis():
    conn = base_conn().rule("write_flash", 1, "")            # nothing ever writes
    conn.rule("grep -c over-current", 0, "7")                # kernel saw brownouts
    res = rf(conn).flash(FIXED, APP)
    assert res.success is False
    assert res.tier is None
    assert "over-current" in res.diagnosis
    # it never even wrote the bootloader -> report 0x0, not the app default,
    # and call out the stronger hardware-damage signal
    assert res.failed_offset == 0x0
    assert "bootloader" in res.diagnosis and "hardware damage" in res.diagnosis


def test_app_stage_failure_omits_the_severe_bootloader_note():
    # writes succeed for the tiny fixed regions but the app never lands -> the
    # verdict should NOT claim it couldn't take the bootloader.
    conn = base_conn().rule("APPIMG", 1, "").rule(CHUNK_FILE, 1, "")
    conn.rule("grep -c over-current", 0, "4")
    res = rf(conn).flash(FIXED, APP)
    assert res.success is False
    assert "bootloader" not in res.diagnosis
    assert res.failed_offset == 0x10000


def test_all_tiers_fail_without_overcurrent_blames_data_link():
    conn = base_conn().rule("write_flash", 1, "")
    conn.rule("grep -c over-current", 0, "0")
    res = rf(conn).flash(FIXED, APP)
    assert res.success is False
    assert "data link" in res.diagnosis or "cable" in res.diagnosis


def test_ladder_is_least_work_first():
    # the default ladder starts with whole-image writes and only then chunks,
    # shrinking chunk size while lowering baud
    assert DEFAULT_LADDER[0].chunk_bytes is None
    chunked = [t for t in DEFAULT_LADDER if t.chunk_bytes]
    sizes = [t.chunk_bytes for t in chunked]
    bauds = [t.baud for t in chunked]
    assert sizes == sorted(sizes, reverse=True)   # chunks shrink
    assert bauds == sorted(bauds, reverse=True)    # baud drops with them
