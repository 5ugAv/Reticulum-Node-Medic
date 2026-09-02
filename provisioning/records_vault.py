"""Encrypt the medic's RECORDS at rest — per file, no cryptsetup, no root.

WHY NOT LUKS.  ``provisioning/vault.py`` builds a LUKS2-on-a-file container and
it works, but three things stop it being the thing that ships:

* ``cryptsetup`` is NOT INSTALLED on the medic and is NOT in ``assets/packages``.
  Offline is the whole point — a clone with no internet cannot apt-get it, so a
  LUKS vault is a feature the second medic could never turn on.
* A locked container cannot be opened without a human at the screen.  That is
  fine for a laptop and wrong for a node: after a power cut the medic would sit
  dark until someone walks to it.  Operator, 2026-08-02 and again 2026-09-02:
  "definitely lean to the side of keeping nodes active in the wild".
* It needs root for losetup/cryptsetup/mount.  Every one of those is a new
  passwordless sudoers rule, on a device where wildcard sudoers rules have
  already been an escalation once (``sudoers-wildcard-escalation``).

This module needs none of that.  ``hashlib.scrypt`` is stdlib; AES-256-GCM comes
from ``cryptography``, which is already installed (43.0.0) and already carried
in the wheelhouse (49.0.0 and 50.0.0), so a clone can do this with the network
unplugged.  Nothing here runs as root and nothing here has to be unlocked for
rnsd and lxmd to start, because the mesh identity is not in scope — see
``RECORDS_ROOTS``: ~/.reticulum and ~/.lxmd stay in the clear, deliberately.

THE KEYSLOT MODEL, which is the part vault.py never actually built.  One random
32-byte DATA KEY encrypts the files.  That data key is then wrapped once per
enrolled door, and each wrapping is independent:

    daily door  (pattern / passphrase / USB key, per the chosen policy)
    passphrase  (always enrolled — ``can_select`` refuses to proceed without it)
    recovery key (written down, typed back, proven before any level is offered)

Any one of them opens the vault; none of them can be derived from another.  That
is what LUKS calls a keyslot, and it is what every screen in the setup flow has
been promising since 2026-08-11 — "the passphrase stays enrolled as the way back
in behind whatever you pick", "the recovery key is the last resort".  Neither
sentence was true: ``luksAddKey`` appears nowhere in this repo, so the LUKS path
only ever created ONE slot, from ``vault_factors.combine()``.  The recovery key
the operator is forced to write down and type back opened nothing at all.

WHAT AN ATTACKER ATTACKS.  The weakest slot, always — that is the cost of having
more than one door, and it is why ``effective_bits`` takes a minimum rather than
a sum.  Wrapping the data key three ways does not make it three times harder; it
makes it exactly as hard as the easiest wrapping.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from provisioning.vault import ScryptParams, derive_key

#: scrypt cost for wrapping the data key. MEASURED on the medic's own Pi 5,
#: 2026-09-03: N=2**17 ran in 0.42s, 2**18 in 0.80s, 2**19 in 1.62s, and 2**20
#: exceeds hashlib's maxmem ceiling. 2**19 is the last rung that fits, and it
#: lands on the ~2s the LUKS design already aimed for (Argon2idParams.iter_time_ms)
#: while costing an attacker 512 MiB PER GUESS — which is the number that
#: matters, because it caps how many guesses a GPU can run side by side.
#:
#: Deliberately NOT vault.py's ScryptParams default (128 MiB): that one derives
#: the LUKS USB keyfile and changing it would invalidate existing key files.
WRAP_PARAMS = ScryptParams(n=1 << 19, r=8, p=1)

#: Bumped when the on-disk shape changes in a way an older medic cannot read.
FORMAT_VERSION = 1

#: Magic bytes at the head of every encrypted file. Lets ``is_encrypted`` answer
#: from the first few bytes instead of trying to decrypt, and stops a half-migrated
#: directory from being double-encrypted on a second pass.
MAGIC = b"RNMVAULT1"

#: AES-GCM nonce length. 96 bits is the size GCM is defined for; anything else
#: forces an internal re-hash and buys nothing.
NONCE_LEN = 12

#: Per-slot salt. 16 is the floor ``derive_key`` enforces; 32 costs nothing.
SALT_LEN = 32

#: Directories under the records root that are NOT encrypted.
#:
#: MEASURED on the live medic, 2026-09-03: the records root is 716 MB, and 714
#: of those are ``maps/offline.mbtiles`` and its terrain companion — public
#: OpenStreetMap tiles. Encrypting them took 20.5s of the 20.7s total and
#: protected nothing: the same tiles ship in every copy of the map. Excluding
#: them leaves ~2 MB of things that ARE worth encrypting (the registry, the
#: certificates, the LXMF store, trust_hmac_key) and turns a 20-second
#: operation into an instant one.
#:
#: This is a whitelist-by-omission and it deserves the scrutiny that implies:
#: anything added here is data an operator will believe is encrypted and is not.
SKIP_DIRS = ("maps",)

#: The wrapped-key file, alongside the records it opens. Public by design —
#: it holds only salts, nonces and ciphertext, and losing it loses the vault.
KEYRING_NAME = "keyring.json"

#: Slot names. Free-form strings on disk, but these three are the ones the
#: setup flow installs and the ones ``weakest_slot`` knows how to rank.
SLOT_DAILY = "daily"
SLOT_PASSPHRASE = "passphrase"
SLOT_RECOVERY = "recovery"


class VaultError(Exception):
    """Anything that stops a vault operation. Never carries key material."""


def _aesgcm(key: bytes):
    """Import at call time, not module import.

    ``cryptography`` is a compiled extension; a medic mid-clone can have the
    wheel not yet installed, and this module is imported by the setup screens
    that RUN that clone. Failing at the call gives a sentence the operator can
    act on instead of an ImportError traceback on a screen they cannot leave.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:                                  # pragma: no cover
        raise VaultError(
            "The encryption library is not installed on this medic. It ships in "
            "assets/packages (cryptography-*.whl) — finish the setup that "
            "installs the carried wheels, then try again."
        ) from exc
    return AESGCM(key)


# --------------------------------------------------------------------------- #
# Slots: one wrapped copy of the data key per door.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Slot:
    """One door. Holds a wrapping of the data key, never the data key itself."""

    name: str
    salt: bytes
    nonce: bytes
    wrapped: bytes
    scrypt_n: int
    scrypt_r: int
    scrypt_p: int

    def to_json(self) -> Dict[str, object]:
        b64 = lambda b: base64.b64encode(b).decode("ascii")   # noqa: E731
        return {"name": self.name, "salt": b64(self.salt),
                "nonce": b64(self.nonce), "wrapped": b64(self.wrapped),
                "scrypt": {"n": self.scrypt_n, "r": self.scrypt_r,
                           "p": self.scrypt_p}}

    @staticmethod
    def from_json(d: Dict[str, object]) -> "Slot":
        try:
            un = lambda k: base64.b64decode(d[k])             # noqa: E731
            s = d["scrypt"]
            return Slot(name=str(d["name"]), salt=un("salt"), nonce=un("nonce"),
                        wrapped=un("wrapped"), scrypt_n=int(s["n"]),
                        scrypt_r=int(s["r"]), scrypt_p=int(s["p"]))
        except Exception as exc:
            raise VaultError(f"keyring slot is unreadable: {exc}") from exc

    @property
    def params(self) -> ScryptParams:
        return ScryptParams(n=self.scrypt_n, r=self.scrypt_r, p=self.scrypt_p)


def wrap_data_key(data_key: bytes, secret: str, name: str,
                  params: Optional[ScryptParams] = None) -> Slot:
    """Wrap *data_key* so that *secret* — and only *secret* — unwraps it.

    The slot NAME is authenticated as GCM associated data. Without that, a
    keyring could be edited to relabel the recovery slot as the daily slot, and
    the unlock screen would present a 160-bit door as though it were the weak
    one the operator chose — or the reverse, which is worse.
    """
    if len(data_key) != 32:
        raise VaultError("data key must be 32 bytes")
    if not secret:
        raise VaultError(f"refusing to wrap the {name} slot with an empty secret")
    p = params or WRAP_PARAMS
    salt = secrets.token_bytes(SALT_LEN)
    kek = derive_key(secret, salt, p)
    nonce = secrets.token_bytes(NONCE_LEN)
    aad = name.encode("utf-8")
    wrapped = _aesgcm(kek).encrypt(nonce, data_key, aad)
    # Read it straight back with the key still in hand. Free — the expensive
    # part is the scrypt derivation above, and this reuses it — and it means a
    # Slot that exists is a Slot that opens.
    if _aesgcm(kek).decrypt(nonce, wrapped, aad) != data_key:
        raise VaultError(f"the {name} slot did not verify — nothing was written")
    return Slot(name=name, salt=salt, nonce=nonce, wrapped=wrapped,
                scrypt_n=p.n, scrypt_r=p.r, scrypt_p=p.p)


def unwrap_data_key(slot: Slot, secret: str) -> Optional[bytes]:
    """The data key, or None if *secret* is wrong for this slot.

    None rather than an exception: a wrong passphrase is the ordinary case at an
    unlock screen, not an error, and the caller must be free to try the next
    slot without distinguishing "wrong secret" from "corrupt slot" — that
    distinction is exactly what a padding-oracle style probe would want.
    """
    if not secret:
        return None
    kek = derive_key(secret, slot.salt, slot.params)
    try:
        return _aesgcm(kek).decrypt(slot.nonce, slot.wrapped,
                                    slot.name.encode("utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# The keyring: every door onto one data key.
# --------------------------------------------------------------------------- #

@dataclass
class Keyring:
    slots: List[Slot] = field(default_factory=list)
    version: int = FORMAT_VERSION

    def names(self) -> List[str]:
        return [s.name for s in self.slots]

    def add(self, data_key: bytes, secret: str, name: str,
            params: Optional[ScryptParams] = None) -> "Keyring":
        """Install another door. Replaces a slot of the same name.

        Replacing rather than appending is what makes "change my passphrase" a
        one-line operation instead of leaving the OLD passphrase working — the
        LUKS equivalent bug is a stale keyslot nobody remembers adding.
        """
        self.slots = [s for s in self.slots if s.name != name]
        self.slots.append(wrap_data_key(data_key, secret, name, params))
        return self

    def remove(self, name: str) -> "Keyring":
        """Take a door away — but never the last one, and never the recovery
        key, which is the slot the operator was made to write down."""
        remaining = [s for s in self.slots if s.name != name]
        if not remaining:
            raise VaultError("that is the only way in — the vault would be lost")
        if name == SLOT_RECOVERY:
            raise VaultError(
                "the recovery key is the last resort; removing it would make a "
                "forgotten passphrase final")
        self.slots = remaining
        return self

    def open(self, secret: str,
             only: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str]]:
        """Try *secret*: (data_key, slot_name), or (None, None) if nothing opens.

        Pass *only* when the door is already known — the unlock screen knows
        which one the operator chose, because they picked it. MEASURED on the
        Pi 5: one slot is 1.6s, all three are 4.8s, and an unlock screen that
        takes five seconds is one operators will assume has hung.

        Without *only*, every slot is tried and none returns early. That is for
        the recovery path, where the operator types a string and the medic does
        not know which door it is. Not returning early there keeps the wrong-guess
        and right-guess times equal, so a stopwatch cannot tell an attacker they
        have found a working key before the screen does.
        """
        candidates = ([s for s in self.slots if s.name == only] if only
                      else self.slots)
        found_key: Optional[bytes] = None
        found_name: Optional[str] = None
        for s in candidates:
            dk = unwrap_data_key(s, secret)
            if dk is not None and found_key is None:
                found_key, found_name = dk, s.name
        return found_key, found_name

    def to_json(self) -> str:
        return json.dumps({"version": self.version,
                           "slots": [s.to_json() for s in self.slots]},
                          indent=2, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Keyring":
        try:
            d = json.loads(text)
        except Exception as exc:
            raise VaultError(f"the keyring file is not readable JSON: {exc}") from exc
        ver = int(d.get("version", 0))
        if ver > FORMAT_VERSION:
            raise VaultError(
                f"this vault was written by a newer medic (format {ver}, this "
                f"one understands {FORMAT_VERSION}). Update before opening it.")
        slots = [Slot.from_json(s) for s in d.get("slots", [])]
        if not slots:
            raise VaultError("the keyring has no slots — nothing can open it")
        return Keyring(slots=slots, version=ver)


def new_data_key() -> bytes:
    """A fresh 32-byte data key from the OS CSPRNG."""
    return secrets.token_bytes(32)


# --------------------------------------------------------------------------- #
# File encryption.
# --------------------------------------------------------------------------- #

def is_encrypted(blob: bytes) -> bool:
    return blob[:len(MAGIC)] == MAGIC


def encrypt_bytes(data_key: bytes, plaintext: bytes, rel_path: str) -> bytes:
    """MAGIC || nonce || ciphertext, with *rel_path* authenticated.

    Binding the path means a file cannot be moved or renamed inside the vault
    without detection — swapping a node's record for another node's is exactly
    the edit that would be worth making, and it needs no key to attempt.
    """
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = _aesgcm(data_key).encrypt(nonce, plaintext, rel_path.encode("utf-8"))
    return MAGIC + nonce + ct


def decrypt_bytes(data_key: bytes, blob: bytes, rel_path: str) -> bytes:
    if not is_encrypted(blob):
        raise VaultError(f"{rel_path} is not an encrypted vault file")
    body = blob[len(MAGIC):]
    if len(body) < NONCE_LEN + 16:
        raise VaultError(f"{rel_path} is truncated")
    nonce, ct = body[:NONCE_LEN], body[NONCE_LEN:]
    try:
        return _aesgcm(data_key).decrypt(nonce, ct, rel_path.encode("utf-8"))
    except Exception as exc:
        raise VaultError(
            f"{rel_path} failed its integrity check — it was altered, truncated, "
            f"or moved from somewhere else in the vault") from exc


def _atomic_write(path: str, blob: bytes) -> None:
    """Write via a temp file in the SAME directory, then rename + fsync.

    A power cut mid-encrypt must leave either the old plaintext or the new
    ciphertext, never a half-written file — on a solar node that loses power
    without warning, this is the difference between a record and a hole
    (``sd-reliability-overlayfs``).
    """
    tmp = f"{path}.rnmtmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dirfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)


def encrypt_tree(data_key: bytes, root: str) -> List[str]:
    """Encrypt every file under *root* in place. Returns what changed.

    Already-encrypted files are skipped, so this is safe to re-run after an
    interrupted pass — which is the state a power cut leaves behind.
    """
    done: List[str] = []
    for path, rel in _walk(root):
        with open(path, "rb") as fh:
            blob = fh.read()
        if is_encrypted(blob):
            continue
        _atomic_write(path, encrypt_bytes(data_key, blob, rel))
        done.append(rel)
    return done


def decrypt_tree(data_key: bytes, root: str) -> List[str]:
    """Turn *root* back into plain files. The way OUT of encryption.

    Every encrypt-at-rest scheme needs this and most ship without it. Without a
    revert, a forgotten passphrase and a format change are the same disaster,
    and the operator can never take the medic back to a state they understand.
    """
    done: List[str] = []
    for path, rel in _walk(root):
        with open(path, "rb") as fh:
            blob = fh.read()
        if not is_encrypted(blob):
            continue
        _atomic_write(path, decrypt_bytes(data_key, blob, rel))
        done.append(rel)
    return done


def _walk(root: str):
    """(abspath, relpath) for every regular file under *root*, sorted.

    Symlinks are skipped: following one would encrypt whatever it points at,
    which for ~/.reticulum-node-medic could reach outside the records entirely.
    The keyring itself is skipped — encrypting the thing that holds the key is
    the one edit that would make the vault unopenable.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        skip = SKIP_DIRS if dirpath == root else ()
        dirnames[:] = sorted(d for d in dirnames
                             if d not in skip
                             and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if name == KEYRING_NAME or name.endswith(".rnmtmp"):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, os.path.relpath(full, root)


# --------------------------------------------------------------------------- #
# Keyring on disk.
# --------------------------------------------------------------------------- #

def keyring_path(root: str) -> str:
    return os.path.join(root, KEYRING_NAME)


def save_keyring(root: str, ring: Keyring) -> str:
    path = keyring_path(root)
    _atomic_write(path, ring.to_json().encode("utf-8"))
    os.chmod(path, 0o600)
    return path


def load_keyring(root: str) -> Keyring:
    path = keyring_path(root)
    if not os.path.exists(path):
        raise VaultError("this medic's records are not encrypted")
    with open(path, "r", encoding="utf-8") as fh:
        return Keyring.from_json(fh.read())


def is_vault(root: str) -> bool:
    return os.path.exists(keyring_path(root))


# --------------------------------------------------------------------------- #
# Turning it on and off.
# --------------------------------------------------------------------------- #

def records_root(home: Optional[str] = None) -> str:
    """The one directory this vault covers.

    Read off ``vault.RECORDS_ROOTS`` rather than spelled again here, so the
    setup screens, the migration planner and the encryptor can never disagree
    about what is protected — they disagreed once already, when the screens
    promised records-only and the default config migrated the mesh identity too.
    """
    from provisioning.vault import RECORDS_ROOTS
    return os.path.join(os.path.expanduser(home or "~"), RECORDS_ROOTS[0])


def enable_vault(root: str, daily_secret: str, passphrase: str,
                 recovery_key: str,
                 params: Optional[ScryptParams] = None) -> Dict[str, object]:
    """Encrypt *root* and install the three doors. Returns what was done.

    ORDER MATTERS. The keyring is written BEFORE the files are encrypted. If the
    power goes between the two, the next run finds a keyring and an unencrypted
    (or half-encrypted) tree, and ``encrypt_tree`` finishes the job. The other
    order loses everything: files encrypted under a data key that was never
    written down anywhere.
    """
    if is_vault(root):
        raise VaultError("this medic's records are already encrypted")
    if not os.path.isdir(root):
        raise VaultError(f"there are no records at {root} to encrypt")
    for name, secret in (("daily unlock", daily_secret),
                         ("passphrase", passphrase),
                         ("recovery key", recovery_key)):
        if not secret:
            raise VaultError(
                f"refusing to encrypt without a {name} — every door has to work "
                f"before the vault is worth opening")

    dk = new_data_key()
    ring = Keyring()
    ring.add(dk, daily_secret, SLOT_DAILY, params)
    ring.add(dk, passphrase, SLOT_PASSPHRASE, params)
    ring.add(dk, recovery_key, SLOT_RECOVERY, params)

    save_keyring(root, ring)

    # Prove every door BEFORE a single file is encrypted — and prove it against
    # the keyring AS WRITTEN, reloaded from disk, because a slot that only works
    # in memory is a locked-out operator once the medic reboots. ``only=`` keeps
    # this to one scrypt derivation per door instead of one per door PER SLOT,
    # which is what made enabling take 19s on the live medic instead of 10.
    check = load_keyring(root)
    for name, secret in ((SLOT_DAILY, daily_secret),
                         (SLOT_PASSPHRASE, passphrase),
                         (SLOT_RECOVERY, recovery_key)):
        if check.open(secret, only=name)[0] != dk:
            os.remove(keyring_path(root))
            raise VaultError(f"the {name} door did not open when read back — "
                             f"nothing was encrypted")

    files = encrypt_tree(dk, root)
    return {"root": root, "files": len(files), "doors": ring.names()}


def open_vault(root: str, secret: str,
               only: Optional[str] = None) -> Tuple[bytes, str]:
    """(data_key, door_name). Raises with a plain sentence if nothing opens.

    *only* names the door the operator chose, when the caller knows it — one
    scrypt derivation (1.6s) instead of three (4.8s)."""
    ring = load_keyring(root)
    dk, name = ring.open(secret, only=only)
    if dk is None:
        raise VaultError("that did not open the vault")
    return dk, name


def disable_vault(root: str, secret: str) -> Dict[str, object]:
    """Decrypt everything and remove the keyring. The way back out.

    The keyring goes LAST, mirroring enable_vault: while any file is still
    encrypted the keyring must survive, or the remaining files become
    unreadable by anyone including the operator.
    """
    dk, door = open_vault(root, secret)
    files = decrypt_tree(dk, root)
    os.remove(keyring_path(root))
    return {"root": root, "files": len(files), "opened_with": door}
