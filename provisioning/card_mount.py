"""Where the medic opens a card with sudo, and the one way it does it.

THE PROPERTY (provisioning/security/README.md). Every card partition the medic
mounts as root goes to a folder under /run/nodemedic. /run belongs to root, so
these folders are root's: the scoped sudo policy lets the app create them with
one exact ``install -d`` rule, and the app account cannot put anything of its
own in their place. The mount is pinned in the same policy, device pattern and
options alike, and every card gets the same options:

    nosymfollow   a symbolic link stored on the card is never followed
    nodev         a device file stored on the card opens nothing
    nosuid        a set-user-id file on the card gains nothing
    noexec        nothing on the card runs

So a file root writes into one of these folders lands on the card, and only
there. (The folders used to be in /tmp, which every account can write to.)

nosymfollow needs Linux 5.10 and util-linux 2.38 (Debian 12 or later; the medic
runs Debian 13). The argument strings below are what
provisioning/sudoers.d/nodemedic grants word for word, and
tests/test_card_mounts.py holds the two together: change one, change both.

Callers keep their own ``sudo -n`` in front, so every privileged call stays
visible to tests/test_privileged_commands.py where it is made.
"""

from __future__ import annotations

import re
import shlex

#: The root-owned parent of every card mount point.
RUN_DIR = "/run/nodemedic"

#: A node card's boot partition (provisioning.sd_edit), and the boot partition
#: the clone check reads back (workflows.mitosis_card).
SD_BOOT = f"{RUN_DIR}/sd_boot"
#: A fresh card's boot partition (pi_imager's first-boot seed), and the ROOT
#: partition the clone check reads back — the name is historical.
PIBOOT = f"{RUN_DIR}/piboot"
#: The boot partition of a card whose first-boot seed is rewritten in place.
RESEED = f"{RUN_DIR}/reseed"
#: A card's root partition (the old cable-birth bake).
PIROOT = f"{RUN_DIR}/piroot"
#: A card's root partition while its shipped account is switched on.
PIROOT_USER = f"{RUN_DIR}/piroot_user"

#: The same for every card; the policy pins exactly this string.
OPTIONS = "nosymfollow,nodev,nosuid,noexec"

_NAME = re.compile(r"[a-z][a-z_]*")


def _inside(mnt: str) -> str:
    """*mnt*, refused unless it is one plain folder directly under RUN_DIR —
    the only places the policy lets a card be mounted."""
    parent, _, name = (mnt or "").rpartition("/")
    if parent != RUN_DIR or not _NAME.fullmatch(name):
        raise ValueError(f"not a card mount point under {RUN_DIR}: {mnt!r}")
    return mnt


def make_dir(mnt: str) -> str:
    """``install`` arguments (after ``sudo -n``) that create *mnt* and its
    parent, both root-owned and readable, so the app can still read a card's
    files as itself."""
    return f"install -d -m 0755 -o root -g root {RUN_DIR} {_inside(mnt)}"


def mount(part: str, mnt: str) -> str:
    """``mount`` arguments (after ``sudo -n``): partition *part* at *mnt*,
    with the pinned options."""
    return f"mount -o {OPTIONS} {shlex.quote(part)} {_inside(mnt)}"
