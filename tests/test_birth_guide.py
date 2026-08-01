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


def test_pi_path_starts_with_sd_then_radio():
    titles = [s["title"] for s in guide_steps("pi")]
    assert "SD card" in titles[0]
    # the radio board is connected before the hand-off to setup
    assert any("radio board" in t.lower() for t in titles[1:])


# --- the CABLE birth (proven 2026-08-01, HOPE) -----------------------------
# These steps described a different flow until then: card into a USB reader,
# radio onto the Pi, network address typed in. Every one of those was wrong.

def test_the_card_goes_into_the_PI_not_into_a_reader():
    """The Pi is its own card reader. Sending the operator hunting for a USB
    reader was the tool making its own limitation their problem."""
    first = guide_steps("pi")[0]
    assert "into the Raspberry Pi" in first["title"]
    assert "don't need a card reader" in first["body"]
    joined = " ".join(s["body"] for s in guide_steps("pi"))
    assert "card reader" not in joined.replace("don't need a card reader", "")


def test_the_radio_goes_on_the_MEDIC_not_on_the_pi():
    """This is what removes the powered-hub problem: a Pi Zero cannot reliably
    feed a Heltec V3, but the medic can, and it does the flashing."""
    radio = [s for s in guide_steps("pi") if "radio" in s["title"].lower()][0]
    assert "Node Medic" in radio["title"]
    assert "not into the Pi" in radio["body"]


def test_the_operator_is_told_to_restart_the_pi_after_imaging():
    """The medic CANNOT power-cycle a Pi — uhubctl on the Pi 5 root hub does
    not cut VBUS (measured: the device stays powered and never reboots). If the
    flow doesn't ask, the operator waits forever for a node that never boots."""
    titles = [s["title"] for s in guide_steps("pi")]
    restart = [s for s in guide_steps("pi") if "Restart" in s["title"]]
    assert restart, "no restart step — the flow would silently stall"
    assert "can't switch the Pi off and on" in restart[0]["hint"]
    # and it must come AFTER imaging and BEFORE the radio step
    i_restart = titles.index(restart[0]["title"])
    i_radio = [i for i, t in enumerate(titles) if "radio" in t.lower()][0]
    assert 0 < i_restart < i_radio


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


def test_last_step_hands_off_to_setup():
    for path in ("radio", "pi", "host"):
        assert guide_steps(path)[-1].get("next", "").strip()


def test_guide_steps_returns_a_copy():
    a = guide_steps("radio")
    a.append({"title": "x", "body": "y"})
    assert len(guide_steps("radio")) == 2          # internal list untouched
