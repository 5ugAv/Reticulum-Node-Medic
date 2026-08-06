"""Wiping a node's card over the wire so it can be born again.

This destroys a disk, so most of these tests are about refusing to point it at
the wrong thing.
"""

import pytest

from provisioning import decommission as dc


class FakeConn:
    def __init__(self, hostname="hope", fail=(), readback="0"):
        self.hostname = hostname
        self.fail = fail
        self.readback = readback
        self.ran = []

    def run(self, cmd, timeout=None):
        self.ran.append(cmd)
        if cmd.strip() == "hostname":
            return (0, self.hostname + "\n", "") if self.hostname is not None \
                else (1, "", "no route to host")
        for f in self.fail:
            if f in cmd:
                return (1, "", "dd: writing: No space left on device")
        if "wc -c" in cmd:
            return (0, self.readback, "")
        return (0, "", "")


# --- refusing to wipe the wrong machine ------------------------------------

def test_the_medic_itself_is_never_a_target():
    """The medic runs the same OS off the same kind of disk. Identity is the
    only thing that distinguishes it from a node."""
    for name in ("nodemedic", "NodeMedic", "nodemedic.local", "localhost"):
        assert dc.is_protected(name), name
        r = dc.decommission(FakeConn(hostname=name), expected_hostname="")
        assert r.ok is False and "medic itself" in r.message


def test_a_different_node_than_expected_is_refused():
    """The realistic bench mistake is aiming at the wrong MACHINE, not the
    wrong device — several nodes, one terminal."""
    conn = FakeConn(hostname="faith")
    r = dc.decommission(conn, expected_hostname="hope")
    assert r.ok is False
    assert "'hope'" in r.message and "'faith'" in r.message
    assert not any("dd if=/dev/zero" in c for c in conn.ran), "it wrote anyway"


def test_an_unreadable_hostname_refuses_rather_than_guessing():
    conn = FakeConn(hostname="")
    r = dc.decommission(conn, expected_hostname="hope")
    assert r.ok is False and "refusing to wipe blind" in r.message.lower()
    assert not any("dd if=/dev/zero" in c for c in conn.ran)


def test_no_expected_name_still_protects_the_medic_but_allows_a_node():
    """Naming the node is optional; protecting the medic is not."""
    assert dc.check_target("", "hope") is None
    assert dc.check_target("", "nodemedic") is not None


def test_hostname_match_is_case_insensitive():
    assert dc.check_target("HOPE", "hope") is None
    assert dc.check_target("hope", "HOPE") is None


# --- the wipe itself --------------------------------------------------------

def test_it_zeroes_enough_to_kill_the_partition_table_and_boot_header():
    cmd = dc.wipe_commands()[0]
    assert "if=/dev/zero" in cmd and "of=/dev/mmcblk0" in cmd
    assert f"count={dc.WIPE_MB}" in cmd
    assert dc.WIPE_MB >= 5, "a Pi image's first partition starts at 4 MiB"


def test_the_write_is_flushed_to_the_card():
    """Without this the zeros can sit in the node's write cache and never reach
    the card — a wipe that looks successful and leaves a bootable card."""
    cmds = dc.wipe_commands()
    assert "conv=fsync" in cmds[0]
    assert any(c.strip().endswith("sync") for c in cmds)


def test_a_successful_wipe_says_what_to_do_next():
    r = dc.decommission(FakeConn(), expected_hostname="hope")
    assert r.ok is True
    assert "power it off and on" in r.message.lower()
    assert r.hostname == "hope"


def test_a_failed_dd_is_reported_not_swallowed():
    r = dc.decommission(FakeConn(fail=("dd if=/dev/zero",)),
                        expected_hostname="hope")
    assert r.ok is False and "Wipe failed" in r.message


# --- verification -----------------------------------------------------------

def test_a_card_that_still_has_data_is_reported_as_not_wiped():
    """Otherwise the operator only discovers it at the next boot."""
    conn = FakeConn(readback="4096")     # 4096 non-zero bytes still there
    r = dc.decommission(conn, expected_hostname="hope")
    assert r.ok is False
    assert "still has data" in r.message and "would still boot" in r.message


@pytest.mark.parametrize("count,expect", [
    ("0", True),
    ("0\n", True),
    ("4096", False),
    ("", False),                       # no read-back is not proof of a wipe
    ("dd: cannot open", False),        # nor is an error message
])
def test_looks_wiped(count, expect):
    assert dc.looks_wiped(count) is expect


def test_verification_does_not_use_od_whose_output_lies():
    """od collapses runs of identical lines into '*', so an all-zero region
    dumps as zeros-plus-asterisk and any all-zeros test fails on formatting.
    That reported a GOOD wipe as failed the first time this ran."""
    cmd = dc.verify_commands()[0]
    assert "od " not in cmd
    assert "tr -d" in cmd and "wc -c" in cmd


def test_verify_reads_from_the_same_device_it_wiped():
    assert "/dev/sdz" in dc.verify_commands("/dev/sdz")[0]


# --- the operator-facing offer ----------------------------------------------
# 2026-08-02: the imaging screen told the operator to "wipe it first" and then
# offered no way to do it. The engine existed; the screen just never used it.

def test_the_screen_offers_the_wipe_it_tells_the_operator_to_do():
    src = open("ui/screens/pi_imager_screen.py").read()
    assert "_add_wipe_offer" in src
    assert "decommission" in src


def test_the_wipe_is_behind_a_SLIDE_not_a_tap():
    """This erases a working node. Modelled on the vault reset: the cost of an
    accidental tap is far higher than the cost of a deliberate drag."""
    src = open("ui/screens/pi_imager_screen.py").read()
    block = src[src.index("def _add_wipe_offer"):src.index("def _do_wipe")]
    assert "SlideToPowerOff" in block
    assert "slide to wipe" in block


def test_the_offer_says_what_is_lost_AND_what_is_not():
    src = open("ui/screens/pi_imager_screen.py").read()
    block = src[src.index("def _add_wipe_offer"):src.index("def _do_wipe")]
    assert "identity" in block and "certificate" in block
    assert "can't be undone" in block
    assert "mesh is not affected" in block, "must say the network survives"


def test_a_successful_wipe_tells_the_operator_to_power_cycle():
    """The medic CANNOT do this itself — uhubctl on the Pi 5 root hub does not
    cut VBUS. Without the prompt the flow silently stalls.

    Asserts the INTENT rather than one phrasing: the operator must be told to
    unplug and plug back in, AND to wait, because a fast in-and-out can leave
    enough charge in the Pi to stop it cold-booting (operator, 2026-08-06) —
    which produces the same silent stall the prompt exists to prevent.
    """
    src = open("ui/screens/pi_imager_screen.py").read()
    block = src[src.index("def _wipe_done"):]
    assert "unplug the Pi" in block and "plug it back in" in block
    assert "ten seconds" in block, (
        "no wait in the power-cycle prompt — a quick replug may not reset the Pi")


def test_the_wipe_reports_failure_rather_than_pretending():
    src = open("ui/screens/pi_imager_screen.py").read()
    block = src[src.index("def _do_wipe"):src.index("def _wipe_done")]
    assert "Couldn't reach the Pi" in block
    assert "DATA port" in block
