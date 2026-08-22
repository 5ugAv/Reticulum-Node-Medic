"""The medic is not its own neighbour (2026-08-22 live findings).

Two bugs seen on VITALS, photo-confirmed:

BUG 2 — the medic listed its OWN destinations as neighbours. Destination
5a0a000a carried identity 5a180018, which IS the medic's own lxmd identity, and
it surfaced as an anonymous "Propagation relay". An announce whose identity is
one of THIS medic's own destinations is the medic hearing itself — it must be
neither recorded nor displayed. The match is by IDENTITY hash, so a node the
medic BUILT (a DIFFERENT identity) is never over-filtered as kin.

BUG 1 — the "Propagation relay <hash8>" row rendered its name, hash and the
"LXMF propagation announces" subtitle ON TOP of each other. Kivy is not
importable in CI, so the layout is asserted by source inspection: each text
line must be a fixed-height, single-line (shorten) label inside a column sized
to its children, which is what stops the wrap-and-overflow overlap.
"""

import pytest

from monitor.registry import NodeRegistry
from provisioning import tool_identity
from tests.srcutil import func_source

NOW = 1_000_000.0
OWN_TRANSPORT = "5a160016" + "00" * 12      # 32-hex, stands in for the rnsd id
OWN_LXMD = "5a180018" + "11" * 12           # 32-hex, the live lxmd identity
KIN = "cafebabe" + "22" * 12                # a node the medic BUILT — different id

# Real captured propagation app_data (same bytes the registry tests use).
PROP_ANNOUNCE = b"\x97\xc2\xcej\x88Gz\xc3\xcd\x01\x00\xcd(\x00\x93\x10\x03\x12\x80"


# ---- BUG 2: ingest never records the medic's own identities ----------------

def test_own_transport_announce_is_not_recorded():
    r = NodeRegistry()
    r.set_own_identities({OWN_TRANSPORT})
    rec = r.ingest_announce(b"\xd4" * 16, b"", NOW, identity_hash=OWN_TRANSPORT)
    assert rec is None
    assert r.nodes == {}


def test_own_lxmd_propagation_announce_is_not_recorded():
    # The exact 2026-08-22 case: our own lxmd propagation announce.
    r = NodeRegistry()
    r.set_own_identities({OWN_LXMD})
    rec = r.ingest_announce(b"\xd4" * 16, PROP_ANNOUNCE, NOW,
                            identity_hash=OWN_LXMD)
    assert rec is None
    assert r.nodes == {}


def test_own_identity_match_is_case_insensitive():
    r = NodeRegistry()
    r.set_own_identities({OWN_LXMD.upper()})
    assert r.ingest_announce(b"\xd4" * 16, b"", NOW,
                             identity_hash=OWN_LXMD.lower()) is None
    assert r.nodes == {}


# ---- no over-filter: a genuine neighbour/kin is still recorded -------------

def test_a_different_identity_is_still_recorded():
    r = NodeRegistry()
    r.set_own_identities({OWN_TRANSPORT, OWN_LXMD})
    rec = r.ingest_announce(b"\xaa" * 16, b"WILDNODE", NOW, identity_hash=KIN)
    assert rec is not None
    assert "aa" * 16 in r.nodes


def test_with_no_own_identities_everything_is_recorded():
    # An un-wired registry (the default, and every test that doesn't set it)
    # must filter nobody.
    r = NodeRegistry()
    rec = r.ingest_announce(b"\xd4" * 16, b"", NOW, identity_hash=OWN_LXMD)
    assert rec is not None
    assert "d4" * 16 in r.nodes


# ---- display path filters an ALREADY-PRESENT own-identity row --------------

def _polluted_registry():
    """A registry that already holds one own-identity row (a pre-fix registry
    loaded from disk) plus one genuine neighbour."""
    r = NodeRegistry()
    r.ingest_announce(b"\xd4" * 16, PROP_ANNOUNCE, NOW, identity_hash=OWN_LXMD)
    r.ingest_announce(b"\xaa" * 16, b"WILDNODE", NOW, identity_hash=KIN)
    assert len(r.nodes) == 2          # both landed before the filter was taught
    return r


def test_devices_excludes_an_already_present_own_row():
    r = _polluted_registry()
    r.set_own_identities({OWN_LXMD})
    rows = r.devices(NOW)
    idents = {d["identity"] for d in rows}
    assert "d4" * 16 not in idents          # our own dest is gone
    assert "aa" * 16 in idents              # the real neighbour stays


def test_all_and_summary_exclude_an_already_present_own_row():
    r = _polluted_registry()
    r.set_own_identities({OWN_LXMD})
    hashes = {rec.dst_hash for rec in r.all(NOW)}
    assert "d4" * 16 not in hashes
    assert "aa" * 16 in hashes
    # summary counts the neighbour, never the medic itself.
    assert sum(r.summary(NOW).values()) == 1


# ---- the propagation label still works for a THIRD-PARTY relay -------------

def test_third_party_propagation_still_labelled_relay():
    r = NodeRegistry()
    r.set_own_identities({OWN_TRANSPORT, OWN_LXMD})
    # A genuine third-party propagation node (identity != ours) is recorded and
    # still gets the "Propagation relay" label.
    r.ingest_announce(b"\xdd" * 16, PROP_ANNOUNCE, NOW, identity_hash=KIN)
    rows = r.devices(NOW)
    relay = next(d for d in rows if d["identity"] == "dd" * 16)
    assert relay["name"] == "Propagation relay " + "dd" * 4
    assert relay["location"] == "LXMF propagation announces"


# ---- own_identity_hashes() helper: defensive + correct --------------------

def test_own_identity_hashes_missing_files_is_empty_set():
    # Missing files must contribute nothing and never crash.
    assert tool_identity.own_identity_hashes(
        ["/no/such/transport_identity", "/no/such/lxmd/identity"]) == set()


def test_own_identity_hashes_no_paths_never_crashes():
    # The real (default-path) call must be crash-free even off-hardware; it
    # returns whatever exists, possibly nothing.
    assert isinstance(tool_identity.own_identity_hashes(), set)


def test_own_identity_hashes_reads_a_real_identity(tmp_path):
    RNS = pytest.importorskip("RNS")
    p = str(tmp_path / "transport_identity")
    ident = RNS.Identity()
    ident.to_file(p)
    got = tool_identity.own_identity_hashes([p])
    assert got == {ident.hash.hex().lower()}


# ---- BUG 1: the name + subtitle stack cannot overlap ----------------------

def test_node_row_name_and_subtitle_are_fixed_single_lines():
    """Source-inspection (Kivy is not importable in CI): each text line is a
    FIXED-height, single-line (shorten) label and the column is sized to its
    children — the structure that stops the 2026-08-22 wrap-and-overlap."""
    body = func_source("ui/screens/vitals_screen.py", "__init__", cls="NodeRow")
    # Both the name and the subtitle carry a fixed height and shorten instead of
    # wrapping — a long "Propagation relay <hash8>" ellipsises, it never spills
    # onto the line below.
    assert body.count("size_hint_y=None") >= 2
    assert body.count("shorten=True") >= 2
    assert "shorten_from=\"right\"" in body
    # The text column is sized to its children (minimum_height) and centred,
    # so the two lines stack cleanly rather than overprinting.
    assert "minimum_height=text.setter(\"height\")" in body
    assert "pos_hint={\"center_y\": 0.5}" in body


def test_node_row_column_height_budget_has_no_overlap():
    """The dp budget: an 80dp row minus 8dp padding top+bottom = 64dp of
    content; name(26) + subtitle(20) + chips(18) = 64 — every line fits with
    zero overlap even in the tallest (capabilities) case."""
    body = func_source("ui/screens/vitals_screen.py", "__init__", cls="NodeRow")
    assert "self.height = dp(80)" in body
    assert "self.padding = dp(8)" in body
    assert "height=dp(26)" in body        # name line
    assert "height=dp(20)" in body        # subtitle line
    assert "height=dp(18)" in body        # capability chips row
