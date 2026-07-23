"""This medic's own identity + lineage (Settings item 2)."""

from provisioning import tool_identity as ti


def test_identity_hash_reads_from_rns(monkeypatch):
    assert ti.identity_hash(run=lambda cmd: (0, "abc123def456\n")) == "abc123def456"


def test_identity_hash_empty_when_unavailable():
    assert ti.identity_hash(run=lambda cmd: (0, "")) == ""
    assert ti.identity_hash(run=lambda cmd: (1, "boom")) == ""


def test_tool_name_defaults_to_hostname(tmp_path, monkeypatch):
    p = str(tmp_path / "id.json")
    monkeypatch.setattr(ti.socket, "gethostname", lambda: "nodemedic")
    assert ti.tool_name(path=p) == "nodemedic"
    ti.set_name("Field Medic One", path=p)
    assert ti.tool_name(path=p) == "Field Medic One"


def test_born_stamped_once_and_idempotent(tmp_path):
    p = str(tmp_path / "id.json")
    assert ti.born(path=p) is None
    b1 = ti.ensure_born(1000.0, path=p, identity_mtime=None)   # no identity file -> now
    assert b1 == 1000.0
    b2 = ti.ensure_born(2000.0, path=p, identity_mtime=None)   # already set -> unchanged
    assert b2 == 1000.0
    assert ti.born(path=p) == 1000.0


def test_born_prefers_identity_mtime(tmp_path):
    p = str(tmp_path / "id.json")
    b = ti.ensure_born(9999.0, path=p, identity_mtime=1234.5)
    assert b == 1234.5


def test_parent_none_for_original(tmp_path):
    assert ti.parent(path=str(tmp_path / "id.json")) is None


def test_set_parent_records_lineage(tmp_path):
    p = str(tmp_path / "id.json")
    ti.set_parent("deadbeef", "Origin Medic", path=p)
    par = ti.parent(path=p)
    assert par["hash"] == "deadbeef"
    assert par["name"] == "Origin Medic"
    assert "cloned" in par["via"].lower()


def test_summary_bundles_everything(tmp_path, monkeypatch):
    p = str(tmp_path / "id.json")
    monkeypatch.setattr(ti.socket, "gethostname", lambda: "medic-2")
    ti.ensure_born(500.0, path=p, identity_mtime=None)
    ti.set_parent("aa11", "Parent", path=p)
    s = ti.summary(run=lambda cmd: (0, "myhash\n"), path=p)
    assert s == {"identity_hash": "myhash", "name": "medic-2",
                 "born": 500.0, "parent": {"hash": "aa11", "name": "Parent",
                                            "via": "cloned from this unit"}}
