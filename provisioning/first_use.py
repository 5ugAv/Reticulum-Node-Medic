"""Has this Node Medic ever been set up — and is it still someone else's?

Operator, 2026-08-11: "there should be a set up process that implements the
security and walks the user through the different functions of the medic."

A medic arrives as a Raspberry Pi in a case that boots straight to a front page
of six mode cards. Nothing on that page says which one to press first, nothing
asks for a password, and nothing explains that the vault is off. The operator
who built it knows; nobody else does. THAT is what this marker exists for: it
records whether the walkthrough has ever been run on THIS device, so the medic
can put itself in front of the operator once, and then never nag again.

WHAT IT DOES NOT RECORD. Not a secret, not a passphrase, not a policy — the
policy lives in ``provisioning.vault_factors.save_policy`` and the secrets live
nowhere. This file answers exactly two questions: has anyone walked the setup on
this medic, and did they configure the security or step past it.

WHY 'SKIPPED' IS ITS OWN ANSWER, kept apart from 'completed'. An operator in a
field with a dying node does not want a nine-screen ceremony first, and a medic
that refuses to be useful until they finish one will simply be worked around.
So skipping is allowed and remembered — but remembered as SKIPPED, so Settings
can still offer the security setup afterwards without the boot screen reappearing
every time the medic is powered on.

WHY A CORRUPT MARKER MEANS 'NOT SET UP'. The failure is asymmetric. Reading a
truncated file as "already done" hides the setup from a brand-new operator, and
they have no way to know it exists. Reading it as "not done" costs a returning
operator one screen they can dismiss. When the tool does not know, it must not
guess in the direction that removes the operator's choice.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Mapping, Optional

#: Where the marker lives. Beside the vault policy and the language pref, in the
#: medic's own config directory — NOT inside the vault, since the wizard has to
#: be able to run before there is a vault to be inside.
MARKER_PATH = os.path.expanduser("~/.reticulum-node-medic/first_use.json")

#: Bumped when the wizard gains a step an already-set-up medic ought to see.
#: Nothing reads it yet; it is written so that a future version CAN tell an old
#: completion from a current one without guessing from a timestamp.
CURRENT_VERSION = 1


@dataclass(frozen=True)
class Record:
    """What happened the last time the setup walkthrough was run here."""

    completed: bool = False
    #: The operator reached the end of the walkthrough but stepped past the
    #: security part. True does NOT mean anything is unprotected — it means the
    #: medic was not the thing that protected it.
    security_skipped: bool = False
    #: Epoch seconds. Recorded for the About screen, never for a decision: this
    #: medic can boot with a wrong clock (no RTC, see the clock-sync check in
    #: PROBE), so a comparison against "now" would be a comparison against a lie.
    at: float = 0.0
    version: int = 0

    def to_dict(self) -> dict:
        return {"completed": self.completed,
                "security_skipped": self.security_skipped,
                "at": self.at, "version": self.version}

    @classmethod
    def from_dict(cls, data: Mapping) -> "Record":
        return cls(completed=bool(data.get("completed")),
                   security_skipped=bool(data.get("security_skipped")),
                   at=float(data.get("at") or 0.0),
                   version=int(data.get("version") or 0))


def load(path: str = MARKER_PATH) -> Record:
    """The recorded setup state — a blank Record if there is none or it is
    unreadable. Never raises: a field tool must not fail to boot over a settings
    file, and see the module docstring for why the doubt resolves to 'not set
    up' rather than to 'done'."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return Record()
        return Record.from_dict(data)
    except (OSError, ValueError, TypeError):
        return Record()


def save(record: Record, path: str = MARKER_PATH, now=None) -> bool:
    """Write the marker. Returns True on success.

    TMP-THEN-REPLACE, like ``save_policy``. This medic is a Pi with no RTC that
    gets its power pulled — that is how the SD corruption in [[sd-reliability]]
    happens — and a marker half-written at the moment of a power cut must read as
    absent on the next boot, not as a truncated file that json happens to parse.
    """
    stamped = Record(completed=record.completed,
                     security_skipped=record.security_skipped,
                     at=record.at or (now() if now else time.time()),
                     version=record.version or CURRENT_VERSION)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(stamped.to_dict()).encode())
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def clear(path: str = MARKER_PATH) -> bool:
    """Forget that setup was ever run — the medic is starting over for someone
    else.

    [[networks-outlast-builders]] is the whole point of the project: a node stays
    useful when its keeper moves away or dies, and so must the tool that builds
    them. A medic handed to the next person has to be able to introduce itself
    from nothing, which means the walkthrough has to be resettable.

    Returns True when the marker is gone afterwards — INCLUDING when it was never
    there. "Already absent" is the state the caller asked for, and reporting it as
    a failure would send a Settings screen into an error path for a no-op.
    """
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def is_first_use(path: str = MARKER_PATH) -> bool:
    """Should the medic show the setup walkthrough instead of the front page?

    Only on a medic that has never finished it. A SKIPPED security section does
    not bring the boot screen back — Settings keeps the door open instead (see
    ``security_outstanding``), because a screen that reappears on every power-up
    trains the operator to dismiss it without reading.
    """
    return not load(path).completed


def security_outstanding(path: str = MARKER_PATH) -> bool:
    """True when nobody has yet chosen how this medic locks its records.

    Both cases: never set up at all, and set up with the security part skipped.
    Settings uses it to keep the entry visible and marked, so the operator who
    said "not now" in a field has somewhere to say "now" from.
    """
    rec = load(path)
    return (not rec.completed) or rec.security_skipped


def mark_completed(security_skipped: bool, path: str = MARKER_PATH,
                   now=None) -> bool:
    """Record that the walkthrough was finished.

    THIS IS NOT A CLAIM ABOUT ENCRYPTION. It says the operator was shown the
    choices and made them; whether a vault exists on this card is
    ``provisioning.vault``'s question, and the summary screen asks it there
    rather than inferring it from this file.
    """
    return save(Record(completed=True, security_skipped=bool(security_skipped)),
                path, now=now)
