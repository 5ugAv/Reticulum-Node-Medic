"""Wi-Fi on, LoRa off shows orange — and the LORA icon leaves green.

Operator, 2026-09-24 01:1x, on the glass: SKYFINGER's mesh ping went
unanswered ("Its VITALS row shows amber until it is heard again"), yet the
row stayed green with SEEN 0.0h and a green LORA icon — its Wi-Fi status
road had answered three minutes earlier and the device fold took the
freshest road. The mesh road is now judged on its own: a failed probe with
nothing heard OVER THE MESH since paints the device amber and the LORA
icon off-green; a Wi-Fi status answer never clears it; a beacon, reply,
announce or path does.
"""
import json
import os
import time

from monitor.http_status import NodeStatus
from monitor.registry import NodeRegistry
from tests.srcutil import ROOT, func_source

DEV = "aa11aa11aa11aa11"
HTTP_ROW = "rtnode:roofy"


def _reg(now):
    reg = NodeRegistry()
    reg.set_kin_roster({
        DEV: {"name": "roofy", "type": "pi_propagation", "device": DEV},
        HTTP_ROW: {"name": "roofy", "type": "pi_propagation", "device": DEV},
    })
    reg.ingest_announce(bytes.fromhex(DEV), b"", now - 2 * 3600)   # heard over the mesh 2 h ago
    reg.record_http_status(HTTP_ROW, NodeStatus(
        reachable=True, status="ok", node_name="roofy", firmware_version="x",
        lora_online=True, local_tcp_server_up=True, faults=[]), now - 60)
    return reg


def _device(reg, now):
    return next(d for d in reg.devices(now) if d["name"] == "roofy")


def test_a_fresh_wifi_status_does_not_hide_an_unanswered_mesh_probe():
    now = time.time()
    reg = _reg(now)
    assert _device(reg, now)["status"] == "ok"
    reg.record_probe(DEV, ok=False, now=now - 30)
    d = _device(reg, now)
    assert d["status"] == "warn"
    assert d["capabilities"]["lora"] is False, "LORA leaves green"
    assert d["last_seen_hours"] < 0.1, "SEEN stays honest: heard over Wi-Fi a minute ago"
    assert 1.9 < d["mesh_seen_hours"] < 2.1


def test_a_later_wifi_answer_still_does_not_clear_it():
    now = time.time()
    reg = _reg(now)
    reg.record_probe(DEV, ok=False, now=now - 30)
    reg.record_http_status(HTTP_ROW, NodeStatus(
        reachable=True, status="ok", node_name="roofy", firmware_version="x",
        lora_online=True, local_tcp_server_up=True, faults=[]), now - 5)
    assert _device(reg, now)["status"] == "warn"


def test_being_heard_over_the_mesh_again_clears_it():
    now = time.time()
    reg = _reg(now)
    reg.record_probe(DEV, ok=False, now=now - 30)
    reg.ingest_announce(bytes.fromhex(DEV), b"", now - 5)
    d = _device(reg, now)
    assert d["status"] == "ok" and d["capabilities"]["lora"] is True


def test_an_answered_probe_clears_it_too():
    now = time.time()
    reg = _reg(now)
    reg.record_probe(DEV, ok=False, now=now - 30)
    reg.record_probe(DEV, ok=True, now=now - 5)
    assert _device(reg, now)["status"] == "ok"


def test_the_node_page_names_both_roads_when_they_differ():
    body = func_source("ui/screens/node_detail_screen.py", "__init__", cls="NodeDetailScreen")
    assert 'tr("Last heard: {when} over Wi-Fi · over the mesh {mesh} ago")' in body
    assert 'tr("Last heard: {when} over Wi-Fi · never over the mesh")' in body
    assert "mesh_seen_hours" in body


def test_the_strings_format_in_every_catalog():
    keys = {"Last heard: {when} over Wi-Fi · never over the mesh": dict(when="x"),
            "Last heard: {when} over Wi-Fi · over the mesh {mesh} ago": dict(when="x", mesh="y")}
    for lang in ("de", "es", "fr", "id", "ja", "pl", "ru", "sv"):
        d = json.load(open(os.path.join(ROOT, f"assets/i18n/{lang}.json"), encoding="utf-8"))
        for k, a in keys.items():
            assert k in d, (lang, k)
            d[k].format(**a)


def test_wrapping_lines_on_the_node_page_grow_with_their_text():
    """The "card drawn" note, the clock line and the last-heard line wrap;
    a one-line box clipped them (operator photo, 2026-09-24)."""
    src = open(os.path.join(ROOT, "ui/screens/node_detail_screen.py"), encoding="utf-8").read()
    assert "def _para(" in src and "texture_size=lambda i, ts: setattr(i, \"height\", ts[1])" in src
    for marker in ('_para(\n            tr("Figures below', "_para(clock_line(clock_entry)",
                   '_para(text, color="text_secondary"'):
        assert marker in src, marker
