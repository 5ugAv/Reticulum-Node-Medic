"""The recycling how-tos.

Operator, 2026-09-03: "maybe we can start building a section on recycling old
materials, just a how-to, as well as the software flash that we needed inside
that". salvage.py decides what a thing could become; this is HOW.

The reader is assumed to be able to look at the object in their hand and
nothing else. These tests are mostly about the writing, because for this
audience the writing IS the feature.
"""
import pytest

from provisioning.salvage import Found, paths_for
from provisioning.salvage_guide import (
    ADD_A_RADIO, GUIDES, HANDHELD, OLD_PHONE, guide, guide_for_path,
)


# --------------------------------------------------------------------------- #
# The guides exist and hold together
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("g", GUIDES, ids=lambda g: g.key)
def test_a_guide_names_what_you_need_before_the_first_step(g):
    """The worst moment to discover you need a soldering iron is with the case
    already open."""
    assert g.needs, f"{g.key} springs its requirements on the reader"
    assert g.steps and g.opening


@pytest.mark.parametrize("g", GUIDES, ids=lambda g: g.key)
def test_every_step_says_something(g):
    for s in g.steps:
        assert s.text.strip()
        assert s.text.endswith((".", "!")), f"{s.text!r} is not a sentence"


@pytest.mark.parametrize("g", GUIDES, ids=lambda g: g.key)
def test_the_sentences_stay_short(g):
    """This is written for someone meeting the technology for the first time,
    possibly reading it in their second or third language."""
    for s in g.steps:
        for sentence in s.text.split(". "):
            assert len(sentence.split()) <= 30, f"too long: {sentence!r}"


@pytest.mark.parametrize("g", GUIDES, ids=lambda g: g.key)
def test_no_jargon_reaches_the_reader(g):
    """Words the reader has no way to know. "KISS" survives in the handheld
    guide only where it is introduced as a name, not used as an assumption."""
    body = " ".join([g.opening, g.expect, g.caution]
                    + [s.text for s in g.steps]).lower()
    for jargon in ("tnc", "afsk", "baud", "gpio", "spi bus", "firmware blob",
                   "kiss interface", "lxmf", "destination hash", "sudo"):
        assert jargon not in body, f"{g.key} uses {jargon!r} at the reader"


# --------------------------------------------------------------------------- #
# The handheld radio
# --------------------------------------------------------------------------- #

def test_the_handheld_guide_sets_the_speed_expectation_early():
    """Someone expecting web pages gives up in disgust. Someone expecting
    written notes between villages is delighted. Say which it is."""
    assert "slow" in HANDHELD.expect.lower()
    assert "picture" in HANDHELD.expect.lower()


def test_the_handheld_guide_carries_the_licence_warning():
    assert "country allows" in HANDHELD.caution
    assert "encrypt" in HANDHELD.caution.lower()


def test_the_handheld_guide_points_at_a_local_radio_club():
    """The regulator's rules are exactly what a local club already knows, and
    it is a real person to ask rather than a website in another language."""
    assert "club" in HANDHELD.caution.lower()


def test_the_guide_steers_to_a_transmit_wire_not_to_vox():
    """CORRECTED 2026-09-03 after reading Reticulum's own discussion #198,
    where the author says of audio-only setups: "there is no direct control of
    the PTT. You need to use very slow and unreliable VOX operation of the
    radio". The first draft of this guide told people to start with VOX because
    it needs no wiring. That was the wrong advice, cheerfully given."""
    ptt = [s for s in HANDHELD.steps if "transmit pin" in s.text.lower()]
    assert ptt, "no step for wiring the transmit pin"
    warn = ptt[0].watch_out.lower()
    assert "vox" in warn, "VOX is not named as the thing to avoid"
    assert "cuts the" in warn or "cut off" in warn


def test_the_cheap_ptt_hardware_is_named():
    """A CM108 sound adaptor costs about the same as a coffee and has a spare
    pin for PTT. Cheap AND correct is the answer this tool should give."""
    assert "CM108" in " ".join(HANDHELD.needs)


def test_the_socket_check_comes_first():
    """"Does it have a socket for an earpiece?" sorts a found radio into easy
    or hard, and anyone holding it can answer."""
    assert "earpiece" in HANDHELD.opening.lower()
    assert "solder inside" in HANDHELD.opening.lower()


def test_the_bench_test_warns_about_deafening_the_radios():
    """Two radios close together at full power desense each other, and it
    presents as a fault that is not there."""
    last = HANDHELD.steps[-1]
    assert "deafen" in last.watch_out.lower()


def test_the_volume_warning_is_on_the_wiring_step():
    """Full volume overloads the input and looks like a wiring fault."""
    wiring = [s for s in HANDHELD.steps
              if "socket" in (s.text + s.detail).lower()]
    assert any("volume" in s.watch_out.lower() for s in wiring)


def test_the_handheld_guide_ends_with_a_test_before_relying_on_it():
    assert "test" in HANDHELD.steps[-1].text.lower()


# --------------------------------------------------------------------------- #
# Adding a radio to a bare board
# --------------------------------------------------------------------------- #

def test_the_frequency_mistake_is_the_first_thing_said():
    """The mistake that wastes the most money: a 433 module cannot talk to an
    868 one, and no software fixes it."""
    first = ADD_A_RADIO.steps[0]
    assert "433" in first.detail and "868" in first.detail


def test_the_voltage_warning_exists():
    """5 volts destroys the module on first power-up."""
    assert any("5 volt" in s.watch_out.lower() or "5 volts" in s.watch_out.lower()
               for s in ADD_A_RADIO.steps)


def test_the_wire_count_agrees_with_what_salvage_promised():
    """The two files quoted different numbers — six in one, eight in the other.
    The reader counts them out on the bench."""
    promise = " ".join(paths_for(
        Found(kind="dev_board", has_lora=False, chip="esp32"))[0].needs)
    assert "eight" in promise
    assert "eight wires" in ADD_A_RADIO.opening


# --------------------------------------------------------------------------- #
# The phone — the only one the medic can already do
# --------------------------------------------------------------------------- #

def test_the_phone_guide_matches_how_the_medic_actually_installs():
    """An earlier draft had the keeper hunting for a cable. The medic serves
    the APK over its OWN wifi behind a QR (workflows/phone_serve.py) — there is
    no cable in it at all."""
    body = " ".join(s.text + s.detail for s in OLD_PHONE.steps).lower()
    assert "wifi" in body and "camera" in body
    assert "plug the phone" not in body


def test_the_phone_guide_is_the_one_marked_as_working_today():
    assert OLD_PHONE.medic_does_it is True


def test_nothing_else_claims_the_medic_will_do_it():
    """A how-to that implies a button exists is the same lie as a screen
    promising a vault that was never built."""
    for g in GUIDES:
        if g.key != "old_phone":
            assert g.medic_does_it is False, f"{g.key} claims a button"


def test_the_phone_guide_says_no_sim_and_no_internet():
    """The reason someone in a remote community would try this at all."""
    assert "no sim" in " ".join(OLD_PHONE.needs).lower()


# --------------------------------------------------------------------------- #
# The two modules stay in step
# --------------------------------------------------------------------------- #

def test_every_route_with_a_how_to_can_find_it():
    for kind in ("handheld_radio", "dev_board", "phone"):
        for p in paths_for(Found(kind=kind, has_lora=False, chip="esp32")):
            g = guide_for_path(p.title)
            if g is not None:
                assert g in GUIDES


def test_the_handheld_route_has_a_how_to():
    """The route the operator asked for by name must not end at a verdict."""
    p = [x for x in paths_for(Found(kind="handheld_radio"))
         if x.role == "radio_node"][0]
    assert guide_for_path(p.title) is HANDHELD


def test_an_unmapped_route_returns_none_rather_than_guessing():
    assert guide_for_path("no such route") is None
    assert guide("no such guide") is None
