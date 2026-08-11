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
