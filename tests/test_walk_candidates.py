"""Who the medic can offer for a boundary walk (operator, 2026-09-19:
"under ANTENNA ... a selection of whichever nodes are currently connected
to the medic, if there's more than one, to choose the boundary").

"Connected" has to mean something checkable: heard recently enough that a
ping has a chance. A node silent for days is not a candidate — offering it
would send the operator walking away from something that was never going to
answer."""
import monitor.boundary_walk as bw
from monitor.boundary_walk import walkable_nodes


class _Rec:
    def __init__(self, name, dst, hours, lat=None, lon=None):
        self.name, self.dst_hash = name, dst
        self._h, self.lat, self.lon = hours, lat, lon

    def last_seen_hours(self, now):
        return self._h


class _Reg:
    def __init__(self, recs):
        self._recs = recs

    def all(self, now):
        return list(self._recs)


def test_recently_heard_nodes_are_offered_newest_first():
    reg = _Reg([_Rec("OLD", "aa" * 16, 3.0), _Rec("FRESH", "bb" * 16, 0.2)])
    out = walkable_nodes(reg, now=1000.0)
    assert [n["name"] for n in out] == ["FRESH", "OLD"]


def test_a_node_silent_for_days_is_not_a_candidate():
    reg = _Reg([_Rec("GHOST", "cc" * 16, 72.0)])
    assert walkable_nodes(reg, now=1000.0) == []


def test_a_never_heard_node_is_not_a_candidate():
    reg = _Reg([_Rec("NEVER", "dd" * 16, None)])
    assert walkable_nodes(reg, now=1000.0) == []


def test_a_node_with_no_mesh_address_cannot_be_walked():
    """The walk pings a destination; a row with nothing to ping is not one."""
    reg = _Reg([_Rec("NAMEONLY", "", 0.5)])
    assert walkable_nodes(reg, now=1000.0) == []


def test_each_candidate_carries_what_the_picker_shows():
    reg = _Reg([_Rec("RTnodet114", "cb" * 16, 0.1, lat=-37.8, lon=144.9)])
    n = walkable_nodes(reg, now=1000.0)[0]
    assert n["name"] == "RTnodet114"
    assert n["dst_hash"] == "cb" * 16
    assert n["heard_hours"] == 0.1
    assert n["lat"] == -37.8 and n["lon"] == 144.9


def test_the_window_is_a_declared_number_not_a_magic_one():
    assert bw.WALK_CANDIDATE_MAX_AGE_H > 0


# -- the two doors are wired, and neither replaces the other ---------------

def test_antenna_offers_the_walk_and_vitals_keeps_its_button():
    from tests.srcutil import src
    tri = src("ui/screens/triage_screen.py")
    assert "on_boundary_walk" in tri and "Range test" in tri
    detail = src("ui/screens/node_detail_screen.py")
    assert "Range test" in detail, (
        "the node's own page keeps its direct button (operator, 2026-09-19: "
        "'leave the boundary walk button inside vitals ... but ALSO under "
        "antenna')")
    app = src("ui/app.py")
    assert "_pick_node_for_walk" in app and "walkable_nodes" in app
    assert "on_boundary_walk=self._pick_node_for_walk" in app


def test_the_picker_says_why_when_it_has_nothing_to_offer():
    from tests.srcutil import func_source
    pick = func_source("ui/app.py", "_pick_node_for_walk", cls="ReticulumNodeMedicApp")
    assert "No node has been heard" in pick, (
        "an empty list with no explanation is the kind of silence this "
        "project keeps banning")
