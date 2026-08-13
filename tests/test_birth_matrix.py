"""THE BIRTH COMBINATION MATRIX — every Pi x every board x both Bluetooth
answers, through the full emulated birth, BEFORE any pairing meets a bench.

Operator, 2026-08-14: "how about running emulators for the birth process with
all the possible pi and board combinations so there is less bench work to do
when we plug them in." This file is that instruction, standing. When a new
board or Pi model joins the registries it inherits the whole sweep with no new
code; a combination that cannot birth must fail HERE, on a Mac in
milliseconds, not on a bench with a screwdriver in hand.

Three sweeps and a table:

  1. The full 17-step build, healthy path: 5 Pi model strings x every board
     in RNODE_BOARDS x Bluetooth on/off (already-provisioned radio attached).
  2. The BLANK-board flash path per board: a fresh, never-flashed board must
     either be provisioned from the cache or refused with a reason that tells
     the operator something true and useful — never a mystery.
  3. The band answer sheet: every autoinstall board must be able to answer
     the 915 MHz menu (this project's band), or refuse with the WHY named.
     (The RAK4631 died on the bench 2026-08-05 for exactly this; three more
     boards carried the same landmine when this matrix first ran.)

Plus: every flashable board must have a power model, so the powered-hub
warning can never be silently absent for a board the chooser offers.
"""
import pytest

from node_profile import NodeProfile, NodeHardware
from tests.test_build_workflow import build_conn, EXPECTED_STEPS
from workflows.build import BuildWorkflow
from workflows.rnode_boards import RNODE_BOARDS, get_board
from workflows import power_compat


# ---- the axes -----------------------------------------------------------

#: cpuinfo Model line -> what detect_hardware must conclude. The 3B+ and 4B
#: are real Pis an operator may plug in; the tool has no profile for them yet,
#: so the HONEST detection is UNKNOWN — and the build must still complete
#: (default config template, default power caution), not crash.
PI_MODELS = {
    "pi_5":       ("Raspberry Pi 5 Model B Rev 1.0",           NodeHardware.PI_5),
    "pi_zero_2w": ("Raspberry Pi Zero 2 W Rev 1.0",            NodeHardware.PI_ZERO_2W),
    "pi_3a_plus": ("Raspberry Pi 3 Model A Plus Rev 1.0",      NodeHardware.PI_3A_PLUS),
    "pi_3b_plus": ("Raspberry Pi 3 Model B Plus Rev 1.3",      NodeHardware.UNKNOWN),
    "pi_4b":      ("Raspberry Pi 4 Model B Rev 1.4",           NodeHardware.UNKNOWN),
}

BOARD_KEYS = sorted(RNODE_BOARDS)
AUTOINSTALL_KEYS = [k for k in BOARD_KEYS
                    if RNODE_BOARDS[k].flash_method == "autoinstall"]

#: Boards whose 915 MHz answer is genuinely unanswerable without the operator:
#: rnodeconf's menu for them selects by RADIO CHIP (SX1276 vs SX1262 variants
#: of the same product), which nothing on this side of the USB cable can see.
#: Transcribed from rnodeconf 2.5.0 on the medic, 2026-08-14. These must
#: REFUSE — but the refusal must say WHY, not just "not yet verified".
CHIP_AMBIGUOUS = {"tbeam", "t3s3"}


def _cpuinfo(model_line: str) -> str:
    return f"processor\t: 0\nModel\t\t: {model_line}\n"


def _wf(pi_key: str, board_key: str, bluetooth: bool, rnode: bool = True):
    cpu_line, _ = PI_MODELS[pi_key]
    conn = build_conn(cpuinfo=_cpuinfo(cpu_line), rnode=rnode)
    prof = NodeProfile(bluetooth_enabled=bluetooth)
    prof.rnode_board_key = board_key
    return BuildWorkflow(conn, prof), conn


# ---- sweep 1: the full build, every combination -------------------------

@pytest.mark.parametrize("bluetooth", [False, True], ids=["bt_off", "bt_on"])
@pytest.mark.parametrize("board_key", BOARD_KEYS)
@pytest.mark.parametrize("pi_key", sorted(PI_MODELS))
def test_full_build_green_for_every_combination(pi_key, board_key, bluetooth):
    w, conn = _wf(pi_key, board_key, bluetooth)
    w.run_all()
    ctx = f"[{pi_key} x {board_key} bt={'on' if bluetooth else 'off'}]"
    bad = [(r.name, r.message) for r in w.results if not r.success]
    assert not bad, f"{ctx} failed steps: {bad}"
    assert len(w.results) == len(EXPECTED_STEPS), (
        f"{ctx} only {len(w.results)}/{len(EXPECTED_STEPS)} steps ran")
    _, expect_hw = PI_MODELS[pi_key]
    assert w.profile.hardware is expect_hw, (
        f"{ctx} detected {w.profile.hardware}, expected {expect_hw}")


@pytest.mark.parametrize("pi_key,template", [
    ("pi_5", "reticulum_transport_pi5.conf"),
    ("pi_zero_2w", "reticulum_transport_pi_zero.conf"),
    ("pi_3a_plus", "reticulum_transport_pi_zero.conf"),
    ("pi_3b_plus", "reticulum_transport_default.conf"),
    ("pi_4b", "reticulum_transport_default.conf"),
])
def test_config_template_tracks_the_pi_model(pi_key, template):
    w, _ = _wf(pi_key, "heltec32_v4", bluetooth=False)
    w.run_all()
    assert w._template_name() == template


@pytest.mark.parametrize("pi_key", sorted(PI_MODELS))
def test_bluetooth_answer_reaches_the_node_or_leaves_it_alone(pi_key):
    """OFF must actually touch the node (dtoverlay=disable-bt); ON must leave
    the stock OS untouched — a node keeping its radio must not be half-cut."""
    w_off, conn_off = _wf(pi_key, "heltec32_v4", bluetooth=False)
    w_off.run_all()
    joined_off = "\n".join(conn_off.history)
    # The boot-config body travels base64-encoded; the observable commands are
    # the immediate-tense actions, which OFF must always run.
    assert "rfkill block bluetooth" in joined_off, (
        f"[{pi_key}] Bluetooth OFF never blocked the radio on the node")

    w_on, conn_on = _wf(pi_key, "heltec32_v4", bluetooth=True)
    w_on.run_all()
    joined_on = "\n".join(conn_on.history)
    assert "rfkill block bluetooth" not in joined_on, (
        f"[{pi_key}] Bluetooth ON still blocked the radio on the node")


# ---- sweep 2: the blank-board flash path, every board -------------------

def _blank_build(board_key, monkeypatch):
    """A build where the attached board is BLANK (never flashed): --info gets
    no answer, the firmware cache is present, autoinstall succeeds."""
    from workflows import rnode_v4_rgb
    # Deterministic: the RGB overlay depends on the medic's local build cache;
    # this sweep tests the stock path. The RGB branch has its own tests.
    monkeypatch.setattr(rnode_v4_rgb, "rgb_firmware_available", lambda: False)
    cpu_line, _ = PI_MODELS["pi_3a_plus"]
    conn = build_conn(cpuinfo=_cpuinfo(cpu_line), rnode=False)
    # A blank board still enumerates a serial port — that presence is exactly
    # what separates "blank (will flash)" from "none".
    conn.rules.insert(0, ("ls /dev/serial/by-id/", 0,
                          "usb-Espressif_USB_JTAG_serial_debug_unit_"
                          "02:00:00:03:00:03-if00", ""))
    conn.rules.insert(0, ("readlink -f", 0, "/dev/ttyACM0", ""))
    conn.rules.insert(0, ("--autoinstall", 0,
                          "RNode Firmware autoinstallation complete!", ""))
    prof = NodeProfile()
    prof.rnode_board_key = board_key
    return BuildWorkflow(conn, prof), conn


@pytest.mark.parametrize("board_key", AUTOINSTALL_KEYS)
def test_blank_board_is_flashed_or_refused_with_the_why(board_key, monkeypatch):
    w, conn = _blank_build(board_key, monkeypatch)
    w.run_all()
    flash = next(r for r in w.results if r.name == "flash_rnode_firmware")
    if board_key in CHIP_AMBIGUOUS:
        # These cannot be answered from here — but the operator deserves the
        # real reason (chip variant), not a shrug.
        assert not flash.success, f"{board_key} flashed on a guessed chip menu"
        assert "chip" in flash.message.lower(), (
            f"{board_key} refusal does not name the chip ambiguity: "
            f"{flash.message!r}")
    else:
        assert flash.success, (
            f"blank {board_key} was not flashed: {flash.message!r}")
        assert not flash.skipped, f"blank {board_key} was SKIPPED, not flashed"
        assert RNODE_BOARDS[board_key].display_name in flash.message


def test_blank_tracker_names_its_different_road(monkeypatch):
    """The Wireless Tracker is an arduino_cli board — the autoinstall path
    must refuse it by name, not die trying."""
    w, _ = _blank_build("heltec_wireless_tracker", monkeypatch)
    w.run_all()
    flash = next(r for r in w.results if r.name == "flash_rnode_firmware")
    assert not flash.success
    assert "autoinstall" in flash.message.lower()


# ---- sweep 3: the band answer sheet -------------------------------------

@pytest.mark.parametrize("board_key", AUTOINSTALL_KEYS)
def test_every_autoinstall_board_answers_915_or_names_why_not(board_key):
    """This project's band is 915 MHz. Every board the chooser offers must
    either hold a transcribed answer sheet for it, or refuse with the reason
    an operator can act on. 'Not yet verified' with no why is the RAK4631
    bench failure of 2026-08-05 waiting to happen again."""
    board = RNODE_BOARDS[board_key]
    if board_key in CHIP_AMBIGUOUS:
        with pytest.raises(ValueError, match="chip"):
            board.autoinstall_answers(915)
    else:
        answers = board.autoinstall_answers(915)
        assert answers[0] == str(board.autoinstall_index)
        assert answers[-1] == "y"


# ---- the power table ----------------------------------------------------

@pytest.mark.parametrize("board_key", BOARD_KEYS)
def test_every_board_has_a_power_model(board_key):
    """A board the chooser offers with no power entry gets NO powered-hub
    warning ever — silence where a warning belongs."""
    assert board_key in power_compat.BOARD_POWER


@pytest.mark.parametrize("board_key", BOARD_KEYS)
@pytest.mark.parametrize("pi_key", sorted(PI_MODELS))
def test_every_pairing_gets_a_power_verdict(pi_key, board_key):
    v = power_compat.check(pi_key, board_key)
    assert v is not None, f"{pi_key} x {board_key}: no power verdict at all"
    assert v.get("verdict") in ("ok", "caution", "blocked"), v
