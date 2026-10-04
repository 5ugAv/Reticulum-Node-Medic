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
        if cmd.startswith("ls -1"):                 # the OS image lookup
            hit = any(p in cmd for p in self.present)
            return 0, ("/home/x/pi_os_lite.img.xz\n" if hit else ""), ""
        if cmd.startswith("ls ") and "wc -l" in cmd:  # wheelhouse.wheel_count
            return 0, ("17\n" if "packages" in self.present else "0\n"), ""
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
    # The map area, the OS image and the toolchain need a human's hand (or a
    # different screen). carry_all must not pretend it will fill them — and
    # each must say exactly HOW it gets aboard (operator, 2026-10-04).
    st = {s.key: s for s in audit(_Disk(present=[]))}
    assert st["map_tiles"].toppable is False and "MAPS" in st["map_tiles"].how
    assert st["os_image"].toppable is False
    assert "pi_os_lite.img.xz" in st["os_image"].how
    assert st["build_toolchain"].toppable is False
    assert "BUILD" in st["build_toolchain"].how
    # the wheels ARE fetched now: pip downloads them on the medic itself
    assert st["wheels"].toppable is True
    assert st["rnode_firmware"].toppable is True
    assert st["phone_apps"].toppable is True


# -- "Prepare for the field" says what it did (operator, 2026-10-04) ---------

ALL = ["arduino15", "platformio", "mbtiles", "packages", "firmware_marker",
       "apps", "pi_os"]


def _canned(monkeypatch, fw_changed=(), fw_failed=(), apps=None):
    """Stand-ins for the three downloaders carry_all composes."""
    from workflows import phone_apps, updater, wheelhouse
    from workflows.updater import SyncResult
    monkeypatch.setattr(updater, "sync_firmware",
                        lambda c, force=False: SyncResult(
                            online=True, changed=list(fw_changed),
                            failed=list(fw_failed), version="1.86"))
    monkeypatch.setattr(phone_apps, "sync_all", lambda c: dict(apps or {}))
    fetched = []
    monkeypatch.setattr(wheelhouse, "cache_wheels",
                        lambda c, **k: (fetched.append("wheels"), (True, "ok"))[1])
    return fetched


def test_everything_current_says_nothing_new_to_fetch(monkeypatch):
    """With every tick already ticked the button used to flip back with no
    word — the operator could not tell whether it had done anything."""
    wheels = _canned(monkeypatch)
    rep = carry_all(_Disk(present=ALL), progress=lambda t: None)
    assert rep.ready and rep.online
    assert rep.topped_up == [] and rep.failed == []
    assert "RNode firmware" in rep.checked and "Python wheels" in rep.checked
    assert "nothing new to fetch" in rep.message
    assert wheels == [], "a full wheelhouse is not downloaded again"


def test_fetched_and_failed_items_are_named(monkeypatch):
    from workflows.updater import SyncResult
    _canned(monkeypatch, fw_changed=["rnode_firmware_1.86.zip"],
            apps={"columba": SyncResult(online=True, changed=["x.apk"]),
                  "sideband": SyncResult(online=True, failed=["y.apk"])})
    rep = carry_all(_Disk(present=ALL))
    assert rep.topped_up == ["RNode firmware 1.86", "columba app"]
    assert rep.failed == ["sideband app"]
    assert rep.message.startswith("Fetched: RNode firmware 1.86, columba app.")
    assert "Could not fetch: sideband app." in rep.message


def test_an_empty_wheelhouse_is_fetched_online(monkeypatch):
    wheels = _canned(monkeypatch)
    disk = _Disk(present=[p for p in ALL if p != "packages"])
    steps = []
    rep = carry_all(disk, progress=steps.append)
    assert wheels == ["wheels"]
    assert "Python wheels" in rep.topped_up
    assert any("wheels" in t.lower() for t in steps)
    assert any("firmware" in t.lower() for t in steps)


def test_the_toolchain_needs_both_build_systems():
    """The RTNode build is a PlatformIO run; the Arduino esp32 core alone
    called a medic ready that could not build."""
    half = {s.key: s for s in audit(_Disk(present=["arduino15"]))}
    assert half["build_toolchain"].carried is False
    assert "PlatformIO" in half["build_toolchain"].detail
    both = {s.key: s for s in audit(_Disk(present=["arduino15", "platformio"]))}
    assert both["build_toolchain"].carried is True


def test_a_medic_with_no_os_image_is_never_called_ready():
    """The audit once checked five things and not the one without which no node
    can be built. A missing OS image must fail readiness, not pass silently."""
    st = {s.key: s for s in audit(_Disk(present=["arduino15", "mbtiles", "packages",
                                                 "firmware_marker", "apps"]))}
    assert st["os_image"].carried is False
    assert st["os_image"].toppable is False, "500MB — never fetched by accident"
