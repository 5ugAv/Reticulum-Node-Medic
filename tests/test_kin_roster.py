"""The medic's fleet roster — its own nodes show as NAMED KIN in VITALS and land
on the MAP at their deployed spot, even a propagation relay it can't hear directly
(the EVERYWHERE bug: the uplink via was invisible)."""

from monitor import kin_roster
from monitor.registry import NodeRegistry
from monitor.service import MonitorService
from monitor.mesh import MeshNode


EVERYWHERE = "5463bddfb8b41e0159c1b867e9981f36"


# ---- the roster file --------------------------------------------------------

def test_register_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation",
                        lat=-37.81, lon=144.96, path=path)
    roster = kin_roster.load_roster(path)
    assert roster[EVERYWHERE]["name"] == "EVERYWHERE"
    assert roster[EVERYWHERE]["type"] == "pi_propagation"
    assert roster[EVERYWHERE]["lat"] == -37.81


def test_register_is_idempotent_update(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation", path=path)
    kin_roster.register(EVERYWHERE, "EVERYWHERE-2", "pi_propagation", path=path)
    roster = kin_roster.load_roster(path)
    assert len(roster) == 1 and roster[EVERYWHERE]["name"] == "EVERYWHERE-2"


def test_set_location_only_touches_existing(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.set_location("deadbeef", 1.0, 2.0, path=path)     # not present
    assert kin_roster.load_roster(path) == {}
    kin_roster.register(EVERYWHERE, "EVERYWHERE", path=path)
    kin_roster.set_location(EVERYWHERE, -37.81, 144.96, path=path)
    assert kin_roster.load_roster(path)[EVERYWHERE]["lon"] == 144.96


def test_load_missing_file_is_empty(tmp_path):
    assert kin_roster.load_roster(str(tmp_path / "nope.json")) == {}


# ---- registry: a rostered node is kin + on the map --------------------------

def test_set_kin_roster_seeds_named_located_kin():
    reg = NodeRegistry()
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE",
                                     "type": "pi_propagation",
                                     "lat": -37.81, "lon": 144.96}})
    rec = reg.get(EVERYWHERE)
    assert rec is not None
    assert rec.name == "EVERYWHERE"
    assert rec.provenance == "kin"                 # named => kin, not neighbour
    assert rec.has_location()                      # => shows on the map
    located = reg.located_nodes(now=0.0)
    assert any(n["name"] == "EVERYWHERE" for n in located)


def test_roster_applies_even_if_heard_first_as_neighbour():
    # Node heard on the mesh (anonymous neighbour) BEFORE the roster loads — once
    # the roster loads it must be reclassified as named kin.
    reg = NodeRegistry()
    reg.ingest_mesh(MeshNode(dst_hash=EVERYWHERE, hops=1, interface="RNodeInterface"),
                    now=100.0)
    assert reg.get(EVERYWHERE).provenance == "neighbour"
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE", "type": "pi_propagation"}})
    assert reg.get(EVERYWHERE).name == "EVERYWHERE"
    assert reg.get(EVERYWHERE).provenance == "kin"


# ---- the via bug: the relay is surfaced -------------------------------------

def test_ingest_relay_surfaces_the_uplink():
    reg = NodeRegistry()
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE", "type": "pi_propagation",
                                     "lat": -37.81, "lon": 144.96}})
    reg.ingest_relay(EVERYWHERE, "RNodeInterface", now=500.0)
    rec = reg.get(EVERYWHERE)
    assert rec.mesh_hops == 1 and rec.last_seen == 500.0
    assert rec.status(now=500.0) == "ok"           # reachable => healthy, named kin


def test_service_surfaces_via_from_rnpath():
    # rnpath: downstream nodes are 2 hops away VIA the relay; the relay itself is
    # never a destination line, so only via-surfacing makes it appear.
    reg = NodeRegistry()
    paths = [{"hash": "02675080aa", "hops": 2, "via": EVERYWHERE,
              "interface": "RNodeInterface"},
             {"hash": "03a1e00abb", "hops": 2, "via": EVERYWHERE,
              "interface": "RNodeInterface"}]
    import json as _json
    svc = MonitorService(registry=reg, run=lambda c: _json.dumps(paths),
                         now=lambda: 1000.0,
                         kin_roster={EVERYWHERE: {"name": "EVERYWHERE",
                                                  "type": "pi_propagation"}})
    svc.discover_mesh()
    relay = reg.get(EVERYWHERE)
    assert relay is not None and relay.name == "EVERYWHERE"
    assert relay.provenance == "kin" and relay.mesh_hops == 1


def test_service_reloads_roster_from_disk_on_rediscover(tmp_path, monkeypatch):
    # A node BIRTHed (or a location edited) while the app runs must appear on the
    # next rediscover without restarting — the disk-backed roster is re-read.
    import monitor.service as service_mod
    path = str(tmp_path / "kin.json")
    monkeypatch.setattr(service_mod, "load_roster", lambda: kin_roster.load_roster(path))
    reg = NodeRegistry()
    svc = MonitorService(registry=reg, run=lambda c: "[]", now=lambda: 1.0)
    assert reg.get(EVERYWHERE) is None                 # roster empty at start
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation",
                        lat=-37.70, lon=145.00, path=path)   # birthed mid-run
    svc.cycle(rediscover=True)
    rec = reg.get(EVERYWHERE)
    assert rec is not None and rec.name == "EVERYWHERE" and rec.has_location()


def test_service_ignores_self_and_local_vias():
    reg = NodeRegistry()
    # bb22 is a direct 1-hop destination whose via is itself — must NOT spawn a
    # phantom relay copy. (Local-interface paths are filtered by discover_mesh.)
    paths = [{"hash": "bb22", "hops": 1, "via": "bb22", "interface": "RNodeInterface"}]
    import json as _json
    svc = MonitorService(registry=reg, run=lambda c: _json.dumps(paths),
                         now=lambda: 1.0, kin_roster={})
    svc.discover_mesh()
    assert set(reg.nodes) == {"bb22"}              # no self-via phantom


def test_kin_declared_links_show_in_capabilities():
    """A kin Pi propagation node the medic only HEARS on LoRa still shows its real
    wifi/bt/internet in VITALS, because the roster declares what it physically has."""
    reg = NodeRegistry()
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE", "type": "pi_propagation",
                                     "links": {"lora": True, "wifi": True,
                                               "bluetooth": True, "internet": True}}})
    reg.ingest_relay(EVERYWHERE, "RNodeInterface", now=10.0)   # heard on LoRa only
    dev = next(d for d in reg.devices(now=10.0) if d.get("name") == "EVERYWHERE")
    caps = dev["capabilities"]
    assert caps["lora"] is True and caps["wifi"] is True
    assert caps["bluetooth"] is True and caps["internet"] is True


def test_register_defaults_links_by_node_type(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation", path=path)
    links = kin_roster.load_roster(path)[EVERYWHERE]["links"]
    assert links == {"lora": True, "wifi": True, "bluetooth": True, "internet": True}


def test_kin_rtnode_declares_lora_by_type():
    """FAITH (an RTNode-2400 reached over WiFi) must still show LoRa — an RTNode is
    definitionally a LoRa node. Declared by node type, but ONLY because it's kin."""
    reg = NodeRegistry()
    reg.register("fa02cafe", name="FAITH RTnode", node_type="rtnode2400")  # kin (named)
    dev = next(d for d in reg.devices(now=0.0) if d.get("name") == "FAITH RTnode")
    assert dev["capabilities"]["lora"] is True


def test_anonymous_neighbour_gets_no_declared_links():
    """A bare mesh neighbour must NOT get guessed wifi/lora from its default type —
    only kin declare capabilities."""
    reg = NodeRegistry()
    reg.ingest_mesh(MeshNode(dst_hash="beefbeef", hops=2, interface="RNodeInterface"),
                    now=1.0)
    dev = next(d for d in reg.devices(now=1.0) if d["provenance"] == "neighbour")
    # heard on the radio -> lora True (real); wifi stays unknown (never guessed)
    assert dev["capabilities"]["wifi"] is None


def test_capabilities_reads_lora_online_from_http_status():
    """Auto-detection: an RTNode that self-reports lora_online over HTTP shows LoRa
    live — no manual declaration needed (this is why FAITH looked WiFi-only)."""
    from monitor.http_status import NodeStatus
    reg = NodeRegistry()
    rec = reg.register("faithlive", name="FAITH RTnode", node_type="rtnode2400")
    rec.latest_http = NodeStatus(reachable=True, status="ok",
                                 lora_online=True, wifi_connected=True)
    dev = next(d for d in reg.devices(0.0) if d["name"] == "FAITH RTnode")
    assert dev["capabilities"]["lora"] is True
    assert dev["capabilities"]["wifi"] is True
    assert dev["capabilities"]["bluetooth"] is None   # not reported => honest grey


def _beacon(**over):
    from monitor.health_beacon import HealthBeacon
    d = dict(format_version=1, uptime_s=100, free_heap_kb=50, wifi_rssi_dbm=-60,
             reset_reason=0, wifi_up=False, lora_up=False, tcp_backbone_up=False,
             local_tcp_server_up=False, wdt_armed=True, psram=True, fault=False,
             airtime_lock=False, board_id=0, firmware_version=0)
    d.update(over)
    return HealthBeacon(**d)


def test_capabilities_reads_lora_up_from_beacon():
    """A node the medic only hears via its LoRa health beacon still shows LoRa when
    the beacon's flags say lora_up — the node reports its own active links."""
    reg = NodeRegistry()
    rec = reg.register("beacononly", name="", node_type="rtnode2400")
    rec.latest_beacon = _beacon(lora_up=True, wifi_up=True)
    rec.mesh_interface = ""                       # not learned from the path table
    dev = reg.devices(0.0)[0]
    assert dev["capabilities"]["lora"] is True    # was ignored before this fix
    assert dev["capabilities"]["wifi"] is True
