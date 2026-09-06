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
