from ui.birth import birth_node_types, rnode_board_choices


def test_birth_offers_four_node_types_in_order():
    # required order: RTNode-2400, RNode, Pi + RNode, then Mitosis (clone —
    # moved under BIRTH 2026-07-19; only usable with a Pi 5 target, which its
    # workflow verifies as step one)
    keys = [k for k, _ in birth_node_types()]
    assert keys == ["rtnode2400", "rnode", "pi_rnode", "mitosis"]


def test_birth_labels_are_human():
    labels = [label for _, label in birth_node_types()]
    assert labels == ["RTNode-2400", "RNode", "Pi + RNode",
                      "Mitosis (clone tool) - requires a Raspberry Pi 5"]


def test_rnode_choices_official_first_custom_last():
    boards = rnode_board_choices()
    assert len(boards) >= 15                      # 14 official + custom
    # official (autoinstall) come before the custom (arduino_cli) board
    methods = [b.flash_method for b in boards]
    assert methods[0] == "autoinstall"
    assert methods[-1] == "arduino_cli"
    assert boards[-1].key == "heltec_wireless_tracker"
