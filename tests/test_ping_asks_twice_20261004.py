"""Ping node now, 2026-10-04: SKYFINGER and ELSEWHERE were alive (heard over
LoRa minutes earlier, answering over Wi-Fi) and the Ping called them
unreachable. Two faults, both fixed and pinned here:

* it asked for a path to the FIRST of the device's destinations in registry
  order — for both nodes an address the node has never been heard speaking
  on — so the request went unanswered (monitor.registry.probe_targets_for);
* one lost broadcast was a verdict, worded "It may be off".

And the range-test picker, the same afternoon: the nodes that answered were
offered as bare hash prefixes, because the answering rows were nameless
aspect records of named devices.
"""
from monitor.health_beacon import decode, encode
from monitor.registry import NodeRegistry
from monitor.boundary_walk import walkable_nodes
from tests.srcutil import func_source

APP = "ui/app.py"
NOW = 1_000_000.0
HEALTH = "aa" * 16
RNSD = "bb" * 16


def _beacon():
    return decode(encode(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


def _ping():
    return func_source(APP, "_ping_node", cls="ReticulumNodeMedicApp")


# -- the Ping ---------------------------------------------------------------

def test_the_ping_asks_the_registry_for_ranked_targets():
    body = _ping()
    assert "probe_targets_for(" in body
    assert "probe_hash_for(" not in body, "one resolver: the ranked one"


def test_a_silent_first_ask_is_asked_again_before_any_verdict():
    body = _ping()
    assert body.count("rnpath -w 20") == 2, "the first ask, then once more"
    assert "asking once" in body          # "…asking once more…", split over two lines
    # the second ask tries the device's next-best destination alongside
    assert "others = [t for t in targets if t != probe][:1]" in body
    assert "other_answered" in body
    assert "its other mesh address answered" in body


def test_silence_is_reported_with_what_the_medic_does_know():
    body = _ping()
    assert "It may be off" not in body
    assert "two path requests" in body
    assert "It last spoke over the mesh" in body
    assert "answered over Wi-Fi" in body
    assert "never heard it speak over the mesh" in body


def test_too_close_is_only_said_about_a_two_hop_road():
    body = _ping()
    assert "hops >= 3" in body
    three = body[body.index("hops >= 3"):body.index("hops >= 2")]
    assert "too close" not in three and "other" in three
    assert body.index("hops >= 3") < body.index("too close")


# -- the picker and the walk ------------------------------------------------

def test_the_picker_hands_the_walk_a_named_record():
    body = func_source(APP, "_walk_from_pick", cls="ReticulumNodeMedicApp")
    assert "copy.copy(rec)" in body and 'rec.name = node["name"]' in body


def test_walkable_nodes_reads_the_device_fold_not_raw_rows():
    wn = func_source("monitor/boundary_walk.py", "walkable_nodes")
    assert "consolidated_records(" in wn and "probe_targets_for(" in wn
    assert "registry.all(" not in wn


def test_the_live_shape_a_pi_whose_beacon_aspect_is_nameless():
    """The registry as the medic held it: the roster names two destinations
    of one Pi; only the health one has ever been heard speaking (a beacon),
    the other sits in the path table. The picker offers ONE entry, named,
    at the speaking address — and a Ping on either row asks that address."""
    reg = NodeRegistry()
    reg.set_kin_roster({
        HEALTH: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
        RNSD: {"name": "SkyFinger", "type": "pi_propagation", "device": HEALTH},
    })
    reg.nodes[RNSD].mesh_heard = NOW - 30
    reg.ingest(HEALTH, _beacon(), NOW - 60)
    assert reg.probe_hash_for(RNSD) == HEALTH
    assert reg.probe_hash_for(HEALTH) == HEALTH
    cands = walkable_nodes(reg, NOW)
    assert [(n["name"], n["dst_hash"]) for n in cands] == [("SkyFinger", HEALTH)]
