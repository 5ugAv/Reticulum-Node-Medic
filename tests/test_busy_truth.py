"""Every busy label tells the truth about WHICH work is slow.

Operator, 2026-08-14: "Everything that's on the screen should be telling the
truth to the user. I don't want anybody misled on any of the processes."

The lie this file kills: one hardcoded busy paragraph claimed "the firmware
compile is the slow part (a first build also downloads the toolchain)" for
EVERY build — true for an RTNode-2400 (arduino-cli compiles for minutes) and
for the Wireless Tracker, false for a Pi provisioning run (no compile at
all; the slow part is installing software on the Pi) and false for every
autoinstall flash (prebuilt firmware from the cache — a write, not a
compile). The activity banner likewise called every build "Flashing", so a
Pi build showed "Flashing everywhere" while it was actually apt/pip deep in
Raspberry Pi OS.
"""
from ui.busy_truth import busy_truth
from workflows.rnode_boards import get_board


def test_pi_build_speaks_of_the_pi_not_a_compile():
    banner, para = busy_truth("pi_rnode", get_board("heltec32_v4"), "everywhere")
    # An explicit denial ("nothing is compiled") is truth; the CLAIM is the lie.
    assert "compile is the slow part" not in para.lower()
    assert "toolchain" not in para.lower()
    assert "pi" in para.lower()
    assert banner.startswith("Building everywhere")
    assert "Flashing" not in banner


def test_autoinstall_flash_is_a_write_not_a_compile():
    board = get_board("heltec32_v4")
    banner, para = busy_truth("rnode_flash", board, "")
    assert "compile is the slow part" not in para.lower(), (
        "autoinstall writes PREBUILT firmware — claiming a compile is a lie")
    assert "prebuilt" in para.lower()
    # Two lines since 2026-08-14 (briefing Task 7): the act first and large,
    # the board name on its own smaller line where it can never shrink it.
    assert banner.startswith("Flashing RNode\n")
    assert board.display_name in banner


def test_tracker_flash_really_does_compile_and_may_say_so():
    board = get_board("heltec_wireless_tracker")
    _banner, para = busy_truth("rnode_flash", board, "")
    assert "compile" in para.lower(), (
        "the arduino-cli path genuinely compiles — that truth should stay")


def test_rtnode_build_keeps_its_true_compile_warning():
    banner, para = busy_truth("rtnode_2400", None, "JONESEY-2")
    assert "compile" in para.lower()
    assert "toolchain" in para.lower()
    assert banner.startswith("Building JONESEY-2")


def test_every_kind_tells_the_operator_to_wait_for_the_green_word():
    for args in [("pi_rnode", get_board("heltec32_v4"), "x"),
                 ("rnode_flash", get_board("rak4631"), ""),
                 ("rtnode_2400", None, "y")]:
        _b, para = busy_truth(*args)
        assert "Build finished" in para, (
            f"{args[0]}: the wait-for-confirmation instruction went missing")


def test_the_screen_uses_the_truth_not_the_old_hardcoded_string():
    """Wiring guard: _launch must render busy_truth's paragraph and
    _mark_activity must raise busy_truth's banner — a revert to the one-lie-
    fits-all string fails HERE, not on the operator's bench."""
    from tests.srcutil import func_source, src
    screen = src("ui/screens/birth_screen.py")
    assert "the firmware compile is the slow part" not in screen, (
        "the compile claim may exist ONLY inside busy_truth, where it is "
        "chosen per build kind")
    launch_src = func_source("ui/screens/birth_screen.py", "_launch",
                             cls="BirthScreen")
    assert "_busy_paragraph" in launch_src
    mark_src = func_source("ui/screens/birth_screen.py", "_mark_activity",
                           cls="BirthScreen")
    assert "_run_banner" in mark_src
    assert "Flashing {nm}" not in mark_src


def test_no_build_can_inherit_the_previous_build_s_words():
    """A V3 RTNode build ran its whole length under "Flashing RNode / Heltec
    LoRa32 v4" — the PREVIOUS run's banner, on the one line telling the operator
    not to power off (bench, 2026-08-18). _launch must take the words for THIS
    run and clear them, so a launch that sets none gets the honest generic line
    rather than the last board's name."""
    from tests.srcutil import func_source
    launch = func_source("ui/screens/birth_screen.py", "_launch",
                         cls="BirthScreen")
    assert "self._busy_banner = self._busy_paragraph = None" in launch, (
        "_launch must clear the busy words after consuming them")
    assert "_run_banner" in launch and "_run_paragraph" in launch


def test_the_rtnode_build_chooses_its_own_words():
    """It builds its workflow directly, so it never reaches the busy_truth call
    in _build_workflow — it must make the call itself or it says nothing and
    inherits whatever ran last."""
    from tests.srcutil import func_source
    run = func_source("ui/screens/birth_screen.py", "_run_rtnode",
                      cls="BirthScreen")
    assert "busy_truth" in run, (
        "the RTNode launch must set the busy words for its own build")


def test_an_rtnode_build_is_never_announced_as_flashing_an_rnode():
    banner, para = busy_truth("rtnode2400", None, "3333v")
    assert "RNode" not in banner
    assert "3333v" in banner
    assert "compile" in para
