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
    def __init__(self, name, dst, hours, announced_name=""):
        self.name, self.dst_hash, self._h = name, dst, hours
        self.announced_name = announced_name
        self.lat = self.lon = None

    def last_seen_hours(self, now):
        return self._h


class _Reg:
    """The registry's device fold, in miniature: ``groups`` is the
    ``consolidated_records`` answer — ``[(device, [members]), ...]`` — and
    ``targets`` maps a device key to the ranked probe addresses the real
    registry's ``probe_targets_for`` would return."""

    def __init__(self, groups, targets=None):
        self._groups, self._targets = list(groups), dict(targets or {})

    def consolidated_records(self, now):
        return list(self._groups)

    def probe_targets_for(self, key):
        if key in self._targets:
            return list(self._targets[key])
        return [key] if len(key) == 32 else []


def test_one_physical_node_is_offered_once_not_once_per_destination():
    """The T114's four rows, exactly as the registry held them — one device,
    offered once, at the address the registry ranks likeliest to answer."""
    a, b, c = (_Rec("RTnodet114", "aa" * 16, 0.5),
               _Rec("RTnodet114", "bb" * 16, 0.1),
               _Rec("RTnodet114", "cc" * 16, 2.0))
    reg = _Reg([(a, [a, b, c])],
               targets={"aa" * 16: ["bb" * 16, "aa" * 16, "cc" * 16]})
    cands = walkable_nodes(reg, now=1000.0)
    assert len(cands) == 1, cands
    # and it keeps the destination the registry ranks first — the one the
    # node was last heard SPEAKING on, the likeliest to answer
    assert cands[0]["dst_hash"] == "bb" * 16


def test_the_picker_names_the_device_not_the_answering_address():
    """2026-10-04: the picker offered the two nodes that answered as bare
    hash prefixes — the answering rows were nameless aspect records, and the
    named rows pointed at addresses the nodes never speak on. The entry
    carries the DEVICE's name and the device's best address."""
    http_row = _Rec("SkyFinger", "rtnode:skyfinger", 0.1)
    voice = _Rec("", "ab" * 16, 0.2)
    reg = _Reg([(http_row, [http_row, voice])],
               targets={"rtnode:skyfinger": ["ab" * 16]})
    cands = walkable_nodes(reg, now=1000.0)
    assert [(n["name"], n["dst_hash"]) for n in cands] == [("SkyFinger", "ab" * 16)]


def test_a_device_with_no_mesh_address_is_not_offered():
    wifi_only = _Rec("Lonely", "rtnode:lonely", 0.1)
    assert walkable_nodes(_Reg([(wifi_only, [wifi_only])]), now=1000.0) == []


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
    q = _Rec("QUIET", "aa" * 16, 20.0)
    reg = _Reg([(q, [q])])
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


# -- a walk is not a placement form (operator, mid-walk 2026-09-21) --------

def test_a_running_walk_puts_the_place_a_node_controls_away():
    """The operator, standing under a tree with a node in it, was offered a
    full-width green "Use this position →" under the walk banner — which
    stamps a position and jumps into BIRTH. During a walk this screen is a
    measuring instrument, not a form."""
    from tests.srcutil import func_source
    begin = func_source("ui/screens/scan_screen.py", "begin_walk",
                        cls="ScanScreen")
    assert "_show_placement(False)" in begin
    end = func_source("ui/screens/scan_screen.py", "end_walk", cls="ScanScreen")
    assert "_show_placement(True)" in end, "the controls must come back"
    hide = func_source("ui/screens/scan_screen.py", "_show_placement",
                       cls="ScanScreen")
    # disabled, not merely invisible — an opacity-0 Kivy widget still takes taps
    assert "disabled" in hide
    # the two numbers a walker actually wants must NOT be hidden — check the
    # CODE, not the prose (the docstring names them to say they stay)
    code = hide.split('"""')[-1]
    assert "_place_row" in code and "detail_btn" in code
    assert "self.badge" not in code and "self.coords" not in code
