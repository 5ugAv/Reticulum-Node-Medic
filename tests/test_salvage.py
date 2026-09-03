"""Show me what you got — turning salvaged hardware into a role in the network.

Operator, 2026-09-03: the medic is for remote communities and has to work with
"any hardware people might have lying around — an old radio, a digital radio,
something with a chip inside", recycled and upcycled. The old flow asked "which
of these 16 boards is yours?", which only answers for someone who bought one of
the 16.
"""
from dataclasses import replace

import pytest

from provisioning.salvage import (
    KINDS, LICENCE, ROLES, Found, found_from_detection, is_unidentified,
    paths_for, questions_for, summary,
)


# --------------------------------------------------------------------------- #
# Nothing is ever a dead end
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kind", [k for k, _ in KINDS])
def test_every_kind_of_thing_gets_at_least_one_route(kind):
    """"We cannot help you" is not an answer this tool is allowed to give. Most
    salvaged hardware arrives unidentified, and that is a starting state, not a
    failure."""
    paths = paths_for(Found(kind=kind))
    assert paths, f"{kind} has no route at all"
    assert all(p.plain for p in paths), "a route with no explanation"


@pytest.mark.parametrize("kind", [k for k, _ in KINDS])
def test_every_route_names_a_role_that_exists(kind):
    for p in paths_for(Found(kind=kind)):
        assert p.role in ROLES, f"{p.role} is not a role the network has"


def test_an_unknown_thing_is_told_what_to_do_next():
    f = Found(kind="unknown")
    assert is_unidentified(f)
    assert "plug it" in summary(f).lower()


def test_an_unidentified_thing_is_not_congratulated():
    """An early version opened with "Good news —" over the answer that means
    the keeper has told the tool nothing at all."""
    assert "good news" not in summary(Found(kind="unknown")).lower()


# --------------------------------------------------------------------------- #
# The handheld radio — the path the operator asked for by name
# --------------------------------------------------------------------------- #

def test_a_handheld_radio_can_carry_messages():
    """It has no LoRa in it and never will. What it has is a transmitter, an
    aerial, and forty years of packet radio saying this works."""
    paths = paths_for(Found(kind="handheld_radio"))
    radio = [p for p in paths if p.role == "radio_node"]
    assert radio, "a handheld radio was told it can only be scrap"
    assert radio[0].needs, "the bill of what to find is missing"


def test_the_radio_route_comes_before_stripping_it_for_parts():
    """THE ordering bug. Parts sorted as "easy" and floated to the top, so a
    keeper holding a working radio was told to take the aerial off it before
    being told it can carry messages."""
    paths = paths_for(Found(kind="handheld_radio"))
    roles = [p.role for p in paths]
    assert roles.index("radio_node") < roles.index("parts")


def test_parts_is_last_for_every_kind_that_offers_it():
    for kind, _ in KINDS:
        roles = [p.role for p in paths_for(Found(kind=kind))]
        if "parts" in roles and len(roles) > 1:
            assert roles[-1] == "parts", f"{kind} puts parts above a real route"


def test_the_radio_route_warns_it_is_slow():
    """Someone expecting web pages puts it down in disgust; someone expecting
    written messages between villages is delighted. Set that up front."""
    p = [x for x in paths_for(Found(kind="handheld_radio"))
         if x.role == "radio_node"][0]
    assert "slow" in p.plain.lower()


# --------------------------------------------------------------------------- #
# Licensing — never a footnote
# --------------------------------------------------------------------------- #

def test_every_transmitting_route_carries_the_licence_warning():
    """Getting someone in a remote community into trouble with their regulator
    would fail them worse than not helping at all."""
    for kind in ("handheld_radio", "computer"):
        for p in paths_for(Found(kind=kind)):
            if "radio" in p.title.lower() or "handheld" in p.plain.lower():
                assert p.caution, f"{kind}: {p.title} transmits with no warning"


def test_the_licence_warning_names_the_two_things_that_surprise_people():
    assert "not allowed" in LICENCE
    assert "encrypt" in LICENCE.lower()


def test_the_licence_warning_does_not_guess_a_country():
    """Rules differ by country and by band, and this tool cannot know which the
    keeper is in. It says there ARE rules; it does not invent them."""
    for word in ("FCC", "Ofcom", "Part 97", "ACMA", "United States", "Europe"):
        assert word not in LICENCE


# --------------------------------------------------------------------------- #
# Boards, known and unknown
# --------------------------------------------------------------------------- #

def test_a_catalogue_board_is_ready_today():
    f = Found(kind="dev_board", has_lora=True, chip="esp32s3",
              board_key="heltec32_v3")
    best = paths_for(f)[0]
    assert best.ready_now and best.role == "radio_node"
    assert "good news" in summary(f).lower()


def test_an_unknown_lora_board_is_a_route_not_a_refusal():
    """A board nobody has met still works — somebody has to tell the medic
    which pins the radio is on. That is a day's work, not a dead end."""
    f = Found(kind="dev_board", has_lora=True, chip="esp32s3")
    best = paths_for(f)[0]
    assert best.role == "radio_node" and not best.ready_now
    assert best.needs


def test_a_chip_with_no_radio_is_offered_the_solder_route():
    f = Found(kind="dev_board", has_lora=False, chip="esp32")
    best = paths_for(f)[0]
    assert "LoRa module" in " ".join(best.needs)
    assert "433" in best.caution, "no warning about buying the wrong band"


def test_an_unsupported_chip_says_so_without_pretending():
    f = Found(kind="dev_board", has_lora=True, chip="stm32wl")
    best = paths_for(f)[0]
    assert best.medic_ready is False and best.difficulty == "hard"


# --------------------------------------------------------------------------- #
# What the medic reads, and what it must ask
# --------------------------------------------------------------------------- #

def test_detection_never_guesses_whether_a_radio_is_fitted():
    """Nothing on the USB bus says so. Guessing would send someone soldering
    for an afternoon on the strength of a chip ID."""
    f = found_from_detection({"found": True, "chip": "esp32s3",
                              "board_key": None})
    assert f.has_lora is None


def test_a_catalogue_board_does_not_get_asked_anything():
    f = found_from_detection({"found": True, "chip": "esp32s3",
                              "board_key": "heltec32_v3"})
    assert questions_for(f) == []


def test_an_unknown_board_is_asked_the_question_that_matters_first():
    f = found_from_detection({"found": True, "chip": "esp32s3",
                              "board_key": None})
    qs = questions_for(f)
    assert qs and qs[0].field == "has_lora"


def test_no_question_is_asked_whose_answer_changes_nothing():
    """The patience of someone already unsure they belong here is the scarcest
    thing this tool spends."""
    f = Found(kind="dev_board", chip="esp32s3")
    for q in questions_for(f):
        yes = [p.title for p in paths_for(replace(f, **{q.field: True}))]
        no = [p.title for p in paths_for(replace(f, **{q.field: False}))]
        assert yes != no, f"{q.field} is asked but changes nothing"


def test_every_question_can_be_answered_by_looking_not_by_knowing():
    """"Does it have a LoRa radio?" is unanswerable by the person this tool is
    for. "Is there a small metal box with an aerial socket?" is answerable by
    anyone holding the board."""
    f = Found(kind="dev_board", chip="esp32s3")
    for q in questions_for(f):
        assert q.look_for, f"{q.field} asks for knowledge, not for a look"


def test_nothing_detected_is_simply_unknown():
    assert found_from_detection({"found": False}).kind == "unknown"
    assert found_from_detection({}).kind == "unknown"


# --------------------------------------------------------------------------- #
# The words themselves
# --------------------------------------------------------------------------- #

def test_the_roles_avoid_reticulum_jargon():
    """The guide teaches "transport node" and "propagation node". This screen
    has to be understood BEFORE anyone has read the guide."""
    words = " ".join(r["name"] + " " + r["plain"] for r in ROLES.values()).lower()
    for jargon in ("transport node", "propagation node", "lxmf", "interface",
                   "rnode", "destination hash"):
        assert jargon not in words, f"{jargon!r} on the first screen"


def test_the_kinds_describe_things_you_can_hold():
    """The one question a person can always answer is about shape, not about
    electronics."""
    assert len(KINDS) >= 5
    for _key, text in KINDS:
        assert len(text.split()) >= 4, f"{text!r} is a category, not a thing"
