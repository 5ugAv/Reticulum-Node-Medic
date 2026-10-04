"""Settings ▸ Encrypt my records — what to ask, what to say, what to run.

The Kivy screen is a shell over this. Everything that decides anything lives
here, so it can be tested without a display, which is how the rest of this
codebase splits its screens (``setup_flow`` / ``setup_wizard_screen``). That
split matters more than usual here: nothing in the suite instantiates Kivy, so
logic left in a screen is logic nothing checks.

THIS IS WHERE THE KEYS ARE ACTUALLY SET, and that took finding out. The setup
wizard collects a passphrase, a pattern and a recovery key — and stores NONE of
them. ``first_use`` says so outright ("not a secret, not a passphrase, not a
policy"), and it is right to: a stored passphrase is a passphrase on the card.
But it means the wizard's keys were collected for a vault that did not exist,
and the first time a secret becomes real is HERE, when it wraps the data key.

So this screen does not ask the operator to "re-enter" anything. It sets each
door the way the wizard sets one — passphrase typed twice, recovery key
generated and typed back, pattern drawn twice — because a typo here is
permanent and nothing on the medic can check it against what they meant. The
alternative, prompting for "the passphrase you set during setup" and silently
enrolling whatever gets typed, would produce a vault whose key is a mistyped
string nobody has ever deliberately entered.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from provisioning import recovery_key as rk
from provisioning import records_vault as rv
from provisioning import vault_factors as vf


def covered_lines(home: Optional[str] = None) -> List[Tuple[bool, str]]:
    """``(covered, sentence)`` for what encryption does and does not reach.

    Both halves, always, and the NOT-covered half is not a footnote. An operator
    who believes their node's identity is encrypted when it is not has been
    misled by this screen, and the whole point of records-only is that the node
    keeps working through a power cut — which is a trade, not a free win.
    """
    from provisioning.vault import RECORDS_ROOTS
    root = RECORDS_ROOTS[0]
    return [
        (True, f"Your fleet records in ~/{root} — the registry, who you trust, "
               f"certificates, your LXMF messages, the beacon history."),
        (False, "NOT your mesh identity (~/.reticulum, ~/.lxmd). It stays "
                "readable on purpose, so this node comes back on the air by "
                "itself after a power cut instead of waiting for you."),
        (False, "NOT the offline map. It is public map data — encrypting it "
                "would protect nothing."),
    ]


def blockers(home: Optional[str] = None) -> List[str]:
    """Why encryption cannot be turned on yet — empty when it can.

    Read off the disk rather than off a wizard's in-memory state, because this
    screen is reachable long after that wizard closed.
    """
    out: List[str] = []
    root = rv.records_root(home)
    if not os.path.isdir(root):
        out.append(f"There are no records at {root} yet — nothing to encrypt.")
    try:
        rv._aesgcm(b"\0" * 32)
    except rv.VaultError as exc:
        out.append(str(exc))
    return out


def doors_to_set(policy: Optional[vf.Policy] = None) -> List[Dict[str, str]]:
    """Every door to set, in the order to ask for it.

    The daily door is asked for factor by factor, because that is how it is
    used: ``combine`` needs each part separately, and a pattern cannot be typed
    into the same box as a passphrase.
    """
    pol = policy or vf.load_policy()
    spec = {
        vf.PATTERN: ("pattern", "Draw your unlock pattern.",
                     "This is the pattern you will draw to open your records."),
        vf.PASSPHRASE: ("passphrase", "Choose your passphrase.",
                        "You will type this to open your records."),
        vf.KEYFILE: ("keyfile", "Plug in your USB key.",
                     "This stick becomes a key to your records."),
    }
    out = [{"factor": f, "kind": spec[f][0], "prompt": spec[f][1],
            "detail": spec[f][2], "door": "daily"} for f in pol.ordered]
    if vf.PASSPHRASE not in pol.ordered:
        # The passphrase is enrolled behind EVERY daily door (``can_select``),
        # so it is a slot that has to be set even when the daily unlock is
        # something else entirely.
        out.append({"factor": vf.PASSPHRASE, "kind": "passphrase", "door": "vault",
                    "prompt": "Choose your passphrase.",
                    "detail": "This is enrolled behind your daily unlock, so a "
                              "lost stick or a forgotten pattern cannot shut "
                              "you out. It is also what your records are worth "
                              "— make it a real passphrase."})
    out.append({"factor": "recovery", "kind": "recovery", "door": "vault",
                "prompt": "Write down your recovery key.",
                "detail": "The last resort behind everything else. Nothing on "
                          "this medic can rescue you without it."})
    return out


#: Old name. Kept so nothing silently gets an empty list if a caller lags.
doors_to_prove = doors_to_set


def new_recovery_key() -> str:
    """A fresh recovery key, grouped for writing down."""
    return rk.generate()


def recovery_matches(shown: str, typed: str) -> bool:
    """Did they copy it down correctly? Compared FOLDED, so a handwritten O
    read back as 0 is a match rather than a lockout."""
    return bool(shown) and rk.normalize(shown) == rk.normalize(typed)


def passphrase_problem(text: str) -> Optional[str]:
    """Why *text* is too weak to be the vault's fallback key, or None.

    Same rule the wizard applies — spelled once, in ``vault_factors``, so the
    two screens cannot drift into accepting different passphrases.
    """
    return vf.passphrase_problem(text)


def confirm_problem(first: str, second: str) -> Optional[str]:
    """Why the two typings do not match, or None.

    A passphrase set with a typo is a vault nobody can open, and unlike the
    wizard's there is no later step that would catch it.
    """
    if first != second:
        return "Those two do not match. Type it again."
    return None


def recovery_problem(text: str) -> Optional[str]:
    """Why *text* cannot be the recovery key, or None.

    Checked for SHAPE here and for correctness by the vault. Catching a
    mistyped key at the keyboard is worth doing, because the alternative is
    encrypting the records behind a recovery key that does not exist.
    """
    if not (text or "").strip():
        return "Type the recovery key you wrote down."
    if not rk.is_wellformed(text):
        n, want = len(rk.normalize(text)), rk.GROUPS * rk.GROUP_LEN
        if n != want:
            return (f"That is {n} characters, and a recovery key is {want} "
                    f"({rk.GROUPS} groups of {rk.GROUP_LEN}). Check for a "
                    f"missed or repeated group.")
        return ("That has a character a recovery key never uses. They are "
                "written without I, L, O or U — if you wrote one of those, it "
                "was meant to be 1, 1, 0 or V.")
    return None


def daily_secret(parts: Dict[str, str],
                 policy: Optional[vf.Policy] = None) -> str:
    """The single secret the daily door is wrapped with."""
    pol = policy or vf.load_policy()
    return vf.combine({f: parts[f] for f in pol.ordered}, pol)


def turn_on(parts: Dict[str, str], passphrase: str, recovery: str,
            home: Optional[str] = None,
            policy: Optional[vf.Policy] = None) -> Dict[str, object]:
    """Encrypt the records behind three proven doors.

    The recovery key is NORMALISED before it becomes a secret. It is written by
    hand and typed back later, possibly by someone else, and Crockford base32
    exists precisely because O/0 and I/1 get confused — folding here means the
    key that opens the vault is the key as WRITTEN, not as typed.
    """
    return rv.enable_vault(rv.records_root(home),
                           daily_secret(parts, policy),
                           passphrase,
                           rk.normalize(recovery))


def turn_off(secret: str, home: Optional[str] = None,
             door: Optional[str] = None) -> Dict[str, object]:
    """Decrypt everything and remove the keyring."""
    return rv.disable_vault(rv.records_root(home), secret)


def state(home: Optional[str] = None) -> Dict[str, object]:
    """Everything the screen needs to draw itself, asked of the disk."""
    root = rv.records_root(home)
    on = rv.is_vault(root)
    doors: List[str] = []
    if on:
        try:
            doors = rv.load_keyring(root).names()
        except rv.VaultError:
            doors = []
    return {"on": on, "root": root, "doors": doors,
            "blockers": blockers(home), "covered": covered_lines(home)}


def headline(st: Dict[str, object]) -> str:
    """The one sentence at the top. Says the state, not a reassurance."""
    if st["on"]:
        n = len(st["doors"]) or "no"
        return (f"Your records on this card are encrypted. {n} "
                f"{'way' if n == 1 else 'ways'} in: "
                f"{', '.join(st['doors']) or 'none readable'}.")
    return ("Your records on this card are NOT encrypted. Anyone who takes the "
            "card can read them.")
