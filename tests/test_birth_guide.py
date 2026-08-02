"""Guided-birth step ordering — pure data, no Kivy (CI has no Kivy installed)."""

from ui.birth_guide_flow import guide_steps, BIRTH_PATHS, _STEPS, ANTENNA_STEP


def test_three_intro_paths_in_order():
    # ordered by rising complexity: RNode -> RTNode-2400 -> Pi + radio
    assert [p[0] for p in BIRTH_PATHS] == ["host", "radio", "pi"]
    for _key, title, subtitle in BIRTH_PATHS:
        assert title and subtitle


def test_step_counts_per_path():
    # The old final 'Let's set it up' filler page (a narration of what the
    # button was about to do) was removed — operator decision 2026-07-31; the
    # previous page now carries the Start-setup handoff.
    assert len(guide_steps("radio")) == 2      # connect -> what-happens-next/setup
    # insert-into-medic -> image -> insert-into-Pi -> connect radio
    assert len(guide_steps("pi")) == 4
    assert len(guide_steps("host")) == 1       # connect page carries Start setup


def test_antenna_is_the_landing_not_a_guided_step():
    # the antenna step is the first BIRTH screen (a landing), NOT inside guide_steps
    for path in ("radio", "pi", "host"):
        assert all(s.get("anim") != "connect_antenna" for s in guide_steps(path))
    # and it carries the damage warning + both connector types
    assert ANTENNA_STEP.get("warning")
    assert ANTENNA_STEP["anim"] == "connect_antenna"
    assert "U.FL" in ANTENNA_STEP["body"] and "SMA" in ANTENNA_STEP["body"]


def test_unknown_path_is_empty():
    assert guide_steps("nonsense") == []


def test_every_step_has_title_and_body():
    for steps in _STEPS.values():
        for s in steps:
            assert s["title"].strip()
            assert s["body"].strip()


def test_pi_path_does_the_RADIO_before_the_card():
    """Radio first, then the Pi's card (operator, 2026-08-02).

    Nothing forces the Pi first — the radio is flashed BY the medic, never
    through the Pi. Doing it first means the medic knows exactly which radio
    this is BEFORE it spends four minutes writing the Pi's card, so a pairing
    that cannot work (a Pi Zero cannot feed a Heltec V4) is caught while it
    still costs nothing to change.
    """
    titles = [s["title"] for s in guide_steps("pi")]
    i_radio = [i for i, t in enumerate(titles) if "radio board" in t.lower()][0]
    i_card = [i for i, t in enumerate(titles) if "SD card" in t][0]
    assert i_radio < i_card, titles


# --- the CABLE birth (proven 2026-08-01, HOPE) -----------------------------
# These steps described a different flow until then: card into a USB reader,
# radio onto the Pi, network address typed in. Every one of those was wrong.

def test_the_card_goes_into_the_PI_not_into_a_reader():
    """The Pi is its own card reader. Sending the operator hunting for a USB
    reader was the tool making its own limitation their problem."""
    card = [s for s in guide_steps("pi") if "SD card" in s["title"]][0]
    assert "into the Raspberry Pi" in card["title"]
    assert "don't need a card reader" in card["body"]
    joined = " ".join(s["body"] for s in guide_steps("pi"))
    assert "card reader" not in joined.replace("don't need a card reader", "")


def test_the_radio_goes_on_the_MEDIC_not_on_the_pi():
    """This is what removes the powered-hub problem: a Pi Zero cannot reliably
    feed a Heltec V3, but the medic can, and it does the flashing."""
    radio = [s for s in guide_steps("pi") if "radio" in s["title"].lower()][0]
    assert "Node Medic" in radio["title"]
    # A hub counts as Node Medic's side — the medic identifies boards by USB
    # serial, not by which port they hang off (verified: nothing in the tree
    # keys on bus/port). What must never happen is the radio going on the Pi.
    assert "not into the pi" in radio["body"].lower()


def test_the_operator_is_told_to_restart_the_pi_after_imaging():
    """The medic CANNOT power-cycle a Pi — uhubctl on the Pi 5 root hub does
    not cut VBUS (measured: the device stays powered and never reboots). If the
    flow doesn't ask, the operator waits forever for a node that never boots."""
    titles = [s["title"] for s in guide_steps("pi")]
    restart = [s for s in guide_steps("pi") if "Restart" in s["title"]]
    assert restart, "no restart step — the flow would silently stall"
    assert "can't switch the Pi off and on" in restart[0]["hint"]
    # and it must come AFTER the imaging hand-off. (It no longer needs to
    # precede the radio step: the radio is now done first, so that the board is
    # known before the card is written.)
    i_restart = titles.index(restart[0]["title"])
    i_image = [i for i, st in enumerate(guide_steps("pi"))
               if st.get("screen") == "pi_imager"][0]
    assert i_restart > i_image, titles


def test_the_data_port_trap_is_called_out_where_it_happens():
    """A Zero has two identical micro-USB sockets and only one carries data.
    This cost an hour on the bench, to the person who designed the flow."""
    connect = [s for s in guide_steps("pi") if "Connect the Pi" in s["title"]][0]
    hint = connect["hint"]
    assert "DATA port" in hint and "PWR IN" in hint
    assert "mini-HDMI" in hint, "must say WHICH socket, not just 'the data one'"
    assert "coiled" in hint, "a thin/coiled cable drops the link - measured"


def test_no_step_asks_for_a_network_address_or_wifi():
    """The medic reaches the Pi over the USB cable. Asking a field operator for
    an IP address was the original complaint that started this whole path."""
    joined = " ".join(s["title"] + " " + s["body"] + " " + s.get("hint", "")
                      for s in guide_steps("pi")).lower()
    for phrase in ("ip address", "hostname of the pi", "wi-fi password",
                   "wifi password", "join your wi-fi"):
        assert phrase not in joined, f"still asks for {phrase!r}"


#: Animations the wizard advances itself on, by watching USB. A step using one
#: needs no button and deliberately has none (operator, 2026-08-02).
_SELF_ADVANCING = {"connect_pi", "connect_board"}


def test_last_step_hands_off_to_setup():
    """Every path must END somewhere — by a labelled button, or by detection.

    The Pi path now finishes on "Restart the Pi", which the medic detects and
    carries forward on its own, so it correctly has no button.
    """
    for path in ("radio", "pi", "host"):
        last = guide_steps(path)[-1]
        assert (last.get("next", "").strip()
                or last.get("anim") in _SELF_ADVANCING), path


def test_guide_steps_returns_a_copy():
    a = guide_steps("radio")
    a.append({"title": "x", "body": "y"})
    assert len(guide_steps("radio")) == 2          # internal list untouched


def test_the_pi_step_uses_the_pi_animation_not_the_radio_one():
    """Walkthrough 2026-08-02: the step said 'Connect the Pi' while showing a
    radio board sliding into the medic."""
    connect = [s for s in guide_steps("pi") if "Connect the Pi" in s["title"]][0]
    assert connect["anim"] == "connect_pi"


def test_the_pi_connect_step_is_not_detected_by_serial_port_polling():
    """The bug that stalled the first walkthrough: a Pi in boot-ROM mode is not
    a serial device at all, so local_board_ports() can never see one and the
    step sat there telling the operator to connect an already-connected Pi."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_start_pi_poll" in src
    assert "pi_usbboot" in src
    # the Pi branch must be chosen BEFORE the generic serial-board branch,
    # since ConnectPiAnim subclasses ConnectBoardAnim
    i_pi = src.index("isinstance(anim, ConnectPiAnim)")
    i_board = src.index("isinstance(anim, ConnectBoardAnim)")
    assert i_pi < i_board, "the Pi branch is unreachable behind its own base class"


def test_the_restart_step_shows_the_pi_not_the_radio():
    """Walkthrough 2026-08-02: 'Restart the Pi' showed a radio board sliding in
    on a red cable."""
    restart = [s for s in guide_steps("pi") if "Restart" in s["title"]][0]
    assert restart["anim"] == "connect_pi"


def test_no_pi_step_uses_the_radio_board_animation_except_the_radio_step():
    for s in guide_steps("pi"):
        if "radio" in s["title"].lower():
            continue
        assert s.get("anim") != "connect_board", f"{s['title']} shows a radio board"


def test_detect_finds_a_pi_so_choose_manually_is_not_required():
    """'Choose manually' is the ADVANCED escape. Needing it to build a Pi — the
    commonest node — made the primary path the fallback."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_start_detect_pi_poll" in src and "_on_pi_detected" in src


def test_the_pi_poll_is_stopped_when_the_step_changes():
    """Left running it fires _on_pi_detected forever and drags the operator back
    to the name screen from wherever they got to."""
    src = open("ui/screens/birth_guide_screen.py").read()
    stop_current = src[src.index("def _stop_current"):][:400]
    assert "_stop_detect_pi_poll" in stop_current


def test_a_pi_build_does_not_open_with_an_antenna_instruction():
    """A Pi has no antenna and no radio attached yet. Opening with 'attach the
    antenna' is an instruction about a board the operator isn't holding."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_pi_present_without_radio" in src


def test_back_and_forward_share_one_notion_of_redundant():
    """Back must never land on a step the forward path would skip.

    While the radio stayed plugged in, Back decremented onto the 'connect the
    radio' step, which the renderer then skipped forward again — so the button
    did nothing at all (operator, 2026-08-02). Both directions now consult
    _step_is_redundant, and Back walks past anything it reports.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "ui" / "screens" / "birth_guide_screen.py").read_text()
    assert "def _step_is_redundant" in src
    back = src[src.index("    def _back(self):"):]
    back = back[:back.index("\n    def ", 10)]
    assert "_step_is_redundant" in back, "Back does not skip redundant steps"
    assert "while" in back, "Back must walk past a RUN of redundant steps"
    fwd = src[src.index("    def _render_step(self):"):]
    fwd = fwd[:fwd.index("\n    def ", 10)]
    assert "_step_is_redundant" in fwd, "forward skip stopped sharing the rule"
