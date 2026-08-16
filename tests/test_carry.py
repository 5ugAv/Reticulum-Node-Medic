"""Provisioning audit — is this medic ready to be taken somewhere with no signal?

The failure these guard against is the one found live on 2026-08-16: a cache
that was never filled looks exactly like a cache that is full, until someone
looks. A readiness report that overstates readiness is worse than none.
"""

from transport.connection import EmulatedConnection
from workflows.carry import audit, carry_all, CarryReport, CarryStatus


class _Disk(EmulatedConnection):
    """A fake filesystem: `present` is a list of substrings that 'exist'."""

    def __init__(self, present=(), online=True):
        super().__init__()
        self.present = list(present)
        self.online = online
        self.synced = []

    def run(self, cmd, *a, **k):
        if cmd.startswith("curl -fsI"):
            return (0 if self.online else 1), "", ""
        if cmd.startswith("ls -1") and "wc -l" in cmd:
            hit = any(p in cmd for p in self.present)
            return 0, ("1\n" if hit else "0\n"), ""
        if cmd.startswith("du -sk"):
            return 0, ("1024\t-\n" if any(p in cmd for p in self.present) else "0\t-\n"), ""
        if cmd.startswith("cat") and "bundle_version" in cmd:
            return (0, "1.86\n", "") if "firmware_marker" in self.present else (1, "", "")
        return 0, "", ""


def test_audit_reports_every_item_even_when_all_are_missing():
    st = audit(_Disk(present=[]))
    keys = {s.key for s in st}
    assert keys == {"rnode_firmware", "phone_apps", "map_tiles", "wheels",
                    "build_toolchain", "os_image"}
    assert all(not s.carried for s in st)
    # Every gap must explain what it costs in the field, or the report is a
    # checklist nobody can act on.
    assert all(s.why for s in st)


def test_empty_phone_app_cache_is_reported_missing():
    # The exact live failure: assets/apps holding nothing but .gitkeep.
    st = {s.key: s for s in audit(_Disk(present=["arduino15", "mbtiles",
                                                 "packages", "firmware_marker"]))}
    assert st["phone_apps"].carried is False
    assert "none" in st["phone_apps"].detail.lower()


def test_ready_is_false_when_anything_at_all_is_missing():
    # "Mostly provisioned" is the state that strands someone, so ready is strict.
    rep = CarryReport(statuses=[CarryStatus("a", "A", "why", True),
                                CarryStatus("b", "B", "why", False)])
    assert rep.ready is False
    assert [s.key for s in rep.missing] == ["b"]


def test_ready_is_true_only_when_everything_is_aboard():
    rep = CarryReport(statuses=[CarryStatus("a", "A", "why", True),
                                CarryStatus("b", "B", "why", True)])
    assert rep.ready is True


def test_offline_run_tops_up_nothing_and_says_so_plainly():
    rep = carry_all(_Disk(present=[], online=False))
    assert rep.online is False
    assert rep.topped_up == []
    assert "offline" in rep.message.lower()
    # It must still audit, so the operator learns what is missing even offline.
    assert rep.statuses, "an offline run must still report what is aboard"


def test_report_names_the_gaps_rather_than_just_failing():
    rep = CarryReport(statuses=[CarryStatus("map_tiles", "Offline map", "w", False)])
    assert rep.missing[0].name == "Offline map"


def test_untoppable_items_are_flagged_so_nobody_waits_for_them():
    # The map area and the wheel set are human decisions. carry_all must not
    # pretend it will fill them.
    st = {s.key: s for s in audit(_Disk(present=[]))}
    assert st["map_tiles"].toppable is False
    assert st["wheels"].toppable is False
    assert st["rnode_firmware"].toppable is True
    assert st["phone_apps"].toppable is True


def test_a_medic_with_no_os_image_is_never_called_ready():
    """The audit once checked five things and not the one without which no node
    can be built. A missing OS image must fail readiness, not pass silently."""
    st = {s.key: s for s in audit(_Disk(present=["arduino15", "mbtiles", "packages",
                                                 "firmware_marker", "apps"]))}
    assert st["os_image"].carried is False
    assert st["os_image"].toppable is False, "500MB — never fetched by accident"
