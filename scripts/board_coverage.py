"""Generate docs/BOARD_COVERAGE.md from the medic's live cert ledger.

    python3 scripts/board_coverage.py [--certs-dir DIR] [--write]

Without --write it prints the report; with it, rewrites docs/BOARD_COVERAGE.md.
Default reads the medic over SSH (the authoritative store); --certs-dir reads a
local copy. The DONE tables are the ledger; the TODO is INTENT minus done
(provisioning.board_intent) — so the todo adjusts itself as births happen
(operator, 2026-09-18).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from provisioning.board_coverage import classify_cert, coverage      # noqa: E402
from provisioning.board_intent import INTENT, PI_HOSTS_INTENT        # noqa: E402

MEDIC = "nodemedic@nodemedic.local"
REMOTE_CERTS = "/home/nodemedic/.reticulum-node-medic/certificates"


def _load_remote():
    """One JSON array over the wire — jq if present, else a python one-liner
    on the medic (it has python3). No fragile concatenation to re-split."""
    remote = (
        "python3 -c 'import json,glob;"
        "print(json.dumps([json.load(open(f)) for f in "
        "glob.glob(\"" + REMOTE_CERTS + "/*\") "
        "if not f.endswith(\".bak\")]))'")
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", MEDIC, remote],
        capture_output=True, text=True, timeout=40).stdout.strip()
    try:
        return json.loads(out)
    except Exception:
        return []


def _load_dir(d):
    out = []
    for fn in os.listdir(d):
        try:
            out.append(json.load(open(os.path.join(d, fn))))
        except Exception:
            pass
    return out


def _parse_concat(text):
    certs = []
    for chunk in text.split("}\n,"):
        chunk = chunk.strip().rstrip(",")
        if not chunk:
            continue
        if not chunk.endswith("}"):
            chunk += "}"
        try:
            certs.append(json.loads(chunk))
        except Exception:
            pass
    return certs


def render(certs):
    facts = [classify_cert(c) for c in certs]
    lines = ["# Board coverage — what the medic has proven",
             "",
             "RTNode-2400 below means the Node Medic build, **RTNode-2400-NM** (see `docs/FIRMWARE_NAMING.md`). **Generated from the cert ledger — do not hand-edit.** Edit only "
             "`provisioning/board_intent.py`, then regenerate with "
             "`python3 scripts/board_coverage.py --write`. DONE is the medic's "
             "own certificates; TODO is intent minus done, so it shrinks itself "
             "as boards get born (operator, 2026-09-18).", ""]

    # --- DONE: every board, in the firmware roles the ledger proves ---------
    by_board = {}
    for f in facts:
        by_board.setdefault(f.board, set()).add(f.kind)
    lines += ["## Proven (from the ledger)", "",
              "| Board | RNode | RTNode-2400 | Pi+RNode |",
              "|---|---|---|---|"]
    for board in sorted(by_board):
        k = by_board[board]
        tick = lambda x: "✅" if x in k else "—"
        lines.append(f"| {board} | {tick('rnode')} | {tick('rtnode2400')} "
                     f"| {tick('pi_rnode')} |")

    # --- TODO: intent minus done -------------------------------------------
    rows = coverage(certs, [(b, k) for b, k in INTENT])
    todo = [r for r in rows if not r.done]
    done = [r for r in rows if r.done]
    lines += ["", "## To prove (intent minus done)", ""]
    if todo:
        lines += ["| Board | Firmware | Status |", "|---|---|---|"]
        for r in todo:
            lines.append(f"| {r.board} | {r.kind} | ⬜ owed"
                         + (f" — {r.note}" if r.note else "") + " |")
    else:
        lines.append("*Nothing owed — every declared intention is proven.*")
    if done:
        lines += ["", "_Already proven from the intent list: "
                  + ", ".join(f"{r.board} ({r.kind})" for r in done) + "._"]

    # --- Pi host coverage ---------------------------------------------------
    pi_done = {f.board for f in facts if f.kind == "pi_rnode"}
    lines += ["", "## Raspberry Pi host builds", "",
              "Proven Pi+RNode radio boards: "
              + (", ".join(sorted(pi_done)) if pi_done else "none") + ".",
              "", "Declared host intentions (host verification is a bench "
              "check — the ledger records the radio, not always the Pi model):"]
    for host, radio in PI_HOSTS_INTENT:
        lines.append(f"- **{host}** with {radio}")
    return "\n".join(lines) + "\n"


def main():
    args = sys.argv[1:]
    if "--certs-dir" in args:
        certs = _load_dir(args[args.index("--certs-dir") + 1])
    else:
        certs = _load_remote()
    report = render(certs)
    if "--write" in args:
        dest = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "docs", "BOARD_COVERAGE.md")
        open(dest, "w").write(report)
        print(f"wrote {dest} ({len(certs)} certs)")
    else:
        print(report)


if __name__ == "__main__":
    main()
