"""scripts/repair_walk_failure_keys.py — the one-pass repair for
walk_failures.jsonl lines banked before node_key existed (2026-09-24).

Pure logic (repair_failure_lines) against synthetic dicts, plus the file-
level behaviour (run_repair) against tmp_path — this script is NEVER run
against real device data as part of building this feature."""

import json
import os

from scripts.repair_walk_failure_keys import (DEFAULT_WINDOW_S, repair_failure_lines,
                                              run_repair)

NOW = 2_000_000_000.0


def _fail(observed_at, node_key=None, distance_km=1.0, lat=1.0, lon=2.0):
    return {"distance_km": distance_km, "observed_at": observed_at,
           "lat": lat, "lon": lon, "node_key": node_key, "confirmed": False,
           "snr_db": None, "note": "boundary walk"}


def _obs(observed_at, heard_from, source="boundary_walk"):
    return {"heard_by": "MEDIC", "heard_from": heard_from,
           "observed_at": observed_at, "source": source,
           "distance_km": 1.0}


# ---- pure logic --------------------------------------------------------------

def test_a_failure_already_keyed_is_left_untouched():
    fails = [_fail(NOW, node_key="already-here")]
    out, repaired, unmatched = repair_failure_lines(fails, [])
    assert out == fails
    assert repaired == 0 and unmatched == 0


def test_a_failure_is_matched_to_the_nearest_observation_in_the_same_walk():
    fails = [_fail(NOW)]
    obs = [_obs(NOW - 30, "far-node"), _obs(NOW - 5, "near-node"),
          _obs(NOW + 40, "far-node-2")]
    out, repaired, unmatched = repair_failure_lines(fails, obs)
    assert repaired == 1 and unmatched == 0
    assert out[0]["node_key"] == "near-node"


def test_two_walks_interleaved_each_failure_finds_its_own_walks_node():
    """Two separate walks (different nodes) recorded on the same day: a
    failure from walk A must never be attributed to walk B's node just
    because B's observations happen to be interleaved in the file."""
    walk_a_t0 = NOW
    walk_b_t0 = NOW + 3 * 3600       # three hours later, a different walk
    fails = [_fail(walk_a_t0 + 200, node_key=None),
            _fail(walk_b_t0 + 200, node_key=None)]
    obs = [_obs(walk_a_t0, "node-a"), _obs(walk_a_t0 + 100, "node-a"),
          _obs(walk_b_t0, "node-b"), _obs(walk_b_t0 + 100, "node-b")]
    out, repaired, unmatched = repair_failure_lines(fails, obs)
    assert repaired == 2 and unmatched == 0
    assert out[0]["node_key"] == "node-a"
    assert out[1]["node_key"] == "node-b"


def test_a_failure_with_nothing_in_the_window_is_left_honestly_unmatched():
    fails = [_fail(NOW)]
    obs = [_obs(NOW - 10 * 3600, "too-old"), _obs(NOW + 10 * 3600, "too-new")]
    out, repaired, unmatched = repair_failure_lines(fails, obs, window_s=3600.0)
    assert repaired == 0 and unmatched == 1
    assert out[0]["node_key"] is None


def test_non_boundary_walk_observations_are_never_used():
    fails = [_fail(NOW)]
    obs = [_obs(NOW, "wrong-source", source="splitter")]
    out, repaired, unmatched = repair_failure_lines(fails, obs)
    assert repaired == 0 and unmatched == 1


def test_an_observation_with_no_heard_from_is_never_used():
    fails = [_fail(NOW)]
    obs = [_obs(NOW, None)]
    out, repaired, unmatched = repair_failure_lines(fails, obs)
    assert repaired == 0 and unmatched == 1


def test_unknown_extra_fields_on_a_failure_line_survive_the_repair():
    """A future format grows a field this script does not know about —
    repair must not silently drop it."""
    f = _fail(NOW)
    f["some_future_field"] = "keep me"
    out, repaired, unmatched = repair_failure_lines([f], [_obs(NOW, "node-x")])
    assert repaired == 1
    assert out[0]["some_future_field"] == "keep me"


def test_node_key_null_is_treated_the_same_as_missing():
    f = _fail(NOW, node_key=None)
    assert f["node_key"] is None
    out, repaired, unmatched = repair_failure_lines([f], [_obs(NOW, "node-y")])
    assert repaired == 1 and out[0]["node_key"] == "node-y"


# ---- file-level: atomic write + read-back verify ---------------------------

def _write_jsonl(path, dicts):
    with open(path, "w", encoding="utf-8") as fh:
        for d in dicts:
            fh.write(json.dumps(d) + "\n")


def test_run_repair_writes_atomically_and_reads_back_verified(tmp_path):
    base = str(tmp_path)
    _write_jsonl(os.path.join(base, "walk_failures.jsonl"),
                [_fail(NOW, node_key=None), _fail(NOW, node_key="kept")])
    _write_jsonl(os.path.join(base, "walk_observations.jsonl"),
                [_obs(NOW - 5, "matched-node")])
    summary = run_repair(base_dir=base)
    assert summary["repaired"] == 1
    assert summary["already_keyed"] == 1
    assert summary["left_unmatched"] == 0
    assert summary["written"] is True

    with open(os.path.join(base, "walk_failures.jsonl")) as fh:
        lines = [json.loads(l) for l in fh if l.strip()]
    assert len(lines) == 2
    assert {l["node_key"] for l in lines} == {"matched-node", "kept"}
    # temp file cleaned up
    assert not os.path.exists(os.path.join(base, "walk_failures.jsonl.tmp"))


def test_run_repair_dry_run_writes_nothing(tmp_path):
    base = str(tmp_path)
    path = os.path.join(base, "walk_failures.jsonl")
    _write_jsonl(path, [_fail(NOW, node_key=None)])
    _write_jsonl(os.path.join(base, "walk_observations.jsonl"),
                [_obs(NOW, "would-match")])
    before = open(path).read()
    summary = run_repair(base_dir=base, dry_run=True)
    assert summary["repaired"] == 1
    assert summary["written"] is False
    after = open(path).read()
    assert before == after                  # nothing written


def test_run_repair_with_nothing_to_repair_leaves_the_file_untouched(tmp_path):
    base = str(tmp_path)
    path = os.path.join(base, "walk_failures.jsonl")
    _write_jsonl(path, [_fail(NOW, node_key="already-fine")])
    _write_jsonl(os.path.join(base, "walk_observations.jsonl"), [])
    mtime_before = os.path.getmtime(path)
    summary = run_repair(base_dir=base)
    assert summary["repaired"] == 0
    assert summary["written"] is False
    assert os.path.getmtime(path) == mtime_before


def test_run_repair_on_missing_files_reports_zero_not_a_crash(tmp_path):
    summary = run_repair(base_dir=str(tmp_path / "nothing-here"))
    assert summary["total_failure_lines"] == 0
    assert summary["repaired"] == 0
    assert summary["written"] is False


def test_a_corrupt_line_is_skipped_and_counted_not_fatal(tmp_path):
    base = str(tmp_path)
    path = os.path.join(base, "walk_failures.jsonl")
    with open(path, "w") as fh:
        fh.write(json.dumps(_fail(NOW, node_key=None)) + "\n")
        fh.write("{not json\n")
    _write_jsonl(os.path.join(base, "walk_observations.jsonl"),
                [_obs(NOW, "node-z")])
    summary = run_repair(base_dir=base)
    assert summary["corrupt_failure_lines_skipped"] == 1
    assert summary["repaired"] == 1


# ---- the CLI wrapper ---------------------------------------------------------

def test_main_dry_run_exits_zero_and_prints_a_summary(tmp_path, capsys):
    from scripts.repair_walk_failure_keys import main
    base = str(tmp_path)
    _write_jsonl(os.path.join(base, "walk_failures.jsonl"),
                [_fail(NOW, node_key=None)])
    _write_jsonl(os.path.join(base, "walk_observations.jsonl"),
                [_obs(NOW, "node-q")])
    rc = main(["--base-dir", base, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "repair" in out.lower()
    assert "dry-run" in out.lower() or "nothing written" in out.lower()
    # dry-run must not have written anything
    with open(os.path.join(base, "walk_failures.jsonl")) as fh:
        assert json.loads(fh.readline())["node_key"] is None


def test_default_window_is_a_few_hours():
    """Sanity on the operator's own "a few hours" ask — not one ping's worth
    (would miss a paused walk) and not a whole day (would cross into an
    unrelated walk)."""
    assert 1 * 3600.0 <= DEFAULT_WINDOW_S <= 8 * 3600.0
