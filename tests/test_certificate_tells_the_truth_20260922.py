"""A certificate is the medic's word — it must never print a default as a fact.

2026-09-22, live: the operator built the propagation node "skyfinger" — a
Pi Zero 2 W with a RAK4631 — on the guide's "I already have a working radio"
road. The certificate the medic wrote said board "Heltec LoRa32 v4" and
serial_port "/dev/ttyUSB0". Both were NodeProfile DEFAULTS: the road never
asked which radio, so rnode_board_key stayed at its default and the cert
printed the default's display name as if the medic had seen the board. Its
VITALS page then read "Board: Heltec LoRa32 v4" under "Built by this medic".

Two things are pinned here:
  1. the "already have one" road now ASKS which radio (picker, photos + names)
     and carries the answer into the Pi hand-off;
  2. the certificate prints an honest board and the udev truth for the port —
     "/dev/rnode (udev, by serial …)" or "(udev, by vendor …)" — never a
     default.
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
    """Source with comment lines stripped, so a pin reads code not prose."""
    return "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))


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


def _pi_build(board_key, usb_serial="", source=""):
    conn = build_conn(rnode=False)
    conn.rules.insert(0, ("^hostname", 0, "skyfinger", ""))
    conn.rules.insert(0, ("^hostname -I", 0, "192.168.1.77", ""))
    conn.rules.insert(0, ("RNS.Identity.from_file", 0, "", ""))
    conn.rules.insert(0, ("udevadm info -q property", 1, "", ""))
    p = NodeProfile()
    p.rnode_board_key = board_key
    p.radio.usb_serial = usb_serial
    p.radio.usb_serial_source = source
    w = wf(conn, p)
    w.steps[0][1](w)                       # detect: no radio on the node
    return w


def test_a_skyfinger_like_build_gets_an_honest_certificate():
    """Board picked (RAK4631), radio not attached, serial known from this
    medic's own flash certificate of that board."""
    w = _pi_build("rak4631", "4631000000000001",
                  "read by this medic when it flashed 'rak4' on 2026-08-07")
    w.connection.rules.insert(0, (
        "cat /etc/udev/rules.d/60-rnode.rules", 0,
        build.rnode_udev_rules("4631000000000001"), ""))
    assert _run_step(w, "install_radio_rule").success
    assert _run_step(w, "birth_certificate").success
    cert = w.birth_certificate
    assert cert["board"] == "RAK4631"
    assert cert["board_key"] == "rak4631"
    assert cert["serial_port"] == (
        "/dev/rnode (udev, by serial 4631000000000001 — read by this medic "
        "when it flashed 'rak4' on 2026-08-07)")
    assert cert["radio_rule"] == {
        "by": "serial", "serial": "4631000000000001",
        "source": "read by this medic when it flashed 'rak4' on 2026-08-07"}
    # The Pi's certificate must NOT carry the radio's serial under the key
    # save_cert retires other certificates by — that would delete the RAK's
    # own flash certificate the moment the Pi's is saved.
    assert "usb_serial" not in cert


def test_an_unpicked_board_and_a_vendor_rule_are_written_as_such():
    w = _pi_build("")
    w.connection.rules.insert(0, (
        "cat /etc/udev/rules.d/60-rnode.rules", 0,
        build.rnode_udev_rules(""), ""))
    assert _run_step(w, "install_radio_rule").success
    assert _run_step(w, "birth_certificate").success
    cert = w.birth_certificate
    assert cert["board"] == build.UNKNOWN_BOARD_TEXT
    assert cert["board_key"] is None
    assert cert["serial_port"].startswith("/dev/rnode (udev, by vendor")
    assert cert["radio_rule"]["by"] == "vendor"
    assert "ttyUSB0" not in str(cert)


def test_the_certificate_never_prints_the_profile_default_port():
    """The old line was `"serial_port": r.serial_port` — the NodeProfile
    default, /dev/ttyUSB0, on every Pi ever birthed."""
    body = _code(func_source("workflows/build.py", "birth_certificate"))
    assert "r.serial_port" not in body
    assert "wf.profile.hardware.value" not in body, \
        "the profile's default hardware must never stand in for the board"
    assert "radio_port_text(" in body and "board_text(" in body


def test_the_rule_step_records_what_it_actually_wrote():
    w = _pi_build("rak4631")
    w.connection.rules.insert(0, (
        "cat /etc/udev/rules.d/60-rnode.rules", 0,
        build.rnode_udev_rules(""), ""))
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
    """Every board in the catalogue is ESP32 (CP210x/CH340 bridge or native
    303a) or nRF52 (239a). The RAK4631 is 239a:8029 running — read live
    2026-08-06 (ui/board_detect.py)."""
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
    assert 'prof.rnode_board_key = board.key if board else ""' in body, (
        "with no board picked the profile default (heltec32_v4) survived into "
        "the build and onto the certificate (skyfinger, 2026-09-22)")
    assert "prof.radio.usb_serial_source" in body


def test_the_pi_certificate_never_takes_the_fingerprint_of_a_bystander_board():
    """_commit_cert stamped usb_serial from whatever board sat on the medic's
    USB. For a Pi certificate that is a lie AND data loss: save_cert retires
    every other certificate with that serial — the radio's own."""
    body = _code(func_source(BIRTH, "_commit_cert", cls="BirthScreen"))
    gate = body.split('not cert.get("usb_serial")', 1)[1][:120]
    assert '!= "pi_rnode"' in gate


# ---------------------------------------------------------------------------
# 1. the "already have one" road asks which radio
# ---------------------------------------------------------------------------

def test_the_have_one_road_goes_through_the_board_picker():
    body = _code(func_source(GUIDE, "_already_have_one", cls="BirthGuideScreen"))
    assert "self._pi_flash_radio = False" in body
    assert "_render_pick_board(absent=True)" in body
    choice = _code(func_source(GUIDE, "_render_pi_radio_choice",
                               cls="BirthGuideScreen"))
    assert "self._already_have_one" in choice, \
        "the choice card must call the method the harness drives"


def test_the_absent_picker_reads_the_catalogue_not_the_medics_usb():
    body = _code(func_source(GUIDE, "_render_pick_board", cls="BirthGuideScreen"))
    absent = body.split("if absent:", 1)[1]
    assert "rnode_board_choices" in absent.split("else:", 1)[0], \
        "the radio is not on this bench — detection has nothing to narrow"
    assert "_render_pi_radio_choice" in body, "Back returns to the radio question"
    assert "BoardCard(key, name=name" in body, \
        "every photo card carries its name (2026-09-21 rule)"


def test_picking_on_the_have_one_road_continues_to_the_name():
    body = _code(func_source(GUIDE, "_board_picked", cls="BirthGuideScreen"))
    assert "_pi_flash_radio" in body and "_render_name()" in body
    assert "_remember_board" in body
    # and the serial the medic may already hold for that board is looked up
    assert "radio_serial_for_board" in body


def test_the_hand_off_carries_where_the_serial_came_from():
    body = _code(func_source(GUIDE, "_hand_over_name", cls="BirthGuideScreen"))
    assert "radio_usb_serial_source=" in body
    bg = _code(func_source(BIRTH, "begin_guided", cls="BirthScreen"))
    assert "radio_usb_serial_source" in bg
    fresh = _code(func_source(BIRTH, "_fresh_lap", cls="BirthScreen"))
    assert "_guided_radio_usb_serial_source" in fresh, \
        "one node's serial provenance must not leak into the next lap"


def test_the_confirm_screen_shows_a_picked_radio_on_the_have_one_road():
    body = _code(func_source(GUIDE, "_render_confirm_pair", cls="BirthGuideScreen"))
    assert "_board_display_name(" in body
    assert "if flashing:" not in body, \
        "the radio row is gated on a board being KNOWN, not on flashing"


# ---------------------------------------------------------------------------
# the serial this medic already holds for a board it flashed earlier
# ---------------------------------------------------------------------------

def test_one_flash_certificate_for_that_board_yields_its_serial():
    from ui.cert_store import radio_serial_for_board
    certs = [{"node_type": "rnode", "board": "RAK4631", "node_name": "rak4",
              "usb_serial": "usb-RAKwireless_WisCore_RAK4631_Board_4631000000000001-if00",
              "born": "2026-08-07 21:14"},
             {"node_type": "rnode", "board": "Heltec LoRa32 v4",
              "node_name": "ttt", "usb_serial": "usb-Espressif_x_AAAA-if00"}]
    serial, how = radio_serial_for_board("RAK4631", certs)
    assert serial == "4631000000000001"
    assert how.startswith("read by this medic when it flashed 'rak4' on 2026-08-")


def test_two_different_boards_of_that_model_is_not_a_guess():
    from ui.cert_store import radio_serial_for_board
    certs = [{"node_type": "rnode", "board": "RAK4631", "node_name": "a",
              "usb_serial": "usb-RAK_x_AAAA-if00"},
             {"node_type": "rnode", "board": "RAK4631", "node_name": "b",
              "usb_serial": "usb-RAK_x_BBBB-if00"}]
    serial, how = radio_serial_for_board("RAK4631", certs)
    assert serial == ""
    assert "2 RAK4631" in how


def test_no_certificate_or_a_placeholder_serial_yields_nothing():
    from ui.cert_store import radio_serial_for_board
    assert radio_serial_for_board("RAK4631", []) == ("", "no RAK4631 flashed by this medic")
    certs = [{"node_type": "rnode", "board": "LilyGO T-Beam", "node_name": "x",
              "usb_serial": "usb-Silicon_Labs_CP2102_0001-if00-port0"}]
    assert radio_serial_for_board("LilyGO T-Beam", certs)[0] == ""


def test_a_pi_certificate_naming_that_board_is_not_a_flash_record():
    from ui.cert_store import radio_serial_for_board
    certs = [{"role": "LXMF propagation node", "board": "RAK4631",
              "node_name": "skyfinger", "radio_rule": {"serial": "X"}}]
    assert radio_serial_for_board("RAK4631", certs)[0] == ""


def test_the_vitals_page_shows_the_port_truth_when_the_cert_has_it():
    body = _code(func_source(DETAIL, "_birth_lines", cls="NodeDetailScreen"))
    assert 'tr("Radio port: {port}")' in body
    assert "/dev/rnode" in body, "only the udev sentence is shown, never a bare port"
