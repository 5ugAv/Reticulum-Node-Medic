"""A node can be taken off the medic's own map after birth.

Operator, 2026-09-23: SKYFINGER and ELSEWHERE were placed on the same spot;
SKYFINGER stays on the roof, ELSEWHERE is a test name — "we don't need that
on the map". Removing is the medic forgetting where it placed the node
(every aspect row of that device, and the roster), confirmed first; what
the node publishes is location_share's business and is not touched.
"""
import json
import os

from tests.srcutil import ROOT, func_source


def test_the_roster_forgets_a_placed_position(tmp_path):
    from monitor import kin_roster as kr
    p = str(tmp_path / "kin.json")
    kr.register("aa11", "Test", node_type="pi_propagation", lat=-37.7, lon=145.0, path=p)
    assert kr.load_roster(p)["aa11"]["lat"] == -37.7
    kr.clear_location("aa11", path=p)
    e = kr.load_roster(p)["aa11"]
    assert "lat" not in e and "lon" not in e and e["name"] == "Test"
    kr.clear_location("nope", path=p)            # unknown hash: nothing, no raise


def test_the_registry_clears_every_row_of_the_device():
    import time
    from monitor.registry import NodeRegistry
    from monitor.mesh import MeshNode
    now = time.time()
    reg = NodeRegistry()
    dev = "aa11aa11aa11aa11"
    reg.set_kin_roster({
        "aa11aa11aa11aa11": {"name": "ELSEWHERE", "type": "pi_propagation", "device": dev, "lat": -37.7, "lon": 145.0},
        "bb22bb22bb22bb22": {"name": "ELSEWHERE", "type": "pi_propagation", "device": dev, "lat": -37.7, "lon": 145.0},
        "cc33cc33cc33cc33": {"name": "OTHER", "type": "pi_propagation", "lat": -37.8, "lon": 145.1},
    })
    for h in ("aa11aa11aa11aa11", "bb22bb22bb22bb22", "cc33cc33cc33cc33"):
        reg.ingest_mesh(MeshNode(dst_hash=h, hops=1, interface="LoRa"), now)
    cleared = reg.clear_location("bb22bb22bb22bb22")
    assert set(cleared) == {"aa11aa11aa11aa11", "bb22bb22bb22bb22"}
    assert not any(d["name"] == "ELSEWHERE" for d in reg.located_nodes(now))
    assert any(d["name"] == "OTHER" for d in reg.located_nodes(now)), "another node keeps its pin"
    assert reg.clear_location("zz") == []


def test_the_popup_offers_it_confirmed_and_forgets_both_stores():
    src = open(os.path.join(ROOT, "ui/widgets/map_sharing.py"), encoding="utf-8").read()
    assert 'tr("Remove from the map")' in src
    body = func_source("ui/widgets/map_sharing.py", "_confirm_remove", cls="MapSharingPopup")
    assert "confirm_danger(" in body and 'tr("Remove it")' in body
    assert "if not rec.has_location():" in body, "nothing to remove is nothing to ask"
    rm = func_source("ui/widgets/map_sharing.py", "_remove_from_map", cls="MapSharingPopup")
    assert "clear_location(rec.dst_hash)" in rm and "kin_roster.clear_location(h)" in rm
    assert "self._persist()" in rm and "self._refresh()" in rm
    ref = func_source("ui/widgets/map_sharing.py", "_refresh", cls="MapSharingPopup")
    assert "btn.disabled = not rec.has_location()" in ref


def test_the_strings_format_in_every_catalog():
    keys = ["Remove from the map", "Remove {name} from the map?", "Remove it",
            "Node Medic will forget where you placed {name}, and its pin leaves the map. "
            "What the node publishes is not changed here. If the node ever sends its own "
            "GPS fix, it will place itself again."]
    for lang in ("de", "es", "fr", "id", "ja", "pl", "ru", "sv"):
        d = json.load(open(os.path.join(ROOT, f"assets/i18n/{lang}.json"), encoding="utf-8"))
        for k in keys:
            assert k in d, (lang, k)
            d[k].format(name="X")
