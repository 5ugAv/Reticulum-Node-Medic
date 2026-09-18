"""Board coverage — what the medic has actually proven, derived from its own
certificate ledger, crossed with what we INTEND to prove.

Operator, 2026-09-18: "the todo lists are gonna get done, so the list will
need to be adjusted as we move along." A hand-kept done-list rots the moment
work happens and then quietly lies. So DONE is never hand-kept: it is read
from the certificates the medic writes on every birth (the same store
ui.cert_store manages). Only INTENT — the small set of pairings we mean to
prove — is declared by hand, and TODO is intent minus done. Birth a board,
regenerate, and it leaves the todo by itself.

Pure: feed it cert dicts (from the medic, or a local copy) and an intent
list; it classifies and crosses them. scripts/board_coverage.py pulls the
certs and writes docs/BOARD_COVERAGE.md.

Classification is honest about a ledger written across format versions:
the firmware VERSION is the reliable tell (an RTNode-2400 firmware is the
0.x line, an RNode firmware the 1.8x line — verified against the medic's
store 2026-09-18), the role names a Pi build, node_type catches the rest,
and anything genuinely unreadable is "unknown", never guessed into a bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


def FIRMWARE_KIND(firmware: Optional[str]) -> str:
    """"rtnode2400" | "rnode" | "unknown" from a firmware version string.

    The RTNode-2400 firmware carries a 0.x version; the RNode firmware a
    1.x (1.85 was the line in the field, 2026-08). A missing version tells
    nothing on its own — the caller falls back to role/node_type."""
    v = (firmware or "").strip().lstrip("vV")
    if not v or not v[0].isdigit():
        return "unknown"
    major = v.split(".", 1)[0]
    if major == "0":
        return "rtnode2400"
    return "rnode"


@dataclass
class BoardFact:
    """One cert, classified. ``kind`` is the firmware role the board was
    proven in: "rnode" | "rtnode2400" | "pi_rnode" | "unknown"."""
    board: str
    kind: str
    node_name: str = ""
    firmware: str = ""
    born: str = ""


def classify_cert(cert: dict) -> BoardFact:
    board = (cert.get("board") or "").strip()
    role = (cert.get("role") or "").lower()
    node_type = (cert.get("node_type") or "").lower()
    fw = cert.get("firmware")
    # A Pi build: the board is a radio hung off a Raspberry Pi, and the
    # certificate says so in its role, not its board.
    if "propagation" in role or "lxmf" in role or node_type in (
            "pi_rnode", "propagation"):
        kind = "pi_rnode"
    elif node_type == "rtnode2400":
        kind = "rtnode2400"
    elif node_type == "rnode":
        # node_type wins when set, EXCEPT a 0.x firmware would contradict it —
        # trust the firmware line then (it is what actually got flashed).
        kind = "rtnode2400" if FIRMWARE_KIND(fw) == "rtnode2400" else "rnode"
    else:
        kind = FIRMWARE_KIND(fw)
        if kind == "unknown" and (cert.get("node_name") or "").lower().startswith(
                "rtnode"):
            kind = "rtnode2400"
    return BoardFact(board=board, kind=kind,
                     node_name=cert.get("node_name") or "",
                     firmware=str(fw or ""), born=str(cert.get("born") or "")[:10])


@dataclass
class CoverageRow:
    board: str
    kind: str              # the intended firmware role
    done: bool
    note: str = ""         # context: what the ledger DOES show for this board


def coverage(certs: Iterable[dict],
             intent: Iterable[Tuple[str, str]]) -> List[CoverageRow]:
    """Cross the declared intent against what the ledger proves.

    *intent* is ``[(board_display_name, kind)]`` — the pairings we mean to
    prove. A row is done when a cert proves that exact (board, kind). The
    note carries the OTHER kinds the ledger shows for that board, so an
    RNode-only board reads as "rnode proven, rtnode still owed" rather than
    a bare red cross.
    """
    facts = [classify_cert(c) for c in certs]
    proven: dict = {}
    for f in facts:
        proven.setdefault(f.board, set()).add(f.kind)
    rows = []
    for board, kind in intent:
        have = proven.get(board, set())
        done = kind in have
        others = sorted(have - {kind} - {"unknown"})
        note = ("already " + ", ".join(others) if others and not done
                else "")
        rows.append(CoverageRow(board=board, kind=kind, done=done, note=note))
    return rows
