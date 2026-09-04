import os

import pytest

from workflows.rnode_boards import (
    RNodeBoard,
    RNODE_BOARDS,
    available_boards,
    official_boards,
    custom_boards,
    get_board,
)
from ui.safety import recovery_text


def test_wireless_tracker_registered():
    b = get_board("heltec_wireless_tracker")
    assert isinstance(b, RNodeBoard)
    assert b.display_name == "Heltec Wireless Tracker"
    assert b.board_model == 0x52
    assert "esp32s3" in b.fqbn
    assert "CDCOnBoot=cdc" in b.fqbn


def test_bootloader_instructions_mention_the_real_buttons():
    b = get_board("heltec_wireless_tracker")
    assert "USER" in b.bootloader_instructions
    assert "RST" in b.bootloader_instructions
    # native-USB caveat researched from Heltec docs
    assert "native USB" in b.bootloader_instructions or "USB" in b.bootloader_instructions


def test_recovery_matches_the_shared_safety_module():
    b = get_board("heltec_wireless_tracker")
    assert b.recovery_instructions == recovery_text("Wireless Tracker")


def test_provisioning_codes_match_the_flasher():
    b = get_board("heltec_wireless_tracker")
    assert b.provision["platform"] == "0x80"     # ESP32
    assert b.provision["product"] == "cb"
    assert b.provision["model"] == "ca"
    assert b.provision["hwrev"] == "1"


def test_available_boards_lists_it():
    keys = [b.key for b in available_boards()]
    assert "heltec_wireless_tracker" in keys


def test_get_unknown_board_returns_none():
    assert get_board("does_not_exist") is None


def test_compile_command_uses_fqbn_and_board_model():
    b = get_board("heltec_wireless_tracker")
    cmd = b.compile_command()
    assert b.fqbn in cmd
    assert "-DBOARD_MODEL=0x52" in cmd
    assert "arduino-cli compile" in cmd


def test_upload_command_uses_same_fqbn_and_port():
    b = get_board("heltec_wireless_tracker")
    cmd = b.upload_command("/dev/cu.usbmodem2101")
    assert b.fqbn in cmd                          # same FQBN as compile (the fix)
    assert "/dev/cu.usbmodem2101" in cmd
    assert "arduino-cli upload" in cmd


def test_provision_commands_wipe_then_provision():
    b = get_board("heltec_wireless_tracker")
    cmds = b.provision_commands("/dev/ttyUSB0")
    assert any("--eeprom-wipe" in c for c in cmds)
    prov = next(c for c in cmds if "--product" in c)
    assert "--platform 0x80" in prov and "--model ca" in prov


# ---- full official-board registry ---------------------------------------


def test_registry_lists_more_than_just_the_tracker():
    # the whole point: all official RNode boards, not one custom board
    assert len(available_boards()) >= 14
    names = {b.display_name for b in available_boards()}
    assert {"Heltec LoRa32 v3", "LilyGO T-Beam", "RAK4631",
            "Seeed XIAO ESP32S3 (Wio-SX1262)"} <= names


def test_official_boards_are_autoinstall_with_unique_menu_indices():
    off = official_boards()
    assert len(off) >= 14
    idxs = [b.autoinstall_index for b in off]
    assert len(idxs) == len(set(idxs))              # no collisions
    assert all(3 <= i <= 16 for i in idxs)          # real rnodeconf menu range
    assert all(b.flash_method == "autoinstall" for b in off)


# ---- the picker names the number PRINTED ON THE BOARD --------------------
#
# Operator, 2026-08-06, about to bench-test several boards: "lillygo v1.6.1 / v2
# please check this when I plug it in as lillygo naming is confusing". They are
# right, and it is a flashing hazard, not a cosmetic one: LilyGO silkscreens the
# board T3_V1.6 / labels it MODEL: T3 V1.6.1, while the RNode world calls that
# same board "LoRa32 v2.1" — and 1.6.1 looks far closer to v1.0 than to v2.1.
# Picking v1.0 flashes lora32v10.zip at v2.1 hardware.


def test_the_lora32_v21_row_names_the_silkscreen_number():
    b = get_board("lora32_v21")
    assert "T3 v1.6.1" in b.picker_label, "the number ON the board must be in "\
        "the row the operator taps, not only in a note"
    assert "v2.1" in b.picker_label      # still findable by the name it's sold as


def test_the_three_lora32_entries_stay_distinct_and_only_one_claims_a_t3_number():
    v21, v20, v10 = (get_board(k) for k in
                     ("lora32_v21", "lora32_v20", "lora32_v10"))
    names = [b.display_name for b in (v21, v20, v10)]
    assert len(set(names)) == 3
    assert (v21.autoinstall_index, v20.autoinstall_index,
            v10.autoinstall_index) == (3, 4, 5)
    # Nobody here has read the v2.0 / v1.0 silkscreens, so they claim no T3
    # revision — which is also what makes entry 3 unambiguous.
    assert "T3" not in v20.display_name and "T3" not in v10.display_name


def test_autoinstall_indices_match_rnodeconfs_own_device_menu():
    """Verbatim from rnodeconf 2.5.0's "What kind of device is this?" menu.
    The index IS the answer typed into autoinstall, so a drift here flashes a
    different board's firmware while the screen says the right name."""
    menu = {
        3: "lora32_v21", 4: "lora32_v20", 5: "lora32_v10", 6: "tbeam",
        7: "heltec32_v2", 8: "heltec32_v3", 9: "heltec32_v4", 10: "t3s3",
        11: "rak4631", 12: "techo", 13: "tbeam_supreme", 14: "tdeck",
        15: "heltec_t114", 16: "xiao_esp32s3",
    }
    assert {b.autoinstall_index: b.key for b in official_boards()} == menu


def test_picker_rows_stay_short_enough_for_the_5_inch_panel():
    """800x480, and the picker button wraps rather than shortens — a long row
    spills out of its dp(46) height. The disambiguation must not cost more
    width than rows that already ship (the XIAO and the Tracker are 48/50)."""
    off = official_boards()
    num = max(b.autoinstall_index for b in off) + 1     # customs continue after
    for b in available_boards():
        if b.flash_method == "autoinstall":
            row = f"{b.autoinstall_index:>2}.  {b.picker_label}"
        else:
            row = f"{num:>2}.  {b.picker_label}  (custom)"
            num += 1
        assert len(row) <= 50, f"picker row too wide: {row!r} ({len(row)})"


def test_boards_with_no_transcribed_flash_sequence_still_refuse_to_guess():
    """The catalogue's safety property, pinned next to the naming change that
    sits beside it: an EMPTY band_map means nobody has read that board's
    autoinstall menu, so autoinstall_answers() must raise rather than type a
    band choice into rnodeconf and hope."""
    # rak4631 and techo were on this list until 2026-08-05, and heltec32_v2
    # until 2026-08-14 — each left when its menu was transcribed from the
    # rnodeconf source on the medic. tbeam and t3s3 are different: their menus
    # were READ the same day and turned out to be unanswerable from here
    # (the band choice differs by which radio chip variant the board carries),
    # so they stay refusing — and the refusal must say that WHY, because
    # "not yet verified" reads as a to-do when it is actually a fact.
    for key in ("tbeam", "t3s3"):
        b = get_board(key)
        assert b.autoinstall_bands == {}
        with pytest.raises(ValueError, match="chip"):
            b.autoinstall_answers(915)
    # ...and the transcribed ones must NOT refuse.
    for key in ("rak4631", "techo", "heltec32_v2"):
        assert get_board(key).autoinstall_answers(915)


def test_the_board_picker_renders_the_disambiguated_label():
    """Guards the wiring, not just the data: a picker that goes back to
    display_name silently drops the silkscreen number again.

    This used to assert TWO uses — "popup + guidance list". One of them lived in
    ``show_boards()``, which nothing ever called: a test whose own docstring
    says it guards the wiring was guarding a picker no operator could reach, and
    reporting two working pickers where there was one. show_boards() was deleted
    2026-09-05 (the live picker is _add_rnode_board_pick, reached from
    _build_action) and the count follows it down.
    """
    src = open("ui/screens/birth_screen.py").read()
    assert src.count("board.picker_label") == 1
    assert "{board.display_name}  [{board.platform}]" not in src


def test_official_boards_are_offline_flashable_via_autoinstall():
    b = get_board("heltec32_v3")
    assert b.autoinstall_index == 8
    # V3 is an ESP32-S3 chip (verified live with esptool; the old "ESP32"
    # label kept it out of the detect shortlist — fixed 2026-07-31)
    assert b.platform == "ESP32-S3"
    cmd = b.autoinstall_command("/dev/ttyACM0", version="1.86")
    assert "rnodeconf /dev/ttyACM0 --autoinstall" in cmd
    assert "--nocheck" in cmd                       # offline by default
    assert "--fw-version 1.86" in cmd


def test_every_board_has_bootloader_and_recovery_text():
    for b in available_boards():
        assert b.bootloader_instructions.strip()
        assert b.recovery_instructions.strip()


def test_nrf52_guidance_never_promises_a_usb_drive():
    """No UF2 drive appears on this board, so we must not say one does.

    Verified on a RAK4631 (2026-08-05): in DFU the board enumerates as
    239a:002a exposing ONLY CDC interfaces — class 02 and 0a, both bound to
    cdc_acm — with nothing on usb-storage and no block device. It is serial DFU,
    full stop. The text used to read "a USB drive appears, and the tool flashes
    into it", which left the operator watching for a sign that could never come
    while the real failure sat elsewhere.
    """
    rak = get_board("rak4631")
    assert rak.platform == "nRF52"
    txt = rak.bootloader_instructions.lower()
    assert "usb drive" not in txt or "no usb drive" in txt or \
        "nothing appears as a usb drive" in txt, \
        "nRF52 guidance promises a UF2 drive that does not exist"
    assert "serial" in txt, "say what the bootloader actually speaks"


def test_nrf52_guidance_does_not_demand_a_button_press():
    """rnodeconf flashes nRF52 with `adafruit-nrfutil dfu serial ... -t 1200`;
    the -t 1200 IS the touch, so the tool enters the bootloader by itself. The
    double-tap is a fallback and must not be presented as the required step."""
    txt = get_board("rak4631").bootloader_instructions.lower()
    assert "no button" in txt or "by itself" in txt or "on its own" in txt, \
        "the automatic touch is the normal path — don't send someone hunting RST"


def test_custom_boards_is_everything_we_build_here_not_just_arduino_cli():
    """custom_boards() must mean "not autoinstall", not "arduino_cli".

    When it tested for arduino_cli specifically, adding the serial_dfu
    MeshPocket put it in NEITHER official_boards() nor custom_boards(): it sat
    in the catalogue and appeared in no picker, so the board could not be
    chosen at all. Assert the partition instead of a hard-coded membership, so
    the next flash method cannot vanish the same way."""
    customs = custom_boards()
    assert {b.key for b in customs} == {"heltec_wireless_tracker",
                                        "heltec_meshpocket"}
    assert all(b.flash_method != "autoinstall" for b in customs)
    assert {b.flash_method for b in customs} == {"arduino_cli", "serial_dfu"}


def test_every_board_appears_in_exactly_one_picker_list():
    """The partition itself: official + custom == the whole catalogue. This is
    the invariant that the arduino_cli test above silently violated."""
    from workflows.rnode_boards import available_boards, official_boards
    o = {b.key for b in official_boards()}
    c = {b.key for b in custom_boards()}
    assert not (o & c), f"a board is in both lists: {o & c}"
    assert o | c == {b.key for b in available_boards()}, "a board is in neither"


def test_meshpocket_provisions_exactly_as_the_verified_birth_did():
    """--platform is OMITTED, because the birth proven on hardware omitted it.
    Provisioning a byte that run never wrote would store something untested."""
    mp = RNODE_BOARDS["heltec_meshpocket"]
    cmds = mp.provision_commands("/dev/ttyACM0")
    assert cmds[0] == "rnodeconf /dev/ttyACM0 --eeprom-wipe"
    assert cmds[1] == ("rnodeconf /dev/ttyACM0 -r --product d2 --model ce "
                       "--hwrev 1")
    assert "--platform" not in cmds[1]
    assert mp.board_model == 0x46          # BOARD_HELTEC_MESHP in Boards.h


def test_carried_flasher_script_exists_and_is_hardened():
    b = get_board("heltec_wireless_tracker")
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        b.carried_script)
    body = open(path).read()
    # the multi-board / eeprom-wipe guard and single FQBN fixes
    assert "More than one USB board" in body
    assert "count_boards" in body
    assert 'FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc"' in body
    assert "mapfile -t" not in body               # bash 3.2 safe (no mapfile cmd)


def test_serial_dfu_boards_carry_what_the_flash_button_requires():
    """The birth screen only offers a Flash button when the board is "ready",
    and what ready MEANS differs by method. A serial_dfu board has no band menu
    to have transcribed, so a readiness test written as `autoinstall_bands` is
    permanently false for it — the board gets listed, described, and cannot be
    flashed. Assert the fields that condition actually depends on."""
    for b in RNODE_BOARDS.values():
        if b.flash_method != "serial_dfu":
            continue
        assert b.build_dir, f"{b.key}: no build_dir, so nothing can be flashed"
        assert b.dfu_package.endswith(".zip"), (
            f"{b.key}: serial DFU needs a signed .zip, not a bare binary")
        assert b.provision, f"{b.key}: no provision codes"
