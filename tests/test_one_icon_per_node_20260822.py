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
HEALTH = "dd44dd44" + "00" * 12          # health-reporter destination (device id)
TRANSPORT = "a1" * 16                     # rnsd transport destination
LXMD = "bb22bb22" + "11" * 12            # lxmd lxmf.propagation destination
I_HEALTH, I_TRANSPORT, I_LXMD = "e1" * 16, "e2" * 16, "cc33cc33" + "22" * 12


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


# ---- 4. NO name fold across identities (the hazard must be GONE) ------------

def test_two_operator_named_machines_with_different_identities_stay_two_rows():
    """THE HAZARD, PROVEN GONE (Finding 1, 2026-08-22). Two roster-backed rows
    that share an operator name but bear DIFFERENT identities and NO shared
    device are TWO MACHINES (the operator reused "A2" on a spare). They must NOT
    fold — otherwise a board that dies in the field would show green off its
    live namesake's freshness (the 2026-08-13 dead-behind-a-namesake hazard).
    Uniqueness is enforced at BIRTH (retire_same_name), never guessed here."""
    reg = NodeRegistry()
    reg.set_kin_roster({
        "aa" * 16: {"name": "A2", "type": "pi_propagation"},
        "bb" * 16: {"name": "A2", "type": "pi_propagation"},
    })
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", NOW, identity_hash="id-a")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"", NOW, identity_hash="id-b")
    assert len(reg.devices(NOW)) == 2


def test_a_dead_board_does_not_show_green_behind_a_live_namesake():
    """The freshness leak the fold would have caused: an old board last heard
    long ago must keep its OWN (stale/alert) status, not inherit a live
    namesake's max(last_seen)."""
    from monitor.registry import STALE_ALERT_HOURS
    reg = NodeRegistry()
    reg.set_kin_roster({
        "aa" * 16: {"name": "A2", "type": "pi_propagation"},
        "bb" * 16: {"name": "A2", "type": "pi_propagation"},
    })
    old = NOW - (STALE_ALERT_HOURS + 10) * 3600
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", old, identity_hash="dead")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"", NOW, identity_hash="live")
    rows = reg.devices(NOW)
    assert len(rows) == 2
    dead_row = [d for d in rows if d["identity"] == "aa" * 16][0]
    assert dead_row["status"] == "alert"        # its own truth, not masked green


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


# ---- 6. register_device NEVER unions two existing devices (Finding 2) -------

def test_register_device_does_not_union_two_existing_devices(tmp_path):
    """A hash set that spans TWO existing devices (re-imaged card / harvested
    identity_hash) must NOT be merged — a hash is never stolen off its rightful
    machine."""
    path = str(tmp_path / "kin.json")
    D1, D2 = "11" * 16, "22" * 16
    kin_roster.register_device([D1], "NodeOne", path=path)
    kin_roster.register_device([D2], "NodeTwo", path=path)
    # A set that touches BOTH devices at once.
    kin_roster.register_device([D1, D2], "Confused", path=path)
    roster = kin_roster.load_roster(path)
    assert roster[D1]["device"] == D1          # each keeps its own device
    assert roster[D2]["device"] == D2
    assert roster[D1]["device"] != roster[D2]["device"]


def test_register_device_never_steals_a_known_hash_onto_another_device(tmp_path):
    """Even a genuine single-device merge must leave OTHER devices' hashes put."""
    path = str(tmp_path / "kin.json")
    D1, D2, NEW = "11" * 16, "22" * 16, "33" * 16
    kin_roster.register_device([D1], "NodeOne", path=path)
    kin_roster.register_device([D2], "NodeTwo", path=path)
    # NEW is brand-new and D2 is known-elsewhere; the set spans D2 + a new hash.
    kin_roster.register_device([NEW, D2], "NodeTwo", path=path)
    roster = kin_roster.load_roster(path)
    assert roster[D2]["device"] == D2          # not moved
    assert roster[NEW]["device"] == D2         # new hash joins the one device
    assert roster[D1]["device"] == D1          # untouched


# ---- 7. birth enforces one-name-one-machine (retire_same_name) --------------

def test_rebirth_under_a_reused_name_retires_the_old_device(tmp_path):
    """The operator births a NEW board under a name a DIFFERENT device already
    holds -> the old device is retired so the name belongs to the new board."""
    path = str(tmp_path / "kin.json")
    OLD = "aa" * 16
    NEW = "bb" * 16
    kin_roster.register_device([OLD], "A2", path=path)
    retired = kin_roster.retire_same_name("A2", [NEW], path=path)
    assert retired == [OLD]
    kin_roster.register_device([NEW], "A2", path=path)
    roster = kin_roster.load_roster(path)
    assert OLD not in roster                    # old board's row gone
    assert roster[NEW]["name"] == "A2"          # new board is the sole A2


def test_retire_same_name_leaves_the_same_node_being_rebirthed(tmp_path):
    """Re-running birth for the SAME node (a shared hash) must NOT retire it."""
    path = str(tmp_path / "kin.json")
    H1, H2 = "aa" * 16, "bb" * 16
    kin_roster.register_device([H1, H2], "A2", path=path)
    # Re-birth carrying one of the same hashes -> same node, nothing retired.
    retired = kin_roster.retire_same_name("A2", [H1], path=path)
    assert retired == []
    assert set(kin_roster.load_roster(path)) == {H1, H2}


def test_retire_same_name_spares_a_same_serial_board(tmp_path):
    """Same physical board (hw_serial) is handled by retire_previous_lives, not
    treated as a different machine here."""
    path = str(tmp_path / "kin.json")
    OLD, NEW = "aa" * 16, "bb" * 16
    kin_roster.register(OLD, "A2", device=OLD, hw_serial="SER123", path=path)
    retired = kin_roster.retire_same_name("A2", [NEW], hw_serial="SER123",
                                          path=path)
    assert retired == []                        # same board, left for the serial path


def test_a_replaced_but_still_live_old_board_is_an_anon_neighbour_not_folded():
    """After a name is reused, if the OLD board is still alive it re-announces.
    With its roster entry retired, it surfaces as an anonymous neighbour — a
    separate row, never masked green under the new same-named node."""
    reg = NodeRegistry()
    OLD, NEW = "aa" * 16, "bb" * 16
    # The new board is the current 'A2'; the old board is NOT in the roster.
    reg.set_kin_roster({NEW: {"name": "A2", "type": "pi_propagation",
                              "device": NEW}})
    reg.ingest_announce(bytes.fromhex(NEW), b"", NOW, identity_hash="new-id")
    reg.ingest_announce(bytes.fromhex(OLD), b"", NOW, identity_hash="old-id")
    rows = reg.devices(NOW)
    assert len(rows) == 2                        # two separate rows
    a2 = [d for d in rows if d["name"] == "A2"]
    assert len(a2) == 1 and a2[0]["provenance"] == "kin"
    ghost = [d for d in rows if d["provenance"] == "neighbour"]
    assert len(ghost) == 1                       # the old board, honestly anon


# ---- 8. lxmd identity is regenerated per node at birth (Finding 3) ----------

def test_build_regenerates_the_lxmd_identity_at_birth():
    """A golden card can ship a PREVIOUS node's ~/.lxmd/identity, so two nodes
    would announce the SAME propagation dest. Birth must delete it before lxmd
    first starts so each node mints its own unique propagation identity."""
    fn = func_source("workflows/build.py", "configure_services")
    assert "rm -f ~/.lxmd/identity" in fn
    # ...and it must happen BEFORE lxmd is started, or lxmd reuses the stale one.
    assert fn.index("rm -f ~/.lxmd/identity") < fn.index("systemctl start")


def test_birth_screen_enforces_name_uniqueness_at_birth():
    """_register_kin must retire a different device that already holds the name
    (a clean REPLACE), not leave two same-named machines for the display."""
    fn = func_source("ui/screens/birth_screen.py", "_register_kin")
    assert "retire_same_name" in fn
    assert "forget_node" in fn
