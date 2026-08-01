"""The vault's RECOVERY KEY — the one way back in after a forgotten passphrase.

The medic's records vault is protected by a passphrase only the operator knows.
That is the point: a stolen SD card can't be opened. But it means a forgotten
passphrase would destroy the fleet's records forever, so — exactly like
FileVault or BitLocker — a second, independent key is generated at setup and
shown ONCE for the operator to write down.

Design decisions, deliberately:

* **No server, ever.** A recovery service would mean somebody else holds a key
  to the operator's fleet — the opposite of the project's anonymity ethos
  ([[anonymity-ethos]]). The key exists only where the operator puts it.
* **It is never stored on the medic.** It goes into its own LUKS keyslot (the
  container supports 8), so the medic holds only material that can *verify* the
  key, never the key itself.
* **Crockford-style alphabet** — no I/L/O/U, so a handwritten key can't be
  misread (1/I, 0/O) and can't accidentally spell words. Input is normalised
  case-insensitively and those confusable characters are folded, so a operator
  who writes "O" for zero still gets in.
* **160 bits of entropy** in 32 characters — far beyond guessing, while staying
  short enough to copy onto paper in five groups.

Pure stdlib, no Kivy: the format and its checking are unit-tested, and the
screens are just presentation over this.
"""

from __future__ import annotations

import re
import secrets
from typing import List

#: Crockford base32 minus the ambiguous letters. 32 symbols = 5 bits each.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 8 groups of 4 = 32 symbols = 160 bits.
GROUP_LEN = 4
GROUPS = 8

#: Characters a human might write instead of the canonical symbol.
_FOLD = {"I": "1", "L": "1", "O": "0", "U": "V"}


def generate() -> str:
    """A fresh recovery key, formatted for writing down (groups of 4)."""
    symbols = "".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN * GROUPS))
    return format_key(symbols)


def format_key(symbols: str) -> str:
    """Group a bare symbol string into ``XXXX-XXXX-…`` for display."""
    bare = normalize(symbols)
    return "-".join(bare[i:i + GROUP_LEN]
                    for i in range(0, len(bare), GROUP_LEN))


def normalize(text: str) -> str:
    """Fold a typed/handwritten key to its canonical symbols: uppercase, no
    separators or spaces, confusable characters mapped (I/L→1, O→0, U→V)."""
    out = []
    for ch in (text or "").upper():
        if ch in ("-", " ", "\t", "\n", "_"):
            continue
        ch = _FOLD.get(ch, ch)
        out.append(ch)
    return "".join(out)


def is_wellformed(text: str) -> bool:
    """True if *text* could be a recovery key (right length, right alphabet).
    Says nothing about whether it is THE key — only the vault can decide that."""
    bare = normalize(text)
    if len(bare) != GROUP_LEN * GROUPS:
        return False
    return all(c in ALPHABET for c in bare)


def groups(text: str) -> List[str]:
    """The key split into display groups (for a large, readable layout)."""
    bare = normalize(text)
    return [bare[i:i + GROUP_LEN] for i in range(0, len(bare), GROUP_LEN)]


def entropy_bits() -> int:
    return GROUP_LEN * GROUPS * 5


#: Attempts allowed on the passphrase before the screen offers the recovery key
#: (operator spec 2026-08-02). Not a lockout — the medic must never brick
#: itself — just the point at which it stops assuming a typo.
ATTEMPTS_BEFORE_RECOVERY = 3


def should_offer_recovery(failed_attempts: int) -> bool:
    return failed_attempts >= ATTEMPTS_BEFORE_RECOVERY
