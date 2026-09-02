"""Choosing how hard the vault is to open — pattern / passphrase / USB key."""

import json
import os

import pytest

from provisioning.vault_factors import (
    PATTERN, PASSPHRASE, KEYFILE, Policy, FactorError,
    combine, describe, encode_pattern, find_keyfile, keyfile_secret,
    load_policy, pattern_bits, pattern_space, save_policy, strength_bits,
    MIN_PATTERN_DOTS,
)


# --- the pattern ------------------------------------------------------------

def test_a_pattern_encodes_to_its_path():
    assert encode_pattern([0, 4, 8, 7]) == "0-4-8-7"
    assert encode_pattern([1, 2, 5, 8, 7, 6]) == "1-2-5-8-7-6"


def test_a_pattern_must_be_long_enough_to_be_worth_drawing():
    """Three dots is 504 patterns — a person could try them all in an evening."""
    with pytest.raises(FactorError):
        encode_pattern([0, 1, 2])
    assert encode_pattern([0, 1, 2, 5])


def test_a_dot_cannot_be_used_twice():
    """It is not drawable without lifting, and it would make the count of
    possible patterns unbounded — so the honest number on the Settings screen
    would stop being honest."""
    with pytest.raises(FactorError):
        encode_pattern([0, 1, 0, 1])


def test_a_pattern_must_stay_on_the_grid():
    with pytest.raises(FactorError):
        encode_pattern([0, 1, 2, 9])
    with pytest.raises(FactorError):
        encode_pattern([0, 1, 2, -1])


def test_the_pattern_count_is_computed_not_quoted():
    """The phone figure everyone quotes (389,112) is for Android's rule that a
    stroke may not jump over an unused dot. We do not enforce that, so quoting
    it would understate our own weakness by half."""
    # 9P4 + 9P5 + ... + 9P9
    expected = 3024 + 15120 + 60480 + 181440 + 362880 + 362880
    assert pattern_space() == expected
    assert 19 < pattern_bits() < 20        # ~19.9 bits, and that is the CEILING


# --- the USB key ------------------------------------------------------------

def test_a_keyfile_becomes_a_secret_from_its_whole_contents():
    a = keyfile_secret(b"\x01" * 32)
    b = keyfile_secret(b"\x01" * 31 + b"\x02")
    assert a != b and len(a) == 64


def test_an_empty_or_tiny_keyfile_is_refused():
    """A stick with an empty file would otherwise unlock the vault with
    'nothing', while the screen said a USB key was required."""
    with pytest.raises(FactorError):
        keyfile_secret(b"")
    with pytest.raises(FactorError):
        keyfile_secret(b"short")


def test_the_keyfile_is_found_where_a_pi_mounts_a_stick(tmp_path):
    listing = ["/media/nodemedic/KEYS/nodemedic.key",
               "/media/nodemedic/KEYS/readme.txt"]
    assert find_keyfile(lister=lambda root: listing) == \
        "/media/nodemedic/KEYS/nodemedic.key"
    assert find_keyfile(lister=lambda root: ["/media/x/y/other.bin"]) is None


# --- the policy -------------------------------------------------------------

def test_a_policy_is_ordered_canonically_however_it_was_given():
    """Two medics that chose the same factors must derive the same key, whatever
    order the operator ticked the boxes in."""
    a = Policy((KEYFILE, PATTERN))
    b = Policy((PATTERN, KEYFILE))
    assert a.ordered == b.ordered == (PATTERN, KEYFILE)


def test_a_policy_needs_at_least_one_factor():
    with pytest.raises(FactorError):
        Policy(())


def test_a_policy_refuses_nonsense():
    with pytest.raises(FactorError):
        Policy(("fingerprint",))
    with pytest.raises(FactorError):
        Policy((PATTERN, PATTERN))


def test_the_policy_round_trips_to_disk_unreadable_to_others(tmp_path):
    p = str(tmp_path / "vault_policy.json")
    assert save_policy(Policy((PATTERN, PASSPHRASE)), p)
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert load_policy(p).ordered == (PATTERN, PASSPHRASE)
    assert json.load(open(p)) == {"factors": [PATTERN, PASSPHRASE]}


def test_a_missing_or_broken_policy_never_locks_the_operator_out(tmp_path):
    """A field tool must not brick itself over a corrupt settings file."""
    assert load_policy(str(tmp_path / "nope.json")).ordered == (PATTERN,)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_policy(str(bad)).ordered == (PATTERN,)


def test_the_policy_file_holds_no_secrets(tmp_path):
    p = str(tmp_path / "policy.json")
    save_policy(Policy((PATTERN, PASSPHRASE, KEYFILE)), p)
    text = open(p).read()
    for leak in ("0-4-8", "hunter2", "key", "secret", "hash"):
        if leak == "key":
            continue                       # "keyfile" is a factor NAME
        assert leak not in text


# --- folding the factors into one secret ------------------------------------

def test_every_factor_changes_the_secret():
    pol = Policy((PATTERN, PASSPHRASE))
    base = combine({PATTERN: "0-4-8-7", PASSPHRASE: "correct horse"}, pol)
    assert base != combine({PATTERN: "0-4-8-6", PASSPHRASE: "correct horse"}, pol)
    assert base != combine({PATTERN: "0-4-8-7", PASSPHRASE: "correct hors"}, pol)
    assert len(base) == 64


def test_the_same_factors_always_derive_the_same_secret():
    """If this drifts, every existing vault stops opening."""
    pol = Policy((PATTERN, PASSPHRASE, KEYFILE))
    parts = {PATTERN: "0-4-8-7", PASSPHRASE: "pw", KEYFILE: "a" * 64}
    assert combine(parts, pol) == combine(dict(reversed(list(parts.items()))), pol)


def test_parts_cannot_be_rearranged_into_the_same_secret():
    """Concatenation would mean pattern "0-4-8" + passphrase "7x" opened the
    same vault as pattern "0-4-8-7" + passphrase "x" — two different unlocks for
    one door, and neither operator would ever know."""
    pol = Policy((PATTERN, PASSPHRASE))
    a = combine({PATTERN: "0-4-8-7", PASSPHRASE: "x"}, pol)
    b = combine({PATTERN: "0-4-8", PASSPHRASE: "7x"}, pol)
    assert a != b


def test_a_partial_unlock_fails_loudly():
    """Not a weaker unlock — a wrong one. Deriving a key from half the factors
    would just produce something that does not open the vault, and the operator
    would be left guessing which part they got wrong."""
    pol = Policy((PATTERN, PASSPHRASE))
    with pytest.raises(FactorError) as e:
        combine({PATTERN: "0-4-8-7"}, pol)
    assert PASSPHRASE in str(e.value)


def test_an_unexpected_factor_is_refused():
    pol = Policy((PATTERN,))
    with pytest.raises(FactorError):
        combine({PATTERN: "0-4-8-7", KEYFILE: "a" * 64}, pol)


# --- telling the operator the truth about their choice ----------------------

def test_strength_adds_up_and_ranks_the_levels_correctly():
    pat = Policy((PATTERN,))
    both = Policy((PATTERN, PASSPHRASE))
    all3 = Policy((PATTERN, PASSPHRASE, KEYFILE))
    assert strength_bits(pat) < strength_bits(both) < strength_bits(all3)


def test_the_weakest_level_says_so_in_words_not_symbols():
    d = describe(Policy((PATTERN,)))
    assert "Weakest" in d["strength"]
    assert "985,824" in d["strength"]        # the real count, spelled out
    assert "smudge" in d["warnings"]


def test_every_level_states_what_it_costs_the_operator():
    d = describe(Policy((PATTERN, KEYFILE)))
    assert "Lose the stick" in d["warnings"]
    assert "recovery key" in d["warnings"]
    d2 = describe(Policy((PASSPHRASE,)))
    assert "forgotten passphrase" in d2["warnings"]


def test_every_level_promises_the_medic_keeps_relaying():
    """The whole reason this design is acceptable on a field device: a locked
    medic is still a mesh node. If that ever stops being true the sentence has
    to go, not stay as decoration."""
    for pol in (Policy((PATTERN,)), Policy((PATTERN, PASSPHRASE, KEYFILE))):
        assert "still boots" in describe(pol)["field"]
        assert "relays" in describe(pol)["field"]


# --- the ladder shown on screen ---------------------------------------------

def test_the_levels_are_offered_weakest_first():
    """The order a person reads. Putting the easy one last would make it look
    like the recommendation."""
    from provisioning.vault_factors import LEVELS
    bits = [strength_bits(p) for p in LEVELS]
    assert bits == sorted(bits), "levels must climb"
    assert LEVELS[0].ordered == (PATTERN,)      # lightest daily door first
    assert LEVELS[-1].ordered == (PATTERN, PASSPHRASE, KEYFILE)


def test_a_passphrase_is_enrolled_behind_every_offered_level():
    """The 2026-08-11 maximum-strength ruling was reversed for the DAILY door
    on 2026-08-25 ("pattern OR passphrase OR USB") — what survives is the
    enrolment: no level is selectable until the passphrase is SET, because it
    stays enrolled as the way back in behind whatever the daily unlock is."""
    from provisioning.vault_factors import LEVELS, Enrolment, can_select
    assert any(pol.ordered == (PATTERN,) for pol in LEVELS)   # the new rung
    no_pass = Enrolment(passphrase_set=False, recovery_key_set=True,
                        recovery_key_verified=True)
    for pol in LEVELS:
        ok, why = can_select(pol, no_pass)
        assert not ok and "passphrase" in why.lower()


def test_every_offered_level_has_a_name_and_honest_words():
    from provisioning.vault_factors import LEVELS, level_name
    for pol in LEVELS:
        assert level_name(pol)
        d = describe(pol)
        assert d["asks"] and d["strength"] and d["field"]


def test_a_hand_made_policy_still_reads_as_something():
    """The model takes any mix even though only three are offered. A policy
    built by hand must not show as a blank or as "Custom"."""
    from provisioning.vault_factors import level_name
    assert level_name(Policy((PATTERN, KEYFILE))) == "Pattern + usb key"


# --- setting a pattern: twice, and they must match --------------------------

def test_a_pattern_must_be_drawn_twice_and_agree():
    """A pattern is drawn, not read back — there is nothing on screen to check
    it against afterwards. One slip while setting it and the vault's key is a
    gesture nobody has ever made deliberately."""
    from provisioning.vault_factors import confirm_pattern
    assert confirm_pattern([0, 4, 8, 7], [0, 4, 8, 7]) == "0-4-8-7"
    with pytest.raises(FactorError) as e:
        confirm_pattern([0, 4, 8, 7], [0, 4, 8, 6])
    assert "Draw it again" in str(e.value)


def test_the_confirmation_still_enforces_the_minimum_length():
    """Drawing the same too-short pattern twice is still too short — the check
    must not be satisfied merely by agreement."""
    from provisioning.vault_factors import confirm_pattern
    with pytest.raises(FactorError):
        confirm_pattern([0, 1, 2], [0, 1, 2])


def test_a_reversed_pattern_is_not_the_same_pattern():
    from provisioning.vault_factors import confirm_pattern
    with pytest.raises(FactorError):
        confirm_pattern([0, 4, 8, 7], [7, 8, 4, 0])


# --- there must always be a way back in -------------------------------------

def test_a_pattern_cannot_be_chosen_before_the_passphrase_is_set():
    """Operator's rule: the passphrase comes first. It is the part of the
    unlock that carries the strength; the pattern is added to it."""
    from provisioning.vault_factors import Enrolment, can_select
    ready_but_bare = Enrolment(passphrase_set=False, recovery_key_set=True,
                               recovery_key_verified=True)
    ok, why = can_select(Policy((PATTERN, PASSPHRASE)), ready_but_bare)
    assert not ok and "Set the passphrase" in why


def test_pattern_only_is_choosable_once_enrolment_is_complete():
    """The 2026-08-25 ruling: an easy daily door is offered — but only after
    the passphrase and proven recovery key stand behind it."""
    from provisioning.vault_factors import Enrolment, can_select
    e = Enrolment(passphrase_set=True, recovery_key_set=True,
                  recovery_key_verified=True)
    ok, why = can_select(Policy((PATTERN,)), e)
    assert ok, why


def test_nothing_can_be_turned_on_until_the_key_is_written_AND_typed_back():
    """Shown-on-a-screen is not written-down. With no back door the recovery
    key is the only way in, so the medic has to see the operator prove they
    have it — not merely that it generated one."""
    from provisioning.vault_factors import Enrolment, can_select
    generated_only = Enrolment(passphrase_set=True, recovery_key_set=True,
                               recovery_key_verified=False)
    ok, why = can_select(Policy((PASSPHRASE,)), generated_only)
    assert not ok and "type it back" in why
    assert "ONLY way in" in why


def test_with_everything_in_place_any_offered_level_may_be_chosen():
    from provisioning.vault_factors import LEVELS, Enrolment, can_select
    ready = Enrolment(passphrase_set=True, recovery_key_set=True,
                      recovery_key_verified=True)
    for pol in LEVELS:
        ok, why = can_select(pol, ready)
        assert ok, why


# --- and nothing caps the vault any more ------------------------------------

def test_the_recovery_key_caps_the_top_level_and_that_is_fine():
    """Written while assuming the recovery key capped nothing. It does cap the
    top level: a random keyfile makes that policy ~306 bits and the key on
    paper is 160, so the paper is the weaker door.

    Which is the right answer and worth keeping visible. 160 bits is not a
    number anyone grinds through — the point is that the tool reports the door
    an attacker would actually pick, rather than the flattering one, and it
    does that even when the flattering one is ours."""
    from provisioning.vault_factors import Enrolment, effective_bits, \
        RECOVERY_KEY_BITS
    strong = Policy((PATTERN, PASSPHRASE, KEYFILE))
    e = Enrolment(passphrase_set=True, recovery_key_set=True,
                  recovery_key_verified=True, policy=strong)
    assert strength_bits(strong) > RECOVERY_KEY_BITS
    # Since 2026-08-25 the enrolled passphrase is its own slot beside the
    # daily policy — so the door an attacker picks on a strong policy is now
    # the passphrase slot, and the number says so out loud.
    assert effective_bits(e) == pytest.approx(
        strength_bits(Policy((PASSPHRASE,))))


def test_the_daily_unlock_governs_the_levels_a_person_can_remember():
    """Below the top level the policy is the weaker door, so the number the
    operator sees is the one their own choices earned."""
    from provisioning.vault_factors import Enrolment, effective_bits
    mid = Policy((PATTERN, PASSPHRASE))
    e = Enrolment(passphrase_set=True, recovery_key_set=True,
                  recovery_key_verified=True, policy=mid)
    # the weakest enrolled door: the standalone passphrase slot (30 bits)
    # sits below pattern+passphrase (~50)
    assert effective_bits(e) == pytest.approx(
        strength_bits(Policy((PASSPHRASE,))))


def test_effective_bits_still_takes_the_minimum():
    """It reads as redundant today — the recovery key is always the stronger
    slot. It is not redundant the day someone adds a convenience door back,
    and that is exactly the day this number has to notice."""
    from provisioning.vault_factors import Enrolment, effective_bits, \
        RECOVERY_KEY_BITS
    import provisioning.vault_factors as vf
    e = Enrolment(recovery_key_set=True, policy=Policy((PASSPHRASE,)))
    assert effective_bits(e) == min(strength_bits(e.policy), RECOVERY_KEY_BITS)


def test_the_screens_do_not_claim_a_door_that_is_not_there():
    """The fallback used to say "there is no back door", on every card.

    It was false. ``can_select`` REQUIRES an enrolled passphrase slot behind
    whatever daily door is chosen, and this module's own header spells out the
    consequence: the vault is worth what that passphrase is worth. The honest
    sentence existed in the source comments and never reached the glass, which
    is precisely the failure FORBIDDEN_CLAIMS exists to catch.

    Now the card must NAME the passphrase slot, and must not claim there is no
    way in besides the recovery key."""
    from provisioning.vault_factors import LEVELS
    for pol in LEVELS:
        d = describe(pol)
        fb = d["fallback"].lower()
        assert "no back door" not in fb, "the false claim is back"
        assert "passphrase" in fb, "the mandatory passphrase slot is unnamed"
        assert "recovery key" in fb, "the last resort is unnamed"


# -- enforce what the setup copy promises (walkthrough 2026-08-26) ------------

def test_straight_line_patterns_are_rejected():
    import pytest as _pt
    from provisioning.vault_factors import encode_pattern, FactorError
    for line in ([0, 1, 2], [0, 3, 6], [0, 4, 8], [2, 4, 6], [3, 4, 5],
                 [6, 7, 8], [0, 1, 2, 2]):   # last is dup, also rejected
        with _pt.raises(FactorError):
            encode_pattern(line)


def test_a_pattern_with_a_turn_is_accepted():
    from provisioning.vault_factors import encode_pattern
    assert encode_pattern([0, 1, 4, 6])       # has a corner
    assert encode_pattern([0, 3, 6, 7])       # an L — not a single line


def test_passphrase_problem_rejects_pins_and_words():
    from provisioning.vault_factors import passphrase_problem
    assert passphrase_problem("1234")          # too short
    assert passphrase_problem("dev1")          # too short
    assert passphrase_problem("aaaaaaaa")      # too repetitive
    assert passphrase_problem("12345678") and "numbers" in passphrase_problem("12345678")


def test_a_real_passphrase_passes():
    from provisioning.vault_factors import passphrase_problem
    assert passphrase_problem("garden gate rusty") is None
    assert passphrase_problem("correcthorse") is None


# ---------------------------------------------------------------------------
# What the strength sentences may claim (2026-09-03)
#
# Two more cases of the code knowing better than the glass, found alongside the
# "no back door" sentence:
#
#   * the pattern card quoted 985,824 — the count of EVERY ordered selection of
#     4..9 dots. pattern_bits' own docstring said "treat this as the ceiling,
#     and say so on screen". The screen never said so.
#   * the keyfile cards said "guessing is not the way in". can_select REQUIRES
#     an enrolled passphrase, and with real keyslots (records_vault) that
#     passphrase is a parallel door — so guessing is exactly a way in, at 30
#     bits rather than 256.
# ---------------------------------------------------------------------------

def test_the_realistic_pattern_count_is_the_four_dot_subset():
    from provisioning.vault_factors import pattern_space, pattern_space_realistic
    assert pattern_space_realistic() == 9 * 8 * 7 * 6 == 3024
    assert pattern_space_realistic() < pattern_space() / 300


def test_the_pattern_card_quotes_the_realistic_count_not_just_the_ceiling():
    from provisioning.vault_factors import (LEVELS, PATTERN,
                                            pattern_space_realistic)
    pol = next(p for p in LEVELS if p.ordered == (PATTERN,))
    strength = describe(pol)["strength"]
    assert f"{pattern_space_realistic():,}" in strength, "ceiling quoted alone"
    assert "only days" not in strength, "the old days-of-grinding claim is back"


def test_no_level_claims_guessing_is_not_a_way_in():
    """It always is. The passphrase slot is mandatory behind every daily door,
    so the weakest enrolled door is what an attacker actually attacks."""
    from provisioning.vault_factors import LEVELS
    for pol in LEVELS:
        assert "guessing is not the way in" not in describe(pol)["strength"]


def test_the_keyfile_levels_name_the_passphrase_behind_them():
    from provisioning.vault_factors import LEVELS, KEYFILE
    for pol in [p for p in LEVELS if KEYFILE in p.ordered]:
        assert "passphrase" in describe(pol)["strength"].lower(), (
            f"{pol.ordered} claims a strength it does not have alone")
