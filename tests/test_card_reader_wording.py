"""No screen may send the operator to a card slot on Node Medic.

The medic has NO native card slot. The only one on the machine holds the card
the medic is running from. MITOSIS learned this and recorded why
(ui/screens/mitosis_screen.py:314): "a person taking the old wording literally
goes looking for a slot and finds the one that must not be touched."

The Pi birth path kept the old wording anyway — while its OWN animation
(ui/widgets/birth_anims.py:947) already said "it has no native card slot".
Found by audit 2026-09-09. This is the one instruction fault in the flow that
can destroy the tool rather than waste time, so it gets its own guard.
"""

import re

from ui.birth_guide_flow import guide_steps
from ui.pi_connectors import PI_CONNECTORS

#: Wording that reads as "there is a slot on the medic — put the card in it".
#: Note the USB escape: "plug the reader into any USB socket on Node Medic" is
#: the CORRECT sentence and must not trip this. The fault is a card (or a
#: reader) entering the medic with no USB socket named — that is what sends
#: someone hunting for a slot. (My first pattern flagged the fix itself.)
_SLOT = re.compile(
    r"(card|sd)\b[^.]{0,40}\binto\s+node medic"
    r"|(card\s+)?reader\s+on\s+node medic",
    re.I)


def _implies_a_slot(text: str) -> bool:
    if not _SLOT.search(text):
        return False
    return "usb" not in text.lower()   # routed through a USB socket = fine


def _bodies():
    for key in list(PI_CONNECTORS) + [""]:
        for i, s in enumerate(guide_steps("pi", key)):
            yield f"pi/{key} step {i + 1}", (s.get("title", ""),
                                             s.get("body", ""),
                                             s.get("hint", "") or "")


def test_no_step_points_the_card_at_the_medic():
    bad = [(n, t) for n, parts in _bodies() for t in parts
           if _implies_a_slot(t)]
    assert not bad, (
        "sends the operator to a card slot the medic does not have: "
        + "; ".join(f"{n}: {t[:70]}" for n, t in bad))


def test_the_card_step_names_the_reader_and_a_usb_socket():
    """The correct mental model: card -> reader -> USB. Say all three."""
    hit = [parts for _, parts in _bodies()
           if any("card reader" in t.lower() or "your card reader" in t.lower()
                  for t in parts)]
    assert hit, "no step explains that the card goes into a READER"
    joined = " ".join(t.lower() for parts in hit for t in parts)
    assert "usb" in joined, "must say the reader plugs into a USB socket"


def test_it_warns_off_the_medics_own_card():
    joined = " ".join(t.lower() for _, parts in _bodies() for t in parts)
    assert "running the tool" in joined or "do not touch" in joined, \
        "must warn against the card the medic is running from"
