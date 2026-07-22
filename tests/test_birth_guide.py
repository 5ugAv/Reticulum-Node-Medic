"""Guided-birth step ordering — pure data, no Kivy window."""

from ui.screens.birth_guide_screen import guide_steps, BIRTH_PATHS, _STEPS


def test_three_intro_paths_in_order():
    assert [p[0] for p in BIRTH_PATHS] == ["radio", "pi", "host"]
    for _key, title, subtitle in BIRTH_PATHS:
        assert title and subtitle


def test_step_counts_per_path():
    assert len(guide_steps("radio")) == 2
    assert len(guide_steps("pi")) == 4
    assert len(guide_steps("host")) == 2


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
