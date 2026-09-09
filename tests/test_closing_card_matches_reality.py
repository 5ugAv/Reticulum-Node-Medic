"""A closing card may only ask for unplugs that are real.

Operator, with photos, 2026-09-09, finishing a Pi Zero 2 W + RAK4631 build:
the last card said "Unplug BOTH boards from Node Medic" when the walkthrough
had told them to take the radio out four steps earlier — and had watched it go.

The old test for this was three token assertions:

    assert "on_medic" in body
    assert "_flash_radio" in body
    assert '"10.55.0."' in body

All three tokens were present. The boolean joining them was wrong:
`_flash_radio` means "this build flashed a radio somewhere", not "a radio is on
the medic now". Source-token tests cannot see that, so this one drives the real
decision instead — and takes its expectations from the NARRATION, so it moves
if the flow moves.
"""

from ui.birth_guide_flow import guide_steps

#: What each narrated step DOES to the medic's USB. Derived from the flow, so
#: reordering _STEPS["pi"] or dropping "Take the radio out" moves the oracle
#: with it — this cannot go stale the way a hardcoded string would.
_EFFECT = {
    "connect_board": lambda st: st.update(radio="medic"),
    "disconnect_board": lambda st: st.update(radio="hand"),
    "connect_pi": lambda st: st.update(pi="medic"),
    "radio_to_pi": lambda st: st.update(radio="pi", pi="own_supply"),
}


def medic_state_after_narration(pi_key="pi_zero_2w", flash_radio=True):
    """Replay the steps the operator was actually shown, and report the state."""
    st = {"radio": "hand", "pi": "hand"}
    for s in guide_steps("pi", pi_key, flash_radio):
        fn = _EFFECT.get(s.get("anim"))
        if fn:
            fn(st)
    return st


def test_the_flow_itself_takes_the_radio_off_the_medic():
    """The premise. If this ever stops being true the rest of the file is moot."""
    titles = [s["title"] for s in guide_steps("pi", "pi_zero_2w", True)]
    assert any("Take the radio out" in t for t in titles), \
        "the flow no longer removes the radio — recheck the closing copy"
    st = medic_state_after_narration()
    assert st["radio"] != "medic", \
        "after the narration the radio is NOT on the medic"


def test_the_closing_copy_asks_the_hardware_instead_of_guessing():
    """_flash_radio is history; local_board_ports() is now."""
    src = open("ui/screens/birth_screen.py").read()
    i = src.index("def _whats_on_the_medic")
    body = src[i:i + 1800]
    assert "local_board_ports" in body, "ask what is attached, don't infer it"
    assert "10.55.0." in body, "a cable address still means the Pi is attached"


def test_neither_closing_surface_hardcodes_both_boards():
    """The popup and the panel behind it must agree, and neither may assert
    two unplugs unconditionally — that mismatch put opposite instructions on
    one screen."""
    from tests.srcutil import func_source
    for marker in ("_popup_outcome", "_handoff_block"):
        body = "\n".join(
            l for l in func_source("ui/screens/birth_screen.py", marker,
                                   cls="BirthScreen").splitlines()
            if not l.strip().startswith("#"))
        assert "radio_on" in body and "pi_on" in body, \
            f"{marker} must branch on what is actually attached"


def test_the_panel_uses_per_board_socket_words():
    """"PWR IN" and "nearer the mini-HDMI" are Pi Zero words. A 3A+ has no data
    micro-USB; a 4B/5 has one USB-C for both. pi_connectors already knows."""
    from tests.srcutil import func_source
    body = "\n".join(
        l for l in func_source("ui/screens/birth_screen.py", "_handoff_block",
                               cls="BirthScreen").splitlines()
        if not l.strip().startswith("#"))
    assert "standalone_power_hint" in body, "use the per-board sentence"
    assert "mini-HDMI" not in body, "Pi Zero wording must not be hardcoded"


# --- the board in their hands ---------------------------------------------
# Operator, 2026-09-09, holding a RAK4631 and watching a Heltec slide into a Pi
# Zero: "all the animations should match the hardware that's being used".
# ConnectBoardAnim has accepted a board_key since 2026-08-02 and NOTHING ever
# passed one, so every radio animation on every path drew a LilyGO LoRa32 while
# fifteen board photos sat unused in assets/boards/.

def test_the_animations_are_told_which_board():
    from tests.srcutil import func_source
    body = func_source("ui/screens/birth_guide_screen.py", "_render_step",
                       cls="BirthGuideScreen")
    assert "_BOARD_ANIMS" in body, "radio animations must be given the board"
    assert "board_key=" in body, "and given it by name"


def test_the_board_anim_tuple_covers_every_class_that_accepts_one():
    """If someone adds a board-drawing animation, it must be listed or it
    silently falls back to the generic sprite again — which is exactly how
    this went unnoticed for five weeks.

    Read from SOURCE, not by import: another test in this suite installs Kivy
    stubs, and importing ui.widgets.birth_anims after that blows up inside the
    import machinery. tests/srcutil.py records why source inspection is the
    house style here.
    """
    import ast
    tree = ast.parse(open("ui/widgets/birth_anims.py").read())
    accepts = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for fn in node.body
        if isinstance(fn, ast.FunctionDef) and fn.name == "__init__"
        and any(a.arg == "board_key" for a in fn.args.args)
    }
    screen = open("ui/screens/birth_guide_screen.py").read()
    listed_src = screen[screen.index("_BOARD_ANIMS = ("):]
    listed_src = listed_src[:listed_src.index(")") + 1]
    listed = {n for n in accepts if n in listed_src}
    missing = accepts - listed
    assert accepts, "no animation accepts a board_key — did the API change?"
    assert not missing, f"accept board_key but are never given one: {missing}"


# --- an nRF52 RTNode has no Wi-Fi to onboard over --------------------------
# techo, rak4631 and heltec_t114 are serial-DFU boards with no Wi-Fi radio;
# wifi_onboarding diverts them to _onboard_techo, whose own docstring says the
# captive portal "cannot exist for it". The step describing a setup AP and a
# Wi-Fi hand-over was shown to them anyway, and the NEXT screen contradicted it.

def test_a_wifiless_rtnode_is_not_promised_a_setup_portal():
    from ui.birth_guide_flow import guide_steps
    for key in ("rak4631", "techo", "heltec_t114"):
        step = [s for s in guide_steps("radio", board_key=key)
                if s.get("anim") == "provision"]
        assert step, f"{key}: no provisioning step found"
        body = step[0]["body"].lower()
        assert "no wi-fi" in body, f"{key} has no Wi-Fi — say so"
        assert "setup wi-fi" not in body and "hands over your wi-fi" not in body


def test_a_wifi_rtnode_keeps_the_portal_story():
    from ui.birth_guide_flow import guide_steps
    for key in ("heltec_v4", "eora_s3"):
        step = [s for s in guide_steps("radio", board_key=key)
                if s.get("anim") == "provision"]
        assert "setup Wi-Fi" in step[0]["body"], f"{key} does use the portal"
