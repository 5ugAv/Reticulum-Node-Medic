"""The first-use marker — pure, Kivy-free.

These are about the ways the marker can be WRONG, because every one of them is
silent: a medic that hides its own setup, or one that re-offers it forever, both
look like ordinary behaviour to somebody who has never seen the other.
"""

import json
import os

from provisioning import first_use as fu


def _p(tmp_path):
    return str(tmp_path / "first_use.json")


# --- a brand-new medic -------------------------------------------------------

def test_a_medic_with_no_marker_is_first_use(tmp_path):
    assert fu.is_first_use(_p(tmp_path))
    assert fu.security_outstanding(_p(tmp_path))


def test_a_completed_setup_is_not_offered_again(tmp_path):
    p = _p(tmp_path)
    assert fu.mark_completed(security_skipped=False, path=p)
    assert not fu.is_first_use(p)
    assert not fu.security_outstanding(p)


# --- the asymmetry that decides how doubt resolves ---------------------------

def test_an_unreadable_marker_offers_the_setup_rather_than_hiding_it(tmp_path):
    """The two mistakes do not cost the same.

    Reading a truncated file as "already done" hides the whole setup from a
    brand-new operator, who has no way to know it exists. Reading it as "not
    done" costs a returning operator one screen they can dismiss. When the tool
    does not know, it must not guess in the direction that removes the choice.
    """
    p = _p(tmp_path)
    open(p, "w").write('{"completed": tru')          # a power cut mid-write
    assert fu.is_first_use(p)
    assert fu.load(p).completed is False


def test_a_marker_that_is_not_even_an_object_does_not_raise(tmp_path):
    p = _p(tmp_path)
    open(p, "w").write('["completed"]')
    assert fu.load(p) == fu.Record()
    assert fu.is_first_use(p)


def test_a_marker_is_written_whole_or_not_at_all(tmp_path):
    """tmp-then-replace. The medic is a Pi with no RTC whose power gets pulled;
    a file half-written at that moment must read as ABSENT next boot, not as a
    truncation that json happens to parse into 'setup done'."""
    from tests.srcutil import func_source
    src = func_source("provisioning/first_use.py", "save")
    assert "os.replace" in src, "a partial marker can be left on the card"
    assert '".tmp"' in src


def test_the_marker_is_not_world_readable(tmp_path):
    p = _p(tmp_path)
    fu.mark_completed(security_skipped=False, path=p)
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


# --- skipping is its own answer ---------------------------------------------

def test_skipping_the_security_does_not_bring_the_boot_screen_back(tmp_path):
    """An operator in a field with a dying node does not want a nine-screen
    ceremony first, and a medic that reappears with one on every power-up
    teaches them to dismiss it without reading."""
    p = _p(tmp_path)
    fu.mark_completed(security_skipped=True, path=p)
    assert not fu.is_first_use(p)


def test_but_settings_still_has_to_offer_it(tmp_path):
    """Skipped is remembered as SKIPPED, not as done — otherwise the operator
    who said "not now" has nowhere to say "now" from."""
    p = _p(tmp_path)
    fu.mark_completed(security_skipped=True, path=p)
    assert fu.security_outstanding(p)


# --- handing the medic on ----------------------------------------------------

def test_the_setup_can_be_reset_for_the_next_person(tmp_path):
    """[[networks-outlast-builders]]: a node stays useful when its keeper moves
    away, and so must the tool that builds them. A medic handed on has to be
    able to introduce itself from nothing."""
    p = _p(tmp_path)
    fu.mark_completed(security_skipped=False, path=p)
    assert fu.clear(p)
    assert fu.is_first_use(p)


def test_clearing_a_marker_that_was_never_there_is_not_a_failure(tmp_path):
    """"Already absent" is the state the caller asked for. Reporting it as a
    failure sends a Settings screen into an error path for a no-op."""
    assert fu.clear(str(tmp_path / "never_existed.json"))


# --- what the marker must NOT claim -----------------------------------------

def test_the_marker_holds_no_secret_and_no_policy(tmp_path):
    """It records that a walkthrough was run. The policy belongs to
    vault_factors.save_policy and the secrets belong nowhere."""
    p = _p(tmp_path)
    fu.mark_completed(security_skipped=False, path=p)
    data = json.load(open(p))
    assert set(data) == {"completed", "security_skipped", "at", "version"}


def test_completion_is_not_a_claim_that_anything_is_encrypted():
    """mark_completed says the operator was shown the choices and made them.
    Whether a vault exists on this card is provisioning.vault's question, and
    inferring it from this file is how a screen ends up congratulating somebody
    on records that are sitting in the clear."""
    from tests.srcutil import func_source
    doc = func_source("provisioning/first_use.py", "mark_completed")
    assert "NOT A CLAIM ABOUT ENCRYPTION" in doc


def test_the_timestamp_never_changes_the_answer(tmp_path):
    """The medic has no RTC and comes back from a power cut believing it is
    1970 — that is why PROBE carries a clock-sync check at all. Any rule of the
    form "show the setup again if it was run more than N ago" would fire on
    every such boot, so the recorded time must not reach a decision.
    """
    p = _p(tmp_path)
    for stamp in (0.0, 1.0, 4102444800.0):        # epoch, ancient, year 2100
        fu.save(fu.Record(completed=True), p, now=lambda s=stamp: s)
        assert not fu.is_first_use(p)
        assert not fu.security_outstanding(p)


def test_a_stamped_time_survives_the_round_trip(tmp_path):
    p = _p(tmp_path)
    fu.save(fu.Record(completed=True), p, now=lambda: 1234.5)
    assert fu.load(p).at == 1234.5
    assert fu.load(p).version == fu.CURRENT_VERSION
