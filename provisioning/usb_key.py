"""Writing the vault's USB key onto a stick — finding it, writing it, reading it
back.

``vault_factors`` knows what a key file is WORTH (``keyfile_secret``) and where
to look for one at unlock time (``find_keyfile``). It deliberately never writes
anything. This module is the other half: the one moment in a medic's life when a
key file is created, during the first-use setup.

THREE THINGS IT REFUSES TO DO, each of them a way to lose a vault:

* **Overwrite a key file that is already there.** The stick in the operator's
  hand may be the one that unlocks their OTHER medic, or a second medic's spare.
  ``nodemedic.key`` is one filename on every stick this project has ever
  written, so a silent overwrite is not a hypothetical — it is the default
  outcome of plugging in the wrong stick, and the medic it locks out is not the
  one on the bench. It refuses and names the file it found.
* **Trust its own write.** Every write is read back and compared before this
  module says a word ([[say-only-what-you-checked]]). A stick that is failing,
  mounted read-only, or full will accept a write and return success; the failure
  turns up months later, at the only moment the key is needed.
* **Guess which stick.** With two removable volumes mounted the operator is told
  which paths were found and picks. The medic choosing for them means a key
  written to a device they were not thinking about.

The listing and the file operations are injected so the whole thing is testable
without a stick, a mount point or root.
"""

from __future__ import annotations

import os
import secrets
from typing import Callable, List, Optional

from provisioning.vault_factors import KEYFILE_NAME

#: Where a desktop Raspberry Pi mounts removable media. A stick usually lands at
#: /media/<user>/<label>; some setups mount straight to /media/<label>.
MEDIA_ROOT = "/media"

#: Bytes of random in a key file. 32 would already exceed what the hash can
#: carry; 64 costs nothing and leaves room to change the derivation later
#: without every existing stick becoming the weak link.
KEY_BYTES = 64


class KeyfileError(RuntimeError):
    """The stick could not be used, and the message says which of the reasons."""


def mount_points(media_root: str = MEDIA_ROOT, lister: Callable = None
                 ) -> List[str]:
    """Directories that look like a mounted removable volume, shallowest first.

    Both shapes, because both happen: ``/media/<label>`` and
    ``/media/<user>/<label>``. Returned SORTED and complete rather than
    first-match, so the caller can tell the difference between "no stick" and
    "which of these two did you mean".
    """
    ls = lister or _safe_listdir
    found: List[str] = []
    for first in ls(media_root):
        p1 = os.path.join(media_root, first)
        found.append(p1)
        for second in ls(p1):
            found.append(os.path.join(p1, second))
    return sorted(found)


def _safe_listdir(path: str) -> List[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def existing_key(directory: str, name: str = KEYFILE_NAME,
                 exists: Callable = None) -> Optional[str]:
    """The path of a key file already on this stick, or None."""
    path = os.path.join(directory, name)
    check = exists or os.path.exists
    return path if check(path) else None


def write_key(directory: str, name: str = KEYFILE_NAME, size: int = KEY_BYTES,
              rand: Callable = None, writer: Callable = None,
              reader: Callable = None, exists: Callable = None) -> tuple:
    """Write a fresh key file to *directory*. Returns ``(path, data)``.

    Raises ``KeyfileError`` — never a bare OSError — so the screen has one thing
    to catch and one sentence to show.

    THE READ-BACK IS NOT OPTIONAL and it is not a courtesy. A stick mounted
    read-only, a stick that is dying, and a stick with no space left all accept
    a write and report success; the difference only shows on the read. The
    medic must not tell an operator their USB key is made until it has seen the
    bytes come back.
    """
    if existing_key(directory, name, exists=exists):
        raise KeyfileError(
            f"There is already a {name} on this stick. It may be the key to "
            f"another Node Medic — overwriting it would lock that one out. Use "
            f"a different stick, or delete that file yourself if you are sure.")

    data = (rand or secrets.token_bytes)(size)
    path = os.path.join(directory, name)
    try:
        (writer or _write_owner_only)(path, data)
    except OSError as e:
        raise KeyfileError(f"Couldn't write to the stick: {e}") from e

    try:
        back = (reader or _read)(path)
    except OSError as e:
        raise KeyfileError(
            f"Wrote the key but couldn't read it back: {e}") from e
    if back != data:
        raise KeyfileError(
            "The key read back from the stick is not what was written. Do not "
            "rely on this stick — try another one.")
    return (path, data)


def _write_owner_only(path: str, data: bytes) -> None:
    """0600 where the filesystem carries permissions.

    A stick is usually vfat, which carries none, and that is worth saying out
    loud rather than pretending otherwise: the protection on a USB key is that
    the operator holds it, not that a mode bit is set. The mode is applied
    anyway for the case where the stick is ext4, and because it costs nothing.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)          # a stick pulled a second later must still have it
    finally:
        os.close(fd)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def describe_targets(points: List[str]) -> str:
    """One honest sentence about what was found — for the screen.

    Never "insert a USB stick" when one IS inserted and merely unreadable, and
    never a count without the paths: with two sticks plugged in the operator has
    to be able to tell which is which, and the mount path is the only thing the
    medic can show them that they can also check.
    """
    if not points:
        return ("No USB stick found. Plug one into Node Medic — it appears here "
                "on its own.")
    if len(points) == 1:
        return f"Found one removable volume: {points[0]}"
    return ("More than one removable volume is mounted — pick the one you mean:\n"
            + "\n".join(points))
