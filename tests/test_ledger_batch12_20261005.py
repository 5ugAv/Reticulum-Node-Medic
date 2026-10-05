"""Readiness ledger, 2026-10-05 — batch twelve: the keeper's calls. The Clone
screen stops calling the Tracker a radio (#120), PROBE gets its door on the
node page (#144), the brightness rule is rendered for the installing machine's
own backlight (#16), and the card's timezone finally reaches the rootfs (#130)."""
import importlib.util
import json
import os
import stat
import subprocess

from tests.srcutil import ROOT, func_source, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _catalogs():
    for code in SHIPPED:
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"),
                  encoding="utf-8") as f:
            yield code, json.load(f)


# --- #120: the Tracker is the clone's GPS and clock, not its radio -----------

def test_the_clone_screen_calls_the_tracker_gps_and_clock():
    m = src("ui/screens/mitosis_screen.py")
    assert "the new medic's own radio" not in m
    assert "a Heltec Wireless Tracker — the new medic's GPS and clock" in m
    assert "Then it fits its radio" not in m
    assert "Then it fits its GPS - that is the Tracker" in m
    assert "Its own mesh radio is a separate job" in m
    assert "own mesh radio is not set up by this flow yet" in m
    for code, cat in _catalogs():
        parts = [k for k in cat if k.startswith("This copies this Node Medic onto a second one")]
        done = [k for k in cat if k.startswith("[b]Done - you have made a Node Medic.[/b]")]
        assert len(parts) == 1 and len(done) == 1, code
        assert "GPS and clock" in parts[0] and "radio" not in cat[parts[0]].split("\n")[7].lower() \
            or "GPS" in cat[parts[0]].split("\n")[7], code
        assert "Its own mesh radio is a separate job" in done[0], code


# --- #144: PROBE's door -----------------------------------------------------

def test_the_node_page_opens_probe_for_a_board_node_only():
    ctor = func_source("ui/screens/node_detail_screen.py", "__init__", cls="NodeDetailScreen")
    assert "on_probe=None" in ctor
    assert 'tr("Probe this node — plug it into the medic")' in ctor
    assert '.startswith("pi")' in ctor, "a Pi node is reached over the network, not USB"
    assert "self._on_probe(self.record)" in ctor
    assert "self.add_widget(self._probe_row)" in ctor
    app = src("ui/app.py")
    assert 'on_probe=lambda rec: self.switch_mode("probe")' in app
    for code, cat in _catalogs():
        assert cat.get("Probe this node — plug it into the medic"), code
    tour = src("ui/setup_flow.py")
    assert "open it from a node's page: VITALS, tap the " in tour
    assert '"node, then Probe.' in tour


# --- #16: the brightness rule names THIS machine's backlight ----------------

RENDER = os.path.join(ROOT, "provisioning", "security", "render_sudoers.sh")
POLICY = os.path.join(ROOT, "provisioning", "sudoers.d", "nodemedic")


def _render(tmp_path, dev):
    out = tmp_path / "rendered"
    r = subprocess.run(["bash", RENDER, POLICY, str(out), dev],
                       capture_output=True, text=True)
    return r, out


def test_render_substitutes_only_the_backlight_path(tmp_path):
    r, out = _render(tmp_path, "10-0045")
    assert r.returncode == 0, r.stderr
    rendered = out.read_text().splitlines()
    source = open(POLICY).read().splitlines()
    assert len(rendered) == len(source)
    changed = [(a, b) for a, b in zip(source, rendered) if a != b]
    # the rule AND the comment that documents it — nothing else moves
    assert changed and all("/sys/class/backlight/10-0045/brightness" in b
                           for _a, b in changed), changed
    assert "    /usr/bin/tee /sys/class/backlight/10-0045/brightness" in rendered
    rules = [ln for ln in rendered if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("panel_backlight@1" in ln for ln in rules)


def test_render_keeps_the_pinned_device_verbatim(tmp_path):
    r, out = _render(tmp_path, "panel_backlight@1")
    assert r.returncode == 0 and out.read_text() == open(POLICY).read()


def test_render_refuses_an_odd_device_name(tmp_path):
    r, out = _render(tmp_path, "x; rm -rf /")
    assert r.returncode != 0 and not out.exists()


def test_apply_renders_before_validating():
    apply = src("provisioning/security/apply_sudoers.sh")
    assert 'render_sudoers.sh" "$SRC" "$TMP"' in apply
    assert apply.index("render_sudoers.sh") < apply.index('visudo -cf "$TMP"')
    assert stat.S_IXUSR & os.stat(RENDER).st_mode
    assert "render_sudoers.sh" in src("provisioning/security/README.md")


# --- #130: the timezone reaches the rootfs ----------------------------------

def _helper():
    path = os.path.join(ROOT, "assets", "scripts", "prepare_card.py")
    spec = importlib.util.spec_from_file_location("prepare_card_tz", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rootfs(tmp_path, zones=("Australia/Melbourne",)):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "localtime").symlink_to("/usr/share/zoneinfo/Europe/London")
    for z in zones:
        p = tmp_path / "usr/share/zoneinfo" / z
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"TZif2")
    return str(tmp_path)


def test_a_known_timezone_is_written_onto_the_rootfs(tmp_path, capsys):
    pc = _helper()
    mnt = _rootfs(tmp_path)
    pc.write_rootfs(mnt, {"medic": False, "cable_link": False,
                          "timezone": "Australia/Melbourne"})
    assert (tmp_path / "etc" / "timezone").read_text() == "Australia/Melbourne\n"
    assert os.readlink(tmp_path / "etc" / "localtime") == "/usr/share/zoneinfo/Australia/Melbourne"
    assert "set the timezone (Australia/Melbourne)" in capsys.readouterr().out


def test_an_unknown_or_crafted_timezone_leaves_the_image_default(tmp_path, capsys):
    pc = _helper()
    mnt = _rootfs(tmp_path)
    for bad in ("Mars/Olympus", "../../etc/passwd", "/etc/passwd", "Australia/Melbourne;x"):
        assert pc._set_timezone(mnt, bad) is False
    assert not (tmp_path / "etc" / "timezone").exists()
    assert os.readlink(tmp_path / "etc" / "localtime") == "/usr/share/zoneinfo/Europe/London"
    assert capsys.readouterr().out.count("PREPARE_WARN: unknown timezone") == 4


def test_write_rootfs_without_a_timezone_touches_no_clock_files(tmp_path):
    pc = _helper()
    mnt = _rootfs(tmp_path)
    pc.write_rootfs(mnt, {"medic": False, "cable_link": False})
    assert not (tmp_path / "etc" / "timezone").exists()


def _medic_run():
    def run(argv):
        if argv[:3] == ["findmnt", "-no", "SOURCE"]:
            return (0, "/dev/mmcblk0p2\n")
        if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
            return (0, "mmcblk0\n")
        if argv[:2] == ["lsblk", "-dno"]:
            return (0, "mmcblk0 59.5G disk mmc  0 \nsdb 29.7G disk usb  1 Generic SD Reader")
        if argv[:2] == ["openssl", "passwd"]:
            return (0, "$6$abc$deadbeefhash\n")
        return (0, "")
    return run


def test_flash_puts_the_timezone_in_the_card_config(monkeypatch):
    from provisioning import pi_imager as pi
    import provisioning.wifi as wifi
    cfg = {}
    monkeypatch.setattr(pi, "write_card_config",
                        lambda c, path="/tmp/x": (cfg.update(c), path)[1])
    monkeypatch.setattr(wifi, "medic_country", lambda *a, **k: "AU")
    ok, msg = pi.flash("/dev/sdb", "faithpi", "pi", "secret", image_path="/img.xz",
                       run=_medic_run(), run_shell=lambda c: (0, ""),
                       timezone="Pacific/Auckland")
    assert ok, msg
    assert cfg["timezone"] == "Pacific/Auckland"
    ok, msg = pi.flash("/dev/sdb", "faithpi", "pi", "secret", image_path="/img.xz",
                       run=_medic_run(), run_shell=lambda c: (0, ""))
    assert ok and cfg["timezone"] == ""


def test_the_clone_card_carries_the_medics_timezone():
    from workflows import mitosis_card
    calls = {}

    def fake_flash(device, hostname, username, password, **kw):
        calls.update(kw)
        return True, "ok"

    ok, _msg, _pw = mitosis_card.image_medic_card(
        "/dev/sda", "NodeMedic2.0", flash=fake_flash, wifi=("home", "pass"),
        helper_check=lambda: "", timezone="Australia/Hobart")
    assert ok and calls["timezone"] == "Australia/Hobart"
    # the default asks this medic (empty on a box with no timedatectl, never an error)
    ok, _msg, _pw = mitosis_card.image_medic_card(
        "/dev/sda", "NodeMedic2.0", flash=fake_flash, wifi=("home", "pass"),
        helper_check=lambda: "")
    assert ok and isinstance(calls["timezone"], str)


# --- #136: a nameless stranger's row leaves VITALS after seven days ---------

HASH_A = "a1" * 16


def _registry():
    from monitor.registry import NodeRegistry
    return NodeRegistry()


def test_a_nameless_neighbour_expires_after_seven_days_and_comes_back_when_heard():
    r = _registry()
    now = 1_700_000_000.0
    rec = r.ingest_announce(bytes.fromhex(HASH_A), b"\x01\x02", now)
    assert rec is not None and rec.provenance == "neighbour" and not rec.name
    day = 24 * 3600.0
    assert any(x.dst_hash == HASH_A for x in r.all(now + 6 * day))
    assert not any(x.dst_hash == HASH_A for x in r.all(now + 8 * day))
    assert not any(d["identity"] == HASH_A for d in r.devices(now + 8 * day))
    assert sum(r.summary(now + 8 * day).values()) == 0
    assert HASH_A in r.nodes, "expired is hidden, not deleted"
    # heard again: straight back, history and all. (Different bytes — the
    # registry's own replay rule treats identical announce bytes as the
    # transport echoing a cached announce, not the node speaking.)
    r.ingest_announce(bytes.fromhex(HASH_A), b"\x01\x03", now + 9 * day)
    assert any(x.dst_hash == HASH_A for x in r.all(now + 9 * day + 60))


def test_a_named_node_never_expires_on_the_timer():
    r = _registry()
    now = 1_700_000_000.0
    month = 30 * 24 * 3600.0
    r.register(HASH_A, name="ROOFTOP")              # the operator named it
    r.ingest_announce(bytes.fromhex(HASH_A), b"\x01\x02", now)
    assert any(x.dst_hash == HASH_A for x in r.all(now + month))
    # a stranger that announced a NAME stays too — it is somebody's node
    other = "b2" * 16
    rec = r.ingest_announce(bytes.fromhex(other), b"\x01\x02", now)
    rec.announced_name = "Someone's phone"
    assert rec.provenance == "neighbour"
    assert not rec.expired_neighbour(now + month)
    assert any(x.dst_hash == other for x in r.all(now + month))


# --- #57: the Location panel writes the STORED record ------------------------

def test_registry_set_location_moves_the_stored_pin_and_logs_it():
    r = _registry()
    now = 1_700_000_000.0
    r.register(HASH_A, name="ROOFTOP", lat=-37.8, lon=144.9)
    r.nodes[HASH_A].share_applied_at = now - 100
    rec = r.set_location(HASH_A, -37.81, 144.96, now=now)
    assert (rec.lat, rec.lon) == (-37.81, 144.96)
    assert rec.share_applied_at is None, "a new place is a new thing to publish"
    assert any(e.kind == "location" for e in rec.events)
    assert r.set_location("ff" * 16, 0.0, 0.0) is None


def test_the_panel_writes_through_and_mirrors_onto_the_pages_copy():
    panel = src("ui/widgets/map_sharing.py")
    assert "self._live = nodes.get(record.dst_hash)" in panel
    confirmed = func_source("ui/widgets/map_sharing.py", "_location_confirmed",
                            cls="MapSharingPopup")
    assert "self._registry.set_location(rec.dst_hash, lat, lon," in confirmed
    assert "self._mirror()" in confirmed
    choose = func_source("ui/widgets/map_sharing.py", "_choose", cls="MapSharingPopup")
    assert "self._mirror()" in choose
    applied = func_source("ui/widgets/map_sharing.py", "_applied", cls="MapSharingPopup")
    assert "self._mirror()" in applied
    # and the node page really is handed a copy — the reason this matters
    assert "consolidated_record(ident, now) or rec" in src("ui/app.py")
    assert "merged = copy.copy(base)" in src("monitor/registry.py")


# --- #49: the catalogue says which RNode births are proven -------------------

def test_the_catalogue_marks_the_four_proven_rnode_births_and_nothing_else():
    from workflows.rnode_boards import RNODE_BOARDS
    proven = sorted(b.key for b in RNODE_BOARDS.values() if b.proven)
    assert proven == ["heltec32_v4", "heltec_wireless_tracker", "lora32_v21",
                      "xiao_esp32s3"], proven
    assert not any(hasattr(b, "experimental") for b in RNODE_BOARDS.values()), \
        "the dead flag is gone"
    picker = func_source("ui/screens/birth_screen.py", "_choose_board", cls="BirthScreen")
    assert "elif not board.proven:" in picker and 'tr("(untested)")' in picker
    for code, cat in _catalogs():
        assert cat.get("(untested)"), code
    cov = src("docs/BOARD_COVERAGE.md")
    # column 2 = RNode, column 4 = Pi+RNode (the same RNode flash, on a Pi node)
    for name, col in (("Heltec LoRa32 v4 (RGB NeoPixel)", 2), ("Heltec LoRa32 v4 |", 4),
                      ("Heltec Wireless Tracker", 2), ("LilyGO LoRa32 v2.1", 2),
                      ("Seeed XIAO ESP32S3", 2)):
        line = next(ln for ln in cov.splitlines() if ln.startswith("| " + name))
        assert line.split("|")[col].strip() == "✅", line


# --- #150: Japanese can be offered because its font is carried ---------------

def test_the_japanese_font_is_carried_in_the_repo_under_its_licence():
    font = os.path.join(ROOT, "assets", "fonts", "NotoSansJP-Regular.otf")
    lic = os.path.join(ROOT, "assets", "fonts", "LICENSE-NotoSansJP-OFL.txt")
    with open(font, "rb") as f:
        assert f.read(4) == b"OTTO", "an OpenType (CFF) font"
    assert os.path.getsize(font) > 1_000_000
    assert "SIL OPEN FONT LICENSE Version 1.1" in open(lic, encoding="utf-8").read()
    gi = src(".gitignore")
    assert "!assets/fonts/NotoSansJP-Regular.otf" in gi
    assert "!assets/fonts/LICENSE-NotoSansJP-OFL.txt" in gi
    from ui import i18n
    assert i18n.japanese_font_path() == font
    assert any(row[0] == "ja" for row in i18n.available_languages())


# --- #38: the tour names the two mode pictures -------------------------------

def test_the_tour_names_the_cottage_and_the_hiker():
    tour = src("ui/setup_flow.py")
    assert "the cottage " in tour and "is Home, the hiker is Backpack" in tour
    assert "tap the grey one to switch" in tour
