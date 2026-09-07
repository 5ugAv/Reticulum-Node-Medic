"""The 'failure card can't show its failure' fix (UX review, 2026-08-14).

The shared hazard/failure card (ui.requirement_popup) is handed 9-11 line,
400-500 char messages by BIRTH / PROBE / the guided flow — the [FAIL] line plus
per-board button-press recovery. The old fixed card gave the body 2-3 lines and
a plain Label CLIPPED the rest, so the instruction the operator needed was
off-screen. And it never showed the board it told them to press buttons on.

These are source-inspection tests: Kivy is not importable in CI (see
tests/srcutil), so they assert the STRUCTURE — a ScrollView body, an image
hook, no truncation, and the probe workers that re-enable their controls on a
raised exception — rather than instantiating the widgets.
"""
from tests.srcutil import func_source, src


# -- the card body scrolls, so a long message is never clipped --------------

def test_popup_wraps_the_body_in_a_scrollview():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    assert "ScrollView(" in popup, "the body must scroll so 9-11 lines all show"
    assert "scroll.add_widget(body)" in popup


def test_popup_never_truncates_the_message():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    # the whole message goes to the Label untouched — no slicing/indexing of it
    assert "text=message" in popup
    assert "message[" not in popup, "no truncation logic may drop the message"


def test_popup_clamps_to_the_panel():
    src_txt = src("ui/requirement_popup.py")
    # a max-height clamp keyed off the window keeps the card inside the glass
    assert "_MAX_H_FRAC" in src_txt
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    assert "Window.height" in popup


def test_popup_body_font_goes_through_the_theme():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    assert "theme.font_sp(" in popup
    # the operator's bigger-success-text note still holds
    assert 'if tone == "success"' in popup and "21sp" in popup


# -- show-don't-tell: an optional board photo above the text ----------------

def test_popup_accepts_optional_image_path_defaulting_to_none():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    assert "image_path" in popup
    assert "image_path=None" in popup or "image_path: str | None = None" in popup


def test_popup_only_renders_the_image_when_given():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    # None (or a missing file) => no Image widget, i.e. the old text-only card
    assert "if image_path:" in popup
    assert "Image(source=image_path" in popup


# -- the default title + button run through i18n ----------------------------

def test_popup_default_title_and_button_are_translated():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    assert "tr(title)" in popup          # default "Heads up" flows through tr()
    # The label is a parameter now (a waypoint card says "Continue"), but it
    # still goes through tr() — both the caller's word and the default.
    assert 'tr(button_text or "Got it")' in popup


def test_got_it_key_is_in_every_catalog():
    import json, os
    from ui import i18n
    for code in ("es", "fr", "de", "sv", "pl", "id", "ru", "ja"):
        path = os.path.join(i18n._I18N_DIR, f"{code}.json")
        with open(path, encoding="utf-8") as f:
            assert "Got it" in json.load(f), f"{code}.json missing 'Got it'"


def test_every_button_label_a_caller_passes_is_in_every_catalog():
    """A label handed in as button_text never appears as a tr("...") literal,
    so the AST coverage guard cannot see it — it would ship untranslated in
    silence. Pin the labels the callers actually pass."""
    import json, os
    from ui import i18n
    from tests.srcutil import src
    passed = set()
    for path in ("ui/screens/birth_screen.py",):
        for line in src(path).splitlines():
            if "button_text=" in line and '"' in line.split("button_text=", 1)[1]:
                passed.add(line.split("button_text=", 1)[1].split('"')[1])
    # birth_screen passes its label via a variable; catch that spelling too.
    for line in src("ui/screens/birth_screen.py").splitlines():
        if "_btn = " in line and '"' in line:
            passed.update(p for p in line.split('"')[1::2] if p)
    assert passed, "no caller-supplied button labels found — has the wiring moved?"
    for code in ("es", "fr", "de", "sv", "pl", "id", "ru", "ja"):
        with open(os.path.join(i18n._I18N_DIR, f"{code}.json"),
                  encoding="utf-8") as f:
            cat = json.load(f)
        for label in passed:
            assert label in cat, f"{code}.json missing button label {label!r}"


# -- the birth-failure call site now shows the board it names ----------------

def test_birth_failure_popup_passes_the_board_image():
    birth = src("ui/screens/birth_screen.py")
    assert "board_images.image_for(getattr(self, \"_last_board\"" in birth
    assert "image_path=board_png" in birth


# -- probe workers re-enable their control on a raised exception -------------

def test_probe_run_reenables_button_on_exception():
    run = func_source("ui/screens/probe_screen.py", "_run", cls="ProbeScreen")
    assert "try:" in run and "except Exception" in run
    assert "_run_failed" in run
    failed = func_source("ui/screens/probe_screen.py", "_run_failed",
                         cls="ProbeScreen")
    assert "self.run_btn.disabled = False" in failed


def test_probe_fix_one_reenables_on_exception():
    fix = func_source("ui/screens/probe_screen.py", "_fix_one", cls="ProbeScreen")
    assert "except Exception" in fix and "ok = False" in fix


def test_probe_fix_all_reenables_on_exception():
    fix = func_source("ui/screens/probe_screen.py", "_fix_all", cls="ProbeScreen")
    assert "except Exception" in fix and "_fix_all_failed" in fix
    failed = func_source("ui/screens/probe_screen.py", "_fix_all_failed",
                         cls="ProbeScreen")
    assert "self._busy = False" in failed
    assert "self.fix_all_btn.disabled = False" in failed


# -- long-kiosk-session hygiene: no leaked Window callback, floor can't overflow

def test_popup_unbinds_its_window_callback_on_dismiss():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    # the resize callback is held by name and dropped on dismiss — otherwise
    # every popup permanently adds a Window height callback over dead widgets
    assert "Window.bind(height=_on_win_resize)" in popup
    assert "Window.unbind(height=_on_win_resize)" in popup
    assert "on_dismiss=lambda" in popup


def test_popup_floor_never_beats_the_clamp():
    popup = func_source("ui/requirement_popup.py", "requirement_popup")
    # the one-line floor is itself capped at the room available, so on a panel
    # too short for a line under 0.9H the card stays inside the glass
    assert "floor = min(dp(44), max(avail, 0))" in popup
    assert "scroll.height = max(floor, min(body.height, avail))" in popup
