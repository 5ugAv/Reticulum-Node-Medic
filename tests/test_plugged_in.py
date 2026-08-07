"""The medic must know what is physically plugged into it (task #71).

Every case here is one the old code got wrong by collapsing it into a single
"Unplugged — plug it back in…". They are written from the operator's bench
report of 2026-08-06 rather than from the implementation.
"""

from provisioning import plugged_in as pin


GADGET = "Bus 001 Device 004: ID 0525:a4a2 Netchip Linux-USB Ethernet/RNDIS Gadget"
BOOTROM = "Bus 001 Device 003: ID 0a5c:2764 Broadcom BCM2836/2837"
MASS = "Bus 001 Device 003: ID 0a5c:0001 Broadcom BCM2835 boot"
HUB = "Bus 001 Device 002: ID 05e3:0610 Genesys Logic, Inc. Hub"
NOTHING = "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"

ONE_CARD = {"state": "one", "path": "/dev/sdb", "label": "29.7G SD Reader",
            "detail": "Found 29.7G SD Reader."}


def test_a_working_gadget_is_success_and_stops_the_polling():
    s = pin.read(GADGET, node_name="hope")
    assert s.state == pin.PI_ALIVE
    assert s.is_good and s.is_settled
    assert "hope" in s.headline


# --- THE ONE THAT STUNG ------------------------------------------------------
# "medic is still on this screen after we unplugged the pi and put the sd card
# in the reader." The medic had every fact it needed and said the wrong thing.

def test_our_own_card_appearing_in_our_own_reader_is_recognised():
    s = pin.read(NOTHING, card=ONE_CARD, waited_s=300,
                 our_card_serial="AABBCC", card_serial="AABBCC",
                 node_name="hope")
    assert s.state == pin.OUR_CARD_BACK
    assert "just wrote" in s.headline
    assert "plug it back in" not in (s.headline + s.detail).lower()
    # and it must say where the card actually needs to go
    assert "Pi's own slot" in s.detail


def test_a_card_it_cannot_identify_is_NOT_claimed_as_ours():
    """Claiming recognition we don't have is how a diagnostic tool loses its
    authority. An unknown card must be reported as unknown."""
    s = pin.read(NOTHING, card=ONE_CARD, waited_s=300,
                 our_card_serial="AABBCC", card_serial="ZZZZZZ")
    assert s.state == pin.A_CARD_BACK
    assert "can't tell" in s.detail


def test_no_serial_available_means_unknown_not_assumed():
    """Serials are not always readable. Absence must degrade to 'a card',
    never to 'your card'."""
    s = pin.read(NOTHING, card=ONE_CARD, waited_s=300,
                 our_card_serial="", card_serial="")
    assert s.state == pin.A_CARD_BACK


def test_two_cards_are_never_guessed_between():
    s = pin.read(NOTHING, card={"state": "several", "detail": "Take one out."},
                 waited_s=300)
    assert s.state == pin.SEVERAL_CARDS
    assert s.is_settled


# --- diagnoses the old copy buried ------------------------------------------

def test_bootrom_is_reported_as_a_card_that_did_not_boot():
    """The Pi IS plugged in. Telling the operator to plug it in is worse than
    saying nothing — it sends them to check hardware that is already fine."""
    s = pin.read(BOOTROM, waited_s=300, node_name="hope")
    assert s.state == pin.PI_WONT_BOOT
    assert "didn't boot the card" in s.headline
    assert "plug it back in" not in s.headline.lower()
    assert any(a["key"] == "rewrite_card" for a in s.actions)


def test_mass_storage_says_it_is_acting_as_a_reader():
    s = pin.read(MASS, waited_s=300)
    assert s.state == pin.PI_AS_READER
    assert "card reader" in s.detail


# --- the split the old copy could not make ----------------------------------
# "no USB device appeared" and "a device appeared but no gadget" are different
# faults with different fixes. One snapshot cannot tell them apart, which is
# why the baseline is threaded through.

def test_nothing_new_since_the_baseline_blames_cable_port_power():
    base = pin._ids(NOTHING)
    s = pin.read(NOTHING, waited_s=300, baseline_ids=base)
    assert s.state == pin.NO_USB_AT_ALL
    assert "cable" in s.detail
    assert "charge-only" in s.detail          # the fault that keeps recurring


def test_something_new_appeared_blames_the_cards_setup_not_the_cable():
    base = pin._ids(NOTHING)
    s = pin.read(NOTHING + "\n" + HUB, waited_s=300, baseline_ids=base)
    assert s.state == pin.NO_GADGET
    assert "cable and power are fine" in s.detail


def test_the_two_silences_give_DIFFERENT_answers():
    """The regression that matters: if these ever collapse back into one
    message, the operator is again sent to check the wrong thing."""
    base = pin._ids(NOTHING)
    quiet = pin.read(NOTHING, waited_s=300, baseline_ids=base)
    noisy = pin.read(NOTHING + "\n" + HUB, waited_s=300, baseline_ids=base)
    assert quiet.state != noisy.state
    assert quiet.detail != noisy.detail


# --- patience ---------------------------------------------------------------

def test_it_does_not_cry_fault_before_the_pi_has_had_time_to_boot():
    """A Zero 2 W takes a while off a freshly written card. Calling a fault
    early teaches the operator to ignore the screen."""
    s = pin.read(NOTHING, waited_s=5, baseline_ids=pin._ids(NOTHING))
    assert s.state == pin.WAITING
    assert not s.is_settled


def test_patience_expires():
    s = pin.read(NOTHING, waited_s=pin.PATIENCE_S + 1,
                 baseline_ids=pin._ids(NOTHING))
    assert s.state != pin.WAITING


# --- it reports, it never acts ----------------------------------------------

def test_this_module_proposes_but_never_does():
    """Naming a branch is not taking it. Nothing here may mount, write or
    reboot — the destructive step stays behind a deliberate press.

    Checked over the AST, not the text: the first version of this test grepped
    for "subprocess" and tripped on the docstring PROMISING not to use it."""
    import ast
    tree = ast.parse(open("provisioning/plugged_in.py").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "os", "shutil", "pty"):
        assert forbidden not in imported, (
            f"plugged_in.py imports {forbidden} — it must only decide, not act")


def test_every_situation_offers_a_way_onward_once_it_is_settled():
    """The screen this replaces had ONE exit, worded as giving up. A settled
    state with no action is a trap (see #76)."""
    cases = [
        pin.read(BOOTROM, waited_s=300),
        pin.read(MASS, waited_s=300),
        pin.read(NOTHING, card=ONE_CARD, waited_s=300,
                 our_card_serial="A", card_serial="A"),
        pin.read(NOTHING, card={"state": "several"}, waited_s=300),
        pin.read(NOTHING, waited_s=300, baseline_ids=pin._ids(NOTHING)),
    ]
    for s in cases:
        assert s.actions, f"{s.state} offers the operator nothing"


# --- the screen wiring ------------------------------------------------------
# CI has no Kivy, so these assert the contract from source, like the surgery
# and birth-guide tests do.

def _screen_src():
    return open("ui/screens/pi_imager_screen.py").read()


def test_the_waiting_screen_asks_plugged_in_rather_than_mapping_one_state():
    """The bug was a chain of elifs on a single USB state. If that comes back,
    the four situations collapse into one message again."""
    src = _screen_src()
    tick = src[src.index("def _boot_tick"):src.index("def _swap_callout_to_action")]
    assert "plugged_in.read(" in tick
    assert "Unplugged — plug it back in" not in tick, (
        "the catch-all message is back")


def test_the_card_serial_is_recorded_while_the_card_is_still_in_the_reader():
    """Recognition needs something to match against, and the only moment the
    card is definitely in OUR reader is when we write it."""
    src = _screen_src()
    assert "_written_card_serial = pi_imager.disk_serial(path)" in src


def test_the_situation_panel_survives_a_rebuilt_column():
    """self.col is cleared in several places; a remembered box would be an
    orphan and the buttons would render nowhere at all."""
    src = _screen_src()
    show = src[src.index("def _show_situation"):src.index("def _situation_action")]
    assert "box.parent is not self.col" in show


def test_unsettled_states_keep_polling_so_a_late_pi_is_still_caught():
    """A diagnosis that could still come good must not stop the watch — the Pi
    may simply be slow, and the screen should correct itself."""
    src = _screen_src()
    tick = src[src.index("def _boot_tick"):src.index("def _swap_callout_to_action")]
    settled = tick.index("if sit.is_settled:")
    unsettled = tick.index("if sit.state != plugged_in.WAITING:")
    assert settled < unsettled
    # the stop lives ONLY in the settled branch
    assert "_stop_boot_poll()" in tick[settled:unsettled]
    assert "_stop_boot_poll()" not in tick[unsettled:]
