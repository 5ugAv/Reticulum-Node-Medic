"""How the operator chooses to lock the vault: pattern, passphrase, USB key.

Operator, 2026-08-11: "give the user an option as to what level of security
they want. just pattern unlock or pattern and password or pattern password and
USB key. or a mix of those."

So the vault does not have ONE unlock method. It has a POLICY — an ordered list
of factors, any combination — and all of them are folded into the single
passphrase that ``cryptsetup`` sees. LUKS never learns there was more than one:
it gets one secret, and its own argon2id does the slow, memory-hard stretching
that makes an offline guess expensive.

    pattern                      easiest, weakest
    pattern + passphrase         something you know twice over
    pattern + USB key            no stick, no unlock — even with the pattern
    all three                    for a medic carrying other people's fleets

WHY A PATTERN AT ALL, given it is the weak one. This is a touchscreen appliance
that lives in a bag and gets used in the field with cold hands. A device whose
lock is annoying enough gets left unlocked, and an unlocked vault protects
nothing. The pattern is the floor, not the ceiling — and this module's job is to
report honestly how strong each choice is (``describe``), so the Settings screen
can say "about 20 bits, days of grinding for a thief with your card, not much
against someone determined" instead of a padlock icon.

WHAT THIS MODULE DOES NOT DO. It never touches the disk, never runs cryptsetup,
and never holds a secret longer than the call. It turns choices into one string.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

#: The three things the operator can be asked for.
PATTERN = "pattern"
PASSPHRASE = "passphrase"
KEYFILE = "keyfile"

#: Canonical order, so a policy always presents its factors the same way and
#: two policies with the same factors derive the same key.
FACTOR_ORDER = (PATTERN, PASSPHRASE, KEYFILE)

#: Where the policy (which factors — never the secrets) is recorded.
POLICY_PATH = os.path.expanduser("~/.reticulum-node-medic/vault_policy.json")

#: Dots on the grid, and the shortest pattern accepted. Four is the phone
#: convention. Three would be 504 patterns — guessable by hand.
GRID = 9
MIN_PATTERN_DOTS = 4

#: The filename looked for on a mounted removable device. The stick's CONTENTS
#: are the secret; the name is just how the medic finds it.
KEYFILE_NAME = "nodemedic.key"

#: Domain separator. Fold it in so a vault secret can never collide with some
#: other hash this project computes over the same inputs.
_DOMAIN = b"nodemedic-vault-factors-v1"


class FactorError(ValueError):
    """A factor was missing, malformed, or too weak to accept."""


# --------------------------------------------------------------------------- #
# The pattern.
# --------------------------------------------------------------------------- #

def encode_pattern(dots: Sequence[int]) -> str:
    """A drawn pattern as a canonical string, e.g. ``"0-4-8-7"``.

    Dots are grid positions 0..8, top-left to bottom-right. A dot may not be
    used twice — that is what makes the count knowable, and it matches what a
    finger can actually draw without lifting.

    Unlike the phone convention, a stroke MAY jump over an unused dot. Enforcing
    the skip rule would cut the number of possible patterns by more than half,
    and this is the weakest factor already.
    """
    seq = list(dots)
    if len(seq) < MIN_PATTERN_DOTS:
        raise FactorError(
            f"a pattern needs at least {MIN_PATTERN_DOTS} dots — "
            f"shorter ones are guessable by hand")
    if len(seq) > GRID:
        raise FactorError("a pattern cannot be longer than the grid")
    if any(not isinstance(d, int) or d < 0 or d >= GRID for d in seq):
        raise FactorError(f"dots must be 0..{GRID - 1}")
    if len(set(seq)) != len(seq):
        raise FactorError("a pattern cannot use the same dot twice")
    return "-".join(str(d) for d in seq)


def pattern_space(min_len: int = MIN_PATTERN_DOTS, grid: int = GRID) -> int:
    """How many patterns exist under our rules — counted, not quoted.

    Every ordered selection of *min_len*..*grid* distinct dots.
    """
    total = 0
    for k in range(min_len, grid + 1):
        perms = 1
        for i in range(k):
            perms *= grid - i
        total += perms
    return total


def pattern_bits(min_len: int = MIN_PATTERN_DOTS) -> float:
    """Entropy of a RANDOMLY CHOSEN pattern, in bits.

    A real person's pattern is worth less: people start top-left, use four
    dots, and draw shapes. Treat this as the ceiling, and say so on screen.
    """
    import math
    return math.log2(pattern_space(min_len))


# --------------------------------------------------------------------------- #
# The USB key.
# --------------------------------------------------------------------------- #

def keyfile_secret(data: bytes) -> str:
    """The secret contributed by a keyfile: a hash of its whole contents.

    Hashed rather than used raw so the file can be any size and any format, and
    so nothing that later gets logged or compared can leak the file itself.
    """
    if not data:
        raise FactorError("the key file on the USB stick is empty")
    if len(data) < 16:
        raise FactorError("the key file is too short to be a key (< 16 bytes)")
    return hashlib.sha256(_DOMAIN + b"\x00keyfile\x00" + data).hexdigest()


def find_keyfile(media_root: str = "/media", name: str = KEYFILE_NAME,
                 lister=None) -> Optional[str]:
    """Path to the key file on a mounted removable device, or None.

    Looks one and two levels down (``/media/<user>/<label>/`` is how a desktop
    Pi mounts a stick). Returns the FIRST match — with two sticks plugged in the
    operator gets whichever the system mounted first, so the screen says which
    path it used.
    """
    walk = lister or _default_lister
    for path in walk(media_root):
        if os.path.basename(path) == name:
            return path
    return None


def _default_lister(media_root: str) -> List[str]:
    found: List[str] = []
    for depth1 in _safe_listdir(media_root):
        p1 = os.path.join(media_root, depth1)
        for entry in _safe_listdir(p1):
            found.append(os.path.join(p1, entry))
            p2 = os.path.join(p1, entry)
            for entry2 in _safe_listdir(p2):
                found.append(os.path.join(p2, entry2))
    return found


def _safe_listdir(path: str) -> List[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# The policy.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Policy:
    """Which factors this medic asks for. Never holds a secret."""

    factors: tuple = (PATTERN,)

    def __post_init__(self):
        if not self.factors:
            raise FactorError("a vault needs at least one factor")
        unknown = [f for f in self.factors if f not in FACTOR_ORDER]
        if unknown:
            raise FactorError(f"unknown factor(s): {', '.join(unknown)}")
        if len(set(self.factors)) != len(self.factors):
            raise FactorError("a factor cannot be listed twice")

    @property
    def ordered(self) -> tuple:
        """Factors in canonical order — the order they are folded in, and the
        order the unlock screen asks for them."""
        return tuple(f for f in FACTOR_ORDER if f in self.factors)

    def to_dict(self) -> dict:
        return {"factors": list(self.ordered)}

    @classmethod
    def from_dict(cls, data: Mapping) -> "Policy":
        return cls(tuple(data.get("factors") or (PATTERN,)))


def load_policy(path: str = POLICY_PATH) -> Policy:
    """The recorded policy, or pattern-only if none has been chosen yet.

    A missing or unreadable file must not lock the operator out of their own
    medic, so it falls back to the simplest policy rather than refusing.
    """
    try:
        with open(path) as f:
            return Policy.from_dict(json.load(f))
    except (OSError, ValueError, FactorError):
        return Policy()


def save_policy(policy: Policy, path: str = POLICY_PATH) -> bool:
    """Record the policy 0600. Returns True on success.

    THE POLICY IS NOT A SECRET, but it does tell a thief which factors to
    prepare for, so it is no more readable than it needs to be.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(policy.to_dict()).encode())
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Folding the factors into one secret.
# --------------------------------------------------------------------------- #

def combine(parts: Mapping[str, str], policy: Optional[Policy] = None) -> str:
    """The single passphrase cryptsetup will see, from the supplied factors.

    LENGTH-PREFIXED AND NAMED. Concatenating the parts would mean a pattern of
    "0-4-8-7" with passphrase "x" produced the same secret as pattern "0-4-8"
    with passphrase "7x" — different unlocks opening one vault. Each part goes
    in as name, length, value, so no rearrangement collides.

    Every factor in the policy must be present: a partial unlock is not a weaker
    unlock, it is a wrong one, and it must fail loudly rather than quietly
    deriving a key that will not open anything.
    """
    pol = policy or Policy(tuple(parts.keys()))
    missing = [f for f in pol.ordered if not parts.get(f)]
    if missing:
        raise FactorError(f"missing factor(s): {', '.join(missing)}")
    extra = [f for f in parts if f not in pol.ordered]
    if extra:
        raise FactorError(f"factor(s) not in this vault's policy: {', '.join(extra)}")

    h = hashlib.sha256()
    h.update(_DOMAIN)
    for name in pol.ordered:
        value = parts[name].encode("utf-8")
        h.update(b"\x00")
        h.update(name.encode("ascii"))
        h.update(b"\x00")
        h.update(str(len(value)).encode("ascii"))
        h.update(b"\x00")
        h.update(value)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Saying honestly what a choice buys.
# --------------------------------------------------------------------------- #

#: A rough floor for a typed passphrase, in bits. Deliberately pessimistic: it
#: assumes the operator picks something human. Used only for the on-screen
#: estimate, never for a decision.
TYPED_PASSPHRASE_BITS = 30.0

#: A keyfile is generated random bytes, so its entropy is the key's, not a
#: person's. Capped at the hash width because that is the real bound.
KEYFILE_BITS = 256.0


def strength_bits(policy: Policy) -> float:
    """Total entropy of a policy, in bits, for a RANDOM choice of each factor.

    Additive because the factors are independent. Optimistic for the human
    ones — see ``describe`` for what to actually put on screen.
    """
    per = {PATTERN: pattern_bits(),
           PASSPHRASE: TYPED_PASSPHRASE_BITS,
           KEYFILE: KEYFILE_BITS}
    return sum(per[f] for f in policy.ordered)


def describe(policy: Policy) -> Dict[str, str]:
    """What to show the operator when they pick a level: what it costs them,
    and what it does not protect against. Plain sentences, no padlock icons.
    """
    names = {PATTERN: "a pattern", PASSPHRASE: "a passphrase",
             KEYFILE: "a USB key"}
    asks = " and ".join(names[f] for f in policy.ordered)
    bits = strength_bits(policy)

    if KEYFILE in policy.ordered and len(policy.ordered) > 1:
        strength = ("Strong. The key on the stick is random, not remembered, so "
                    "guessing is not the way in. Whoever holds the stick and "
                    "knows the rest holds the vault.")
    elif KEYFILE in policy.ordered:
        strength = ("Strong against guessing — the key on the stick is random, "
                    "not remembered. But the stick IS the vault: whoever holds "
                    "it, holds everything, with nothing left to know.")
    elif PASSPHRASE in policy.ordered and PATTERN in policy.ordered:
        strength = ("Good. Two things to know, and the slow unlock makes each "
                    "guess expensive.")
    elif PASSPHRASE in policy.ordered:
        strength = ("Reasonable, and it depends entirely on the passphrase. A "
                    "few common words is not one.")
    else:
        strength = (f"Weakest of the options: about {pattern_space():,} possible "
                    "patterns. The slow unlock still means days of grinding for "
                    "someone with your card — but only days.")

    warnings = []
    if PATTERN in policy.ordered:
        warnings.append("A pattern leaves a smudge on the glass. Wipe the "
                        "screen, and do not draw a plain L or Z.")
    if KEYFILE in policy.ordered:
        warnings.append("Lose the stick and the vault stays shut. Keep the "
                        "recovery key somewhere else entirely, and make a "
                        "second stick.")
    if PASSPHRASE in policy.ordered:
        warnings.append("A forgotten passphrase is not recoverable from the "
                        "device — only the recovery key gets you back in.")

    # No convenience door. The passphrase is IN the unlock, so it cannot cap
    # the vault the way a standalone fallback slot would — and the only other
    # way in is a 160-bit key on paper.

    return {
        "asks": f"Unlocking asks for {asks}.",
        "strength": strength,
        "bits": f"about {bits:.0f} bits if each part is chosen at random",
        "warnings": " ".join(warnings),
        "field": ("The medic still boots, rejoins the mesh and relays while "
                  "locked. Only its own records wait for you."),
        "fallback": ("The recovery key is the only way back in — there is no "
                     "back door, which is what makes the level above worth "
                     "what it says. Keep it away from the medic."),
    }


# --------------------------------------------------------------------------- #
# The levels offered on screen.
# --------------------------------------------------------------------------- #

#: Ordered weakest-first, because that is the order a person reads and the
#: honest way to present a choice: the easy option is the one you scroll PAST to
#: reach the strong ones, not the one hidden at the bottom.
#:
#: Not every combination is offered. ``Policy`` still accepts any mix — a menu
#: of seven permutations is a menu nobody reads. These THREE are the ladder, and
#: a passphrase is in every one of them: it carries the strength, and the
#: pattern and the USB key are added TO it rather than standing beside it as a
#: back door that would cap the whole vault. The model underneath is unchanged,
#: so a different set can be offered later without touching the derivation.
#:
#: (Said "four" until 2026-08-15 — left behind when the pattern-only rung was
#: dropped for maximum strength. A comment that outlives its code is how the
#: next reader learns something false.)
#: The daily-unlock ladder, lightest first. SINGLE-FACTOR rungs returned
#: 2026-08-25 (operator, living with the medic day to day: "it should be
#: pattern OR passphrase OR USB"). This is safe to offer because the daily
#: policy is only ONE of the vault's doors: the recovery key and the
#: mandatory passphrase remain enrolled SLOTS — the ways back in — whatever
#: the operator picks for every day. Each rung's describe() still states
#: plainly what it is worth and what it does not protect from.
LEVELS = (
    Policy((PATTERN,)),
    Policy((PASSPHRASE,)),
    Policy((PATTERN, PASSPHRASE)),
    Policy((KEYFILE,)),
    Policy((PATTERN, PASSPHRASE, KEYFILE)),
)

#: Short names for the chooser. The subtitle comes from describe().
LEVEL_NAMES = {
    (PATTERN,): "Pattern only",
    (PASSPHRASE,): "Passphrase",
    (KEYFILE,): "USB key only",
    (PATTERN, PASSPHRASE): "Pattern + passphrase",
    (PATTERN, PASSPHRASE, KEYFILE): "Pattern + passphrase + USB key",
}


def level_name(policy: "Policy") -> str:
    """A short label for a policy, built from its factors if it is not one of
    the offered levels — so a hand-made policy still shows as something a
    person can read rather than falling back to "Custom"."""
    known = LEVEL_NAMES.get(policy.ordered)
    if known:
        return known
    pretty = {PATTERN: "pattern", PASSPHRASE: "passphrase", KEYFILE: "USB key"}
    return " + ".join(pretty[f] for f in policy.ordered).capitalize()


# --------------------------------------------------------------------------- #
# Enrolment: the ways in, and the rule that there is always a way back.
# --------------------------------------------------------------------------- #
#
# Operator, 2026-08-11: "they need to set a text password first before turning
# security to pattern so they can recover the medic if they forget either."
#
# So the vault does not have ONE way in. LUKS2 holds several key slots for the
# same volume, and this medic uses three:
#
#   recovery key   32 written-down symbols, 160 bits. Generated, never chosen.
#   passphrase     typed. MANDATORY, and enrolled BEFORE a pattern is allowed.
#   daily policy   whatever the operator picked — usually the pattern.
#
# WHAT THAT COSTS, STATED PLAINLY, because it is not free. An attacker with the
# card attacks the WEAKEST slot, not the one you use. Enrolling a passphrase
# alongside a pattern-only policy costs nothing (the pattern was already the
# weakest). But choosing pattern + passphrase + USB key and then keeping a
# passphrase fallback means the vault is worth what that passphrase is worth —
# the USB key protects your daily unlock, not the volume. If the strong policy
# is the point, the fallback passphrase has to be a strong one.
#
# The operator asked for recoverability over that, knowingly: a medic that
# cannot be opened by the person who owns it is a brick, and this is a tool
# people are meant to be able to hand to each other.


@dataclass(frozen=True)
class Enrolment:
    """Which ways into this vault currently exist.

    ``recovery_key_verified`` means the operator typed the key BACK, not merely
    that the medic generated one. A key that was shown on a screen and never
    written down is not a fallback, and with maximum strength it is the only
    one there is.
    """

    passphrase_set: bool = False
    recovery_key_set: bool = False
    recovery_key_verified: bool = False
    policy: Policy = field(default_factory=Policy)


def can_select(policy: Policy, enrolment: Enrolment) -> tuple:
    """(ok, reason) — may this policy be chosen right now?

    TWO RULINGS, BOTH THE OPERATOR'S. 2026-08-11 chose maximum strength:
    every offered level carried the passphrase IN the unlock. 2026-08-25,
    living with the medic day to day, they reversed the daily half: "it
    should be pattern OR passphrase OR USB" — an easy everyday door. What
    SURVIVES from the first ruling is the enrolment: the passphrase must be
    SET (it stays enrolled as a slot — a way back in behind whatever daily
    unlock is picked) and the recovery key must be written down and proven.
    The cost is stated, not hidden: a light daily door caps the vault at that
    door's strength — which is exactly what each level's description says.
    """
    if not enrolment.recovery_key_verified:
        return (False,
                "Write down the recovery key and type it back first. With "
                "maximum strength it is the ONLY way in if you forget your "
                "passphrase or your pattern — nothing on the medic can rescue "
                "you, by design.")
    if not enrolment.passphrase_set:
        return (False,
                "Set the passphrase first. Whatever daily unlock you pick, "
                "it stays enrolled as the way back in behind it.")
    return (True, "")


def effective_bits(enrolment: Enrolment) -> float:
    """Strength of the WEAKEST way in — which is the strength of the vault.

    Since 2026-08-25 a light daily door (pattern-only) may stand beside the
    enrolled passphrase slot — so this minimum is doing real work again: it
    reports the weakest enrolled way in, exactly as its name promises. The
    day the operator picks pattern-only, this number says ~20 bits, and the
    level's own description says the same out loud.
    """
    slots = [strength_bits(enrolment.policy)]
    if enrolment.passphrase_set:
        slots.append(strength_bits(Policy((PASSPHRASE,))))
    if enrolment.recovery_key_set:
        slots.append(RECOVERY_KEY_BITS)
    return min(slots)


#: The generated recovery key: 32 symbols from a 32-character alphabet.
#: Not a person's choice, so its entropy is real. See provisioning.recovery_key.
RECOVERY_KEY_BITS = 160.0


# --------------------------------------------------------------------------- #
# Setting a pattern: twice, and they must match.
# --------------------------------------------------------------------------- #

def confirm_pattern(first: Sequence[int], second: Sequence[int]) -> str:
    """The encoded pattern, if both drawings agree. Raises otherwise.

    Operator, 2026-08-11: "make the user repeat to confirm the pattern when they
    set it."

    A pattern is drawn, not read back — there is nothing on screen afterwards to
    check it against. A single slip while setting it produces a vault whose key
    is a gesture nobody has ever made deliberately, and the operator finds out
    at the worst possible moment. Drawing it twice is the only proof they can
    make it again.
    """
    a = encode_pattern(first)
    b = encode_pattern(second)
    if a != b:
        raise FactorError("The two patterns are different. Draw it again.")
    return a
