#!/usr/bin/env python3
"""Board training — teach the medic a board's fingerprint in one plug-in.

Operator, 2026-08-31: "instead of waiting for a board to be birthed to
narrow down selection for the next time, lets train the medic so a first
time user has had all this work done for them already."

So: plug a board in, tell this tool which model it is, and it reads the
board the way the birth flow reads it and files what it measured. Do that
once per board on the shelf and the picker stops asking — for every keeper,
because the result is written into the repo's shipped seed
(assets/board_traits_seed.json), which every medic and every clone carries.

    # what's plugged in, and what the picker currently thinks
    python3 scripts/train_boards.py --list

    # teach: read the board on this port as this model
    python3 scripts/train_boards.py --port /dev/ttyACM1 --board xiao_esp32s3

    # teach every connected board at once, when each is unambiguous
    python3 scripts/train_boards.py --auto

    # write what this medic has learned into the shippable seed
    python3 scripts/train_boards.py --export

HONESTY: this records only what the medic MEASURED on a board the operator
named. It never copies a datasheet, and --export refuses to invent entries
for boards nobody has plugged in. A wrong answer is corrected by training
the same model again — the newest reading wins.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import board_traits as bt                              # noqa: E402
from ui.board_detect import (_default_reader, parse_chip,       # noqa: E402
                             parse_flash_size, parse_mac, parse_psram)


def _catalogue():
    from workflows.rnode_boards import RNODE_BOARDS
    return RNODE_BOARDS


def _ports():
    from ui.hw_factories import local_board_ports
    return list(local_board_ports())


def read_board(port: str) -> dict:
    """Everything the medic can measure on this port, in one read — the SAME
    reader the birth flow uses, so what is learned is comparable with what is
    later measured (the one-measurement-method rule in ui.board_traits)."""
    out = _default_reader(port)
    return {"port": port, "chip": parse_chip(out), "mac": parse_mac(out),
            "psram": parse_psram(out), "flash_size": parse_flash_size(out),
            "raw": out}


def teach(port: str, board_key: str, quiet: bool = False) -> bool:
    boards = _catalogue()
    if board_key not in boards:
        print(f"! '{board_key}' is not a board this medic knows. Options:")
        for k in sorted(boards):
            print(f"    {k}")
        return False
    r = read_board(port)
    if not r["chip"]:
        print(f"! Couldn't read a chip on {port} — is the board awake, and is "
              f"the cable a DATA cable?")
        return False
    ok_traits = bt.learn(board_key, {"psram": r["psram"],
                                     "flash_size": r["flash_size"]})
    ok_mac = bt.learn_mac_prefix(board_key, r["mac"]) if r["mac"] else False
    if not quiet:
        print(f"  {board_key:<26} chip={r['chip']:<9} psram={str(r['psram']):<5} "
              f"flash={str(r['flash_size']):<5} mac={r['mac']}")
        print(f"    traits {'filed' if ok_traits else 'unchanged'}; "
              f"mac prefix {'filed' if ok_mac else 'unchanged'}")
    return ok_traits or ok_mac


def show_list() -> int:
    from ui.board_detect import detect_board
    boards = list(_catalogue().values())
    ports = _ports()
    if not ports:
        print("No work boards on USB (the medic's own radio doesn't count).")
        return 1
    for p in ports:
        det = detect_board(boards, ports_fn=lambda pp=p: [pp])
        offered = [b.key for b in (det.get("boards") or [])]
        print(f"{p}")
        print(f"   chip {det.get('chip')} | mac {det.get('mac')} | "
              f"psram {det.get('psram')} | flash {det.get('flash_size')}")
        print(f"   picked: {det.get('board_key')} | suggested: "
              f"{det.get('likely_key')}")
        print(f"   grid ({len(offered)}): {offered}")
    return 0


def auto_teach() -> int:
    """Teach every connected board the picker can already name on its own —
    the free half of the exercise (self-naming boards, bridge-port boards).
    Ambiguous ones still need the operator to say which model they are."""
    from ui.board_detect import detect_board
    boards = list(_catalogue().values())
    taught = skipped = 0
    for p in _ports():
        det = detect_board(boards, ports_fn=lambda pp=p: [pp])
        key = det.get("board_key")
        if not key:
            print(f"  {p}: still ambiguous ({len(det.get('boards') or [])} "
                  f"candidates) — teach it with --port {p} --board <key>")
            skipped += 1
            continue
        teach(p, key)
        taught += 1
    print(f"\ntaught {taught}, need you for {skipped}")
    return 0


def export_seed() -> int:
    """Copy what this medic has learned into the repo's shipped seed, so
    every future medic and clone is born knowing it."""
    learned = bt._read(bt.STORE)
    if not learned:
        print("Nothing learned on this medic yet — train some boards first.")
        return 1
    seed = bt._read(bt.SEED)
    for key, entry in learned.items():
        if not isinstance(entry, dict):
            continue
        base = seed.get(key)
        base = dict(base) if isinstance(base, dict) else {}
        pres = list(base.get("mac_prefixes") or [])
        for pre in (entry.get("mac_prefixes") or []):
            if pre not in pres:
                pres.append(pre)
        base.update(entry)
        if pres:
            base["mac_prefixes"] = pres[-16:]
        seed[key] = base
    os.makedirs(os.path.dirname(bt.SEED), exist_ok=True)
    with open(bt.SEED, "w") as f:
        json.dump(seed, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"Seed written: {bt.SEED}\n  {len(seed)} board model(s): "
          f"{', '.join(sorted(seed))}")
    print("Commit it so every medic and clone carries this knowledge.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true",
                    help="what's plugged in and what the picker thinks")
    ap.add_argument("--auto", action="store_true",
                    help="teach every board the picker can already name")
    ap.add_argument("--port", help="the port to read")
    ap.add_argument("--board", help="which model that board IS")
    ap.add_argument("--export", action="store_true",
                    help="write this medic's learning into the shipped seed")
    a = ap.parse_args()
    if a.list:
        return show_list()
    if a.auto:
        return auto_teach()
    if a.export:
        return export_seed()
    if a.port and a.board:
        return 0 if teach(a.port, a.board) else 1
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
