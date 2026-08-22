"""Exactly ONE VITALS icon per physical node (2026-08-22).

A Pi propagation node reaches the medic on THREE Reticulum destinations with
THREE unrelated identities — rnsd transport, the health reporter (its own
identity file), and lxmd's lxmf.propagation aspect. Nothing on the mesh links
them, so SkyFinger sat in VITALS as a named row AND an anonymous "Propagation
relay" (the double-SkyFinger root cause). The fix is two layers:

  * ground-truth capture: birth reads all three off the node and records them
    as ONE device (workflows.build + birth_screen + kin_roster.register_device);
  * belt-and-suspenders: two KIN groups that share an operator-set name fold,
    so a named node cannot double even if a birth-time capture was missed.

These tests exercise the registry/roster layers directly (Kivy-free); the birth
wiring is checked at source level, the same technique/reason as
tests/test_birth_registers_one_device.py.
"""

from monitor import kin_roster
from monitor.health_beacon import encode, decode
from monitor.http_status import NodeStatus
from monitor.registry import NodeRegistry
from tests.srcutil import func_source, src


NOW = 1_755_000_000.0

# One Pi, three genuinely different identities/destinations.
HEALTH = "5a030003" + "00" * 12          # health-reporter destination (device id)
TRANSPORT = "a1" * 16                     # rnsd transport destination
LXMD = "5a0a000a" + "11" * 12            # lxmd lxmf.propagation destination
I_HEALTH, I_TRANSPORT, I_LXMD = "e1" * 16, "e2" * 16, "5a180018" + "22" * 12


def beacon(**over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    return decode(encode(**kw))


# ---- 1. a Pi's three identities fold to one named row -----------------------

def test_a_pi_with_three_identities_is_one_row_not_three():
    """Transport + health + lxmd, all recorded as one device at birth, must
    render as ONE VITALS row named for the node — never a named row plus an
    orphaned 'Propagation relay'."""
    reg = NodeRegistry()
    # What register_device writes at birth: every destination under one device.
    reg.set_kin_roster({
        HEALTH: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
        TRANSPORT: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
        LXMD: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
    })
    # All three announce, each with its OWN identity — the mesh can't link them.
    reg.ingest(HEALTH, beacon(), NOW)                     # health beacon -> lora
    reg.nodes[HEALTH].identity_hash = I_HEALTH
    reg.ingest_announce(bytes.fromhex(TRANSPORT), b"", NOW, identity_hash=I_TRANSPORT)
    reg.ingest_announce(bytes.fromhex(LXMD), b"", NOW, identity_hash=I_LXMD)

    rows = reg.devices(NOW)
    assert len(rows) == 1, f"one machine, one row; got {len(rows)}"
    assert rows[0]["name"] == "SkyFinger"
    assert rows[0]["aspects"] == 3                        # all three merged
    assert rows[0]["capabilities"]["lora"] is True        # health beacon pooled
    # every destination carries the device id, so the fold is by device (strong)
    for h in (HEALTH, TRANSPORT, LXMD):
        assert reg.get(h).device_id == HEALTH


def test_the_lxmd_aspect_is_named_not_an_orphaned_relay():
    """The exact 2026-08-22 symptom: the lxmd destination alone must not sit in
    VITALS as an anonymous 'Propagation relay' — rostered, it is the node."""
    reg = NodeRegistry()
    reg.set_kin_roster({
        HEALTH: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
        LXMD: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
    })
    rec = reg.ingest_announce(bytes.fromhex(LXMD), b"", NOW, identity_hash=I_LXMD)
    assert rec.name == "SkyFinger"          # named the instant it is heard
    assert rec.device_id == HEALTH


# ---- 2. an RTNode's two identities fold to one row --------------------------

def test_an_rtnode_with_two_identities_is_one_row():
    reg = NodeRegistry()
    RT_HEALTH = "b7" * 16
    RT_TRANSPORT = "cc" * 16
    reg.set_kin_roster({
        RT_HEALTH: {"name": "FAITH", "type": "rtnode2400", "device": RT_HEALTH},
        RT_TRANSPORT: {"name": "FAITH", "type": "rtnode2400", "device": RT_HEALTH},
    })
    reg.ingest(RT_HEALTH, beacon(), NOW)
    reg.ingest_announce(bytes.fromhex(RT_TRANSPORT), b"", NOW, identity_hash="rt2")
    rows = reg.devices(NOW)
    assert len(rows) == 1
    assert rows[0]["name"] == "FAITH"


# ---- 3. register_device merges idempotently (re-adopt / repair) -------------

def test_register_device_merges_a_new_address_into_the_existing_device(tmp_path):
    """Re-running birth to add a destination the first birth missed must ADD it
    to the node's one device, never mint a second."""
    path = str(tmp_path / "kin.json")
    kin_roster.register_device([HEALTH, TRANSPORT], "SkyFinger",
                               node_type="pi_propagation", path=path)
    # A later repair adds the lxmd aspect (health already known).
    kin_roster.register_device([HEALTH, LXMD], "SkyFinger",
                               node_type="pi_propagation", path=path)
    roster = kin_roster.load_roster(path)
    devices = {e["device"] for e in roster.values()}
    assert devices == {HEALTH}, f"one device for the machine; got {devices}"
    assert set(roster) == {HEALTH, TRANSPORT, LXMD}


def test_register_device_joins_an_existing_device_even_when_health_is_not_first(tmp_path):
    """The merge keys on ANY overlapping hash, not just the first one given."""
    path = str(tmp_path / "kin.json")
    kin_roster.register_device([HEALTH, TRANSPORT], "SkyFinger", path=path)
    # lxmd first, but TRANSPORT is already on record under device=HEALTH.
    kin_roster.register_device([LXMD, TRANSPORT], "SkyFinger", path=path)
    roster = kin_roster.load_roster(path)
    assert roster[LXMD]["device"] == HEALTH
    assert {e["device"] for e in roster.values()} == {HEALTH}


def test_register_device_of_a_brand_new_machine_uses_its_first_hash(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.register_device([HEALTH, TRANSPORT, LXMD], "SkyFinger", path=path)
    assert {e["device"] for e in kin_roster.load_roster(path).values()} == {HEALTH}


# ---- 4. operator kin-name fold (belt-and-suspenders) -----------------------

def test_two_operator_named_groups_fold_despite_different_identities():
    """A birth-capture gap can leave two ROSTER-BACKED rows for one node under
    the operator's one name (different identities, no shared device). The
    operator's name is unique, so the safety net folds them into one row."""
    reg = NodeRegistry()
    # Two roster entries the operator named identically, with NO shared device
    # (so the device-id fold cannot help) — the belt-and-suspenders case.
    reg.set_kin_roster({
        "aa" * 16: {"name": "SkyFinger", "type": "pi_propagation"},
        "bb" * 16: {"name": "SkyFinger", "type": "pi_propagation"},
    })
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", NOW, identity_hash="id-a")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"", NOW, identity_hash="id-b")
    rows = reg.devices(NOW)
    assert len(rows) == 1
    assert rows[0]["name"] == "SkyFinger"


def test_a_bare_named_row_not_in_the_roster_does_not_fold_by_name():
    """The break-lens guard, restated for the safety net: a name a record merely
    carries — not backed by the operator's roster — is NOT authority. Two such
    identity-bearing rows stay two rows (a corpse must not hide behind a
    live namesake)."""
    reg = NodeRegistry()
    reg.register("aa" * 16, name="Relay")
    reg.nodes["aa" * 16].identity_hash = "id-a"
    reg.register("bb" * 16, name="Relay")
    reg.nodes["bb" * 16].identity_hash = "id-b"
    assert len(reg.devices(NOW)) == 2


def test_two_neighbours_with_the_same_ANNOUNCED_name_do_not_fold():
    """The guard the safety net must NOT break: two neighbours that merely
    announce the same name are two machines — folding would let a dead one hide
    behind a live namesake (break-lens, 2026-08-13)."""
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"\x06Pebble", NOW,
                        identity_hash="id-a")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"\x06Pebble", NOW,
                        identity_hash="id-b")
    rows = reg.devices(NOW)
    assert len(rows) == 2                     # two machines, two rows
    # ...and both are neighbours wearing the announced name (not operator names).
    assert all(d["provenance"] == "neighbour" for d in rows)


def test_genuinely_different_kin_nodes_stay_separate():
    """No over-merge: different names + different identities are different
    machines."""
    reg = NodeRegistry()
    reg.register("aa" * 16, name="SkyFinger 1")
    reg.nodes["aa" * 16].identity_hash = "id-a"
    reg.register("bb" * 16, name="SkyFinger 2")
    reg.nodes["bb" * 16].identity_hash = "id-b"
    assert len(reg.devices(NOW)) == 2


# ---- 5. birth captures the lxmd destination (source-level) ------------------

def test_build_reads_the_lxmd_propagation_destination_off_the_node():
    """workflows.build must read lxmd's lxmf.propagation destination live off the
    node and add it to the certificate, mirroring the health-dst capture."""
    text = src("workflows/build.py")
    assert "_LXMD_DST_CMD" in text
    assert "~/.lxmd/identity" in text
    assert "'lxmf', 'propagation'" in text
    cert_fn = func_source("workflows/build.py", "birth_certificate")
    assert "lxmd_dst" in cert_fn
    assert "_LXMD_DST_CMD" in cert_fn


def test_build_only_records_a_wellformed_lxmd_hash_never_a_fabricated_one():
    """A blank / garbled read is dropped — never rostered as an invented
    identity (honesty ethos)."""
    cert_fn = func_source("workflows/build.py", "birth_certificate")
    assert "len(lxmd_dst) == 32" in cert_fn


def test_birth_registers_the_lxmd_destination_as_part_of_the_device():
    """birth_screen must fold lxmd_dst (and any extra_identities) into the one
    device it records at birth."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert "lxmd_dst" in fn
    assert "extra_identities" in fn
    assert "register_device(" in fn


def test_rtnode_certificate_carries_no_fabricated_lxmd_hash():
    """An RTNode-2400 runs no lxmd — its certificate must not carry an lxmd
    destination at all (nothing to fabricate)."""
    cert_fn = func_source("workflows/rtnode_build.py", "birth_certificate")
    assert "lxmd_dst" not in cert_fn
