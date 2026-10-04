"""Birth section — the node types the tool can create, in presentation order.

Pure data (no Kivy) so the ordering is unit-testable and the Kivy screen stays a
thin view. "Birth" is the tool's name for provisioning a brand-new node; it ends
on the photographable birth certificate the build workflows already produce.
"""

from __future__ import annotations

from typing import List, Tuple

from workflows.rnode_boards import RNodeBoard, official_boards, custom_boards

#: (key, display label) for the three node types, in the exact order shown
#: under Birth: RTNode-2400, then RNode, then the Raspberry Pi.
#:
#: The KEY stays ``pi_rnode`` — it is written into birth certificates and
#: read back by the registry, so renaming it would orphan every node already
#: built. Only the label changed (operator, 2026-09-06): the radio is no
#: longer forced, it is offered at the end.
BIRTH_NODE_TYPES: List[Tuple[str, str]] = [
    ("rtnode2400", "RTNode-2400"),
    ("rnode", "RNode"),
    ("pi_rnode", "Raspberry Pi propagation node"),
    ("mitosis", "Clone - copy this medic (needs a Raspberry Pi 5)"),
]


def birth_node_types() -> List[Tuple[str, str]]:
    return list(BIRTH_NODE_TYPES)


def rnode_board_choices() -> List[RNodeBoard]:
    """Boards shown after choosing RNode — official boards first (by rnodeconf
    menu order), the custom board(s) last."""
    return official_boards() + custom_boards()


def board_blocker(board: RNodeBoard, band_mhz: int = 915) -> str:
    """Why THIS medic cannot flash *board* right now, or "" — the sentence the
    pickers tag a row with and the gate shows instead of a confirm.

    Two causes, both found by the 2026-10-03 readiness sweep: a board whose
    flash sequence cannot be answered from here (T-Beam/T3S3 chip variants),
    and a custom-fork board whose image this medic simply does not carry.
    Both used to be offered as plain rows and refused — or erased — later."""
    why = board.cannot_flash_reason(band_mhz)
    if why:
        return why
    if board.flash_method == "autoinstall":
        return ""
    import os
    try:
        if board.flash_method == "serial_dfu":
            path = os.path.expanduser(f"{board.build_dir}/{board.dfu_package}")
        else:
            from workflows.rnode_flash import fork_image_for
            path = os.path.expanduser(fork_image_for(board, "bin"))
    except Exception:                                              # noqa: BLE001
        return ""
    if os.path.exists(path):
        return ""
    return (f"This Node Medic has no {board.display_name} firmware on it. That "
            f"board's firmware is built from source and this medic doesn't carry "
            f"the build — ask for a release that carries it.")
