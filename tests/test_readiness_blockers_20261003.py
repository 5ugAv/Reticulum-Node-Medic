"""The three blockers the 2026-10-03 readiness sweep found, pinned."""
import os
import re


# -- 1. the Pi walkthrough crashed on its first self-advancing step ------------

def test_wizard_step_hides_and_shows_next_without_a_back_button():
    src = open("ui/widgets/wizard_step.py").read()
    hide = src[src.index("def hide_next"):src.index("def _start_heartbeat")]
    show = src[src.index("def show_next"):src.index("def set_next_enabled")]
    assert "if self.back_btn is not None:" in hide
    assert "if self.back_btn is not None:" in show
    # the guided birth really does build steps without a Back button
    assert "show_back=False, on_back=self._back" in open("ui/screens/birth_guide_screen.py").read()


# -- 2. ANTENNA read a fake signal on the medic ---------------------------------

def test_antenna_feed_is_live_on_the_medic_whatever_the_state_file_says():
    from monitor.triage_feed import feed_choice
    for state in (True, False):
        assert feed_choice("", demo_ok=False, state_present=state) == "live"
        assert feed_choice("demo", demo_ok=False, state_present=state) == "live"   # not even by request
        assert feed_choice("live", demo_ok=False, state_present=state) == "live"
    # a dev box keeps the demo when nothing is feeding, and obeys the override
    assert feed_choice("", demo_ok=True, state_present=False) == "demo"
    assert feed_choice("", demo_ok=True, state_present=True) == "live"
    assert feed_choice("demo", demo_ok=True, state_present=True) == "demo"
    assert feed_choice("live", demo_ok=True, state_present=False) == "live"


def test_app_chooses_the_antenna_feed_through_feed_choice_and_demo_allowed():
    src = open("ui/app.py").read()
    body = src[src.index("def _triage_feed"):src.index("def _demo_triage_feed")]
    assert "feed_choice(mode, demo_allowed()," in body
    assert "read_splitter_state() is not None) == \"demo\"" in body


# -- 3. the EoRa-S3 would have been flashed the Tracker's image -------------------

def test_each_custom_board_flashes_its_own_build_at_its_own_size():
    from workflows.rnode_boards import get_board
    from workflows.rnode_flash import (TRACKER_BUILD_DIR, fork_build_dir_for,
                                       fork_flash_size_for)
    eora = get_board("eora_s3"); tracker = get_board("heltec_wireless_tracker")
    assert fork_build_dir_for(eora) == eora.build_dir and eora.build_dir
    assert fork_build_dir_for(eora) != TRACKER_BUILD_DIR
    assert fork_flash_size_for(eora) == "4MB"
    assert fork_build_dir_for(tracker) == TRACKER_BUILD_DIR
    assert fork_flash_size_for(tracker) == "8MB"
    # and each board's own SKETCH name: the CE tree builds RNode_Firmware_CE.ino.*
    from workflows.rnode_flash import fork_image_for
    assert fork_image_for(eora, "bin").endswith("/RNode_Firmware_CE.ino.bin")
    assert fork_image_for(eora, "bootloader.bin").startswith(eora.build_dir)
    assert fork_image_for(tracker, "bin") == TRACKER_BUILD_DIR + "/RNode_Firmware.ino.bin"


def test_the_fork_flasher_never_hardcodes_the_trackers_directory_or_size():
    src = open("workflows/rnode_flash.py").read()
    flash = src[src.index("def _flash_custom_fork"):]
    assert "d = TRACKER_BUILD_DIR" not in flash
    assert "--flash_size 8MB" not in flash
    ensure = src[src.index("def _ensure_firmware"):src.index("def _flash_custom_fork")]
    assert "fork_image_for(self.board" in ensure
    assert "Tracker fork build is missing" not in ensure
    assert "RNode_Firmware.ino" not in flash and "RNode_Firmware.ino" not in ensure


# -- 4. Self Diagnose could never say the GPS was healthy ---------------------

def test_gps_check_reads_without_a_pipe_or_chain():
    src = open("monitor/self_diagnose_runtime.py").read()
    body = src[src.index("def gather"):]
    gps = body[body.index("gps_text"):body.index("check_gps_fresh(gps_text")]
    assert "||" not in gps and "|" not in gps.replace("||", "")


def test_gps_fresh_from_the_primary_state_file(monkeypatch):
    import monitor.self_diagnose_runtime as rt
    from monitor.self_diagnose import SEV_OK
    now = 1_704_070_000.0
    seen = []
    def run(cmd):
        seen.append(cmd)
        if "nodemedic-gps.json" in cmd:
            return '{"updated": %s, "has_fix": true, "sats": 7, "gps_frames": 120}' % (now - 5)
        return ""
    monkeypatch.setattr(rt, "onboard_radio_serial", lambda *a, **k: "X")
    findings = rt.gather(run=run, now_fn=lambda: now)
    gps = [f for f in findings if f.check == "gps"][0]
    # before the fix: "gps_state.json unreadable." — the refusal text parsed as the file
    assert gps.detail != "gps_state.json unreadable.", gps.detail
    assert "stale" not in gps.detail.lower()
    assert not any("gps_state.json" in c for c in seen)      # primary answered; no fallback


# -- 7. deleting one unnamed node buried every unnamed node ---------------------

def test_forgetting_an_unnamed_neighbour_touches_only_that_machine():
    from monitor.registry import NodeRegistry
    r = NodeRegistry()
    a, b, c = "aa" * 16, "bb" * 16, "cc" * 16
    for h in (a, b, c):
        r.ingest_announce(bytes.fromhex(h), b"", 1.0)
    r.nodes[a].identity_hash = "ia" * 16; r.nodes[b].identity_hash = "ib" * 16
    r.nodes[c].identity_hash = "ia" * 16                      # c is the same machine as a
    assert r.machine_hashes(a) == {a, c}
    assert r.machine_hashes("") == set()
    assert r.forget_node(a) == 2 and b in r.nodes and a not in r.nodes
    assert "reg.machine_hashes(name or rec.dst_hash)" in open("ui/app.py").read()


# -- 5 + 6. a clone that can open a window, image a card and reach a node ------

def test_clone_ladder_installs_the_card_helper_and_an_ssh_key_before_verifying():
    from workflows.clone import _CLONE_STEPS
    names = [n for n, _ in _CLONE_STEPS]
    assert names.index("install_card_helper") < names.index("final_verification")
    assert names.index("ensure_ssh_keypair") < names.index("final_verification")
    assert names.index("configure_autostart") < names.index("install_card_helper")


def test_display_debs_come_from_the_one_cache():
    from workflows.wheelhouse import ALL_PACKAGES, DISPLAY_PACKAGES, apt_download_command
    assert "cage" in DISPLAY_PACKAGES and "cage" in ALL_PACKAGES and "direwolf" in ALL_PACKAGES
    assert "cage" in apt_download_command()
    src = open("workflows/clone.py").read()
    body = src[src.index("def install_display_stack"):src.index("def configure_autostart")] if src.index("def install_display_stack") < src.index("def configure_autostart") else src[src.index("def install_display_stack"):]
    assert "DEB_CACHE" in body and "refresh_deb_cache.py" in body
    assert os.path.exists("scripts/refresh_deb_cache.py")
