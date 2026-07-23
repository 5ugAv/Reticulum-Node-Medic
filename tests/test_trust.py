"""Trusted operators — per-unit, non-transitive trust (Settings item 7)."""

from monitor import trust


def _p(tmp_path):
    return str(tmp_path / "trust.json")


def test_self_unit_always_trusted(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "My Medic", now=100.0, path=p)
    assert trust.is_trusted("aaaa", path=p) is True
    assert trust.classify("aaaa", path=p) == "self"


def test_direct_clone_is_trusted(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", now=1.0, path=p)
    trust.record_child_clone("bbbb", "Friend", parent_hash="aaaa", now=2.0, path=p)
    assert trust.is_trusted("bbbb", path=p) is True
    assert trust.classify("bbbb", path=p) == "trusted"
    u = {u["hash"]: u for u in trust.units(path=p)}["bbbb"]
    assert u["via"] == "cloned from this unit" and u["parent"] == "aaaa"


def test_trust_is_never_transitive(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    trust.record_child_clone("bbbb", "Friend", parent_hash="aaaa", path=p)   # trusted
    trust.note_descendant("cccc", "Stranger", parent_hash="bbbb", path=p)    # clone of a clone
    # cccc's parent (bbbb) is trusted, but cccc must NOT be
    assert trust.is_trusted("bbbb", path=p) is True
    assert trust.is_trusted("cccc", path=p) is False
    assert trust.classify("cccc", path=p) == "untrusted"


def test_manual_approval_of_a_descendant(tmp_path):
    p = _p(tmp_path)
    trust.note_descendant("cccc", "Stranger", parent_hash="bbbb", path=p)
    assert trust.is_trusted("cccc", path=p) is False
    trust.trust("cccc", path=p)
    assert trust.is_trusted("cccc", path=p) is True
    u = {u["hash"]: u for u in trust.units(path=p)}["cccc"]
    assert u["via"] == "manually trusted"


def test_revocation_demotes_birthed_nodes_to_neighbour(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    trust.record_child_clone("bbbb", "Friend", parent_hash="aaaa", path=p)
    # a node birthed by the friend's (trusted) unit reads as kin...
    assert trust.node_provenance("bbbb", path=p) == "kin"
    trust.revoke("bbbb", path=p)
    # ...and drops to neighbour once trust is revoked
    assert trust.node_provenance("bbbb", path=p) == "neighbour"
    assert trust.is_trusted("bbbb", path=p) is False


def test_cannot_revoke_self(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    trust.revoke("aaaa", path=p)
    assert trust.is_trusted("aaaa", path=p) is True    # self stays trusted


def test_own_nodes_are_kin(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    assert trust.node_provenance("aaaa", path=p) == "kin"


def test_unknown_builder_is_neighbour(tmp_path):
    p = _p(tmp_path)
    assert trust.node_provenance("zzzz", path=p) == "neighbour"
    assert trust.node_provenance(None, path=p) == "neighbour"


def test_note_descendant_does_not_downgrade_existing(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    trust.record_child_clone("bbbb", "Friend", parent_hash="aaaa", path=p)
    trust.note_descendant("bbbb", "Friend", parent_hash="aaaa", path=p)   # already known
    assert trust.is_trusted("bbbb", path=p) is True    # not downgraded


def test_units_ordering_self_trusted_untrusted(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", path=p)
    trust.record_child_clone("bbbb", "Bee", parent_hash="aaaa", path=p)
    trust.note_descendant("cccc", "Cee", parent_hash="bbbb", path=p)
    statuses = [u["status"] for u in trust.units(path=p)]
    assert statuses == ["self", "trusted", "untrusted"]
    # untrusted descendant carries its parent's display name for the UI label
    cee = {u["hash"]: u for u in trust.units(path=p)}["cccc"]
    assert cee["parent_name"] == "Bee"
