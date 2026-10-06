"""First-use setup — the ORDER, and the ways round it. Pure, Kivy-free.

Almost nothing here tests that the wizard works when it is used correctly. The
happy path is one straight line and it was never the risk. The risk is the
operator who taps Yes through the write-it-down ceremony without a pen, the one
who picks the strongest level and walks out of the pattern screen, and the one
who reopens a half-finished setup a week later — and every one of those ends in
a medic that cannot be opened by the person who owns it.
"""

import re

import pytest

from provisioning.vault_factors import (KEYFILE, LEVELS, PASSPHRASE, PATTERN,
                                        Policy, FactorError)
from ui import setup_flow as sf

from ui.setup_flow import SetupState, advance
#: An honest "your records are NOT protected" statement, matched by MEANING, not
#: one exact phrase — a negation word ("not", "no", "never", "nothing",
#: "isn't"...) sitting close before a protection word (encrypt / lock /
#: protect). This lets the not-encrypted copy be reworded freely (e.g. "NOT
#: LOCKED YET", "records are not encrypted", "nothing is protected") as long as
#: it keeps negating protection — instead of the guard being hostage to the
#: literal string "NOT encrypted".
_NEGATED_PROTECTION = re.compile(
    r"\b(not|no|never|nothing|isn'?t|aren'?t|without)\b[^.]{0,40}?"
    r"\b(encrypt|lock|protect)",
    re.IGNORECASE)


def _says_not_protected(text: str) -> bool:
    return bool(_NEGATED_PROTECTION.search(text))


def _after_recovery():
    return SetupState(recovery_key_shown=True, recovery_key_verified=True)


def _ready(level=None):
    """Everything the model needs before a level may be chosen."""
    return SetupState(recovery_key_shown=True, recovery_key_verified=True,
                      passphrase_set=True, level=level)


# --------------------------------------------------------------------------- #
# Written down is not the same as shown.
# --------------------------------------------------------------------------- #


@pytest.fixture
def security_half(monkeypatch):
    """The lock-your-records half is OFF in v1 (``setup_flow.SECURITY_HALF``,
    readiness ledger #174); the tests that take this fixture describe it for
    the release that turns it back on."""
    monkeypatch.setattr(sf, "SECURITY_HALF", True)

def test_showing_the_key_is_not_enough_to_move_on():
    """The recovery-key ceremony asks three times whether it has been written
    down, and an operator can answer Yes to all three while looking at a screen
    they will never see again. From the medic's side "shown" and "written down"
    are the same event. Typing it back is the only evidence that can exist."""
    shown = SetupState(recovery_key_shown=True)
    assert sf.blocked_reason(sf.PASSPHRASE_STEP, shown)
    assert "type the recovery key back" in \
        sf.blocked_reason(sf.PASSPHRASE_STEP, shown).lower()


def test_the_type_it_back_step_cannot_be_reached_before_a_key_exists():
    """Otherwise a Back-then-Next pair lands on a field with nothing to check
    against, and whatever is typed there matches nothing."""
    assert sf.blocked_reason(sf.RECOVERY_KEY_BACK, SetupState())


def test_no_level_may_be_chosen_until_the_key_has_been_typed_back():
    """can_select cannot see this: it is handed a generated key and a
    written-down key as the same boolean, because inside the model they ARE the
    same boolean."""
    almost = SetupState(recovery_key_shown=True, passphrase_set=True)
    for policy, ok, why in sf.offered_levels(almost):
        assert not ok, f"{policy.ordered} was selectable without the key back"
        assert "recovery key" in why.lower()


def test_typing_it_back_unlocks_every_offered_level():
    for _policy, ok, why in sf.offered_levels(_ready()):
        assert ok, why


# --------------------------------------------------------------------------- #
# The passphrase comes before the pattern, and the model's rule is not restated.
# --------------------------------------------------------------------------- #

def test_a_pattern_level_is_refused_before_a_passphrase_exists():
    """Operator's rule, 2026-08-11: they need to set a text password first
    before turning security to pattern, so they can recover the medic if they
    forget either."""
    ok, why = sf.can_choose(Policy((PATTERN, PASSPHRASE)), _after_recovery())
    assert not ok and "passphrase" in why.lower()


def test_the_wizard_does_not_reimplement_the_models_rule():
    """can_choose must DEFER to can_select and add only what the model cannot
    see. Two copies of a security rule is two rules, and the copy is the one
    that stops being updated."""
    from tests.srcutil import func_source
    src = func_source("ui/setup_flow.py", "can_choose")
    assert "can_select(policy, state.enrolment)" in src


def test_the_level_screen_itself_is_gated_on_the_passphrase():
    """Not just the individual options. A chooser where all three options refuse
    is a screen that only says no, and the operator cannot tell whether the
    problem is the option or themselves."""
    assert sf.blocked_reason(sf.LEVEL, _after_recovery())
    assert not sf.blocked_reason(sf.LEVEL, _ready())


# --------------------------------------------------------------------------- #
# The pattern is drawn twice, and agreement alone is not enough.
# --------------------------------------------------------------------------- #

def test_the_flow_leaves_the_two_drawings_rule_where_it_already_lives():
    """confirm_pattern owns it. A wizard that compared the two lists itself
    would accept the same four-dot L twice, because agreement is not the only
    condition — length is the other one, and it is easy to forget when you are
    writing an equality check."""
    from provisioning.vault_factors import confirm_pattern
    with pytest.raises(FactorError):
        confirm_pattern([0, 1, 2], [0, 1, 2])         # agreed, still too short


def test_the_pattern_step_vanishes_for_a_level_that_has_no_pattern():
    """A screen that promises work it will never ask for."""
    keys = [s["key"] for s in sf.setup_steps(_ready(Policy((PASSPHRASE,))))]
    assert sf.PATTERN_STEP not in keys
    assert sf.KEYFILE_STEP not in keys


def test_the_pattern_step_is_present_before_a_level_has_been_chosen(security_half):
    """Fail-open. Dropping it while the answer is unknown would leave the flow
    with no way to set the thing the next screen is about to make them pick."""
    keys = [s["key"] for s in sf.setup_steps(SetupState())]
    assert sf.PATTERN_STEP in keys and sf.KEYFILE_STEP in keys


def test_a_pattern_step_reached_for_a_passphrase_only_level_refuses():
    st = _ready(Policy((PASSPHRASE,)))
    assert sf.blocked_reason(sf.PATTERN_STEP, st)


# --------------------------------------------------------------------------- #
# The USB key is last, and for a reason.
# --------------------------------------------------------------------------- #

def test_the_stick_is_asked_for_only_after_the_pattern_is_settled():
    """The stick step is the only one that can fail for a reason outside the
    medic — no stick, unwritable stick, wrong stick. Everything settleable on
    the glass gets settled first, so a failure there is the ONLY thing
    outstanding rather than one of three."""
    st = _ready(Policy((PATTERN, PASSPHRASE, KEYFILE)))
    assert sf.blocked_reason(sf.KEYFILE_STEP, st) == "Draw your pattern first."
    assert not sf.blocked_reason(sf.KEYFILE_STEP,
                                 advance(st, pattern_set=True))


def test_seeing_a_stick_does_not_write_to_it(security_half):
    """The medic notices the stick by itself — it has to, or the operator is
    pressing a button to ask whether their own hardware is plugged in. But this
    is their USB stick and the medic has no idea what else is on it, so the
    WRITE stays a deliberate press. The same line the guided birth draws around
    an SD card: detect automatically, destroy only on request."""
    step = sf.step_for(sf.KEYFILE_STEP, SetupState())
    assert not step.get("self_advancing")
    assert "Write the key" in step["next"]


def test_the_stick_step_warns_that_one_stick_is_not_enough(security_half):
    """Lose the only one and the passphrase is carrying the whole vault by
    itself — which is exactly the thing the operator chose the stick to avoid."""
    step = sf.step_for(sf.KEYFILE_STEP, SetupState())
    assert "second stick" in step["warning"]


# --------------------------------------------------------------------------- #
# The summary cannot be reached by walking away.
# --------------------------------------------------------------------------- #

def test_choosing_the_strongest_level_and_leaving_does_not_count_as_done():
    """The state looks finished from every angle except the one that matters:
    a level is chosen, a passphrase exists, the recovery key is verified — and
    the pattern the level asks for was never drawn."""
    st = _ready(Policy((PATTERN, PASSPHRASE, KEYFILE)))
    assert not st.factors_ready
    assert sf.blocked_reason(sf.SECURITY_SUMMARY, st)
    st = advance(st, pattern_set=True)
    assert sf.blocked_reason(sf.SECURITY_SUMMARY, st), "the stick is still owed"
    assert not sf.blocked_reason(sf.SECURITY_SUMMARY,
                                 advance(st, keyfile_set=True))


def test_a_deliberately_skipped_setup_still_reaches_its_summary():
    """Skipping is allowed — a field operator with a dying node will work around
    a medic that refuses to be useful. But it reaches a summary that SAYS it was
    skipped, rather than one that cannot be reached at all."""
    st = SetupState(security_skipped=True)
    assert not sf.blocked_reason(sf.SECURITY_SUMMARY, st)


# --------------------------------------------------------------------------- #
# Resuming a half-finished setup.
# --------------------------------------------------------------------------- #

def test_resume_never_lands_on_a_step_that_will_refuse_it():
    """A medic closed mid-ceremony and reopened a week later must not drop the
    operator onto a gate. Walked over every reachable state, not one of them."""
    seen = 0
    for shown in (False, True):
        for verified in (False, True):
            for pw in (False, True):
                for level in (None,) + LEVELS:
                    for pat in (False, True):
                        for kf in (False, True):
                            st = SetupState(recovery_key_shown=shown,
                                            recovery_key_verified=verified,
                                            passphrase_set=pw, level=level,
                                            pattern_set=pat, keyfile_set=kf)
                            key = sf.first_incomplete(st)
                            assert not sf.blocked_reason(key, st), \
                                f"{key} refuses the state that resumed to it"
                            seen += 1
    assert seen > 100


def test_a_fresh_medic_resumes_at_the_very_beginning(security_half):
    assert sf.first_incomplete(SetupState()) == sf.WELCOME


def test_a_finished_security_half_resumes_into_the_tour(security_half):
    st = _ready(Policy((PASSPHRASE,)))
    assert sf.first_incomplete(st) == sf.SECURITY_SUMMARY
    # ...and once that has been read there is nothing left but the tour
    assert sf.first_incomplete(advance(st, security_skipped=True)) \
        in (sf.SECURITY_SUMMARY, sf.TOUR_BIRTH)


# --------------------------------------------------------------------------- #
# What is offered, and what is not.
# --------------------------------------------------------------------------- #

def test_every_offered_level_stands_behind_an_enrolled_passphrase():
    """2026-08-11 said maximum strength; 2026-08-25, living with the medic
    day to day, the operator reversed the DAILY half: "pattern OR passphrase
    OR USB". What this now guards is the survivor of the first ruling: no
    level is selectable before the passphrase is SET — it stays enrolled as
    the way back in behind whatever the light daily door is — and the light
    rungs ARE offered, honest words attached."""
    offered = [p for p, _ok, _why in sf.offered_levels(_ready())]
    assert Policy((PATTERN,)) in offered          # the new ruling, on the menu
    from provisioning.vault_factors import Enrolment, can_select
    no_pass = Enrolment(passphrase_set=False, recovery_key_set=True,
                        recovery_key_verified=True)
    for policy, _ok, _why in sf.offered_levels(_ready()):
        ok, why = can_select(policy, no_pass)
        assert not ok, f"{policy.ordered} selectable without a passphrase set"


def test_pattern_only_reads_by_its_menu_name():
    """Pattern-only returned to the menu (2026-08-25), so it now carries its
    chooser name rather than the constructed fallback."""
    from provisioning.vault_factors import level_name
    assert level_name(Policy((PATTERN,))) == "Pattern only"


def test_the_levels_climb():
    from provisioning.vault_factors import strength_bits
    bits = [strength_bits(p) for p in LEVELS]
    assert bits == sorted(bits)


def test_no_level_is_hidden_just_because_it_is_not_yet_available():
    """A chooser that silently grows as the operator works leaves them never
    learning a stronger option existed."""
    assert len(sf.offered_levels(SetupState())) == len(LEVELS)
    for _p, ok, why in sf.offered_levels(SetupState()):
        assert not ok and why, "an unavailable level must say why"


# --------------------------------------------------------------------------- #
# What may go on a screen.
# --------------------------------------------------------------------------- #

def test_no_screen_promises_anything_it_cannot_check():
    """Standing rule: nothing is stated unless it is true NOW. Comfort language
    about security states a conclusion where the tool can only state a fact, and
    copy like that arrives one well-meaning edit at a time."""
    text = sf.prose().lower()
    for claim in sf.FORBIDDEN_CLAIMS:
        assert claim not in text, f"a setup screen says {claim!r}"


def test_the_summary_refuses_to_say_the_records_are_encrypted():
    """[[encrypt-at-rest]]: the container has been built and reviewed and has
    NEVER been enabled on a real medic. A summary congratulating the operator on
    protected records would be the tool lying about its own state, on the screen
    whose entire job is explaining that state."""
    st = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                    passphrase_set=True, pattern_set=True,
                    level=Policy((PATTERN, PASSPHRASE)))
    lines = sf.summary_lines(st, vault_exists=False)
    joined = " ".join(line for _ok, line in lines)
    # it must honestly NEGATE protection (any wording), and never claim a
    # container/encryption as a done (ok=True) fact.
    assert _says_not_protected(joined)
    assert not any(ok for ok, line in lines if "container" in line)


def test_the_summary_reports_the_disk_rather_than_assuming_it():
    """vault_exists is passed IN. A summary that inferred it from the wizard's
    own state would report the operator's intentions back to them as facts."""
    st = SetupState(recovery_key_shown=True, recovery_key_verified=True,
                    passphrase_set=True, level=Policy((PASSPHRASE,)))
    yes = " ".join(l for _o, l in sf.summary_lines(st, vault_exists=True))
    no = " ".join(l for _o, l in sf.summary_lines(st, vault_exists=False))
    assert yes != no
    # no-vault negates protection; a real vault does NOT carry that negation
    # (it states the container exists instead).
    assert _says_not_protected(no) and not _says_not_protected(yes)


def test_the_summary_names_what_was_missed_rather_than_going_quiet():
    st = SetupState()
    lines = dict((line, ok) for ok, line in sf.summary_lines(st, False))
    assert any("NOT confirmed" in l for l in lines)
    assert any("No passphrase set" in l for l in lines)
    assert any("No unlock method chosen" in l for l in lines)


def test_the_honesty_step_matches_what_the_vault_actually_moves(security_half):
    """Every sentence on that screen is read off provisioning/vault.py's own
    account of the trade the operator chose on 2026-08-02. If the set of
    relocated roots ever changes, the screen is wrong."""
    from provisioning import vault
    step = sf.step_for(sf.WHAT_IS_LOCKED, SetupState())
    assert "still boots" in step["body"] and "relays" in step["body"]
    # the mesh identity staying OUTSIDE is the load-bearing claim
    roots = " ".join(getattr(vault, "RECORDS_ROOTS", ()) or ())
    assert ".reticulum" not in roots.split(), \
        "the mesh identity moved into the vault — the screen now lies"


# --------------------------------------------------------------------------- #
# The steps themselves.
# --------------------------------------------------------------------------- #

def test_every_step_has_a_title_and_a_body():
    for s in sf.setup_steps(SetupState()):
        assert s["title"].strip()
        assert s["body"].strip() or s["key"] == sf.SECURITY_SUMMARY


def test_the_summary_body_is_written_from_state_not_from_the_flow(security_half):
    """It is the one screen whose words depend on what actually happened."""
    assert sf.step_for(sf.SECURITY_SUMMARY, SetupState())["body"] == ""


def test_a_self_advancing_step_offers_no_next_button():
    """Asked for outright, 2026-08-02: a button disabled until the moment it
    becomes redundant has never once been the thing that moved the operator
    forward, and it reads on every successful run as the tool waiting on them."""
    for s in sf.setup_steps(SetupState()):
        if s.get("self_advancing"):
            assert not s.get("next"), f"{s['key']} shows a Next it does not need"


def test_no_step_in_this_flow_takes_the_way_out_away():
    """Back is removed only for work that must not be interrupted, and nothing
    here writes a disk, resets a board or spends four minutes on a card."""
    for s in sf.setup_steps(SetupState()):
        assert not s.get("no_back"), s["key"]


def test_the_steps_are_copies():
    a = sf.setup_steps(SetupState())
    a[0]["title"] = "vandalised"
    assert sf.setup_steps(SetupState())[0]["title"] == "Set up this Node Medic"


def test_the_security_half_runs_in_the_order_that_prevents_a_lockout(security_half):
    """Left to themselves an operator sets the pattern first: it is the fun one,
    the one phones taught them, and the one that takes ten seconds. Every
    lockout this design exists to prevent starts there."""
    keys = [s["key"] for s in sf.setup_steps(SetupState())
            if s["part"] == sf.SECURITY]
    assert keys == [sf.WELCOME, sf.WHAT_IS_LOCKED, sf.RECOVERY_KEY,
                    sf.RECOVERY_KEY_BACK, sf.PASSPHRASE_STEP, sf.LEVEL,
                    sf.PATTERN_STEP, sf.KEYFILE_STEP, sf.SECURITY_SUMMARY]


# --------------------------------------------------------------------------- #
# The tour.
# --------------------------------------------------------------------------- #

def test_the_tour_covers_every_mode_the_medic_has():
    opens = {s.get("opens") for s in sf.setup_steps(SetupState())
             if s["part"] == sf.TOUR}
    for mode in ("vitals", "scan", "triage", "probe", "mitosis", "settings"):
        assert mode in opens, f"the tour never mentions {mode}"
    assert "birth_guide" in opens, "BIRTH's door is the guided walkthrough"


def test_the_tour_is_ordered_the_way_a_medic_is_used():
    """Not the poster's card order — that is a picture-composition decision.
    You build something, watch whether it lived, decide where the next goes, go
    and aim it, and fix it when it stops."""
    keys = [s["key"] for s in sf.setup_steps(SetupState())
            if s["part"] == sf.TOUR]
    assert keys.index(sf.TOUR_BIRTH) < keys.index(sf.TOUR_VITALS) \
        < keys.index(sf.TOUR_SCAN) < keys.index(sf.TOUR_TRIAGE)
    assert keys[-1] == sf.FINISH


def test_the_tour_says_what_a_mode_is_for_not_what_it_can_do():
    """A feature list is read by somebody who already knows what they want. This
    runs once, when the medic is new, and answers the question nobody asks out
    loud."""
    probe = sf.step_for(sf.TOUR_PROBE, SetupState())
    assert "Self Diagnose" in probe["body"], \
        "the medic diagnosing ITSELF is the half operators never find"
    mitosis = sf.step_for(sf.TOUR_MITOSIS, SetupState())
    assert "lasts exactly as long as that person stays" in mitosis["body"]


def test_the_tour_is_never_gated():
    """An operator who skipped the security half must still be able to learn
    what the tool does. Locking the explanation behind the ceremony punishes
    exactly the person who most needed the explanation."""
    for s in sf.setup_steps(SetupState()):
        if s["part"] == sf.TOUR:
            assert not sf.blocked_reason(s["key"], SetupState())


def test_a_tour_screen_shows_the_card_the_operator_will_actually_press():
    """[[show-dont-tell-ux]]. The picture is cropped out of the front page
    itself, so a card that moves in the artwork moves in the tour with it."""
    from ui.home_zones import card_rect
    for key in (sf.TOUR_BIRTH, sf.TOUR_VITALS, sf.TOUR_SCAN, sf.TOUR_TRIAGE):
        zone = sf.step_for(key, SetupState())["poster_card"]
        assert card_rect(zone) is not None, f"{key} points at no painted card"


def test_a_mode_with_no_painted_card_borrows_nobody_elses():
    """Showing a neighbouring card because something had to be shown is the
    wrong-picture failure the standing rule exists to stop."""
    from ui.home_zones import card_rect
    for key in (sf.TOUR_PROBE, sf.TOUR_MITOSIS, sf.TOUR_SETTINGS):
        assert not sf.step_for(key, SetupState()).get("poster_card")
    assert card_rect("probe") is None
    assert card_rect("mitosis") is None


def test_the_card_crop_reads_the_same_numbers_the_tap_map_does():
    """Computing the crop from its own copy of the geometry is how a tour ends
    up pointing confidently at the card next door."""
    from ui import home_zones as hz
    for i, zone in enumerate(hz.CARD_ORDER):
        left, top, w, _h = hz.card_rect(zone)
        centre_x = left + w / 2
        assert hz.zone_at(centre_x, top + 0.05) == zone
        assert i == hz.CARD_ORDER.index(zone)


# -- THE FIRSTBORN tour step (the medic births its own Tracker, 2026-08-25) ---

def test_firstborn_step_follows_birth_and_opens_its_screen():
    keys = [s["key"] for s in sf.setup_steps()]
    assert sf.TOUR_FIRSTBORN in keys
    # FIRST, ahead of the tour: finish building the medic, then learn to use it
    assert keys.index(sf.TOUR_FIRSTBORN) == keys.index(sf.TOUR_BIRTH) - 1
    assert keys.index(sf.TOUR_FIRSTBORN) == 1          # straight after the welcome
    step = sf.step_for(sf.TOUR_FIRSTBORN)
    assert step["opens"] == "firstborn"


def test_firstborn_is_optional_so_a_medic_with_no_tracker_can_skip():
    # a keeper without a Tracker on hand must not be trapped at this step
    assert sf.step_for(sf.TOUR_FIRSTBORN).get("optional") is True


def test_firstborn_warns_to_isolate_the_lookalike_radio():
    # the medic's own RNode is a look-alike ESP32-S3; the step must say so
    step = sf.step_for(sf.TOUR_FIRSTBORN)
    text = (step.get("hint", "") + step.get("body", "")).lower()
    assert ("nothing else" in text or "only" in text) and "radio" in text


# --------------------------------------------------------------------------- #
# v1: the tour alone (readiness ledger #174, the keeper's call 2026-10-05).
# --------------------------------------------------------------------------- #

def test_v1_walkthrough_is_the_tour_alone():
    """The lock steps collected secrets the summary never enrolled. Until they
    are wired to Settings ▸ Encrypt my records they are not shown at all."""
    assert sf.SECURITY_HALF is False
    steps = sf.setup_steps()
    keys = [s["key"] for s in steps]
    assert keys[0] == sf.WELCOME and keys[-1] == sf.FINISH
    assert all(s["part"] == sf.TOUR for s in steps)
    assert len(steps) == 1 + len(sf._TOUR_STEPS)
    for secret in (sf.RECOVERY_KEY, sf.RECOVERY_KEY_BACK, sf.PASSPHRASE_STEP,
                   sf.LEVEL, sf.PATTERN_STEP, sf.KEYFILE_STEP, sf.SECURITY_SUMMARY):
        assert secret not in keys


def test_v1_welcome_promises_only_what_follows():
    body = sf.setup_steps()[0]["body"].lower()
    assert "tour" in body
    for promise in ("recovery key", "passphrase", "lock"):
        assert promise not in body, promise


def test_the_lock_half_is_still_there_for_the_next_release(security_half):
    keys = [s["key"] for s in sf.setup_steps()]
    assert keys[:2] == [sf.WELCOME, sf.WHAT_IS_LOCKED]
    assert sf.SECURITY_SUMMARY in keys


def test_steps_are_translated_before_the_maps_sentence_is_filled_in():
    """The catalog key is the template with its {summary} placeholder, so the
    translation must run first and the carried-maps sentence go through the
    same translator."""
    steps = sf.setup_steps(translate=lambda text: "T:" + text)
    maps = sf.step_for(sf.TOUR_MAPS_DOWNLOAD)
    body = next(s["body"] for s in steps if s["key"] == sf.TOUR_MAPS_DOWNLOAD)
    assert body.startswith("T:")
    assert "T:This medic" in body, body          # the summary sentence, translated too
    assert "{summary}" not in body
    assert all(s["title"].startswith("T:") for s in steps)
    assert "{summary}" in maps["body"] or "{summary}" not in sf.step_for(
        sf.TOUR_MAPS_DOWNLOAD, SetupState())["body"]


def test_the_maps_sentence_keeps_its_placeholders_for_the_catalogs():
    sent = sf.maps_summary_sentence({"tiles": 12345, "zmin": 8, "zmax": 14, "terrain": False})
    assert sent == ("This medic already carries an area — 12,345 map tiles (zoom 8–14) "
                    "— but no terrain for it yet.")
    assert sf.maps_summary_sentence({"tiles": 0}) == sf._MAPS_NONE


def test_the_v1_walkthrough_speaks_every_shipped_language():
    """The first screens a keeper meets. Until 2026-10-05 none of these words
    went through tr() at all (readiness ledger #223); now every spoken field of
    the v1 walkthrough, the carried-maps sentence and the leave-the-tour modal
    must be in all eight catalogs, and the sentences must not simply be the
    English handed back."""
    import json
    spoken = set()
    for step in list(sf._TOUR_INTRO) + list(sf._TOUR_STEPS):   # raw templates
        for field in sf._SPOKEN_FIELDS:
            if step.get(field):
                spoken.add(step[field])
    spoken.update((sf._MAPS_NONE, sf._MAPS_ZOOM, sf._MAPS_TERRAIN, sf._MAPS_NO_TERRAIN))
    spoken.update(("Leave the tour? You can run it again whenever you like from Settings.",
                   "No — keep going", "Leave the tour"))
    for code in ("es", "fr", "de", "ja", "ru", "pl", "id", "sv"):
        cat = json.load(open(f"assets/i18n/{code}.json", encoding="utf-8"))
        missing = sorted(s for s in spoken if s not in cat)
        assert not missing, f"{code}.json lacks {len(missing)} walkthrough strings, e.g. {missing[0][:60]!r}"
        english = sorted(s for s in spoken if len(s) > 24 and cat[s] == s)
        assert not english, f"{code}.json hands back English for {english[0][:60]!r}"
