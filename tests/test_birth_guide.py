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


def test_last_step_hands_off_to_setup():
    for path in ("radio", "pi", "host"):
        assert guide_steps(path)[-1].get("next", "").strip()


def test_guide_steps_returns_a_copy():
    a = guide_steps("radio")
    a.append({"title": "x", "body": "y"})
    assert len(guide_steps("radio")) == 2          # internal list untouched
