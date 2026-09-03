"""Settings ▸ Encrypt my records — the logic behind the switch.

Kept out of the Kivy screen because nothing in this suite instantiates Kivy, so
anything decided inside a screen is decided untested. That has bitten this
project before: the setup wizard shipped a TypeError on its first screen with a
green suite (2026-08-31).
"""
import os

import pytest

from provisioning import encryption_flow as ef
from provisioning import records_vault as rv
from provisioning import recovery_key as rk
from provisioning import vault_factors as vf
from provisioning.vault import ScryptParams

#: The vault needs a real AEAD, which comes from ``cryptography``. It is
#: installed on the medic and carried in the wheelhouse, and CI installs it too
#: — but a machine without it should SKIP these, not fail them. The module's own
#: behaviour there is correct and separately tested: it raises a sentence the
#: operator can act on, and ``blockers()`` reports it.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    _HAVE_CRYPTO = True
except Exception:                                          # pragma: no cover
    _HAVE_CRYPTO = False

pytestmark = pytest.mark.skipif(
    not _HAVE_CRYPTO,
    reason="cryptography is not installed; the vault cannot be exercised here")


FAST = ScryptParams(n=1 << 8, r=8, p=1)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = tmp_path / ".reticulum-node-medic"
    (root / "nodes").mkdir(parents=True)
    (root / "registry.json").write_bytes(b'{"kin": 1}')
    (root / "nodes" / "faith.json").write_bytes(b'{"name": "FAITH"}')
    return tmp_path


# --------------------------------------------------------------------------- #
# What the screen says
# --------------------------------------------------------------------------- #

def test_the_headline_states_the_state(home):
    st = ef.state(str(home))
    assert "NOT encrypted" in ef.headline(st)
    ef.turn_on({vf.PATTERN: "0-4-8-7"}, "a real passphrase", "ABCD-EFGH",
               home=str(home), policy=vf.Policy((vf.PATTERN,)))
    assert "are encrypted" in ef.headline(ef.state(str(home)))


def test_both_halves_of_what_is_covered_are_shown(home):
    """An operator who believes their mesh identity is encrypted has been
    misled by this screen. The not-covered half is not a footnote."""
    lines = ef.covered_lines(str(home))
    assert any(ok for ok, _ in lines) and any(not ok for ok, _ in lines)
    text = " ".join(t for _, t in lines).lower()
    assert ".reticulum" in text and "map" in text


def test_the_uncovered_lines_say_WHY_not_just_what(home):
    """"Not covered" without a reason reads as an oversight. Both of these are
    deliberate trades and the operator is entitled to the reasoning."""
    for ok, line in ef.covered_lines(str(home)):
        if not ok:
            assert any(w in line.lower() for w in
                       ("so this node", "would add", "protect nothing",
                        "on purpose")), line


def test_no_records_is_a_blocker(tmp_path):
    b = ef.blockers(str(tmp_path))
    assert b and "nothing to encrypt" in b[0]


def test_a_medic_with_records_has_no_blockers(home):
    assert ef.blockers(str(home)) == []


# --------------------------------------------------------------------------- #
# What the operator has to prove
# --------------------------------------------------------------------------- #

def test_every_factor_of_the_daily_door_is_asked_for():
    doors = ef.doors_to_prove(vf.Policy((vf.PATTERN, vf.PASSPHRASE)))
    daily = [d["factor"] for d in doors if d["door"] == "daily"]
    assert daily == [vf.PATTERN, vf.PASSPHRASE]


def test_the_recovery_key_is_always_asked_for():
    for pol in vf.LEVELS:
        assert any(d["kind"] == "recovery" for d in ef.doors_to_prove(pol))


def test_the_enrolled_passphrase_is_asked_for_even_when_it_is_not_the_daily_door():
    """can_select enrols it behind EVERY level. A screen that only asked for the
    daily factors would wrap a slot with a secret nobody had just proved."""
    doors = ef.doors_to_prove(vf.Policy((vf.KEYFILE,)))
    assert [d["door"] for d in doors if d["kind"] == "passphrase"] == ["vault"]


def test_the_passphrase_is_asked_for_exactly_once_when_it_IS_the_daily_door():
    doors = ef.doors_to_prove(vf.Policy((vf.PASSPHRASE,)))
    assert len([d for d in doors if d["kind"] == "passphrase"]) == 1


@pytest.mark.parametrize("text,ok", [
    ("", False), ("ABCD-EFGH", False), ("A" * 32, True),
])
def test_recovery_key_shape_is_checked_at_the_keyboard(text, ok):
    assert (ef.recovery_problem(text) is None) is ok


def test_a_wrong_length_key_is_told_the_length():
    msg = ef.recovery_problem("ABCD-EFGH")
    assert "8 characters" in msg and "32" in msg


# --------------------------------------------------------------------------- #
# Turning it on and off
# --------------------------------------------------------------------------- #

def test_turn_on_encrypts_and_every_proved_door_opens(home):
    pol = vf.Policy((vf.PATTERN,))
    res = ef.turn_on({vf.PATTERN: "0-4-8-7"}, "a real passphrase",
                     "ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-2345-6789",
                     home=str(home), policy=pol)
    assert res["files"] == 2
    root = rv.records_root(str(home))
    assert b"FAITH" not in (home / ".reticulum-node-medic" / "nodes" /
                            "faith.json").read_bytes()
    assert rv.open_vault(root, ef.daily_secret({vf.PATTERN: "0-4-8-7"}, pol))[0]
    assert rv.open_vault(root, "a real passphrase")[0]


def test_the_recovery_key_is_folded_before_it_becomes_a_secret(home):
    """It is written by hand and typed back later, possibly by someone else.
    Crockford base32 exists because O/0 and I/1 get confused; folding means the
    key that opens the vault is the key AS WRITTEN."""
    written = rk.generate()
    ef.turn_on({vf.PATTERN: "0-4-8-7"}, "a real passphrase", written,
               home=str(home), policy=vf.Policy((vf.PATTERN,)))
    root = rv.records_root(str(home))
    for typed in (written, written.replace("-", ""), written.lower(),
                  written.replace("0", "O").replace("1", "I")):
        assert rv.open_vault(root, rk.normalize(typed))[0], f"{typed!r} failed"


def test_turn_off_gives_the_records_back(home):
    ef.turn_on({vf.PATTERN: "0-4-8-7"}, "a real passphrase", "ABCD-EFGH",
               home=str(home), policy=vf.Policy((vf.PATTERN,)))
    ef.turn_off("a real passphrase", home=str(home))
    root = home / ".reticulum-node-medic"
    assert (root / "registry.json").read_bytes() == b'{"kin": 1}'
    assert not rv.is_vault(str(root))


def test_the_policy_file_is_never_encrypted(home):
    """It lives INSIDE the records root and says which factors the daily door
    is made of. Encrypted, load_policy falls back to pattern-only and asks a
    passphrase operator to draw a shape they never set."""
    root = home / ".reticulum-node-medic"
    vf.save_policy(vf.Policy((vf.PASSPHRASE,)), str(root / "vault_policy.json"))
    ef.turn_on({vf.PASSPHRASE: "a real passphrase"}, "a real passphrase",
               "ABCD-EFGH", home=str(home), policy=vf.Policy((vf.PASSPHRASE,)))
    assert not rv.is_encrypted((root / "vault_policy.json").read_bytes())
    assert vf.load_policy(str(root / "vault_policy.json")).ordered == \
        (vf.PASSPHRASE,)


def test_state_reports_the_doors_that_exist(home):
    ef.turn_on({vf.PATTERN: "0-4-8-7"}, "a real passphrase", "ABCD-EFGH",
               home=str(home), policy=vf.Policy((vf.PATTERN,)))
    assert sorted(ef.state(str(home))["doors"]) == \
        [rv.SLOT_DAILY, rv.SLOT_PASSPHRASE, rv.SLOT_RECOVERY]


def test_the_daily_secret_is_not_any_single_factor(home):
    """combine() is length-prefixed and named. A daily secret that WAS just the
    passphrase would make the daily and passphrase slots identical."""
    pol = vf.Policy((vf.PATTERN, vf.PASSPHRASE))
    parts = {vf.PATTERN: "0-4-8-7", vf.PASSPHRASE: "a real passphrase"}
    s = ef.daily_secret(parts, pol)
    assert s not in parts.values() and "0-4-8-7" not in s
