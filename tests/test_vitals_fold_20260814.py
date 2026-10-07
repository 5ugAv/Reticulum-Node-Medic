"""The night of three births — 2026-08-14, operator's VITALS.

After three births in one evening the operator's own newborn nodes sat in
VITALS as grey strangers. Node ``3a-v3-test`` — birth-certificate hash
aabbccdd…, heard beaconing on health destination dd44dd44… (synthetic
stand-ins throughout this file) — appeared as BOTH "Neighbour dd44dd44" and
"Neighbour aabbccdd" while its named row was nowhere.
Three more grey rows carried garbled names like "j(" and "j-(" — binary
announce app_data (msgpack LXMF payloads) pushed through a lossy decode until
only punctuation residue was left, and that residue shown as a NAME.

Two laws these tests pin down:

1. THE FOLD: a destination a birth minted must land under the node's name from
   its FIRST announce. The announce carries the identity RNS itself verified;
   the roster's keys include identities (a Pi cert's ``reticulum_address``, an
   adopted node's key). Consulting the roster by that identity at announce
   time is identity evidence — nothing here merges by name.

2. NO INVENTED NAMES: bytes that are not clean UTF-8 printable text are not a
   name. The honest label for such a node is "Neighbour <hash8>". Refusing the
   residue also closes a merge hole: name_key("j(") == name_key("j-(") == "j",
   so two garbled STRANGERS could have been folded into one row — the exact
   merge-by-name the registry is sworn never to do.
"""

from monitor.registry import NodeRegistry, _printable_name, name_key
from tests.srcutil import func_source

#: Synthetic hashes. VITALS shows only the first 8 hex digits of a destination;
#: the tail is padding (any 32-hex value exercises the same paths).
IDENT = "aabbccddeeff00112233445566778899"
HEALTH = "dd44dd44" + "00" * 12
NOW = 1_755_000_000.0


def _roster():
    """What birth writes for this node: the certificate hash is the roster key
    and the device id (register_device's first hash)."""
    return {IDENT: {"name": "3a-v3-test", "type": "rtnode2400",
                    "device": IDENT}}


# ---- 1. the fold: certs/roster consulted at ANNOUNCE time ------------------

def test_first_announce_of_a_certified_nodes_dst_is_named_not_neighbour():
    """The record itself — not just a merged dashboard row — must come out of
    ingest_announce named and kin. Everything record-level (adopt candidates,
    search, the map) reads the record, and the field showed what happens when
    only display-time grouping holds the fold together."""
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    rec = reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW,
                              identity_hash=IDENT)
    assert rec.name == "3a-v3-test"
    assert rec.provenance == "kin"
    assert rec.node_type == "rtnode2400"
    assert rec.device_id == IDENT


def test_the_fold_holds_on_the_screen_from_the_first_announce():
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW, identity_hash=IDENT)
    rows = reg.devices(NOW)
    assert len(rows) == 1
    assert rows[0]["name"] == "3a-v3-test"
    assert rows[0]["provenance"] == "kin"


def test_a_roster_reload_folds_a_record_heard_before_the_birth():
    """The announce can beat the birth to the registry (the medic hears the
    bench announce while the certificate is still being written). Reloading the
    roster must fold that already-heard record by its identity, not only the
    records the roster keys directly."""
    reg = NodeRegistry()
    rec = reg.ingest_announce(bytes.fromhex(HEALTH), b"", NOW,
                              identity_hash=IDENT)
    assert rec.name == ""                       # nothing known yet — honest
    reg.set_kin_roster(_roster())
    assert reg.get(HEALTH).name == "3a-v3-test"
    assert reg.get(HEALTH).device_id == IDENT
    assert len(reg.devices(NOW)) == 1


def test_a_stranger_with_a_different_identity_is_never_folded():
    """The law the fold must not bend: identity evidence only. A neighbour that
    announces a DIFFERENT identity stays a neighbour, whatever the roster
    holds."""
    reg = NodeRegistry()
    reg.set_kin_roster(_roster())
    stranger = "ab" * 16
    rec = reg.ingest_announce(bytes.fromhex(stranger), b"", NOW,
                              identity_hash="cd" * 16)
    assert rec.name == ""
    assert rec.provenance == "neighbour"
    assert len(reg.devices(NOW)) == 2           # roster row + stranger, apart


# ---- 2. no invented names --------------------------------------------------

def test_msgpack_residue_is_refused_as_a_display_name():
    # These bytes reproduce the field rows: the old ignore-errors decode
    # stripped the unprintable bytes and showed the residue as a name.
    assert _printable_name(b"\x92\xc4\x02j(") == ""
    assert _printable_name(b"\x92\xc4\x03j-(") == ""


def test_a_garbled_announce_renders_as_neighbour_hash_not_residue():
    reg = NodeRegistry()
    dst = "ee" * 16
    reg.ingest_announce(bytes.fromhex(dst), b"\x92\xc4\x02j(", NOW,
                        identity_hash="i1")
    row = reg.devices(NOW)[0]
    assert row["name"] == f"Neighbour {dst[:8]}"


def test_two_garbled_strangers_do_not_merge_by_their_residue():
    """name_key("j(") == name_key("j-(") == "j": had the residue been kept as
    names, two DIFFERENT identities could have merged by name — the one merge
    this registry must never make."""
    assert name_key("j(") == name_key("j-(")    # the hole is real
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"\x92\xc4\x02j(", NOW,
                        identity_hash="i1")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"\x92\xc4\x03j-(", NOW,
                        identity_hash="i2")
    assert len(reg.devices(NOW)) == 2


def test_clean_lxmf_style_names_still_come_through():
    # LXMF prefixes a length byte before a UTF-8 name — both real forms the
    # suite already relies on must keep working.
    assert _printable_name(b"\x06Pebble") == "Pebble"
    assert _printable_name(b"\x09skyfinger!") == "skyfinger!"


def test_bytes_that_do_not_decode_cleanly_are_not_salvaged():
    # A single leading length/control byte is the LXMF convention; arbitrary
    # garbage ahead of a readable tail is not, and must not become a name.
    assert _printable_name(b"\xffab") == ""
    assert _printable_name(b"") == ""
    assert _printable_name(None) == ""


def test_a_one_letter_residue_is_not_a_name_even_if_it_decodes():
    # "x!" is clean UTF-8, but its alnum residue is a single letter — the same
    # key two records must share to merge, so it is refused as a name.
    assert _printable_name(b"x!") == ""


def test_a_garbled_name_saved_by_the_old_code_heals_on_load():
    """Registries persisted before this fix carry the residue in
    announced_name. Loading one must not keep showing it."""
    data = {"nodes": [{"dst_hash": "aa" * 16, "announced_name": "j("}],
            "history": {}}
    reg = NodeRegistry.from_dict(data)
    assert reg.get("aa" * 16).announced_name == ""
    row = reg.devices(NOW)[0]
    assert row["name"] == "Neighbour " + "aa" * 4


def test_a_garbled_name_already_in_a_live_record_heals_on_the_next_announce():
    reg = NodeRegistry()
    rec = reg.ingest_announce(bytes.fromhex("aa" * 16), b"", NOW,
                              identity_hash="i1")
    rec.announced_name = "j("            # planted by the old decoder
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"\x92\xc4\x02j(", NOW + 60,
                        identity_hash="i1")
    assert reg.get("aa" * 16).announced_name == ""


# ---- 3. birth wires the fold up front (source-inspected: Kivy not importable
#         in CI — same technique and reason as test_birth_registers_one_device)

def test_birth_pushes_the_fresh_roster_into_the_live_registry():
    """register_device writes DISK; the running registry used to find out at
    the next 5-minute rediscover. A node's first announce lands well inside
    that window, so birth must hand the roster to the live registry itself."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert "set_kin_roster(" in fn
    assert "load_roster()" in fn


def test_birth_harvests_the_announced_identity_into_the_device():
    """If the medic has already HEARD a certificate hash announce, the record
    carries the identity RNS verified — the one key every future destination
    of this node will announce under. Birth is the moment to write it down."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert "identity_hash" in fn
    assert "not in hashes" in fn
