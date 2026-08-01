"""The Pi as its own card reader — classification is pure, so all of this runs
with no hardware."""

from provisioning import pi_usbboot as pu

# Real-shaped lsusb output from the medic (Jonesey is always attached).
_MEDIC_BASE = (
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    "Bus 003 Device 002: ID 303a:1001 Espressif USB JTAG/serial debug unit\n"
    "Bus 004 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub\n")

_ZERO_2W_BOOTROM = _MEDIC_BASE + (
    "Bus 001 Device 005: ID 0a5c:2764 Broadcom Corp. BCM2835 Boot\n")

_GADGET_NODE = _MEDIC_BASE + (
    "Bus 001 Device 006: ID 0525:a4a2 Netchip Technology, Inc. "
    "Linux-USB Ethernet/RNDIS Gadget\n")


def test_a_zero_2w_with_a_blank_card_is_recognised_as_imageable():
    """The whole point: no card reader needed for this state."""
    st = pu.classify(_ZERO_2W_BOOTROM)
    assert st.state == pu.BOOTROM
    assert st.needs_rpiboot and st.can_image_without_a_reader
    assert "Zero 2 W" in st.detail


def test_an_already_imaged_pi_is_not_offered_imaging():
    """It booted our OS and came up on the cable link — imaging it would
    destroy the node we just made."""
    st = pu.classify(_GADGET_NODE)
    assert st.state == pu.GADGET
    assert not st.can_image_without_a_reader
    assert not st.needs_rpiboot
    assert "doesn't need imaging" in pu.guidance(st)


def test_the_medics_own_radio_is_never_mistaken_for_a_pi():
    """Jonesey is on USB at all times; a loose ID match would see a Pi
    everywhere."""
    assert pu.classify(_MEDIC_BASE).state == pu.ABSENT


def test_after_rpiboot_the_pi_is_seen_by_the_disk_it_presents():
    """It stops advertising a Broadcom boot ID and just becomes a disk, so the
    ID alone can't detect this state."""
    st = pu.classify(_MEDIC_BASE, disk_appeared=True)
    assert st.state == pu.CARD_READER
    assert st.can_image_without_a_reader


def test_bootrom_wins_over_a_stale_disk_flag():
    """If it's still in boot-ROM mode it has NOT been converted yet, whatever
    the caller thinks it saw."""
    assert pu.classify(_ZERO_2W_BOOTROM, disk_appeared=True).state == pu.BOOTROM


def test_every_documented_soc_is_recognised():
    for usb_id, name in pu.BOOTROM_IDS.items():
        out = f"Bus 001 Device 005: ID {usb_id} Broadcom Corp.\n"
        st = pu.classify(out)
        assert st.state == pu.BOOTROM, usb_id
        assert st.usb_id == usb_id


def test_case_in_lsusb_ids_does_not_matter():
    assert pu.classify("Bus 001 Device 5: ID 0A5C:2764 Broadcom\n").state == pu.BOOTROM


# --- picking the right payload ---------------------------------------------

def test_a_zero_gets_the_32bit_msd_payload_by_default():
    """Bare rpiboot = the msd payload, which is the BCM283x one. Handing a Zero
    the 64-bit gadget would simply not boot it."""
    assert pu.rpiboot_command("0a5c:2764") == ["sudo", "-n", "rpiboot"]
    assert pu.rpiboot_command("") == ["sudo", "-n", "rpiboot"]


def test_pi4_and_pi5_get_their_own_64bit_payload():
    for soc in ("0a5c:2711", "0a5c:2712"):
        cmd = pu.rpiboot_command(soc)
        assert cmd[-2:] == ["-d", pu.MSD_PAYLOAD_64], soc


# --- running it -------------------------------------------------------------

def test_missing_rpiboot_is_reported_not_crashed():
    ok, msg = pu.run_rpiboot(runner=lambda a, **k: (1, "not found"))
    assert ok is False and "isn't installed" in msg


def test_successful_run_reports_the_card_is_available():
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return (0, "")                       # which rpiboot, then rpiboot

    ok, msg = pu.run_rpiboot("0a5c:2764", runner=runner)
    assert ok is True and "presenting its SD card" in msg
    assert calls[-1] == ["sudo", "-n", "rpiboot"]


def test_a_failing_rpiboot_surfaces_its_own_error():
    def runner(argv, **kw):
        if argv[0] == "which":
            return (0, "/usr/bin/rpiboot")
        return (1, "Failed to write to device")

    ok, msg = pu.run_rpiboot(runner=runner)
    assert ok is False and "Failed to write" in msg


# --- what the operator is told ---------------------------------------------

def test_guidance_names_the_data_port_when_nothing_is_plugged_in():
    """The commonest mistake on a Zero — it has two identical micro-USB ports
    and only one carries data."""
    g = pu.guidance(pu.classify(_MEDIC_BASE))
    assert "DATA USB port" in g and "PWR IN" in g


def test_the_unproven_status_is_recorded_in_the_module():
    """This path has never run on real hardware; the module must say so rather
    than read as verified."""
    assert "NOT YET PROVEN" in pu.__doc__
    assert "0a5c:2764" in pu.BENCH_CHECK
