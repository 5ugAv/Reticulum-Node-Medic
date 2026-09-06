"""How big to make a node's root partition, and why not to fill the card.

MEASURED 2026-09-06 from the carried image's own ext4 superblock: the rootfs is
2.43 GB total with 0.33 GB FREE, and nothing in this repo ever expands it —
no resize2fs, no growpart, no parted anywhere. (The medic's own card looks
expanded only because Raspberry Pi Imager wrote it; `ds=nocloud;i=rpi-imager-…`
is still in its cmdline, which is what sent this investigation the wrong way
for a while.)

0.33 GB is not enough: node_mode.py sets `enable_node = yes` and nothing sets
`message_storage_limit`, so a node inherits LXMF's 500 MB default — a store
larger than the free space it has to live in.
"""
import pytest

from provisioning.pi_imager import (
    MAX_USED_FRACTION, NODE_ROOTFS_BYTES, lxmf_storage_limit_mb, rootfs_plan)

GB = 1000 ** 3
#: Where the rootfs starts in the carried image, and how big it is — both read
#: off the real image, not invented.
BOOT_END = 1064960 * 512
IMAGE_ROOT = 2_430_000_000


def _plan(card_gb, current=IMAGE_ROOT):
    return rootfs_plan(int(card_gb * GB), current, BOOT_END)


# --------------------------------------------------------------------------- #
# The rule that matters most
# --------------------------------------------------------------------------- #

def test_it_never_shrinks_the_filesystem():
    """Shrinking below the data on the card destroys it, and a card handed back
    smaller than the image will not boot. If the arithmetic ever asks for less
    than what is there, the plan must be to leave it alone."""
    for card_gb in (2, 3, 4, 8, 16, 64):
        p = _plan(card_gb)
        assert p["target_bytes"] >= IMAGE_ROOT or not p["grow"], card_gb


def test_a_card_too_small_to_grow_says_so_instead_of_trying():
    """Usable space below what the image already occupies. The honest answer is
    "leave it alone and warn", not a resize that would have to shrink."""
    p = _plan(2.9)
    assert p["grow"] is False
    assert p["warning"]


def test_a_small_card_warns_even_when_it_can_grow_a_little():
    """A 4 GB card fits the filesystem but leaves almost no spare pool. It
    works; the operator should know it is not the card to leave on a roof."""
    p = _plan(4)
    assert p["warning"], "grew onto a small card without saying so"


# --------------------------------------------------------------------------- #
# Growing, but not to fill the card
# --------------------------------------------------------------------------- #

def test_a_16gb_card_gets_a_working_node_and_a_big_spare_pool():
    """The operator's standard node card. The unallocated tail is the point:
    it is the controller's spare pool for wear levelling, which is the cheapest
    endurance available on hardware meant to outlive its installer."""
    p = _plan(16)
    assert p["grow"] is True
    assert p["target_bytes"] == NODE_ROOTFS_BYTES
    assert p["spare_bytes"] > p["target_bytes"], "less spare than filesystem"


@pytest.mark.parametrize("card_gb", [16, 32, 64, 128])
def test_a_bigger_card_does_not_get_a_bigger_filesystem(card_gb):
    """A node needs 6 GB whatever card it is on. Extra capacity becomes spare
    pool, not a filesystem nobody fills."""
    assert _plan(card_gb)["target_bytes"] == NODE_ROOTFS_BYTES


@pytest.mark.parametrize("card_gb", [8, 16, 32, 64])
def test_the_filesystem_never_takes_more_than_its_share(card_gb):
    p = _plan(card_gb)
    usable = card_gb * GB - BOOT_END
    assert p["target_bytes"] <= usable * MAX_USED_FRACTION + 1


def test_the_plan_always_fits_the_card():
    for card_gb in (4, 8, 16, 32, 64):
        p = _plan(card_gb)
        assert p["target_bytes"] + BOOT_END <= card_gb * GB


def test_the_reason_is_a_sentence_an_operator_can_read():
    p = _plan(16)
    assert "GB" in p["reason"] and "spare pool" in p["reason"]


def test_an_impossible_card_is_refused_not_guessed_at():
    p = rootfs_plan(BOOT_END // 2, IMAGE_ROOT, BOOT_END)
    assert p["grow"] is False and p["warning"]


# --------------------------------------------------------------------------- #
# The message store must fit the disk it lives on
# --------------------------------------------------------------------------- #

def test_the_store_is_sized_from_free_space_not_the_card():
    """Operators use whatever card they have (operator, 2026-09-06), so the
    limit is computed at build time from what is ACTUALLY free on the node."""
    small = lxmf_storage_limit_mb(1_500_000_000)
    big = lxmf_storage_limit_mb(8 * GB)
    assert small < big


def test_the_store_always_leaves_the_disk_room_to_breathe():
    """THE defect this was written for. A node that fills its root filesystem
    stops forwarding, stops reporting, and looks dead from every screen."""
    for free in (2 * GB, 4 * GB, 16 * GB):
        assert lxmf_storage_limit_mb(free) * 1_000_000 < free * 0.75


def test_the_cramped_image_gets_a_token_store_not_the_500mb_default():
    """0.33 GB free — what every node built from the unexpanded image has —
    cannot hold LXMF's 500 MB default."""
    assert lxmf_storage_limit_mb(330_000_000) == 32


def test_a_grown_node_gets_a_useful_store():
    assert lxmf_storage_limit_mb(4 * GB) >= 500


def test_a_node_with_no_room_still_relays():
    """No mailbox, but a node that cannot STORE must still forward — the floor
    is a token store, never zero."""
    assert lxmf_storage_limit_mb(0) >= 32
    assert lxmf_storage_limit_mb(-1) >= 32


# --------------------------------------------------------------------------- #
# A medic card and a node card want opposite things
# --------------------------------------------------------------------------- #

def test_a_medic_card_fills_the_card_and_a_node_card_does_not():
    """Measured on the live medic: it uses 21 GB — 5.2 GB of that the Arduino
    toolchains a clone must carry. A bounded rootfs would strand HAWKEYE at its
    first build. A NODE needs 6 GB and benefits from the rest staying
    unallocated, so the two cases are opposites and the imager must not treat
    them alike."""
    src = open("provisioning/pi_imager.py", encoding="utf-8").read()
    assert '"rootfs_fill": bool(medic)' in src
    assert '"rootfs_bytes": (0 if medic else _node_rootfs_bytes(device_path))' in src


def test_an_unmeasurable_card_is_left_alone():
    """Guessing a partition size on a device whose capacity cannot be read is
    how a card gets destroyed."""
    from provisioning.pi_imager import _node_rootfs_bytes
    assert _node_rootfs_bytes("/dev/sdz", lambda argv: (1, "")) == 0
    assert _node_rootfs_bytes("/dev/sdz", lambda argv: (0, "not a number")) == 0


def test_a_node_card_is_grown_to_the_planned_size():
    from provisioning.pi_imager import NODE_ROOTFS_BYTES, _node_rootfs_bytes
    sixteen_gb = lambda argv: (0, "15931539456\n")     # noqa: E731
    assert _node_rootfs_bytes("/dev/sdz", sixteen_gb) == NODE_ROOTFS_BYTES


def test_the_privileged_helper_refuses_to_shrink():
    """resize2fs CAN shrink, and a shrink below the data destroys the card.
    The refusal lives in the helper because that is the half that runs as root."""
    src = open("assets/scripts/prepare_card.py", encoding="utf-8").read()
    assert "not shrinking it to" in src
    assert "want_sectors <= size" in src


def test_the_helper_checks_the_target_fits_before_writing():
    src = open("assets/scripts/prepare_card.py", encoding="utf-8").read()
    assert "does not fit on this card" in src


def test_the_filesystem_is_checked_before_it_is_resized():
    """resize2fs refuses an unclean filesystem, and a freshly-dd'd image often
    is one."""
    src = open("assets/scripts/prepare_card.py", encoding="utf-8").read()
    assert 'e2fsck' in src and 'resize2fs' in src
    assert src.index("e2fsck") < src.index('run(["resize2fs"')


def test_the_message_store_limit_is_appended_when_only_commented_out():
    """lxmd ships message_storage_limit as a COMMENTED example, so a sed that
    only rewrites active lines would silently change nothing and the node would
    keep the 500 MB default."""
    from workflows.node_mode import _set_or_append_kv_cmd
    cmd = _set_or_append_kv_cmd("/x/config", "message_storage_limit", "300")
    assert "grep -qE" in cmd and ">>" in cmd


def test_turning_on_propagation_sizes_the_store():
    src = open("workflows/node_mode.py", encoding="utf-8").read()
    assert "storage_limit_for(connection)" in src
    assert "message_storage_limit" in src
