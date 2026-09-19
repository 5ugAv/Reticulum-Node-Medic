"""The picker offers only nodes that answer RIGHT NOW (operator, 2026-09-19:
"the node medic should send out a ping and only offer nodes that are
currently connected when the user is about to do the walk").

The project's oldest law, applied here: a remembered sighting is not a
sighting. A node heard eleven hours ago may be dead, and offering it sends
the operator walking away from something that will never answer. So the
registry only nominates CANDIDATES; a live probe decides who is offered.

Also deduped by device: one physical node announces on several destinations
(a T114 sat in the registry under four, 2026-09-19), and probing each would
waste the operator's time and list the same box four times."""
from monitor.boundary_walk import answers_now, walkable_nodes


class _Rec:
    def __init__(self, name, dst, hours):
        self.name, self.dst_hash, self._h = name, dst, hours
        self.lat = self.lon = None

    def last_seen_hours(self, now):
        return self._h


class _Reg:
    def __init__(self, recs):
        self._recs = recs

    def all(self, now):
        return list(self._recs)


def test_one_physical_node_is_offered_once_not_once_per_destination():
    """The T114's four rows, exactly as the registry held them."""
    reg = _Reg([_Rec("RTnodet114", "aa" * 16, 0.5),
                _Rec("RTnodet114", "bb" * 16, 0.1),
                _Rec("RTnodet114", "cc" * 16, 2.0)])
    cands = walkable_nodes(reg, now=1000.0)
    assert len(cands) == 1, cands
    # and it keeps the FRESHEST destination — the likeliest to answer
    assert cands[0]["dst_hash"] == "bb" * 16


def test_only_the_nodes_that_answer_are_offered():
    cands = [{"name": "ALIVE", "dst_hash": "aa" * 16, "heard_hours": 0.2,
              "lat": None, "lon": None},
             {"name": "DEAD", "dst_hash": "bb" * 16, "heard_hours": 6.0,
              "lat": None, "lon": None}]
    offered = [n for n in (answers_now(c, probe=lambda d: d.startswith("aa"))
                           for c in cands) if n]
    assert [n["name"] for n in offered] == ["ALIVE"]


def test_a_probe_that_blows_up_is_a_no_not_a_crash():
    cands = [{"name": "X", "dst_hash": "aa" * 16, "heard_hours": 0.2,
              "lat": None, "lon": None}]

    def boom(_d):
        raise OSError("rnpath fell over")
    assert answers_now(cands[0], probe=boom) is None


def test_every_offered_node_is_marked_as_live_proven():
    cands = [{"name": "ALIVE", "dst_hash": "aa" * 16, "heard_hours": 9.0,
              "lat": None, "lon": None}]
    n = answers_now(cands[0], probe=lambda d: True)
    assert n["answered_now"] is True, (
        "the picker must be able to say it CHECKED, not that it remembered")


def test_the_prefilter_is_generous_because_the_probe_is_the_real_gate():
    """A node quiet for hours may still be perfectly alive — the registry
    window only decides who is worth a probe, not who is offered."""
    reg = _Reg([_Rec("QUIET", "aa" * 16, 20.0)])
    assert [n["name"] for n in walkable_nodes(reg, now=1000.0)] == ["QUIET"]


# -- the screen actually pings before it offers (wiring guard) -------------

def test_the_picker_probes_before_offering_anything():
    from tests.srcutil import func_source
    pick = func_source("ui/app.py", "_pick_node_for_walk",
                       cls="ReticulumNodeMedicApp")
    assert "_mesh_reachable" in pick, (
        "the picker must PING, not read the registry's memory "
        "(operator, 2026-09-19)")
    assert "Pinging your nodes" in pick
    # and it must say so honestly when nobody answers
    assert "None of your" in pick


def test_the_reachability_probe_drops_the_cached_path_first():
    """A cached path is not a sighting — the project's oldest law."""
    from tests.srcutil import func_source
    probe = func_source("ui/app.py", "_mesh_reachable",
                        cls="ReticulumNodeMedicApp")
    assert "rnpath --drop" in probe and "rnpath -w" in probe
