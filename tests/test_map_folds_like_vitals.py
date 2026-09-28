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


# --- two nodes on one pixel must still be readable (2026-09-28) ------------

def test_a_second_label_on_the_same_spot_steps_aside():
    """skyfinger sits a few metres from ELSEWHERE, which is one pixel at street
    zoom. Their names printed over each other into an unreadable smear."""
    from ui.map_projection import boxes_overlap, place_label
    size, step = (90.0, 20.0), 23.0
    first = place_label((100.0, 300.0), size, [], step)
    taken = [(first[0], first[1], size[0], size[1])]
    second = place_label((100.0, 300.0), size, taken, step)
    assert second != first
    assert not boxes_overlap((second[0], second[1], size[0], size[1]), taken[0])


def test_a_label_with_room_is_left_where_it_belongs():
    """De-collision must not nudge labels that were never colliding — the name
    belongs beside its dot."""
    from ui.map_projection import place_label
    taken = [(500.0, 900.0, 90.0, 20.0)]
    assert place_label((100.0, 300.0), (90.0, 20.0), taken, 23.0) == (100.0, 300.0)


def test_three_on_one_spot_all_stay_clear_of_each_other():
    from ui.map_projection import boxes_overlap, place_label
    size, step = (90.0, 20.0), 23.0
    taken = []
    for _ in range(3):
        x, y = place_label((100.0, 300.0), size, taken, step)
        box = (x, y, size[0], size[1])
        assert not any(boxes_overlap(box, t) for t in taken)
        taken.append(box)
    assert len({(round(b[0]), round(b[1])) for b in taken}) == 3


def test_the_dot_never_moves_only_the_name():
    """A node's position is a fact. If crowding ever moved the DOT the map
    would be lying about where the hardware is."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/scan_screen.py", "_add_label",
                      cls="MapPlot")
    assert "place_label" in src
    assert "Ellipse" not in src, "the label placer must not touch the dot"


def test_the_occupied_list_is_cleared_with_the_labels():
    """Or the next redraw pushes every name down past ghosts that are gone."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/scan_screen.py", "_clear_labels",
                      cls="MapPlot")
    assert "_label_boxes" in src
