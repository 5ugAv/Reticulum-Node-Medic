"""A rebirthed board must be ASKED what to become, not handed its old life back.

Operator, 2026-08-06, on the sequence after choosing not to keep a flashed node:

  "we need to give the option of whether they're gonna make an r node or an r
   node plus pi or if it's a v four, v three, or other appropriate board, would
   they wanna make an RT node? Currently, it just goes straight back to flashing
   what the board previously was."

THE BUG was one misplaced line. birth_screen builds the question from the chip's
firmware options, and any RNode-capable board can equally be a Pi's radio — so
"Pi + RNode" is appended as a second option. That append lived INSIDE the
`if len(det_opts) > 1:` branch that asks the question, which made it unreachable
for exactly the boards that needed it:

    RAK4631 -> firmware_options('nrf52840') == ['rnode']   # length ONE
    -> the ask-branch is skipped
    -> 'pi_rnode' is never appended
    -> the screen shows "RNode" as already decided, with a small "change" link

So the one board family with a single detected firmware could never be offered
the Pi pairing, and a rebirth led straight back to what the board had been.

Moving the append ABOVE the length check is the whole fix: the option now exists
before anything counts them.
"""
from ui.board_detect import firmware_options

RANK = {"rnode": 0, "rtnode2400": 1, "pi_rnode": 2}


def offered(chip):
    """Mirror of birth_screen's option assembly, in the fixed order."""
    opts = list(firmware_options(chip) or [])
    if "rnode" in opts and "pi_rnode" not in opts:
        opts = opts + ["pi_rnode"]
    return sorted(opts, key=lambda k: RANK.get(k, 99))


def test_an_nrf52_board_is_offered_the_pi_pairing():
    """THE regression. One detected firmware must still yield two CHOICES."""
    opts = offered("nrf52840")
    assert "rnode" in opts and "pi_rnode" in opts, (
        "a RAK4631 could only ever be born as a plain RNode — the Pi pairing "
        "was unreachable because it had a single detected firmware")
    assert len(opts) > 1, "one option means the operator is never asked"


def test_an_esp32s3_board_is_offered_RTNode_as_well():
    """The operator's other case: 'if it's a v four, v three... would they wanna
    make an RT node?' RTNode-2400 needs an S3, so it belongs here."""
    opts = offered("esp32s3")
    assert {"rnode", "rtnode2400", "pi_rnode"} <= set(opts)


def test_a_classic_esp32_is_NOT_offered_RTNode():
    """RTNode-2400 needs an ESP32-S3. Offering an impossible build and failing
    later wastes the operator's time and teaches them to distrust the list."""
    assert "rtnode2400" not in offered("esp32")


def test_every_rnode_capable_chip_gets_a_real_choice():
    """Whatever the board, if it can be an RNode it can be a Pi's radio — so no
    board family may land on the pre-decided single-option screen."""
    for chip in ("nrf52840", "esp32s3", "esp32"):
        opts = offered(chip)
        if "rnode" in opts:
            assert len(opts) > 1, f"{chip} would be auto-decided, not asked"


def test_the_family_order_is_the_one_the_operator_specified():
    """RNode -> RTNode-2400 -> Pi + RNode, uniform everywhere (2026-07-31).
    Auto-leading with RTNode-2400 once built the wrong firmware."""
    assert offered("esp32s3") == ["rnode", "rtnode2400", "pi_rnode"]


def test_the_append_happens_before_anything_counts_the_options():
    """Guards the actual fix: if the append slides back inside the ask-branch,
    single-firmware boards silently stop being asked again."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_build_chooser")
    append = src.index('det_opts = list(det_opts) + ["pi_rnode"]')
    gate = src.index("if det_opts and len(det_opts) > 1:")
    assert append < gate, (
        "the pi_rnode append moved back below the length check — boards with a "
        "single detected firmware will stop being offered the Pi pairing")
