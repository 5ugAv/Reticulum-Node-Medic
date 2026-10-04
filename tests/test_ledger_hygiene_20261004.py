"""Readiness ledger, 2026-10-04 evening batch: the small items a first-time
user or the test suite itself would trip on.

  #133  NodeRegistry.all()/visible() walked nodes without the lock
  #136  a stranger's row heard once turned red after 18 h and stayed red
  #192  the suite overwrote the operator's saved screen brightness
  #193  the suite planted a fake trusted clone in the REAL trust store
  #195  conftest promised a scratch language file and never pointed at one
  #44   a boxed V4 was quietly sent to the stock flash on a fresh medic
  #123  every card was baked with the developer's Wi-Fi country
"""
import os
import re

import pytest

from monitor.http_status import NodeStatus
from monitor.registry import NodeRegistry, STALE_ALERT_HOURS
# Bound at import time, BEFORE conftest's autouse stub replaces the module
# attribute for the rest of the suite — these tests want the real function.
from provisioning.wifi import medic_country as real_medic_country
from tests.srcutil import src

HASH = "11223344556677889900aabbccddeeff"
NOW = 1_000_000.0
HOUR = 3600.0


# -- #133: the two dashboard walks hold the lock ---------------------------

def test_all_and_visible_are_locked_walks():
    s = src("monitor/registry.py")
    assert re.search(r"@_locked[^\n]*\n    def all\(self, now: float\)", s)
    assert re.search(r"@_locked[^\n]*\n    def visible\(self, now: float", s)


# -- #136: a silent stranger is grey, not red forever ---------------------

def _kin_reachable(reg, now):
    reg.record_http_status(HASH, NodeStatus(
        reachable=True, status="ok", node_name="MINE", firmware_version="0.6.2",
        lora_online=True, local_tcp_server_up=True, faults=[]), now)
    return reg.get(HASH)


def test_a_silent_neighbour_row_is_grey_not_red():
    reg = NodeRegistry()
    rec = reg.ingest_announce(bytes.fromhex("ee" * 16), b"", NOW)   # a stranger, heard once
    assert rec.provenance == "neighbour"
    assert rec.to_dashboard(NOW)["status"] == "unknown"             # heard != healthy
    late = NOW + (STALE_ALERT_HOURS + 2) * HOUR
    assert rec.status(late) == "alert"                 # the raw SEEN rule: silent -> red
    assert rec.to_dashboard(late)["status"] == "unknown"   # ...but not about a stranger


def test_a_neighbour_the_medic_probed_and_got_nothing_from_keeps_its_warning():
    reg = NodeRegistry()
    rec = reg.ingest_announce(bytes.fromhex("ee" * 16), b"", NOW)
    late = NOW + (STALE_ALERT_HOURS + 2) * HOUR
    reg.record_probe("ee" * 16, ok=False, now=late - 10)   # the medic asked, silence
    assert rec.probe_unanswered is True
    assert rec.to_dashboard(late)["status"] == "warn"


def test_a_silent_kin_node_is_still_red():
    """The rule is about strangers only: a node of ours that falls silent is
    exactly what the red hexagon and the outage watch exist for."""
    reg = NodeRegistry()
    rec = _kin_reachable(reg, NOW)
    assert rec.provenance == "kin"
    late = NOW + (STALE_ALERT_HOURS + 2) * HOUR
    assert rec.to_dashboard(late)["status"] == "alert"


# -- #192 / #193 / #195: the suite never touches the medic's real prefs ------

def test_brightness_pref_path_is_resolved_when_called(monkeypatch, tmp_path):
    from provisioning import brightness as b
    monkeypatch.setattr(b, "CONFIG", str(tmp_path / "bright"))
    b.save_pct(42)
    assert (tmp_path / "bright").read_text().strip() == "42"
    assert b.load_pct() == 42


def test_trust_store_path_is_resolved_when_called(monkeypatch, tmp_path):
    from monitor import trust
    monkeypatch.setattr(trust, "CONFIG", str(tmp_path / "trust.json"))
    trust.set_self("ab" * 16, "me")
    assert (tmp_path / "trust.json").exists()
    assert trust.is_trusted("ab" * 16) is True
    assert any(u.get("name") == "me" for u in trust.units())


def test_no_default_path_is_frozen_at_import_time():
    for f in ("monitor/trust.py", "provisioning/brightness.py"):
        assert not re.search(r"path: str = CONFIG", src(f)), f


def test_the_suite_points_every_pref_at_scratch_and_speaks_english():
    """What conftest's autouse fixtures guarantee for EVERY test: the three
    on-disk preferences are scratch files, and tr() answers in English even
    on a medic whose keeper saved another language (#195: 66 tests went red
    that way)."""
    import ui.i18n as i18n
    from monitor import trust
    from provisioning import brightness
    real = os.path.expanduser("~/.reticulum-node-medic")
    for path in (i18n.LANGUAGE_FILE, brightness.CONFIG, trust.CONFIG):
        assert not os.path.abspath(path).startswith(real), path
    assert i18n.current_language() == "en"


# -- #44: a V4 is built first, or refused honestly — never stock ----------

def test_rgb_build_possible_needs_cli_core_and_source_or_internet(tmp_path):
    from workflows import rnode_v4_rgb as rgb
    core, source = tmp_path / "core", tmp_path / "src"
    kw = dict(firmware_dir=str(source), core_dir=str(core), cli_paths=())
    assert rgb.rgb_build_possible(which=lambda n: None, **kw) is False
    core.mkdir(); source.mkdir()
    assert rgb.rgb_build_possible(which=lambda n: None, **kw) is False     # no arduino-cli
    assert rgb.rgb_build_possible(which=lambda n: "/usr/bin/arduino-cli", **kw) is True
    # the medic's own arduino-cli lives off the app's PATH (~/.local/bin)
    cli = tmp_path / "arduino-cli"; cli.write_text("")
    assert rgb.rgb_build_possible(which=lambda n: None, firmware_dir=str(source),
                                  core_dir=str(core), cli_paths=(str(cli),)) is True
    # nothing aboard, but online: the workflow fetches what it lacks itself
    gone = dict(firmware_dir=str(tmp_path / "nosrc"), core_dir=str(core), cli_paths=())
    assert rgb.rgb_build_possible(which=lambda n: None, online=lambda: True, **gone) is True
    assert rgb.rgb_build_possible(which=lambda n: None, online=lambda: False, **gone) is False
    assert rgb.rgb_build_possible(which=lambda n: None, online=None, **gone) is False


def test_the_factory_never_falls_to_the_stock_flash_for_a_v4():
    s = src("ui/hw_factories.py")
    body = s[s.index("def make_rnode_flash("):s.index("def make_rtnode_build(")]
    assert "rgb_build_possible(" in body and "has_connectivity" in body
    # the stock workflow is only reached by boards that are not a V4
    assert body.index("if board.key == V4_BOARD_KEY:") < body.index("return RNodeFlashWorkflow(")
    assert "and rgb_firmware_available():" not in body


# -- #123: the card gets the medic's own country, and says when it couldn't --

def _medic_run():
    lsblk_disks = ("mmcblk0 59.5G disk mmc  0 \nzram0 2G disk   0 "
                   "\nsdb 29.7G disk usb  1 Generic SD Reader")

    def run(argv):
        if argv[:3] == ["findmnt", "-no", "SOURCE"]:
            return (0, "/dev/mmcblk0p2\n")
        if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
            return (0, "mmcblk0\n")
        if argv[:2] == ["lsblk", "-dno"]:
            return (0, lsblk_disks)
        if argv[:2] == ["openssl", "passwd"]:
            return (0, "$6$abc$deadbeefhash\n")
        return (0, "")
    return run


def _flash(monkeypatch, country_seen, **kw):
    from provisioning import pi_imager as pi
    import provisioning.wifi as wifi
    cfg = {}
    monkeypatch.setattr(pi, "write_card_config",
                        lambda c, path="/tmp/x": (cfg.update(c), path)[1])
    monkeypatch.setattr(wifi, "medic_country", lambda *a, **k: country_seen)
    ok, msg = pi.flash("/dev/sdb", "faithpi", "pi", "secret", image_path="/img.xz",
                       run=_medic_run(), run_shell=lambda c: (0, ""), **kw)
    assert ok, msg
    return cfg, msg


def test_a_card_is_baked_with_the_medics_own_country(monkeypatch):
    cfg, msg = _flash(monkeypatch, "NZ")
    assert cfg["wifi_country"] == "NZ"
    assert 'country = "NZ"' in cfg["custom_toml"]
    assert "Wi-Fi country set to" not in msg          # nothing to confess


def test_the_fallback_country_is_named_when_the_medics_cannot_be_read(monkeypatch):
    cfg, msg = _flash(monkeypatch, "")
    assert cfg["wifi_country"] == "AU"
    assert "Wi-Fi country set to AU" in msg and "raspi-config" in msg


def test_an_explicit_country_is_used_as_given(monkeypatch):
    cfg, msg = _flash(monkeypatch, "NZ", wifi_country="DE")
    assert cfg["wifi_country"] == "DE" and "Wi-Fi country set to" not in msg


def test_medic_country_reads_the_kernel_regdom_first(tmp_path):
    sysfs = tmp_path / "regdom"
    sysfs.write_text("NZ\n")
    assert real_medic_country(run=lambda argv: (1, ""), sysfs=str(sysfs)) == "NZ"


def test_medic_country_falls_back_to_raspi_config_then_to_nothing(tmp_path):
    sysfs = tmp_path / "regdom"
    sysfs.write_text("00\n")                       # the world domain = unset
    calls = []

    def run(argv):
        calls.append(argv)
        return (0, "DE\n")
    assert real_medic_country(run=run, sysfs=str(sysfs)) == "DE"
    assert calls == [["raspi-config", "nonint", "get_wifi_country"]]
    assert real_medic_country(run=lambda a: (1, "not found"),
                              sysfs=str(tmp_path / "missing")) == ""


def test_the_suite_itself_never_depends_on_this_machines_regdom():
    """conftest stubs medic_country() for every test, so an imaging message in
    the suite never carries the fallback note just because the dev box has no
    Wi-Fi country — the real function is tested above with injected inputs."""
    import provisioning.wifi as wifi
    assert wifi.medic_country() == "AU"
    assert wifi.medic_country is not real_medic_country
