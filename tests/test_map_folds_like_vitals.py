"""The map must draw one dot per DEVICE, with the device's status.

Operator, 2026-09-28, with SCAN and VITALS side by side: skyfinger and
ELSEWHERE sat on the same spot with their names printed over each other into
an unreadable smear, and both dots were GREY while VITALS showed them GREEN.

One cause. ``located_nodes`` walked the RAW records while ``devices`` walked
``consolidated_records`` — so a node with several located aspects drew one dot
PER ASPECT (four each, stacked), and each took that aspect's own status. An
aspect never heard is "unknown", so the grey dot drawn last won.
"""
import time

from monitor.registry import NodeRegistry


def _reg():
    """One device reached three ways, as a real Pi node is: a beacon dest, an
    rnsd dest, and the LAN probe row — linked by the roster's device anchor,
    which is what birth records."""
    reg = NodeRegistry()
    now = time.time()
    for h in ("aa" * 16, "bb" * 16):
        reg.ingest_announce(bytes.fromhex(h), b"", now)
    reg.ingest_announce(bytes.fromhex("cc" * 16), b"", now - 400000)
    entry = {"name": "SKYFINGER", "type": "pi_propagation",
             "device": "aa" * 16, "lat": -37.7, "lon": 145.0}
    reg.set_kin_roster({"aa" * 16: entry, "bb" * 16: entry, "cc" * 16: entry})
    return reg, now


def test_one_dot_per_device_not_one_per_aspect():
    reg, now = _reg()
    dots = reg.located_nodes(now)
    assert len(dots) == 1, (
        "a node reached three ways drew three stacked dots: %s"
        % [(d["name"], d["status"]) for d in dots])


def test_the_dot_and_the_vitals_row_agree_on_status():
    """Same machine, two screens, one answer — the sibling rule. A grey dot
    beside a green row is the tool contradicting itself."""
    reg, now = _reg()
    dots = reg.located_nodes(now)
    rows = [r for r in reg.devices(now) if r.get("lat") is not None
            or r.get("name")]
    by_name = {r["name"]: r["status"] for r in rows}
    for d in dots:
        assert d["status"] == by_name.get(d["name"]), (
            "map says %s, VITALS says %s for %s"
            % (d["status"], by_name.get(d["name"]), d["name"]))


def test_the_map_reads_the_shared_fold():
    """Pinned at the source, so the two screens cannot drift apart again."""
    from tests.srcutil import func_source
    src = func_source("monitor/registry.py", "located_nodes",
                      cls="NodeRegistry")
    assert "consolidated_records" in src
    assert "self.nodes.values()" not in src, \
        "walking raw records is exactly the bug"
