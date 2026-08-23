"""Crash-safe writes — the guarantee the field device leans on.

The medic is a solar/battery unit on an SD card; power can vanish mid-write and
the documented failure is a TRUNCATED file, not media death. These tests pin
the atomic helpers (``write_text`` / ``write_bytes`` / ``write_json``) and, in
particular, prove that a FAILED write leaves the OLD file intact rather than an
empty/half-written one.
"""

import json
import os
import stat

import pytest

from monitor import atomic_json


def test_write_text_round_trips(tmp_path):
    p = str(tmp_path / "pref.txt")
    assert atomic_json.write_text(p, "propagation") is True
    with open(p, encoding="utf-8") as f:
        assert f.read() == "propagation"


def test_write_text_utf8(tmp_path):
    p = str(tmp_path / "lang.txt")
    atomic_json.write_text(p, "français — ✓")
    with open(p, encoding="utf-8") as f:
        assert f.read() == "français — ✓"


def test_write_text_creates_parent_dirs(tmp_path):
    p = str(tmp_path / "deep" / "nested" / "v")
    assert atomic_json.write_text(p, "7") is True
    assert os.path.exists(p)


def test_write_text_mode_is_applied(tmp_path):
    p = str(tmp_path / "secret")
    atomic_json.write_text(p, "x", mode=0o600)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_write_bytes_round_trips(tmp_path):
    p = str(tmp_path / "key.bin")
    data = os.urandom(32)
    assert atomic_json.write_bytes(p, data, mode=0o600) is True
    with open(p, "rb") as f:
        assert f.read() == data
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_write_json_round_trips(tmp_path):
    p = str(tmp_path / "d.json")
    atomic_json.write_json(p, {"days": 30}, indent=2)
    with open(p) as f:
        assert json.load(f) == {"days": 30}


def test_failed_write_leaves_old_file_intact(tmp_path, monkeypatch):
    """THE guarantee: if the swap-in fails partway (simulating a power-cut before
    the rename lands), the pre-existing file is UNTOUCHED — never truncated to
    empty — and no temp turd is left behind."""
    p = str(tmp_path / "roster.json")
    atomic_json.write_text(p, "OLD-GOOD-CONTENT")

    # Make the atomic rename blow up, mimicking a crash mid-swap.
    def boom(src, dst):
        raise OSError("simulated power cut before rename completed")
    monkeypatch.setattr(atomic_json.os, "replace", boom)

    assert atomic_json.write_text(p, "NEW-CONTENT-NEVER-LANDS") is False

    # Old file still whole (readers see the old value, not an empty file)...
    with open(p, encoding="utf-8") as f:
        assert f.read() == "OLD-GOOD-CONTENT"
    # ...and the temp file was cleaned up, not left cluttering the dir.
    leftovers = [n for n in os.listdir(tmp_path) if n != "roster.json"]
    assert leftovers == []


def test_write_json_of_unserialisable_returns_false(tmp_path):
    p = str(tmp_path / "x.json")
    assert atomic_json.write_json(p, {1, 2, 3}) is False   # a set isn't JSON
    assert not os.path.exists(p)
