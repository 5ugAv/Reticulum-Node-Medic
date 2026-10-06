"""The setup wizard's screen logic, run for real without a display.

Kivy cannot open a window here or in CI, so the screen cannot be instantiated —
the same wall ``test_birth_back_swipe`` hit. Rather than settle for
substring-matching the source, these tests COMPILE the shipped methods out of
the file and run them against a stub self. Real behaviour, no display, and a
rename fails loudly because ``func_source`` raises rather than matching nothing.

What is under test is the handful of places where the screen makes a decision
instead of drawing: comparing a typed recovery key, holding the two pattern
drawings apart, refusing a step whose precondition has gone, and deciding what —
if anything — gets applied at the end.
"""

import textwrap
import types

import pytest

from provisioning import recovery_key
from provisioning.vault_factors import (KEYFILE, PASSPHRASE, PATTERN,
                                        FactorError, Policy, confirm_pattern,
                                        encode_pattern)
from tests.srcutil import func_source
from ui import setup_flow as sf
from ui.setup_flow import SetupState

SCREEN = "ui/screens/setup_wizard_screen.py"

#: A Clock that fires nothing. Every deferred hop in this screen is a
#: presentation delay ("that matches" left on screen for half a second); running
#: them would test Kivy's scheduler, not the decision that scheduled them.
_CLOCK = types.SimpleNamespace(schedule_once=lambda *a, **k: None,
                               schedule_interval=lambda *a, **k: None)

#: theme is only ever asked for a colour here.
_THEME = types.SimpleNamespace(COLORS={"amber": "#ffbf00", "green": "#00ff00",
                                       "warning_yellow": "#ffd700",
                                       "text_primary": "#ffffff"},
                               hex_to_rgba=lambda h, a=1: (0, 0, 0, a))


def _load(name, **extra):
    """One method from the shipped file, as a callable taking self first."""
    ns = {"recovery_key": recovery_key, "sf": sf, "Clock": _CLOCK,
          "theme": _THEME, "encode_pattern": encode_pattern,
          "confirm_pattern": confirm_pattern, "FactorError": FactorError,
          "PATTERN": PATTERN, "PASSPHRASE": PASSPHRASE, "KEYFILE": KEYFILE}
    ns.update(extra)
    exec(compile(textwrap.dedent(func_source(SCREEN, name)), SCREEN, "exec"), ns)
    return ns[name]


class _Field:
    def __init__(self, text=""):
        self.text = text
        self.password = True
        self.foreground_color = None


class _Label:
    def __init__(self):
        self.text = ""
        self.color = None


def _self(**attrs):
    obj = types.SimpleNamespace(_state=SetupState(), _i=3, _goto=lambda i: None,
                                _notice="", _render=lambda: None)
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


# --------------------------------------------------------------------------- #
# Typing the recovery key back.
# --------------------------------------------------------------------------- #

def _typing_self(shown_key, typed):
    """A stand-in for the eight group boxes, filled the way typing fills them.

    The screen stopped being one long field on 2026-08-18: the key is SHOWN as
    two rows of four groups, and asking the operator to reproduce that grouping
    inside a single box made them responsible for spacing that was never the
    thing being checked. ``typed`` is still written here the way a person would
    say it, and split on its separators into the boxes it would have landed in.
    """
    import re
    boxes = [_Field(g) for g in re.split(r"[-\s_]+", typed) if g]
    obj = _self(_recovery=shown_key, _key_boxes=boxes, _key_status=_Label())
    obj._typed_key = types.MethodType(_load("_typed_key"), obj)
    obj._mark_wrong_groups = types.MethodType(_load("_mark_wrong_groups"), obj)
    return obj


def _boxes_self(shown_key="ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789"):
    """Eight empty boxes plus the focus/validation wiring they call."""
    obj = _self(_recovery=shown_key, _key_status=_Label(),
                _key_boxes=[_Field("") for _ in range(recovery_key.GROUPS)])
    obj.focused = []
    obj._focus_key_box = lambda i: obj.focused.append(i)
    obj._typed_key = types.MethodType(_load("_typed_key"), obj)
    obj._check_typed_key = lambda: None
    return obj



@pytest.fixture
def security_half(monkeypatch):
    """The lock-your-records half is OFF in v1 (``setup_flow.SECURITY_HALF``,
    readiness ledger #174); the tests that take this fixture describe it for
    the release that turns it back on."""
    monkeypatch.setattr(sf, "SECURITY_HALF", True)

def test_a_full_group_moves_to_the_next_box():
    """Four characters IS the group — the operator's eyes are already on the
    next one, so the cursor should be too."""
    typed = _load("_key_box_typed")
    s = _boxes_self()
    s._key_boxes[0].text = "ABCD"
    typed(s, 0, "ABCD")
    assert s.focused == [1]


def test_a_part_typed_group_does_not_jump_away():
    typed = _load("_key_box_typed")
    s = _boxes_self()
    s._key_boxes[0].text = "AB"
    typed(s, 0, "AB")
    assert s.focused == []


def test_the_last_group_has_nowhere_to_advance_to():
    typed = _load("_key_box_typed")
    s = _boxes_self()
    last = recovery_key.GROUPS - 1
    s._key_boxes[last].text = "6789"
    typed(s, last, "6789")
    assert s.focused == []


def test_overflow_is_carried_forward_never_dropped():
    """A fast typist or a paste puts more than four in a box. Silently eating a
    character the operator watched themselves type is the one behaviour this
    screen must not have."""
    typed = _load("_key_box_typed")
    s = _boxes_self()
    s._key_boxes[0].text = "ABCDEF"
    typed(s, 0, "ABCDEF")
    assert s._key_boxes[0].text == "ABCD"
    assert s._key_boxes[1].text == "EF", "the spill must land in the next box"
    assert s.focused == [1]


def test_separators_belong_to_the_layout_not_the_typing():
    """The boxes ARE the grouping now, so a hyphen or a space typed out of habit
    is noise — it must not eat one of the four places."""
    typed = _load("_key_box_typed")
    s = _boxes_self()
    s._key_boxes[0].text = "AB-C"
    typed(s, 0, "AB-C")
    assert s._key_boxes[0].text == "ABC"


def test_the_boxes_read_back_as_the_key_that_was_shown():
    shown = "ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789"
    s = _boxes_self(shown)
    for box, group in zip(s._key_boxes, recovery_key.groups(shown)):
        box.text = group
    assert recovery_key.normalize(s._typed_key()) == recovery_key.normalize(shown)


def test_there_is_one_box_per_group_of_the_shown_key():
    """The formation has to match what the operator is copying from: the key is
    displayed as two rows of four groups (ui/screens/recovery_key_screen.py)."""
    from tests.srcutil import func_source
    src = func_source(SCREEN, "_render_type_it_back")
    assert "recovery_key.GROUPS" in src, "box count must follow the key, not a literal"
    assert "GROUPS // 2" in src, "two rows of four, as the key is shown"


def test_a_wrong_key_does_not_verify_anything():
    check = _load("_check_typed_key")
    s = _typing_self("ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789",
                     "0000-0000-0000-0000-0000-0000-0000-0000")
    check(s)
    assert not s._state.recovery_key_verified
    assert "not the key that was shown" in s._key_status.text


def test_a_near_miss_says_WHICH_group_is_wrong():
    """One wrong character in 32 used to produce "that is not the key", which
    gives the operator no method: they re-read all eight groups, find nothing,
    and conclude the medic is broken (bench, 2026-08-19 — two characters were
    wrong on a page of 32 and the screen would not say where)."""
    shown = "H4AH-ZHEC-2GMX-9Q3T-7XRG-JF3C-JA90-GV0W"
    check = _load("_check_typed_key")
    s = _typing_self(shown, "H4AM-ZHEC-2GMX-9Q3T-7XRG-5F3C-JA90-GV0W")
    check(s)
    assert not s._state.recovery_key_verified
    assert "groups 1, 6" in s._key_status.text, s._key_status.text


def test_a_single_wrong_group_is_named_in_the_singular():
    shown = "H4AH-ZHEC-2GMX-9Q3T-7XRG-JF3C-JA90-GV0W"
    check = _load("_check_typed_key")
    s = _typing_self(shown, "H4AH-ZHEC-2GMX-9Q3T-7XRG-JF3C-JA90-GV0X")
    check(s)
    assert "group 8 does not match" in s._key_status.text, s._key_status.text


def test_the_wrong_groups_are_the_ones_tinted():
    shown = "H4AH-ZHEC-2GMX-9Q3T-7XRG-JF3C-JA90-GV0W"
    s = _typing_self(shown, "H4AM-ZHEC-2GMX-9Q3T-7XRG-5F3C-JA90-GV0W")
    assert s._mark_wrong_groups() == [1, 6]


def test_a_handwriting_fold_is_never_called_a_wrong_group():
    """normalize forgives O for 0 across the whole key; the per-group marker
    must forgive it too, or it would point at a group that is actually right."""
    shown = "H4AH-ZHEC-2GMX-9Q3T-7XRG-JF3C-JA90-GV0W"
    s = _typing_self(shown, "H4AH ZHEC 2GMX 9Q3T 7XRG JF3C JA9O GVOW")
    assert s._mark_wrong_groups() == []


def test_a_handwritten_key_still_gets_the_operator_in():
    """The key is read off PAPER. A handwritten I is a 1 and an O is a 0, and
    recovery_key.normalize already folds them. Refusing a correctly-written key
    over a letter shape would teach the operator that their paper copy is wrong,
    which is the most expensive wrong lesson this screen could teach."""
    shown = "1111-0000-2222-3333-4444-5555-6666-7777"
    check = _load("_check_typed_key")
    s = _typing_self(shown, "IIII oooo 2222 3333 4444 5555 6666 7777".lower())
    check(s)
    assert s._state.recovery_key_verified, s._key_status.text


def test_a_half_typed_key_is_encouragement_not_a_refusal():
    """Live-checking every keystroke means most of what the operator sees is a
    partial key. Calling that "wrong" thirty times while they type teaches them
    to ignore the message that finally matters."""
    check = _load("_check_typed_key")
    s = _typing_self("ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789", "ABCD-EFGH")
    check(s)
    assert "Keep going" in s._key_status.text
    assert not s._state.recovery_key_verified


def test_an_empty_field_says_nothing_at_all():
    check = _load("_check_typed_key")
    s = _typing_self("ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789", "")
    check(s)
    assert s._key_status.text == ""


def test_the_right_key_is_the_only_thing_that_verifies():
    shown = recovery_key.generate()
    check = _load("_check_typed_key")
    s = _typing_self(shown, shown)
    check(s)
    assert s._state.recovery_key_verified


# --------------------------------------------------------------------------- #
# The pattern, drawn twice.
# --------------------------------------------------------------------------- #

def _pattern_self(first=None):
    return _self(_pattern_first=first, _pattern_status=_Label(),
                 _pad=types.SimpleNamespace(clear=lambda: None), _pattern="")


def test_a_too_short_first_drawing_is_refused_before_it_is_remembered():
    """If it were stored, the confirmation would compare against a pattern the
    model will reject anyway — and the operator would be told the two do not
    match, which is not the problem."""
    drawn = _load("_pattern_drawn")
    s = _pattern_self()
    drawn(s, [0, 1, 2])
    assert s._pattern_first is None
    assert "at least" in s._pattern_status.text


def test_the_same_too_short_pattern_twice_is_still_refused():
    """Agreement is not the only condition, and it is the one that is easy to
    write an equality check for and stop there."""
    with pytest.raises(FactorError):
        confirm_pattern([0, 1, 2], [0, 1, 2])


def test_a_mismatch_throws_away_BOTH_drawings():
    """Keeping the first would have the operator confirming against a pattern
    they may have got wrong the first time and have no way to see. There is
    nothing on the screen afterwards to check it against — that is the entire
    reason this step is drawn twice."""
    drawn = _load("_pattern_drawn")
    s = _pattern_self()
    drawn(s, [0, 4, 8, 7])
    assert s._pattern_first == [0, 4, 8, 7]
    drawn(s, [0, 4, 8, 6])
    assert s._pattern_first is None, "the first drawing survived a mismatch"
    assert not s._state.pattern_set
    assert "Start again" in s._pattern_status.text


def test_two_matching_drawings_set_the_pattern():
    drawn = _load("_pattern_drawn")
    s = _pattern_self()
    drawn(s, [0, 4, 8, 7])
    drawn(s, [0, 4, 8, 7])
    assert s._state.pattern_set
    assert s._pattern == "0-4-8-7"


def test_a_reversed_pattern_is_not_a_match():
    drawn = _load("_pattern_drawn")
    s = _pattern_self()
    drawn(s, [0, 4, 8, 7])
    drawn(s, [7, 8, 4, 0])
    assert not s._state.pattern_set


def test_the_rules_are_not_reimplemented_in_the_widget_layer():
    """A widget that checked ``len(path) >= 4`` itself would be a second copy of
    a security rule, and the copy is the one that stops being updated."""
    src = func_source(SCREEN, "_pattern_drawn")
    assert "encode_pattern" in src and "confirm_pattern" in src
    body = src.split('"""')[2]          # past the docstring, which names the bug
    assert "len(path)" not in body
    assert "MIN_PATTERN" not in body


# --------------------------------------------------------------------------- #
# The passphrase, typed twice.
# --------------------------------------------------------------------------- #

def _pw_self(a, b):
    return _self(_pw1=_Field(a), _pw2=_Field(b), _pw_status=_Label(),
                 _passphrase="")


def test_a_mismatched_passphrase_clears_only_the_second_field():
    """Wiping both means re-typing something that was probably right, on the
    touchscreen keypad the typo came from in the first place."""
    setp = _load("_set_passphrase")
    s = _pw_self("correct horse battery", "correct hors battery")
    setp(s)
    assert not s._state.passphrase_set
    assert s._pw1.text == "correct horse battery"
    assert s._pw2.text == ""


def test_an_empty_passphrase_is_refused():
    setp = _load("_set_passphrase")
    s = _pw_self("", "")
    setp(s)
    assert not s._state.passphrase_set
    assert s._pw_status.text


def test_a_matching_pair_is_taken_verbatim():
    """Not stripped, not case-folded. Whatever the operator typed is what the
    unlock screen will have to reproduce, and a wizard that quietly trimmed a
    trailing space would set a passphrase nobody can enter."""
    setp = _load("_set_passphrase")
    s = _pw_self("  two spaces  ", "  two spaces  ")
    setp(s)
    assert s._state.passphrase_set
    assert s._passphrase == "  two spaces  "


# --------------------------------------------------------------------------- #
# The gate, on the way in to a step.
# --------------------------------------------------------------------------- #

def test_a_blocked_step_is_refused_and_the_reason_is_SHOWN(security_half):
    """A wizard that silently stayed put looks like a dead button — the single
    hardest fault for an operator to report, and the one they blame themselves
    for."""
    goto = _load("_goto")
    steps = sf.setup_steps(SetupState())
    s = _self(_steps=lambda: steps, _i=0)
    goto(s, [x["key"] for x in steps].index(sf.PASSPHRASE_STEP))
    assert s._i == 0, "walked onto a step whose precondition had gone"
    assert "recovery key" in s._notice.lower()


def test_the_gate_is_asked_of_the_step_not_of_a_button():
    """A wizard has more ways forward than its own Next: a resume, a Back and
    two Nexts, a screen that advances itself. Every one bypasses a check that
    lives on a widget."""
    src = func_source(SCREEN, "_goto")
    assert "sf.blocked_reason" in src


def test_a_permitted_step_clears_the_last_refusal():
    goto = _load("_goto")
    steps = sf.setup_steps(SetupState())
    s = _self(_steps=lambda: steps, _i=0, _notice="something old")
    goto(s, 1)
    assert s._i == 1 and s._notice == ""


# --------------------------------------------------------------------------- #
# What actually gets applied, and when.
# --------------------------------------------------------------------------- #

def _apply_self(state, enrol=None, saved=True):
    calls = []

    def save(policy):
        calls.append(policy)
        return saved
    fn = _load("_apply", save_policy=save)
    s = _self(_state=state, _applied=False, _applied_msg="", _enrol_fn=enrol,
              _pattern="0-4-8-7", _passphrase="pw", _keyfile_secret="kf" * 32,
              _recovery="KEY")

    def forget():
        s._pattern = s._passphrase = s._keyfile_secret = s._recovery = ""
    s._forget_secrets = forget
    return fn, s, calls


def test_an_abandoned_ceremony_applies_nothing():
    """A ceremony stopped in the middle must leave the medic exactly as it was.
    Applying a passphrase at step five and a policy at step six would leave a
    medic that half-locks, whose operator does not know which half."""
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True,
                       level=Policy((PATTERN, PASSPHRASE)))   # no pattern drawn
    fn, s, saved = _apply_self(state)
    fn(s)
    assert saved == [], "a policy was written for a half-finished setup"
    assert "not finished" in s._applied_msg


def test_a_finished_ceremony_records_the_policy():
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, pattern_set=True,
                       level=Policy((PATTERN, PASSPHRASE)))
    fn, s, saved = _apply_self(state)
    fn(s)
    assert [p.ordered for p in saved] == [(PATTERN, PASSPHRASE)]


def test_applying_twice_does_not_enrol_twice():
    """The summary is reachable a second time by stepping Back and forward, and
    a repeated enrolment burns another LUKS keyslot for the same passphrase."""
    seen = []
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, pattern_set=True,
                       level=Policy((PATTERN, PASSPHRASE)))
    fn, s, saved = _apply_self(
        state, enrol=lambda parts, pol, key: (seen.append(pol) or (True, "ok")))
    fn(s)
    fn(s)
    assert len(seen) == 1 and len(saved) == 1


def test_only_the_factors_the_level_asks_for_are_handed_over():
    """combine() refuses a factor that is not in the policy — loudly, and
    rightly. Handing it a pattern the operator never chose would fail at the one
    moment there is nothing left to do about it."""
    seen = {}
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, level=Policy((PASSPHRASE,)))
    fn, s, _saved = _apply_self(
        state, enrol=lambda parts, pol, key: (seen.update(parts) or (True, "")))
    fn(s)
    assert set(seen) == {PASSPHRASE}


def test_the_strongest_level_hands_over_all_three():
    seen = {}
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, pattern_set=True, keyfile_set=True,
                       level=Policy((PATTERN, PASSPHRASE, KEYFILE)))
    fn, s, _saved = _apply_self(
        state, enrol=lambda parts, pol, key: (seen.update(parts) or (True, "")))
    fn(s)
    assert set(seen) == {PATTERN, PASSPHRASE, KEYFILE}
    # and every one of them derives a secret rather than being empty
    from provisioning.vault_factors import combine
    assert combine(seen, Policy((PATTERN, PASSPHRASE, KEYFILE)))


def test_with_no_enrol_fn_the_screen_claims_only_what_it_did():
    """[[encrypt-at-rest]] has never been enabled on a real medic. With nothing
    wired behind it, the honest report is that a CHOICE was recorded — not that
    anything was protected."""
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, level=Policy((PASSPHRASE,)))
    fn, s, _saved = _apply_self(state)
    fn(s)
    assert s._applied_msg == "Your choice is recorded."


def test_a_policy_that_could_not_be_written_is_not_reported_as_written():
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, level=Policy((PASSPHRASE,)))
    fn, s, _saved = _apply_self(state, saved=False)
    fn(s)
    assert "could NOT be written" in s._applied_msg


def test_an_enrolment_that_throws_is_reported_not_swallowed():
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, level=Policy((PASSPHRASE,)))

    def boom(*_a):
        raise RuntimeError("cryptsetup is not installed")
    fn, s, _saved = _apply_self(state, enrol=boom)
    fn(s)
    assert "cryptsetup is not installed" in s._applied_msg


# --------------------------------------------------------------------------- #
# The USB step.
# --------------------------------------------------------------------------- #

def test_seeing_a_stick_never_writes_to_it():
    """This is the operator's own USB stick and the medic has no idea what else
    is on it. Detect automatically, destroy only on request — the same line the
    guided birth draws around an SD card."""
    src = func_source(SCREEN, "_sticks_seen")
    for destructive in ("write_key", "_write_stick(", "open("):
        assert destructive not in src, f"{destructive!r} fires on merely seeing a stick"


def test_the_write_button_is_dark_until_there_is_one_stick_to_write_to():
    """Two mounted volumes is not a stick to write to — it is a question. The
    medic choosing between them means a key written to a device the operator was
    not thinking about."""
    from provisioning import usb_key
    seen = _load("_sticks_seen", usb_key=usb_key)
    enabled = []
    cur = types.SimpleNamespace(set_next_enabled=enabled.append)
    s = _self(_stick_status=_Label(), _current=cur, _stick_dirs=[])
    seen(s, [])
    seen(s, ["/media/pi/A"])
    seen(s, ["/media/pi/A", "/media/pi/B"])
    assert enabled == [False, True, False]


def test_a_stick_step_rendered_before_any_poll_does_not_explode():
    """The poll runs on a background thread and lands through the Clock, so it
    can arrive after the operator has walked off the step."""
    from provisioning import usb_key
    seen = _load("_sticks_seen", usb_key=usb_key)
    s = _self(_stick_status=None)
    seen(s, ["/media/pi/A"])          # must simply return


def test_a_failed_write_lets_them_try_another_stick():
    """The refusal for an existing key file names 'use a different stick' as the
    way out. If the poll stayed stopped there would be no way to take it."""
    written = _load("_stick_written")
    restarted = []
    s = _self(_stick_status=_Label(), _start_stick_poll=lambda: restarted.append(1),
              _keyfile_secret="")
    written(s, False, "", "There is already a nodemedic.key on this stick.")
    assert restarted == [1]
    assert not s._state.keyfile_set


def test_a_successful_write_records_the_secret_not_the_bytes():
    """keyfile_secret is a hash of the whole file. Holding the raw bytes on the
    screen object would put the USB key's contents in a core dump."""
    written = _load("_stick_written")
    s = _self(_stick_status=_Label(), _keyfile_secret="",
              _start_stick_poll=lambda: None)
    written(s, True, "a" * 64, "Key written and read back: /media/pi/A/nodemedic.key")
    assert s._state.keyfile_set
    assert s._keyfile_secret == "a" * 64
    src = func_source(SCREEN, "_write_stick")
    assert "keyfile_secret(data)" in src, "raw key bytes must not be kept"


# --------------------------------------------------------------------------- #
# Finishing, and the marker.
# --------------------------------------------------------------------------- #

def test_the_marker_is_written_only_at_the_end(tmp_path):
    """Writing it as each step completed would mean a medic closed halfway
    through recorded itself as set up, and the rest would never be offered."""
    from provisioning import first_use
    src = func_source(SCREEN, "_finish")
    assert "mark_completed" in src
    for other in ("_key_written", "_set_passphrase", "_choose_level",
                  "_pattern_drawn", "_apply"):
        assert "mark_completed" not in func_source(SCREEN, other), \
            f"{other} records the setup as finished"


def test_finishing_a_half_done_ceremony_records_it_as_skipped(tmp_path, security_half):
    from provisioning import first_use
    fin = _load("_finish", first_use=first_use)
    path = str(tmp_path / "m.json")
    s = _self(_state=SetupState(), _marker_path=path,
              _stop_stick_poll=lambda: None, _forget_secrets=lambda: None,
              _on_finish=None)
    fin(s)
    assert first_use.load(path).completed
    assert first_use.load(path).security_skipped


def test_finishing_a_complete_ceremony_does_not_record_it_as_skipped(tmp_path):
    from provisioning import first_use
    fin = _load("_finish", first_use=first_use)
    path = str(tmp_path / "m.json")
    s = _self(_state=SetupState(recovery_key_shown=True,
                                recovery_key_verified=True, passphrase_set=True,
                                level=Policy((PASSPHRASE,))),
              _marker_path=path, _stop_stick_poll=lambda: None,
              _forget_secrets=lambda: None, _on_finish=None)
    fin(s)
    assert not first_use.load(path).security_skipped


def test_the_factors_are_dropped_the_moment_they_have_been_handed_over():
    """The tour runs for another eight screens after the summary, and the object
    stays alive for the whole session because Settings can re-open it."""
    state = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, pattern_set=True,
                       level=Policy((PATTERN, PASSPHRASE)))
    fn, s, _saved = _apply_self(state, enrol=lambda *a: (True, "done"))
    fn(s)
    assert s._passphrase == "" and s._pattern == "" and s._recovery == ""


def test_an_abandoned_ceremony_drops_them_too():
    """The half-typed passphrase of a walkthrough somebody gave up on is exactly
    as worth keeping out of memory as a finished one."""
    fn, s, _saved = _apply_self(SetupState())
    fn(s)
    assert s._passphrase == "" and s._recovery == ""


def test_the_secrets_do_not_outlive_the_ceremony():
    """A wizard sitting on the screen stack with a passphrase in an attribute is
    a passphrase in a core dump — and this screen stays alive for the whole
    session, because Settings can re-open it."""
    forget = func_source(SCREEN, "_forget_secrets")
    for attr in ("_passphrase", "_recovery", "_pattern", "_keyfile_secret"):
        assert attr in forget, f"{attr} survives the walkthrough"
    assert "_forget_secrets" in func_source(SCREEN, "_finish")
    assert "_forget_secrets" in func_source(SCREEN, "reset")


def test_a_rerun_starts_from_nothing():
    """The Settings entry exists for a medic being handed to somebody else.
    Showing THEM a summary of the previous operator's choices is the one thing
    that entry must never do."""
    reset = func_source(SCREEN, "reset")
    assert "sf.SetupState()" in reset
    assert "self._i = 0" in reset
    assert "_applied = False" in reset, "the last operator's enrolment sticks"


# --------------------------------------------------------------------------- #
# Where a boot lands.
# --------------------------------------------------------------------------- #

def test_a_forced_start_screen_still_wins(monkeypatch):
    """RNM_START is how every bench session, screenshot and preview reaches a
    screen directly. A first-use check that overrode it would silently swallow
    RNM_START on any developer machine whose home has no marker — all of them."""
    import os
    opening = _load2("ui/app.py", "_opening_screen")
    monkeypatch.setenv("RNM_START", "vitals")
    assert opening(None) == "vitals"


def test_a_never_set_up_medic_opens_on_the_walkthrough(monkeypatch, tmp_path):
    from provisioning import first_use
    monkeypatch.delenv("RNM_START", raising=False)
    # Patch the FUNCTION, not MARKER_PATH: the default argument was bound to
    # the constant at import, so a patched constant would leave the real
    # ~/.reticulum-node-medic marker deciding the test's answer.
    path = str(tmp_path / "m.json")
    monkeypatch.setattr(first_use, "is_first_use",
                        lambda *a, **k: first_use.load(path).completed is False)
    opening = _load2("ui/app.py", "_opening_screen")
    assert opening(None) == "setup"
    first_use.mark_completed(False, path=path)
    assert opening(None) == "home"


def test_an_unreadable_config_directory_still_boots(monkeypatch):
    """Refusing to start because a marker file is unreadable would be a field
    tool bricking itself over a settings file."""
    import os
    from provisioning import first_use
    monkeypatch.delenv("RNM_START", raising=False)
    monkeypatch.setattr(first_use, "is_first_use",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    opening = _load2("ui/app.py", "_opening_screen")
    assert opening(None) == "home"


def _load2(path, name, **extra):
    import os
    ns = {"os": os}
    ns.update(extra)
    exec(compile(textwrap.dedent(func_source(path, name)), path, "exec"), ns)
    return ns[name]


def test_the_walkthrough_is_reset_when_it_is_opened(monkeypatch):
    """Reset before the transition, like the imager: resetting after the slide
    had started put the previous run's screen up for a beat mid-transition."""
    src = func_source("ui/app.py", "switch_mode")
    block = src[src.index('if mode_name == "setup":'):]
    block = block[:block.index("self.sm.current = mode_name")]
    assert "reset(" in block             # reset(resume=...) since the radio hand-off
    assert src.index('if mode_name == "setup":') < src.index("self.sm.current = mode_name")


def test_the_walkthrough_does_not_wear_the_back_to_home_chrome():
    """That chrome's Back goes HOME, and home is the one place this walkthrough
    must not offer as an escape from its first screen: the operator has not seen
    the front page yet and would not know what they were skipping."""
    from tests.srcutil import src
    text = src("ui/app.py")
    block = text[text.index('setup = Screen(name="setup")'):]
    block = block[:block.index('self.sm.add_widget(setup)')]
    assert "_with_back" not in block


def test_the_summary_asks_the_disk_rather_than_the_wizard():
    """vault_exists_fn is answered by the app looking at the card. A summary
    that inferred it from the operator's own choices would report their
    intentions back to them as facts."""
    src = func_source("ui/app.py", "_vault_exists")
    # the per-file records vault that Settings actually builds — not the dead
    # LUKS container, which said NOT ENCRYPTED over encrypted records (2026-10-03)
    assert "is_vault(records_root())" in src and "CONTAINER_PATH" not in src
    assert "return False" in src, "a failure to look must not read as yes"


def test_every_level_card_carries_the_way_back_in():
    """The recovery-key sentence is printed on ALL THREE cards, not just the
    ones with a pattern.

    It used to be gated on PATTERN, and correctly so: ``describe()``'s fallback
    then ended "forget the pattern and you are inconvenienced, not locked out",
    and on the passphrase-only card there was no pattern to forget. The model
    dropped its convenience door on 2026-08-11 ("the passphrase is a factor,
    not a back door"), so the sentence is now about the recovery key and reads
    identically on every level — and the card the gate used to skip is exactly
    the one where "there is no back door" is load-bearing.

    Guarded from BOTH ends, because either alone fails open: the model must
    still be saying one thing on every level, and the screen must still be
    printing it unconditionally.
    """
    from provisioning.vault_factors import LEVELS, describe
    fallbacks = {describe(p)["fallback"] for p in LEVELS}
    assert len(fallbacks) == 1, \
        "the fallback sentence differs per level again — the gate may be back"
    assert "recovery key" in fallbacks.pop().lower()

    src = func_source(SCREEN, "_level_card")

    def indent_of(needle):
        line = next(ln for ln in src.splitlines() if needle in ln)
        return len(line) - len(line.lstrip())

    # Same nesting as the unconditional "field" line above it. A re-added
    # ``if`` would indent it one level deeper.
    assert indent_of('d["fallback"]') == indent_of('d["field"]'), \
        "the fallback line is nested under a condition again"


def test_finishing_the_tour_records_nothing_as_skipped_while_the_lock_half_is_off(tmp_path):
    """v1 offers no lock steps, so nothing was skipped — Settings must not keep
    an amber 'nobody has chosen how this medic locks itself' line for a choice
    that was never put to the operator (readiness ledger #174)."""
    from provisioning import first_use
    fin = _load("_finish", first_use=first_use)
    path = str(tmp_path / "m.json")
    s = _self(_state=SetupState(), _marker_path=path,
              _stop_stick_poll=lambda: None, _forget_secrets=lambda: None,
              _on_finish=None)
    fin(s)
    assert first_use.load(path).completed
    assert not first_use.load(path).security_skipped
