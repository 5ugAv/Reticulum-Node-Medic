"""A certificate is the medic's word — it must never print a default as a fact.

2026-09-22, live: the operator built the propagation node "skyfinger" — a
Pi Zero 2 W with a RAK4631 — on the guide's "I already have a working radio"
road. The certificate the medic wrote said board "Heltec LoRa32 v4" and
serial_port "/dev/ttyUSB0". Both were NodeProfile DEFAULTS: the road never
asked which radio, so rnode_board_key stayed at its default and the cert
printed the default's display name as if the medic had seen the board. Its
VITALS page then read "Board: Heltec LoRa32 v4" under "Built by this medic".

Pinned here:
  1. the "already have one" road ASKS which radio (picker, photos + names)
     and carries the answer into the Pi hand-off — and NOTHING else: the
     radio was never on this medic, so no serial is guessed for it (the
     first cut looked one up in the flash ledger and printed "read by this
     medic when it flashed 'rak4'" about hardware it had never seen — two
     review lenses, 2026-09-22);
  2. the certificate prints an honest board, HOW the board was known, and
     the udev truth for the port — never a default; provenance crosses the
     hand-off as codes, and only the screen turns them into sentences;
  3. every Back on the new road lands on the screen forward came from.
"""
import textwrap

from node_profile import NodeProfile
from tests.srcutil import func_source
from tests.test_build_workflow import build_conn, wf, _run_step
from workflows import build

GUIDE = "ui/screens/birth_guide_screen.py"
BIRTH = "ui/screens/birth_screen.py"
DETAIL = "ui/screens/node_detail_screen.py"


def _code(body):
    """Source with comment lines stripped, so a pin reads code not prose,
    and wrapped string literals joined, so a pinned sentence reads whole."""
    import re
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    return re.sub(r'"\s*\n\s*"', "", code)


# ---------------------------------------------------------------------------
# 2. the certificate text
# ---------------------------------------------------------------------------

def test_an_unknown_board_is_written_as_unknown_not_as_the_default():
    assert build.board_text("") == build.UNKNOWN_BOARD_TEXT
    assert build.board_text("no-such-board") == build.UNKNOWN_BOARD_TEXT
    assert "unknown" in build.UNKNOWN_BOARD_TEXT
    assert "Heltec" not in build.UNKNOWN_BOARD_TEXT


def test_a_known_board_is_written_by_its_catalogue_name():
    assert build.board_text("rak4631") == "RAK4631"
    assert build.board_text("heltec32_v4") == "Heltec LoRa32 v4"


def test_the_port_text_says_how_udev_will_name_the_radio():
    by_serial = build.radio_port_text(
        {"by": "serial", "serial": "4631000000000001",
         "source": "read by the medic when it flashed the board"})
    assert by_serial == ("/dev/rnode (udev, by serial 4631000000000001 — "
                         "read by the medic when it flashed the board)")
    by_vendor = build.radio_port_text({"by": "vendor", "serial": "",
                                       "source": ""})
    assert by_vendor.startswith("/dev/rnode (udev, by vendor — ")
    assert "5 makers" in by_vendor and "no serial known" in by_vendor
    # No record of the rule at all: say so, never fall back to a port name.
    assert build.radio_port_text(None) == \
        "/dev/rnode (udev rule not recorded by this build)"
    for text in (by_serial, by_vendor, build.radio_port_text(None)):
        assert "ttyUSB" not in text and "ttyACM" not in text


def _pi_build(board_key, usb_serial="", source="", board_source=""):
    conn = build_conn(rnode=False)
    conn.rules.insert(0, ("^hostname", 0, "skyfinger", ""))
    conn.rules.insert(0, ("^hostname -I", 0, "192.168.1.77", ""))
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "", ""))
    conn.rules.insert(0, ("udevadm info -q property", 1, "", ""))
    p = NodeProfile()
    p.rnode_board_key = board_key
    p.rnode_board_source = board_source
    p.radio.usb_serial = usb_serial
    p.radio.usb_serial_source = source
    w = wf(conn, p)
    w.steps[0][1](w)                       # detect: no radio on the node
    return w


def _readback(w, serial, source=""):
    w.connection.rules.insert(0, (
        "cat /etc/udev/rules.d/60-rnode.rules", 0,
        build.rnode_udev_rules(serial, source), ""))


def test_a_skyfinger_like_build_gets_an_honest_certificate():
    """Board named by the operator (RAK4631), radio not attached, no serial
    known: the rule is the vendor net and the certificate says so — and says
    the board was NAMED, not read."""
    w = _pi_build("rak4631", board_source="operator")
    _readback(w, "")
    assert _run_step(w, "install_radio_rule").success
    assert _run_step(w, "birth_certificate").success
    cert = w.birth_certificate
    assert cert["board"] == "RAK4631"
    assert cert["board_key"] == "rak4631"
    assert cert["board_source"] == "operator"
    assert cert["serial_port"].startswith("/dev/rnode (udev, by vendor")
    assert cert["radio_rule"] == {"by": "vendor", "serial": "", "source": ""}
    assert "usb_serial" not in cert
    assert "ttyUSB0" not in str(cert)


def test_a_serial_the_flash_read_is_pinned_and_its_provenance_is_a_code():
    w = _pi_build("heltec32_v4", "4631000000000001", "flash", board_source="usb")
    _readback(w, "4631000000000001", "flash")
    assert _run_step(w, "install_radio_rule").success
    assert _run_step(w, "birth_certificate").success
    cert = w.birth_certificate
    assert cert["radio_rule"] == {"by": "serial", "serial": "4631000000000001",
                                  "source": "flash"}
    assert cert["serial_port"] == (
        "/dev/rnode (udev, by serial 4631000000000001 — read by the medic "
        "when it flashed the board)")
    assert cert["board_source"] == "usb"


def test_a_serial_with_no_provenance_code_is_a_flash_reading_by_default():
    """The flash hand-back predates the code; its serial always meant this."""
    w = _pi_build("heltec32_v4", "ABCDEF")
    _readback(w, "ABCDEF", "flash")
    assert _run_step(w, "install_radio_rule").success
    assert w.radio_rule["source"] == "flash"


def test_a_serial_read_off_the_node_itself_wins_and_says_so():
    w = _pi_build("heltec32_v4", "ABCDEF", "flash")
    w.connection.rules.insert(0, ("udevadm info -q property", 0,
                                  "ID_SERIAL_SHORT=NODE111\n", ""))
    _readback(w, "NODE111", "node")
    assert _run_step(w, "install_radio_rule").success
    assert w.radio_rule == {"by": "serial", "serial": "NODE111", "source": "node"}


def test_a_serial_that_is_not_a_serial_never_reaches_the_rule():
    """`ATTRS{serial}=="*"` is a udev glob: EVERY tty would take /dev/rnode
    while the step, the certificate and the page all said "this radio only".
    A carried value that is not a serial is treated as none, and said."""
    for bad in ("*", "AB\nSUBSYSTEM", "a b", '"', ""):
        assert build.rnode_udev_rules(bad).count("idVendor") >= 5, repr(bad)
        assert "ATTRS{serial}" not in build.rnode_udev_rules(bad)
    w = _pi_build("rak4631", "*", "flash")
    _readback(w, "")
    r = _run_step(w, "install_radio_rule")
    assert r.success
    assert w.radio_rule["by"] == "vendor"
    assert "not a usable serial" in r.message


def test_the_udev_file_says_where_the_serial_came_from():
    text = build.rnode_udev_rules("ABCDEF", "flash")
    assert "read by the medic when it flashed the board" in text
    assert "read from the board at birth" not in text, \
        "the old comment claimed a reading the build did not always make"
    assert 'ATTRS{serial}=="ABCDEF"' in text


def test_the_port_text_turns_codes_into_sentences():
    assert build.SERIAL_SOURCES["node"] in build.radio_port_text(
        {"by": "serial", "serial": "X", "source": "node"})
    assert build.radio_port_text({"by": "serial", "serial": "X", "source": ""}) \
        == "/dev/rnode (udev, by serial X)"
    assert build.BOARD_SOURCES["operator"].startswith("named by the operator")


def test_an_unpicked_board_and_a_vendor_rule_are_written_as_such():
    w = _pi_build("")
    _readback(w, "")
    assert _run_step(w, "install_radio_rule").success
    assert _run_step(w, "birth_certificate").success
    cert = w.birth_certificate
    assert cert["board"] == build.UNKNOWN_BOARD_TEXT
    assert cert["board_key"] is None and cert["board_source"] is None
    assert cert["serial_port"].startswith("/dev/rnode (udev, by vendor")
    assert "ttyUSB0" not in str(cert)


def test_an_attached_blank_board_with_no_name_is_refused_in_words():
    conn = build_conn(rnode=True)
    p = NodeProfile()
    p.rnode_board_key = ""
    w = wf(conn, p)
    w.steps[0][1](w)
    w.profile.rnode_present, w.profile.has_rnode = True, False   # blank board
    r = _run_step(w, "flash_rnode_firmware")
    assert not r.success
    assert "''" not in r.message and "no board was named" in r.message


def test_the_certificate_never_prints_the_profile_default_port():
    body = _code(func_source("workflows/build.py", "birth_certificate"))
    assert "r.serial_port" not in body
    assert "wf.profile.hardware.value" not in body
    assert "radio_port_text(" in body and "board_text(" in body
    assert "rnode_board_source" in body


def test_the_rule_step_records_what_it_actually_wrote():
    w = _pi_build("rak4631")
    _readback(w, "")
    assert w.radio_rule is None
    assert _run_step(w, "install_radio_rule").success
    assert w.radio_rule == {"by": "vendor", "serial": "", "source": ""}


def test_a_failed_rule_write_records_nothing():
    w = _pi_build("rak4631", "ABCDEF")
    w.connection.rules.insert(0, (
        "cat /etc/udev/rules.d/60-rnode.rules", 0, "something else", ""))
    assert not _run_step(w, "install_radio_rule").success
    assert w.radio_rule is None
    assert build.radio_port_text(w.radio_rule).endswith("not recorded by this build)")


def test_the_vendor_net_covers_every_board_family_the_picker_offers():
    from workflows.rnode_boards import RNODE_BOARDS
    vids = {vid for vid, _ in build._RNODE_USB_VENDORS}
    assert {"239a", "303a", "10c4", "1a86"} <= vids
    assert {b.platform for b in RNODE_BOARDS.values()} <= {"ESP32", "ESP32-S3",
                                                          "nRF52"}


# ---------------------------------------------------------------------------
# the Pi build must not carry a default board key into the build
# ---------------------------------------------------------------------------

def test_the_pi_workflow_gets_an_empty_board_key_when_none_was_picked():
    body = _code(func_source(BIRTH, "_make_workflow", cls="BirthScreen"))
    assert 'prof.rnode_board_key = board.key if board else ""' in body
    assert "prof.radio.usb_serial_source" in body
    assert "prof.rnode_board_source" in body
    # how the board was known: the have-one road names it, the flash road
    # read it off the medic's USB — and no board is no source
    assert '"operator"' in body and '"usb"' in body


def test_the_pi_certificate_never_takes_the_fingerprint_of_a_bystander_board():
    body = _code(func_source(BIRTH, "_commit_cert", cls="BirthScreen"))
    gate = body.split('not cert.get("usb_serial")', 1)[1][:120]
    assert '!= "pi_rnode"' in gate


def test_the_birth_screen_still_says_the_radio_is_not_flashed_here():
    """With a board named on the have-one road the row used to fall into the
    flash road's "✓ Confirmed earlier" branch and lose the one instruction
    this road needs: plug the radio into the Pi when the build finishes."""
    body = _code(func_source(BIRTH, "_build_chooser", cls="BirthScreen"))
    i = body.index('not getattr(self, "_flash_radio", True)')
    branch = body[i:i + 1600]
    assert "Not flashed here" in branch
    assert "_sel_board" in branch, "the named board is shown on this road too"
    assert branch.index("Not flashed here") < branch.index("_declared_board_key")


# ---------------------------------------------------------------------------
# 1. the "already have one" road asks which radio — and guesses nothing
# ---------------------------------------------------------------------------

def test_the_have_one_road_goes_through_the_board_picker():
    body = _code(func_source(GUIDE, "_already_have_one", cls="BirthGuideScreen"))
    assert "self._pi_flash_radio = False" in body
    assert "_render_pick_board(absent=True)" in body
    choice = _code(func_source(GUIDE, "_render_pi_radio_choice",
                               cls="BirthGuideScreen"))
    assert "self._already_have_one" in choice


def test_the_absent_picker_reads_the_catalogue_not_the_medics_usb():
    body = _code(func_source(GUIDE, "_render_pick_board", cls="BirthGuideScreen"))
    absent = body.split("if absent:", 1)[1]
    assert "rnode_board_choices" in absent.split("else:", 1)[0]
    assert "_render_pi_radio_choice" in body, "Back returns to the radio question"
    assert "BoardCard(key, name=name" in body


def test_picking_on_the_have_one_road_guesses_no_serial():
    body = _code(func_source(GUIDE, "_board_picked", cls="BirthGuideScreen"))
    assert "_pi_flash_radio" in body and "_render_name()" in body
    assert "_remember_board" in body
    assert "radio_serial_for_board" not in body and "cert_store" not in body, \
        "the radio was never on this medic; the ledger is history, not a reading"
    assert '"operator"' in body and '"usb"' in body
    import ui.cert_store as cs
    assert not hasattr(cs, "radio_serial_for_board")


def test_the_hand_off_carries_where_the_serial_came_from_as_a_code():
    body = _code(func_source(GUIDE, "_hand_over_name", cls="BirthGuideScreen"))
    assert "radio_usb_serial_source=" in body and "board_source=" in body
    bg = _code(func_source(BIRTH, "begin_guided", cls="BirthScreen"))
    assert "radio_usb_serial_source" in bg and "board_source" in bg
    fresh = _code(func_source(BIRTH, "_fresh_lap", cls="BirthScreen"))
    assert "_guided_radio_usb_serial_source" in fresh
    assert "_guided_board_source" in fresh
    keep = _code(func_source(GUIDE, "_keep_and_continue", cls="BirthGuideScreen"))
    assert '"medic_usb"' in keep
    src = open("ui/screens/birth_screen.py", encoding="utf-8").read()
    i = src.index('payload["radio_usb_serial"] = serial')
    assert 'payload["radio_usb_serial_source"] = "flash"' in _code(src[i:i + 400]), \
        "the serial and its provenance must travel together, or resume() " \
        "overwrites one and keeps the other lap's"


def test_the_confirm_screen_shows_a_picked_radio_on_the_have_one_road():
    body = _code(func_source(GUIDE, "_render_confirm_pair", cls="BirthGuideScreen"))
    assert "_board_display_name(" in body
    assert "if flashing:" not in body


# ---------------------------------------------------------------------------
# 3. every Back lands where forward came from
# ---------------------------------------------------------------------------

def test_back_from_the_name_on_the_have_one_road_is_the_picker():
    body = _code(func_source(GUIDE, "_render_name", cls="BirthGuideScreen"))
    assert "_render_pick_board(absent=True)" in body
    assert "_render_intro" in body


def test_back_from_the_power_verdict_keeps_the_road():
    """Newly reachable on the have-one road (the Pi feeds the named radio).
    Back went to the USB-reading picker, which with a bystander board on the
    bench replaces the operator's answer with no screen shown."""
    body = _code(func_source(GUIDE, "_render_power_verdict", cls="BirthGuideScreen"))
    assert "_render_pick_board(absent=" in body
    assert "_board_candidates()" not in body, \
        "the board's name comes from the catalogue, never a USB re-read"
    assert "_board_display_name(" in body
    change = _code(func_source(GUIDE, "_change_hardware", cls="BirthGuideScreen"))
    assert "absent=" in change


def test_back_from_the_pair_confirmation_shows_the_pi_picker_again():
    """The Pi is asked before the steps (2026-09-22), so _render_pick_pi
    short-circuits to the confirmation when the Pi is known — and Back from
    that confirmation went to _render_pick_pi: a tap that did nothing."""
    body = _code(func_source(GUIDE, "_render_confirm_pair", cls="BirthGuideScreen"))
    assert "_render_pick_pi(force=True)" in body
    pick = _code(func_source(GUIDE, "_render_pick_pi", cls="BirthGuideScreen"))
    assert "def _render_pick_pi(self, force=False)" in pick
    assert "not force" in pick


def test_choosing_a_road_forgets_the_last_laps_serial_and_provenance():
    body = _code(func_source(GUIDE, "_choose", cls="BirthGuideScreen"))
    assert '_radio_usb_serial = ""' in body
    assert '_radio_usb_serial_source = ""' in body
    assert '_board_source = ""' in body
    flash = func_source(GUIDE, "_render_pi_radio_choice", cls="BirthGuideScreen")
    fh = flash[flash.index("def flash_here"):]
    assert '_radio_usb_serial_source = ""' in fh[:400]


# ---------------------------------------------------------------------------
# what VITALS says, in the operator's language, from the codes
# ---------------------------------------------------------------------------

def test_the_vitals_page_composes_its_lines_from_the_codes_not_the_prose():
    body = _code(func_source(DETAIL, "_birth_lines", cls="NodeDetailScreen"))
    assert 'tr("Radio port: {port}")' not in body, \
        "English certificate prose inside a translated frame"
    assert '"radio_rule"' in body and '"board_source"' in body
    for key in ("Radio found by its serial number {serial} ({source})",
                "Radio found by its USB maker — no serial number was read",
                "read from the radio on the node",
                "read by Node Medic when it flashed the radio",
                "read by Node Medic from the radio on its own USB",
                "Board named by you from the catalogue — the radio was never on Node Medic",
                "Board: not named at birth — the radio was never on Node Medic"):
        assert key in body, key
    assert "UNKNOWN_BOARD_TEXT" not in body
    assert 'cert.get("serial_port")' not in body, "the sentence is composed, not copied"
