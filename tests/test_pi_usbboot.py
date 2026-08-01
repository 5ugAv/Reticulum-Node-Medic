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


def test_the_hardware_proof_is_recorded_with_what_was_actually_seen():
    """The module claimed NOT YET PROVEN until 2026-08-01. It is proven now, and
    a caveat left standing after the fact misleads exactly as much as an
    unearned claim — so the docstring must carry the observed USB IDs."""
    assert "NOT YET PROVEN" not in pu.__doc__
    assert "PROVEN" in pu.__doc__
    assert "0a5c:2764" in pu.__doc__ and "0a5c:0001" in pu.__doc__
    assert "0a5c:2764" in pu.BENCH_CHECK


# --- doing it FOR the operator ---------------------------------------------
# Operator requirement 2026-08-02: a brand-new Pi plugged in at the start of
# BIRTH must become a card reader BY ITSELF. Running rpiboot by hand is fine on
# a bench and useless in the field.

class Fake:
    """A medic whose USB state changes when rpiboot is 'run'."""

    def __init__(self, lsusb, disks_before, disks_after=None, rpiboot_ok=True):
        self.lsusb_text = lsusb
        self.disks = list(disks_before)
        self.after = disks_after
        self.rpiboot_ok = rpiboot_ok
        self.rpiboot_calls = []

    def lsusb(self):
        return self.lsusb_text

    def disks_fn(self):
        return list(self.disks)

    def rpiboot(self, usb_id):
        self.rpiboot_calls.append(usb_id)
        if not self.rpiboot_ok:
            return (False, "rpiboot failed: no device found")
        if self.after is not None:
            self.disks = list(self.after)
        return (True, "presenting")


CARD = {"name": "sda", "path": "/dev/sda", "size": "29.7G"}
STICK = {"name": "sdb", "path": "/dev/sdb", "size": "8G"}


def _run(fake, **kw):
    return pu.ensure_card_reader(fake.lsusb, fake.disks_fn, fake.rpiboot,
                                 sleep=lambda s: None,
                                 now=_ticker(), **kw)


def _ticker():
    t = {"v": 0.0}

    def now():
        t["v"] += 1.0
        return t["v"]
    return now


def test_a_brand_new_pi_is_turned_into_a_card_reader_automatically():
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[], disks_after=[CARD])
    r = _run(f)
    assert r.ok and r.state == pu.CARD_READER
    assert r.device == "/dev/sda"
    assert f.rpiboot_calls == ["0a5c:2764"], "rpiboot was not run for the operator"


def test_it_claims_the_disk_that_APPEARED_not_whatever_is_lying_around():
    """A USB stick already plugged in must never be chosen — this device is
    about to have an operating system written over it."""
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[STICK], disks_after=[STICK, CARD])
    r = _run(f)
    assert r.ok and r.device == "/dev/sda", "grabbed the wrong disk"


def test_an_already_imaged_node_is_never_converted():
    """It boots as a node; forcing it into a reader would invite imaging over a
    working node."""
    f = Fake(_GADGET_NODE, disks_before=[])
    r = _run(f)
    assert r.ok is False and r.state == pu.GADGET
    assert f.rpiboot_calls == []
    assert "wipe it first" in r.message
    assert r.needs_operator


def test_nothing_plugged_in_asks_for_the_data_port():
    f = Fake(_MEDIC_BASE, disks_before=[])
    r = _run(f)
    assert r.ok is False and r.state == pu.ABSENT
    assert "DATA USB port" in r.message and r.needs_operator
    assert f.rpiboot_calls == []


def test_a_pi_that_never_offers_its_card_says_what_to_do():
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[], disks_after=[])   # no card ever
    r = _run(f, timeout=5)
    assert r.ok is False
    assert "never offered its card" in r.message
    assert "plug it back in" in r.message


def test_rpiboot_failure_is_surfaced_verbatim():
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[], rpiboot_ok=False)
    r = _run(f)
    assert r.ok is False and "no device found" in r.message


def test_progress_is_reported_in_the_operators_words():
    """The word 'rpiboot' must never reach the screen."""
    seen = []
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[], disks_after=[CARD])
    pu.ensure_card_reader(f.lsusb, f.disks_fn, f.rpiboot, sleep=lambda s: None,
                          now=_ticker(), on_progress=seen.append)
    assert seen and all("rpiboot" not in m.lower() for m in seen)
    assert any("card" in m.lower() for m in seen)


def test_needs_operator_separates_retryable_from_go_do_something():
    f = Fake(_ZERO_2W_BOOTROM, disks_before=[], disks_after=[])
    assert _run(f, timeout=5).needs_operator is False   # retryable
    assert _run(Fake(_MEDIC_BASE, [])).needs_operator is True


def test_the_imager_screen_asks_the_pi_before_asking_for_a_reader():
    """Source-level guard: the 'plug in a card reader' prompt must be the
    FALLBACK, not the first thing an operator with a Pi in hand is told."""
    src = open("ui/screens/pi_imager_screen.py").read()
    assert "_offer_pi_as_reader" in src
    i_pi = src.index("if not targets and self._offer_pi_as_reader()")
    i_reader = src.index("Put the Pi's microSD into a USB card reader")
    assert i_pi < i_reader, "the reader prompt comes first"


def test_the_imager_screen_never_says_rpiboot_to_the_operator():
    src = open("ui/screens/pi_imager_screen.py").read()
    shown = [l for l in src.splitlines() if "_line(" in l or "text=" in l]
    assert not any("rpiboot" in l.lower() for l in shown)


def test_an_already_built_node_is_not_offered_imaging_by_the_screen():
    """Converting a working node into a card reader would invite writing an OS
    over it."""
    src = open("ui/screens/pi_imager_screen.py").read()
    assert "pi_usbboot.GADGET" in src
    assert "wipe it first" in src
