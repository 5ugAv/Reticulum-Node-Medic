"""Batch B of the 2026-10-03 readiness sweep: the T-Beam blocker and the
confirmed majors/minors behind it, pinned at source and in pure code."""
import pytest

from transport.connection import EmulatedConnection
from workflows.rnode_boards import get_board
from workflows.rnode_flash import (RNodeFlashWorkflow, cached_firmware_version,
                                   refused_before_write)


def _src(p): return open(p).read()


# ---- the blocker: a T-Beam must never be erased over an unanswerable band ----

def test_an_ambiguous_board_is_refused_before_any_step_touches_it():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    wf = RNodeFlashWorkflow(conn, get_board("tbeam"), port="/dev/ttyACM0")
    results = wf.run_all()
    assert len(results) == 1 and results[0].name == "detect_port"
    assert not results[0].success and "chip" in results[0].message
    history = " ".join(getattr(conn, "history", []) or [])
    assert "erase_flash" not in history and "--autoinstall" not in history


def test_the_refusal_never_says_rnodeconf_by_hand():
    for key in ("tbeam", "t3s3"):
        why = get_board(key).cannot_flash_reason(915)
        assert why and "rnodeconf" not in why and "terminal" not in why
        assert "chip" in why


def test_a_pre_write_refusal_is_not_treated_as_foreign_firmware():
    assert refused_before_write("Refusing to flash: that is the medic's own radio")
    assert refused_before_write("Node Medic can't flash a LilyGO T-Beam yet: the T-Beam ships…")
    assert not refused_before_write("Serial port opened, but RNode did not respond")
    s = _src("workflows/rnode_flash.py")
    assert "refused_before_write(msg)" in s
    assert s.index("refused_before_write(msg)") < s.index("erased, emsg = self._erase_chip()")


def test_the_pickers_tag_and_the_gates_stop_a_board_this_medic_cannot_flash():
    from ui.birth import board_blocker
    assert board_blocker(get_board("tbeam")) and not board_blocker(get_board("heltec32_v4"))
    assert "(not yet)" in _src("ui/screens/birth_screen.py")
    assert "_blocked_board_popup(board, why)" in _src("ui/screens/birth_screen.py")
    assert "_render_blocked_board(_bd, why)" in _src("ui/screens/birth_guide_screen.py")
    assert "board.cannot_flash_reason(wf.profile.rnode_band_mhz)" in _src("workflows/build.py")


# ---- firmware cache: flash what is cached, not only what is pinned ----

def test_the_offline_cache_lookup_takes_any_real_version_and_no_stray_word():
    class C:
        def __init__(self, answers): self.answers = answers
        def run(self, cmd, timeout=30):
            for k, v in self.answers.items():
                if k in cmd:
                    return v
            return (0, "ok", "")            # the emulated connection's habit
    pinned_missing = {"update/1.86/*.zip": (2, "", ""), ".rnm_bundle_version": (0, "1.90\n", ""),
                      "update/1.90/*.zip": (0, "a.zip", "")}
    assert cached_firmware_version(C(pinned_missing), "1.86") == "1.90"
    nothing = {"update/1.86/*.zip": (2, "", ""), ".rnm_bundle_version": (1, "", ""),
               "update/1.90/*.zip": (2, "", "")}
    assert cached_firmware_version(C(nothing), "1.86") == ""   # "ok" is not a version


def test_two_work_boards_are_refused_not_coin_tossed():
    s = _src("ui/hw_factories.py")
    assert s.count("Two boards plugged in") == 2


# ---- antenna test ----

def test_antenna_test_finish_is_final_and_offers_a_fresh_start():
    s = _src("ui/screens/antenna_test_screen.py")
    assert 'self._stage = "done"' in s and "def _start_again" in s
    assert "tr(problem[1])" in s                    # the backend's reason is shown
    assert "-95 dBm" in s and "HIGHER" in s          # the sign of the numbers is explained


# ---- encryption: the USB-key door is real ----

def test_the_encrypt_switch_reads_the_usb_key():
    s = _src("ui/screens/encryption_screen.py")
    assert '"USB key unlock is not wired to this switch yet."' not in s
    assert "vf.find_keyfile()" in s and "self._parts[vf.KEYFILE] = secret" in s


# ---- maps ----

def test_download_region_unions_bounds_and_every_zoom_has_the_centre_tile():
    from ui.map_download import tiles_in_radius, tile_of
    s = _src("ui/map_download.py")
    i = s.index("def download_region"); j = s.index("\ndef ", i + 10)
    assert "_union_bounds(prev.get(\"bounds\")" in s[i:j]
    lat, lon = -37.8, 144.9
    tiles = tiles_in_radius(lat, lon, 25.0, zmin=8, zmax=10)
    for z in (8, 9, 10):
        assert (z,) + tile_of(lat, lon, z) in tiles


def test_the_map_screen_keeps_a_placed_pin_and_says_when_there_is_no_map():
    s = _src("ui/screens/scan_screen.py")
    assert "the operator put the pin there; the GPS does not take it back" in s
    assert "def _reflect_tiles" in s and s.count("self._reflect_tiles()") >= 3
    assert "No offline map on this medic yet" in s
    assert "No offline map is loaded yet, so there is no " in s and "terrain to show" in s
    assert "def _defer_pick" in s and "self._cancel_pending_pick()" in s
    # the world-fill guard runs BEFORE the world download starts
    i = s.index("def _on_download"); body = s[i:s.index("\n    def ", i + 10)]
    assert body.index("world_fill_service_active()") < body.index("args=(None, None, dest)")
    assert body.count("world_fill_service_active()") == 1


# ---- triage ----

def test_triage_translates_what_the_scorer_says_and_holds_the_lighthouse_text():
    s = _src("ui/screens/triage_screen.py")
    assert "tr(snap['guidance'])" in s and "lbl.text = tr(_label)" in s
    assert "self._pin_guidance(45.0 if state == \"need_power\" else 8.0)" in s
    assert "Antenna test instead (no beacon needed)" in s
    a = _src("ui/app.py")
    assert 'tr("Power on your beacon node ({nm})' in a and 'tr("strong link")' in a


def test_a_stale_packet_no_longer_scores():
    from monitor.triage_feed import live_triage_feed
    import json
    p = "/tmp/nm_triage_feed_stale_test.json"
    with open(p, "w") as f:
        json.dump({"last_rssi": -80, "last_snr": 7.0, "noise_floor": -104,
                   "packet_heard_at": 100.0, "updated": 999.0}, f)
    s = live_triage_feed(p, max_age_s=30, now=lambda: 1000.0)()
    assert s and s.get("partial") is True


# ---- minors ----

def test_language_screen_tells_the_truth_about_its_list_and_how_to_restart():
    s = _src("ui/screens/language_screen.py")
    assert "Portuguese, Italian" not in s and "Latin scripts only" not in s
    assert "Listed here: every language the display font can draw" in s
    assert "Slide to power off on the front page" in s


def test_mode_icons_fire_on_release_with_tap_slop():
    s = _src("ui/widgets/mode_toggle.py")
    i = s.index("class ModeIcon"); body = s[i:s.index("\nclass ModePair")]
    assert "def on_touch_up" in body and "touch.grab(self)" in body and "_tap_slop" in body
    down = body[body.index("def on_touch_down"):body.index("def on_touch_up")]
    assert "self._on_select(self.mode)" not in down


def test_settings_rows_draw_their_subtitle_and_translate_it():
    s = _src("ui/screens/settings_screen.py")
    assert "[size=13sp][color=" in s and "markup=True" in s
    assert 'tr("Run Node Medic in your own language")' in s
    assert 'tr("SD card space and what\'s using it")' in s


def test_storage_does_not_count_the_map_twice_and_the_power_hint_is_placed():
    assert "- storage.path_size(MAPS_DIR)" in _src("ui/screens/storage_screen.py")
    s = _src("ui/widgets/slide_to_power.py")
    i = s.index('if self._style == "neon":', s.index("def _layout"))
    assert "self.hint.pos, self.hint.size = (self.x, self.y), (self.width, self.height)" in s[i:i + 1600]


def test_gps_time_failures_are_told_apart():
    from provisioning.tool_datetime import gps_time_or_reason, sync_from_gps
    assert "installed" in gps_time_or_reason(lambda c: (127, "sh: gpspipe: not found"))[1]
    assert "didn't answer" in gps_time_or_reason(lambda c: (1, "Command timed out"))[1]
    assert "No GPS fix" in gps_time_or_reason(lambda c: (0, '{"class":"VERSION"}'))[1]
    ok, msg = sync_from_gps(run=lambda c: (127, "gpspipe: not found"))
    assert not ok and "gpspipe" in msg
