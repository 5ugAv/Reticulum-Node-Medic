"""The medic's fleet roster — its own nodes show as NAMED KIN in VITALS and land
on the MAP at their deployed spot, even a propagation relay it can't hear directly
(the EVERYWHERE bug: the uplink via was invisible)."""

from monitor import kin_roster
from monitor.registry import NodeRegistry
from monitor.service import MonitorService
from monitor.mesh import MeshNode


EVERYWHERE = "33445566778899aabbccddeeff001122"


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
    paths = [{"hash": "5a090009aa", "hops": 2, "via": EVERYWHERE,
              "interface": "RNodeInterface"},
             {"hash": "5a100010bb", "hops": 2, "via": EVERYWHERE,
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


def test_a_roster_declaration_does_not_become_a_working_interface():
    """REVERSED ON PURPOSE, 2026-08-10. This test used to assert that a roster
    entry saying "this board has wifi and bluetooth" made VITALS show wifi and
    bluetooth as WORKING on a node the medic had only ever heard on LoRa.

    It caught up with us on SolarLove: the row showed BT for a node whose
    Bluetooth adapter was rfkill-blocked and had never been asked about. The
    operator's rule — nothing is stated unless it is true, and what is true is
    what the NODE said — makes a datasheet inadmissible as evidence about a
    particular node on a particular roof."""
    reg = NodeRegistry()
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE", "type": "pi_propagation",
                                     "links": {"lora": True, "wifi": True,
                                               "bluetooth": True, "internet": True}}})
    reg.ingest_relay(EVERYWHERE, "RNodeInterface", now=10.0)   # heard on LoRa only
    dev = next(d for d in reg.devices(now=10.0) if d.get("name") == "EVERYWHERE")
    caps = dev["capabilities"]
    assert caps["lora"] is True, "heard over the radio IS evidence"
    for unheard in ("wifi", "bluetooth", "internet"):
        assert caps[unheard] is None, (
            f"{unheard} was never reported by the node — unknown, not working")


def test_register_records_no_links_it_was_not_given(tmp_path):
    """It used to fall back to the board type's datasheet and write that into the
    roster as fact, where the display read it straight back out. An assumption
    that gets persisted stops looking like an assumption."""
    path = str(tmp_path / "kin.json")
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation", path=path)
    entry = kin_roster.load_roster(path)[EVERYWHERE]
    assert not entry.get("links"), "no links were measured, so none are recorded"


def test_register_still_records_links_it_was_given(tmp_path):
    """A deliberate answer — an operator choosing Bluetooth at birth, or a
    measurement — is evidence and is kept."""
    path = str(tmp_path / "kin.json")
    kin_roster.register(EVERYWHERE, "EVERYWHERE", "pi_propagation",
                        links={"bluetooth": False}, path=path)
    assert kin_roster.load_roster(path)[EVERYWHERE]["links"] == {"bluetooth": False}


def test_even_an_rtnode_has_to_say_so_itself():
    """"An RTNode-2400 is definitionally a LoRa node" was the most defensible
    version of the assumption, and it is still an assumption: a node that has
    said nothing may be off, may be broken, may have lost its antenna. Registered
    and never heard from, it shows unknown — and the moment it IS heard on the
    radio, or reports lora_up in its beacon, that becomes True on evidence."""
    reg = NodeRegistry()
    reg.register("fa02cafe", name="FAITH RTnode", node_type="rtnode2400")  # kin (named)
    dev = next(d for d in reg.devices(now=0.0) if d.get("name") == "FAITH RTnode")
    assert dev["capabilities"]["lora"] is None

    reg.ingest_relay("fa02cafe", "RNodeInterface", now=1.0)     # now it has spoken
    dev = next(d for d in reg.devices(now=1.0) if d.get("name") == "FAITH RTnode")
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


# --- the type is looked up, never defaulted --------------------------------

def test_a_pi_propagation_cert_is_not_called_an_rtnode():
    """2026-08-10, the first Pi propagation node ever built: VITALS showed 2k13
    as "rtnode2400" with LoRa as its only interface. The caller read
    cert.get("type", "rtnode2400"), no birth has ever written a "type" key, so
    the fallback answered every time — and DEFAULT_LINKS["rtnode2400"] is
    LoRa-only, so the node's wifi, bluetooth and internet went invisible."""
    from monitor.kin_roster import type_for_cert, CAPABLE_OF
    t = type_for_cert({"role": "LXMF propagation node", "board": "Heltec LoRa32 v4"})
    assert t == "pi_propagation"
    # CAPABLE_OF is reference material — what a board of this class CAN have. It
    # is deliberately not consulted by anything that draws a screen; see
    # monitor/registry.py _capabilities. The type still has to be right, because
    # it is how the medic knows what to ASK about a node.
    assert CAPABLE_OF[t]["wifi"], "a Pi is capable of more than a radio"


def test_a_transport_node_is_still_an_rtnode():
    from monitor.kin_roster import type_for_cert
    assert type_for_cert({"role": "Transport node"}) == "rtnode2400"


def test_an_explicit_type_wins():
    from monitor.kin_roster import type_for_cert
    assert type_for_cert({"type": "something_new",
                          "role": "Transport node"}) == "something_new"


def test_an_unknown_role_returns_nothing_rather_than_guessing():
    """Handing back a confident wrong answer is what caused this. Empty lets the
    caller decide, and makes the decision visible where it is made."""
    from monitor.kin_roster import type_for_cert
    assert type_for_cert({"role": "Gateway node"}) == ""
    assert type_for_cert({}) == ""


# --- nothing is stated unless the node said it -----------------------------

def test_capabilities_never_invents_an_interface(tmp_path):
    """The rule the operator drew on 2026-08-10, after VITALS showed Bluetooth
    for a node whose adapter was rfkill-blocked and had never been asked:
    nothing is stated unless it is true, and what is true is what the NODE said.
    A board's datasheet is not evidence about the thing on the roof."""
    reg = NodeRegistry()
    reg.register("aabbccdd", name="ROOFTOP", node_type="pi_propagation")
    dev = next(d for d in reg.devices(now=0.0) if d.get("name") == "ROOFTOP")
    caps = dev["capabilities"]
    assert set(caps) == {"lora", "wifi", "bluetooth", "internet"}
    assert all(v is None for v in caps.values()), \
        "a node that has said nothing has claimed nothing"


def test_bluetooth_is_never_claimed_from_a_board_type():
    """The specific one that was wrong on screen. No node reports Bluetooth
    today, so it must read unknown everywhere until one does."""
    reg = NodeRegistry()
    reg.set_kin_roster({EVERYWHERE: {"name": "EVERYWHERE", "type": "pi_propagation",
                                     "links": {"bluetooth": True}}})
    reg.ingest_relay(EVERYWHERE, "RNodeInterface", now=5.0)
    dev = next(d for d in reg.devices(now=5.0) if d.get("name") == "EVERYWHERE")
    assert dev["capabilities"]["bluetooth"] is None


def test_the_three_states_are_kept_apart_on_screen():
    """True/False/None used to collapse into two looks — green for working and
    one grey for both "the node says it is down" and "never mentioned". That
    shared grey is the gap the board-type guess was poured into."""
    from tests.srcutil import func_source
    src = open("ui/screens/vitals_screen.py").read()
    assert "state is False" in src, "reported-down needs its own look"
    assert 'COLORS[colour]' in src or '"amber"' in src
    detail = open("ui/screens/node_detail_screen.py").read()
    assert "not reported by the node" in detail
    assert "down — the node says so" in detail
