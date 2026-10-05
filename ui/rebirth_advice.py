"""When to suggest a rebirth for a node that has stopped answering (task #30).

Operator suggestion, 2026-07-31: surface rebirth as a RECOMMENDATION in the
diagnosis path when a kin node is unresponsive — "not answering — consider a
rebirth if it's physically reachable" — and offer it from node-detail for red
nodes. The wipe-and-rebuild machinery already exists (a805c43); what was missing
was anything pointing at it from the screen where an operator first notices a
node is dead.

THE CONSTRAINT THAT SHAPES ALL OF THIS. A rebirth is an esptool erase over USB.
It requires the board to be PLUGGED INTO THE MEDIC. A node that has gone quiet
on a rooftop across town cannot be rebirthed from here, however red its
dot is.

So this deliberately draws a line between ADVICE and an ACTION:

  * a red node with its board NOT attached gets a sentence — what to consider,
    and that it means physically fetching the thing;
  * only a node whose board is attached to this medic right now is offered a
    button.

Offering a button that cannot work is the exact failure the honesty gate exists
to prevent ([[no-fake-demos-honesty-gate]]): it looks like a repair path, it is
one tap away, and it does nothing. Worse on a diagnostic tool than saying
nothing at all.

AND IT IS NOT THE FIRST THING TO TRY. A rebirth destroys the node's identity and
its certificate. A node can be quiet for reasons a wipe would be an absurd
answer to — a flat battery on a solar node mid-winter, an antenna knocked out of
line, the medic's own radio being the thing at fault. So the advice always names
the cheaper checks first, and rebirth is offered as the last of them.

Pure copy + data, no Kivy — the wording is testable without a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ui.i18n import tr


@dataclass
class Advice:
    """What to suggest about a node that is not answering."""
    #: Short line for the panel. Empty means: say nothing at all.
    headline: str = ""
    #: The cheaper things to try first, in order.
    steps: List[str] = field(default_factory=list)
    #: True only when a rebirth can actually be performed right now.
    offer_rebirth: bool = False
    #: Why a rebirth is not offered, when it isn't.
    rebirth_note: str = ""


def advise(status: str, board_attached: bool = False, name: str = "",
           hours_quiet: Optional[float] = None,
           heard_ever: bool = True) -> Optional[Advice]:
    """What to tell the operator about a node in *status*.

    *heard_ever* — has anything at all ever been heard from this node? An
    unanswered probe promotes an unknown node to "warn" (Registry.status), so
    amber alone does not mean the node is talking, and the advice must not say
    it is.

    *board_attached* — is THIS node's board plugged into the medic right now?
    Only then is a rebirth a thing that can be done rather than a thing to
    consider. Callers that cannot tell must pass False: claiming a repair is
    available when it is not is worse than not offering it.

    Returns None for a healthy node — a screen should not manufacture concern.
    """
    who = name or tr("This node")
    if status in ("ok", "green", ""):
        return None

    if status == "unknown":
        return Advice(
            headline=tr("{who} hasn't been heard from yet.").format(who=who),
            steps=[tr("It may still be starting up — give it a few minutes."),
                   tr("Check the antenna is attached at both ends.")],
            rebirth_note=tr("Too early to consider a rebuild."))

    if status in ("warn", "amber"):
        # AMBER DOES NOT ALWAYS MEAN "ANSWERING". Registry.status promotes an
        # unknown node to warn the moment a probe goes unanswered — the
        # operator ASKED and nothing came back — so a node that has NEVER been
        # heard arrives here amber. Told it "is answering, but not happily"
        # and "a rebirth would be premature, it is still talking", on a page
        # whose own header read "Last heard: never" and "the node is NOT
        # answering right now" (operator, with SolarLove on the screen,
        # 2026-09-09). Three statements, one screen, two of them false.
        if not heard_ever:
            return Advice(
                headline=tr("{who} still hasn't answered.").format(who=who),
                steps=[tr("Nothing has been heard from it yet — the amber is "
                          "the unanswered ping, not a poor reply."),
                       tr("It may still be starting up, switched off, out of "
                          "range, or behind a relay that is asleep."),
                       tr("Check the antenna is attached at both ends.")],
                rebirth_note=tr("Too early to consider a rebuild."))
        return Advice(
            headline=tr("{who} is answering, but not happily.").format(who=who),
            steps=[tr("Try 'Ping node now' — a clean reply clears this."),
                   tr("Check its battery and signal on this page.")],
            rebirth_note=tr("A rebuild would be premature — it is still "
                            "talking."))

    # alert / red / down — the case this task is about.
    quiet = ""
    if hours_quiet is not None and hours_quiet >= 1:
        quiet = tr(" It has been quiet for about {h} hours.").format(
            h=int(hours_quiet))
    steps = [
        tr("Try 'Ping node now' first — the mesh path may simply be stale."),
        tr("If it runs on solar, it may be waiting for sun rather than broken."),
        tr("Check this medic's own radio is healthy (Settings ▸ Self Diagnose) — "
           "a deaf medic makes every node look dead."),
    ]
    if board_attached:
        return Advice(
            headline=tr("{who} isn't answering.{quiet}").format(
                who=who, quiet=quiet),
            steps=steps + [tr("Its board is plugged into Node Medic now, so it "
                              "can be wiped and built fresh.")],
            offer_rebirth=True)
    return Advice(
        headline=tr("{who} isn't answering.{quiet}").format(who=who, quiet=quiet),
        steps=steps,
        rebirth_note=tr("If none of that helps and you can physically reach it, "
                        "bring the board back to Node Medic and rebuild it — "
                        "that wipes the node and builds it fresh. It can't be "
                        "done over the air."))
