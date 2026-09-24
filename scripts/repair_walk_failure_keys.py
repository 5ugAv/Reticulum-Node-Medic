#!/usr/bin/env python3
"""Repair ``walk_failures.jsonl`` lines written before the boundary-ring
feature (2026-09-24) stamped every ``LinkFailure`` with the node it belongs
to (``node_key``; monitor/synapse_range.py's ``LinkFailure.node_key``,
monitor/boundary_walk.py's ``BoundaryWalkSession.evidence()``).

A failure banked before that date carries no ``node_key`` (or ``null``) —
real evidence, un-attributable to any one node's boundary ring. This is a
one-pass, read-only-until-verified repair: for each such line, find the
nearest-in-time ``LinkObservation`` in ``walk_observations.jsonl`` with
``source == "boundary_walk"`` and use ITS ``heard_from`` as the failure's
node — the same walk that produced a loss almost always also produced at
least one hit nearby in time, banked under the SAME node key
(BoundaryWalkSession.evidence stamps both currencies identically:
``evidence_key or node_key``).

    # see what would change, writes nothing
    python3 scripts/repair_walk_failure_keys.py --dry-run

    # repair in place (atomic write + read-back verification)
    python3 scripts/repair_walk_failure_keys.py

NEVER guessed: a failure with no observation inside the time window is left
``node_key: None``, exactly as before — reported, not silently dropped.

HONESTY: this is a tool for the OPERATOR to run later against a real medic's
files. It is not executed against any real device data as part of building
this feature — only against synthetic fixtures in
tests/test_repair_walk_failure_keys.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

# Run from anywhere: the repo root, not scripts/, is the import root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_WALK_DIR = "~/.reticulum-node-medic"
_FAIL_FILE = "walk_failures.jsonl"
_OBS_FILE = "walk_observations.jsonl"

#: How close in time a failure and an observation must be to count as "the
#: same walk". A boundary walk pings every monitor.boundary_walk.PING_EVERY_S
#: (20 s) and a typical walk runs minutes, not hours — but the operator can
#: pause mid-walk (backgrounded app, a wait for a GPS fix), so a tight window
#: risks missing the walk's own hits. Four hours (2026-09-24, operator
#: judgement call — "a few hours" per the review brief) comfortably covers
#: any real pause inside ONE walk while staying far short of a typical gap
#: between two SEPARATE walks against two different nodes on the same day,
#: which would misattribute a loss to the wrong node.
DEFAULT_WINDOW_S = 4.0 * 3600.0


# ---- pure logic (testable against plain dicts, no file I/O) ----------------

def _read_jsonl_lines(path: str) -> Tuple[List[dict], int]:
    """Every line that parses as a JSON object, plus a count of lines that
    did not (corrupt/partial lines are skipped, never fatal — the same rule
    monitor.boundary_walk._load_jsonl already uses for these same files)."""
    out: List[dict] = []
    skipped = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(d, dict):
                    out.append(d)
                else:
                    skipped += 1
    except OSError:
        return [], 0
    return out, skipped


def repair_failure_lines(failure_lines: List[dict], observation_lines: List[dict],
                         window_s: float = DEFAULT_WINDOW_S
                         ) -> Tuple[List[dict], int, int]:
    """The pure repair: ``(repaired_lines, n_repaired, n_unmatched)``.

    *repaired_lines* is EVERY failure line, same order, same length, with
    ``node_key`` filled in where a match was found. A line that already had
    a usable ``node_key`` passes through byte-for-byte (as a dict; no field
    is dropped, including ones this module does not know about — a future
    format grows and this repair must not truncate it). A line that could
    not be matched gets an EXPLICIT ``node_key: None`` — never left
    ambiguous about whether repair was attempted.

    Nearest-in-time match, not first-within-window: several observations
    can fall inside *window_s* (a chatty walk pings every 20 s) and the
    nearest one is the best evidence of what the operator was standing next
    to at that moment."""
    candidates: List[Tuple[float, str]] = []
    for o in observation_lines:
        if o.get("source") != "boundary_walk":
            continue
        heard_from = o.get("heard_from")
        observed_at = o.get("observed_at")
        if not heard_from or not isinstance(observed_at, (int, float)):
            continue
        candidates.append((float(observed_at), str(heard_from)))
    candidates.sort(key=lambda c: c[0])

    out: List[dict] = []
    repaired = 0
    unmatched = 0
    for f in failure_lines:
        existing = f.get("node_key")
        if existing:
            out.append(f)
            continue
        t = f.get("observed_at")
        best_key: Optional[str] = None
        best_dt: Optional[float] = None
        if isinstance(t, (int, float)):
            for obs_t, heard_from in candidates:
                dt = abs(obs_t - float(t))
                if dt <= window_s and (best_dt is None or dt < best_dt):
                    best_dt = dt
                    best_key = heard_from
        new_f = dict(f)
        if best_key is not None:
            new_f["node_key"] = best_key
            repaired += 1
        else:
            new_f["node_key"] = None
            unmatched += 1
        out.append(new_f)
    return out, repaired, unmatched


# ---- the file: atomic write + read-back verify ------------------------------

def _write_jsonl_atomic(path: str, lines: List[dict]) -> None:
    """Temp file + fsync + os.replace — mirrors monitor/walk_anchor.py's
    ``_write_all`` atomic-write pattern: a power cut mid-write leaves the OLD
    file, never half a new one. ``allow_nan=False`` for the same reason
    monitor.boundary_walk.append_evidence uses it: a NaN would otherwise
    write as ``NaN``, not JSON, and every future read of this file would
    silently drop that line forever."""
    text = "".join(json.dumps(d, sort_keys=True, allow_nan=False) + "\n"
                   for d in lines)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def run_repair(base_dir: str = _WALK_DIR, window_s: float = DEFAULT_WINDOW_S,
               dry_run: bool = False) -> dict:
    """Do the repair against the files in *base_dir*. Returns a summary dict
    (also what the CLI prints); writes nothing when *dry_run* or when there
    is nothing to repair. Read-only-until-verified: the rewritten file is
    parsed back and compared to what was intended to write before this
    function reports success — a write that landed wrong is a raised
    exception here, never a quiet "done"."""
    base = os.path.expanduser(base_dir)
    fail_path = os.path.join(base, _FAIL_FILE)
    obs_path = os.path.join(base, _OBS_FILE)

    fail_lines, fail_skipped = _read_jsonl_lines(fail_path)
    obs_lines, obs_skipped = _read_jsonl_lines(obs_path)

    repaired_lines, n_repaired, n_unmatched = repair_failure_lines(
        fail_lines, obs_lines, window_s=window_s)

    summary = {
        "total_failure_lines": len(fail_lines),
        "already_keyed": len(fail_lines) - n_repaired - n_unmatched,
        "repaired": n_repaired,
        "left_unmatched": n_unmatched,
        "corrupt_failure_lines_skipped": fail_skipped,
        "corrupt_observation_lines_skipped": obs_skipped,
        "written": False,
    }

    if dry_run or n_repaired == 0:
        return summary

    _write_jsonl_atomic(fail_path, repaired_lines)

    # Read back and verify before declaring success (project law: a
    # privileged write is read back and compared, never assumed).
    verify_lines, verify_skipped = _read_jsonl_lines(fail_path)
    if verify_skipped or verify_lines != repaired_lines:
        raise RuntimeError(
            "repair_walk_failure_keys: the rewritten file did not read back "
            "as written — refusing to report success")
    summary["written"] = True
    return summary


def _print_summary(summary: dict, dry_run: bool) -> None:
    verb = "would repair" if dry_run else "repaired"
    print(f"[repair] {summary['total_failure_lines']} failure line(s): "
          f"{summary['already_keyed']} already keyed, "
          f"{verb} {summary['repaired']}, "
          f"left honestly unmatched {summary['left_unmatched']}.",
          flush=True)
    if summary["corrupt_failure_lines_skipped"]:
        print(f"[repair] {summary['corrupt_failure_lines_skipped']} corrupt "
              "failure line(s) skipped (unchanged).", flush=True)
    if summary["corrupt_observation_lines_skipped"]:
        print(f"[repair] {summary['corrupt_observation_lines_skipped']} "
              "corrupt observation line(s) skipped.", flush=True)
    if dry_run:
        print("[repair] --dry-run: nothing written.", flush=True)
    elif summary["repaired"]:
        print("[repair] walk_failures.jsonl rewritten and read back clean.",
              flush=True)
    else:
        print("[repair] nothing to repair; file left untouched.", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-dir", default=_WALK_DIR,
                   help=f"directory holding {_FAIL_FILE}/{_OBS_FILE} "
                        f"(default: {_WALK_DIR})")
    p.add_argument("--window-hours", type=float,
                   default=DEFAULT_WINDOW_S / 3600.0,
                   help="max time gap between a failure and the observation "
                        "used to attribute it (default: "
                        f"{DEFAULT_WINDOW_S / 3600.0:g})")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change; write nothing")
    args = p.parse_args(argv)
    try:
        summary = run_repair(base_dir=args.base_dir,
                             window_s=args.window_hours * 3600.0,
                             dry_run=args.dry_run)
    except Exception as e:                                          # noqa: BLE001
        print(f"[repair] failed: {e}", flush=True)
        return 1
    _print_summary(summary, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
