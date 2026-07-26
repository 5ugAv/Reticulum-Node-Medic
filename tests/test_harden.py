"""At-rest permission hardening — clamps sensitive files to owner-only."""

import os
import stat

from provisioning.harden import harden_permissions


def test_tightens_world_readable_file(tmp_path):
    f = tmp_path / "identity"
    f.write_bytes(b"secretkey")
    os.chmod(f, 0o644)
    changed = harden_permissions([(str(f), 0o600)])
    assert str(f) in changed
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_already_tight_is_left_alone(tmp_path):
    f = tmp_path / "kin.json"
    f.write_text("{}")
    os.chmod(f, 0o600)
    assert harden_permissions([(str(f), 0o600)]) == []   # nothing to change


def test_missing_path_is_ignored(tmp_path):
    assert harden_permissions([(str(tmp_path / "nope"), 0o600)]) == []


def test_dir_gets_0700(tmp_path):
    d = tmp_path / "storage"
    d.mkdir()
    os.chmod(d, 0o755)
    harden_permissions([(str(d), 0o700)])
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_never_raises_on_bad_input():
    # non-existent + odd paths must not blow up
    assert harden_permissions([("/root/definitely/not/allowed", 0o600)]) == []
