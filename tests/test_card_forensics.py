"""Reading a node's card to answer "does this need writing again?".

Operator, 2026-08-09, with a Pi that enumerated on USB and then wedged: "can you
diagnose the sd card from here, this is worth doing if it wont connect, to let
the user know if the sd needs to be re imaged."

The reasoning is pure and tested here; the mounting needs a card and root.
"""
from provisioning.card_forensics import (CardReport, Check, first_boot_completed,
                                         diagnose)

CMDLINE_FRESH = ("console=serial0,115200 root=PARTUUID=abc rootfstype=ext4 "
                 "fsck.repair=yes rootwait systemd.run=/boot/firstrun.sh "
                 "systemd.run_success_action=reboot")
CMDLINE_BOOTED = ("console=serial0,115200 root=PARTUUID=abc rootfstype=ext4 "
                  "fsck.repair=yes rootwait")


# --- the single most useful fact on the card -------------------------------

def test_the_firstrun_marker_means_it_has_never_finished_booting():
    """Raspberry Pi OS rewrites cmdline.txt to drop systemd.run= during the
    first boot. Still there = that boot never completed."""
    assert first_boot_completed(CMDLINE_FRESH, True, True) is False
    # and it outranks anything else: marker present is decisive
    assert first_boot_completed(CMDLINE_FRESH, False, False) is False


def test_agreeing_signals_are_needed_to_claim_a_boot():
    """One signal could be a quirk of an image; three is a story."""
    assert first_boot_completed(CMDLINE_BOOTED, False, False) is True


def test_disagreeing_signals_say_so_rather_than_pick_one():
    # marker gone (suggests booted) but firstrun.sh still sitting there
    assert first_boot_completed(CMDLINE_BOOTED, True, False) is None


def test_an_unreadable_cmdline_is_unknown_not_false():
    assert first_boot_completed(None, None, None) is None


# --- "unknown" must never be rendered as "fine" ----------------------------

def test_could_not_look_is_not_a_clean_result():
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"), Check("first_boot", "unknown")])
    assert r.booted is None
    assert r.needs_reimaging is None, "no verdict without evidence"
    assert "unclear" in r.headline


def test_a_card_with_no_pi_system_needs_writing():
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "bad")])
    assert r.needs_reimaging is True
    assert "no Raspberry Pi system" in r.headline


def test_missing_cable_settings_need_writing_even_if_it_booted():
    """It can boot perfectly and still never appear over USB — which is exactly
    the failure that looks identical to a dead cable."""
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"), Check("gadget", "bad"),
                           Check("first_boot", "ok")])
    assert r.needs_reimaging is True
    assert "never appear over USB" in r.headline


def test_a_card_that_has_booted_does_not_need_writing():
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"), Check("gadget", "ok"),
                           Check("first_boot", "ok")])
    assert r.needs_reimaging is False
    assert r.booted is True


def test_never_booted_is_not_by_itself_a_verdict():
    """A card that has never booted may simply never have been powered. The
    card cannot know that, so it does not pretend to."""
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"), Check("gadget", "ok"),
                           Check("first_boot", "bad")])
    assert r.booted is False
    assert r.needs_reimaging is None
    assert "nothing has ever booted from it" in r.headline


# --- safety ----------------------------------------------------------------

def test_no_card_is_reported_plainly_and_nothing_is_touched():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return ""                       # findmnt / lsblk see nothing
    r = diagnose(run=run)
    assert r.boot_partition is None
    assert "No node card" in r.checks[0].detail
    assert not any("mount" in c and "-o ro" not in c for c in calls), \
        "nothing may be mounted read-write"


def test_it_never_writes():
    """Read-only, always: the decision to spend four minutes writing a card
    stays with the operator, and a card that looks blank has held the previous
    night's evidence before now."""
    from tests.srcutil import src
    text = src("provisioning/card_forensics.py")
    body = text.split('"""', 2)[2]      # past the module docstring
    for danger in ("mkfs", "dd ", " > ", "rm -", "wipefs", "parted", "fdisk"):
        assert danger not in body, f"{danger!r} has no business in a diagnosis"
    assert "-o ro" in body, "the only mount must be read-only"


def test_an_empty_reader_makes_no_claim_about_a_card():
    """Caught the first time this ran against a real (empty) reader: it said
    "This card has no Raspberry Pi system on it" — a confident statement about
    a card that was not there."""
    r = CardReport(checks=[Check("present", "bad")])
    assert "no card in Node Medic's reader" in r.headline
    assert r.needs_reimaging is None, "nothing to judge"


# --- the rule this module exists to keep, broken by its own first live run ---

def test_a_failed_mount_is_never_a_verdict():
    """Live, 2026-08-09: the mount was refused (the medic's sudo whitelists
    mount BY FULL COMMAND LINE, and this module had invented its own mount
    point). is_a_pi_card then read False and fell through to "nothing bootable
    on it at all" — needs_reimaging: True, against a card that was fine."""
    r = CardReport(checks=[Check("present", "ok"), Check("readable", "unknown")])
    assert r.needs_reimaging is None, "could not look is not a fault in the card"
    assert "not the same as the card being bad" in r.headline


def test_labels_still_say_something_when_the_files_cannot_be_read():
    """lsblk names the partitions without any mounting, and Raspberry Pi OS
    labels them bootfs/rootfs. Weaker than reading the files, but real."""
    r = CardReport(checks=[Check("present", "ok"), Check("readable", "unknown"),
                           Check("pi_card", "ok")])
    assert r.needs_reimaging is None
    assert "could not read into it" in r.headline


def test_it_mounts_only_where_the_medic_allows():
    from tests.srcutil import src
    text = src("provisioning/card_forensics.py")
    assert "from provisioning.sd_edit import SD_MOUNT as INSPECT_MOUNT" in text, \
        "sudo whitelists mount by full command line — an invented path is refused"


def test_it_says_who_the_card_is_for_and_whether_wifi_is_on_it():
    """2026-08-09: a Pi ran perfectly off its own supply and never appeared on
    the network. Answering "is Wi-Fi even written on this card" took a LAN
    sweep and twenty minutes, with the card by then back inside the Pi. Both
    facts live in custom.toml, right next to everything else this reads."""
    from tests.srcutil import func_source
    src = func_source("provisioning/card_forensics.py", "diagnose")
    assert "custom.toml" in src
    assert "node_name" in src and "wifi" in src
    assert "ONLY ever be reached" in src, \
        "a card with no Wi-Fi is a node with one road in — say so"


# --- written is not the same as applied ------------------------------------

def test_settings_written_but_never_applied_needs_writing_again():
    """2026-08-09: a card said "Wi-Fi details are on it" AND "it has booted",
    and the node never appeared on the network. Both boot-partition facts were
    true. What they cannot see is a first boot that STARTED, cleared its own
    marker, and then died on a browning-out rail before writing the network
    config."""
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"),
                           Check("gadget", "ok"), Check("wifi", "ok"),
                           Check("first_boot", "ok"), Check("applied", "bad")])
    assert r.needs_reimaging is True
    assert "never applied" in r.headline


def test_applied_settings_leave_the_card_alone():
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"),
                           Check("gadget", "ok"), Check("first_boot", "ok"),
                           Check("applied", "ok")])
    assert r.needs_reimaging is False


def test_not_being_able_to_open_the_system_partition_is_not_a_verdict():
    r = CardReport(checks=[Check("present", "ok"), Check("pi_card", "ok"),
                           Check("gadget", "ok"), Check("first_boot", "ok"),
                           Check("applied", "unknown")])
    assert r.needs_reimaging is False, "the boot partition still said it booted"


def test_an_empty_settings_directory_is_not_applied_settings():
    """THE false OK, 2026-08-09. `test -s` on a DIRECTORY always succeeds — a
    directory has non-zero size whether or not anything is in it. So an empty
    /etc/NetworkManager/system-connections read as "the Wi-Fi settings were
    applied", and the report told the operator their card was fine while the
    node sat there unable to join anything. A false OK ends the search."""
    from tests.srcutil import func_source
    src = func_source("provisioning/card_forensics.py", "_check_rootfs")
    assert "-d " in src, "a directory must be LISTED, not size-tested"
    assert "elif [ -s " in src, "the size test is for files only"


def test_the_directory_branch_needs_a_real_entry(tmp_path):
    """Behavioural: an empty directory must not count."""
    import os
    from provisioning.card_forensics import CardReport, _check_rootfs
    empty = tmp_path / "etc" / "NetworkManager" / "system-connections"
    empty.mkdir(parents=True)
    calls = {}

    def run(cmd):
        if "lsblk" in cmd:
            return "/dev/sdb1 vfat\n/dev/sdb2 ext4\n"
        if "findmnt" in cmd:
            return str(tmp_path) if calls.get("mounted") else ""
        if "mount -o ro" in cmd:
            calls["mounted"] = True
            return ""
        if cmd.startswith("if [ -d "):
            import subprocess
            return subprocess.run(["bash", "-lc", cmd.replace("{mnt}", "")],
                                  capture_output=True, text=True).stdout
        return ""
    rep = CardReport()
    _check_rootfs(run, rep, "sdb")
    states = {c.key: c.state for c in rep.checks}
    assert states.get("applied") == "bad", "an empty directory is not settings"
